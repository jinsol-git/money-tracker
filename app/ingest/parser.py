"""엑셀을 읽어 구조화한다. 형식은 docs/banksalad-format.md를 따른다.

- 예상과 다르면 FormatChanged로 멈춘다. 잘못 읽어 장부에 넣는 것보다 격리가 낫다.
- 고객정보 구역(이름, 신용점수, 이메일)은 읽지 않는다.
- 합계 셀은 수식이라 값이 없으므로 읽지 않고 직접 계산한다.
- 긴 숫자열은 끝 4자리만 남긴다.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import openpyxl
from openpyxl.xml import DEFUSEDXML

PARSER_VERSION = "1"
MAX_ROWS = 50_000  # 실제 파일은 1년에 2천 행 남짓이다
MAX_COLUMNS = 30
LEDGER_SHEET = "가계부 내역"
SUMMARY_SHEET = "뱅샐현황"
LEDGER_COLUMNS = ("날짜", "시간", "타입", "대분류", "소분류", "내용", "금액", "화폐", "결제수단", "메모")
TRANSACTION_TYPES = {"수입", "지출", "이체"}
SECTION_TITLES = {
    "1": "고객정보",
    "2": "현금흐름현황",
    "3": "재무현황",
    "4": "보험현황",
    "5": "투자현황",
    "6": "대출현황",
}
DETAIL_SECTIONS = {
    "4": ("insurance", ("금융사", "보험명")),
    "5": ("investment", ("투자상품종류", "금융사", "상품명")),
    "6": ("loan", ("대출종류", "금융사", "상품명")),
}
TITLE_RE = re.compile(r"^(\d)\.\s*(\S+)$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
LONG_NUMBER_RE = re.compile(r"\d(?:[\d\- ]*\d)?")


class FormatChanged(Exception):
    """파일 구조가 예상과 다르다. 메시지에는 구조 정보만 넣는다."""


@dataclass(frozen=True, slots=True)
class SourceRow:
    row_number: int
    occurred_at: datetime
    type: str
    major: str
    minor: str
    description: str
    amount: Decimal
    currency: str
    instrument: str
    memo: str | None

    @property
    def month(self) -> str:
        return f"{self.occurred_at.year:04d}-{self.occurred_at.month:02d}"


@dataclass(frozen=True, slots=True)
class AssetLine:
    side: str  # asset 또는 liability
    group: str
    product: str
    amount: int


@dataclass(frozen=True, slots=True)
class DetailLine:
    section: str  # insurance, investment, loan
    fields: dict[str, Any]


@dataclass(frozen=True)
class ParsedExport:
    rows: list[SourceRow]
    months: list[str]
    income_table: dict[str, dict[str, int]]
    expense_table: dict[str, dict[str, int]]
    assets: list[AssetLine]
    details: list[DetailLine]


def mask_numbers(text: str) -> str:
    """숫자가 8자리 이상 이어지면 끝 4자리만 남긴다. 계좌·카드·전화번호를 저장하지 않기 위해서다."""

    def replace(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group())
        return f"****{digits[-4:]}" if len(digits) >= 8 else match.group()

    return LONG_NUMBER_RE.sub(replace, text)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return mask_numbers(unicodedata.normalize("NFC", str(value)).strip())


def _values(sheet: Any) -> list[list[Any]]:
    """작은 파일이 큰 메모리로 풀리는 공격을 막기 위해 행과 열에 상한을 둔다."""
    sheet.reset_dimensions()
    rows: list[list[Any]] = []
    for row in sheet.iter_rows(max_col=MAX_COLUMNS):
        rows.append([cell.value for cell in row])
        if len(rows) > MAX_ROWS:
            raise FormatChanged("행 수가 상한을 넘습니다.")
    width = max((len(row) for row in rows), default=0)
    for row in rows:
        row.extend([None] * (width - len(row)))
    return rows


def _cell(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _to_date(value: Any, row_number: int) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise FormatChanged(f"{row_number}행의 날짜 형식이 다릅니다.")


def _to_time(value: Any, row_number: int) -> time:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        rounded = datetime.combine(date(2000, 1, 1), value.replace(microsecond=0))
        if value.microsecond >= 500_000:
            rounded += timedelta(seconds=1)
        return rounded.time()
    raise FormatChanged(f"{row_number}행의 시간 형식이 다릅니다.")


def _to_amount(value: Any, currency: str, row_number: int) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise FormatChanged(f"{row_number}행의 금액 형식이 다릅니다.")
    amount = Decimal(str(value))
    if currency == "KRW" and amount != amount.to_integral_value():
        raise FormatChanged(f"{row_number}행의 원화 금액에 소수가 있습니다.")
    return amount


def _table_int(value: Any) -> int:
    """표의 금액 칸. 비어 있으면 0이다."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FormatChanged("현황 표의 금액 칸 형식이 다릅니다.")
    return round(value)


def _parse_ledger(rows: list[list[Any]]) -> list[SourceRow]:
    if not rows or tuple(_text(v) for v in rows[0][: len(LEDGER_COLUMNS)]) != LEDGER_COLUMNS:
        raise FormatChanged("가계부 내역의 열 구성이 다릅니다.")

    parsed: list[SourceRow] = []
    for number, row in enumerate(rows[1:], start=2):
        if all(value in (None, "") for value in row):
            continue
        transaction_type = _text(row[2])
        if transaction_type not in TRANSACTION_TYPES:
            raise FormatChanged(f"{number}행의 타입이 예상한 값이 아닙니다.")
        currency = _text(row[7])
        if not currency:
            raise FormatChanged(f"{number}행의 화폐가 비어 있습니다.")
        instrument = _text(row[8])
        if not instrument:
            raise FormatChanged(f"{number}행의 결제수단이 비어 있습니다.")
        parsed.append(
            SourceRow(
                row_number=number,
                occurred_at=datetime.combine(_to_date(row[0], number), _to_time(row[1], number)),
                type=transaction_type,
                major=_text(row[3]),
                minor=_text(row[4]),
                description=_text(row[5]),
                amount=_to_amount(row[6], currency, number),
                currency=currency,
                instrument=instrument,
                memo=_text(row[9]) or None,
            )
        )
    return parsed


def _locate_sections(rows: list[list[Any]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, row in enumerate(rows):
        value = _cell(row, 1)
        if not isinstance(value, str):
            continue
        match = TITLE_RE.match(value.strip())
        if match and SECTION_TITLES.get(match.group(1)) == match.group(2):
            found[match.group(1)] = index
    if set(found) != set(SECTION_TITLES):
        raise FormatChanged("뱅샐현황의 구역 제목이 예상과 다릅니다.")
    ordered = [found[key] for key in sorted(found)]
    if ordered != sorted(ordered):
        raise FormatChanged("뱅샐현황의 구역 순서가 예상과 다릅니다.")
    return found


def _parse_cashflow(rows: list[list[Any]]) -> tuple[list[str], dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    header_index = next(
        (
            i
            for i, row in enumerate(rows)
            if _cell(row, 1) == "항목" and _cell(row, 2) == "총계" and _cell(row, 3) == "월평균"
        ),
        None,
    )
    if header_index is None:
        raise FormatChanged("현금흐름 표의 머리글을 찾을 수 없습니다.")

    months = []
    for value in rows[header_index][4:]:
        if value in (None, ""):
            break
        label = _text(value)
        if not MONTH_RE.match(label):
            raise FormatChanged("현금흐름 표의 월 표기가 다릅니다.")
        months.append(label)
    if not months:
        raise FormatChanged("현금흐름 표에 월이 없습니다.")

    income: dict[str, dict[str, int]] = {}
    expense: dict[str, dict[str, int]] = {}
    current = income
    reached_expense = reached_end = False
    for row in rows[header_index + 1 :]:
        label = _text(_cell(row, 1))
        if not label:
            continue
        if label == "월수입 총계":
            current, reached_expense = expense, True
            continue
        if label == "월지출 총계":
            continue
        if label == "순수입 총계":
            reached_end = True
            break
        current[label] = {month: _table_int(_cell(row, 4 + i)) for i, month in enumerate(months)}
    if not (reached_expense and reached_end):
        raise FormatChanged("현금흐름 표의 합계 행을 찾을 수 없습니다.")
    return months, income, expense


def _parse_balance_sheet(rows: list[list[Any]]) -> list[AssetLine]:
    header_index = next(
        (
            i
            for i, row in enumerate(rows)
            if _cell(row, 1) == "항목"
            and _cell(row, 2) == "상품명"
            and _cell(row, 4) == "금액"
            and _cell(row, 5) == "항목"
            and _cell(row, 6) == "상품명"
            and _cell(row, 8) == "금액"
        ),
        None,
    )
    if header_index is None:
        raise FormatChanged("재무현황 표의 머리글을 찾을 수 없습니다.")

    lines: list[AssetLine] = []
    asset_group = liability_group = ""
    for row in rows[header_index + 1 :]:
        if _text(_cell(row, 1)) == "총자산":
            return lines
        asset_group = _text(_cell(row, 1)) or asset_group
        liability_group = _text(_cell(row, 5)) or liability_group
        for side, group, product, amount in (
            ("asset", asset_group, _cell(row, 2), _cell(row, 4)),
            ("liability", liability_group, _cell(row, 6), _cell(row, 8)),
        ):
            if product in (None, "") and amount in (None, ""):
                continue
            lines.append(
                AssetLine(side=side, group=group, product=_text(product) or group, amount=_table_int(amount))
            )
    raise FormatChanged("재무현황의 총자산 행을 찾을 수 없습니다.")


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _parse_detail(rows: list[list[Any]], section: str, required: tuple[str, ...]) -> list[DetailLine]:
    """rows[0]은 구역 제목, rows[1]은 안내문, rows[2]는 머리글이다."""
    if len(rows) < 3:
        raise FormatChanged("현황 구역의 행 수가 부족합니다.")
    headers = {i: _text(v).replace("\n", " ") for i, v in enumerate(rows[2]) if v not in (None, "")}
    if not set(required) <= set(headers.values()):
        raise FormatChanged("현황 표의 머리글이 다릅니다.")

    lines = []
    for row in rows[3:]:
        if _text(_cell(row, 1)) == "총계":
            break
        fields = {name: _json_safe(_cell(row, i)) for i, name in headers.items() if _cell(row, i) not in (None, "")}
        if fields:
            lines.append(DetailLine(section=section, fields=fields))
    return lines


def _parse_summary(rows: list[list[Any]]) -> tuple[list[str], dict, dict, list[AssetLine], list[DetailLine]]:
    sections = _locate_sections(rows)
    boundaries = {key: (sections[key], sections.get(str(int(key) + 1), len(rows))) for key in sections}

    def section_rows(key: str) -> list[list[Any]]:
        start, end = boundaries[key]
        return rows[start:end]

    months, income, expense = _parse_cashflow(section_rows("2"))
    assets = _parse_balance_sheet(section_rows("3"))
    details: list[DetailLine] = []
    for key, (section, required) in DETAIL_SECTIONS.items():
        details.extend(_parse_detail(section_rows(key), section, required))
    return months, income, expense, assets, details


def parse_workbook(xlsx: bytes) -> ParsedExport:
    if not DEFUSEDXML:
        raise RuntimeError("defusedxml 없이는 외부 엑셀을 읽지 않습니다.")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    except Exception as exc:  # 라이브러리 오류 메시지에 파일 내용이 섞일 수 있다
        raise FormatChanged("엑셀을 열 수 없습니다.") from exc
    try:
        if set(workbook.sheetnames) != {LEDGER_SHEET, SUMMARY_SHEET}:
            raise FormatChanged("시트 구성이 다릅니다.")
        ledger_rows = _values(workbook[LEDGER_SHEET])
        summary_rows = _values(workbook[SUMMARY_SHEET])
    except FormatChanged:
        raise
    except Exception as exc:  # 망가진 XML 등. 500이 되면 수신이 재시도를 반복한다.
        raise FormatChanged("시트를 읽을 수 없습니다.") from exc
    finally:
        workbook.close()

    rows = _parse_ledger(ledger_rows)
    months, income, expense, assets, details = _parse_summary(summary_rows)
    return ParsedExport(
        rows=rows,
        months=months,
        income_table=income,
        expense_table=expense,
        assets=assets,
        details=details,
    )
