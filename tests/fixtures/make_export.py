"""뱅크샐러드 내보내기를 흉내 낸 합성 파일을 만든다. 실제 데이터는 넣지 않는다.

구조는 docs/banksalad-format.md를 따른다. SENTINELS는 출력에 나오면 안 되는 값이다.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import openpyxl
import pyzipper
from msoffcrypto.format.ooxml import OOXMLFile
from openpyxl.utils import get_column_letter

CUSTOMER_NAME = "홍길동테스트"
CUSTOMER_EMAIL = "secret-owner@example.com"
PERSON_NAME = "김비밀수신"
MERCHANT = "비밀가맹점커피"
MEMO = "비밀메모입니다"
ACCOUNT_NUMBER = "110-123-456789"
CARD_NAME = "신한카드 1234-5678"
BANK_ACCOUNT_NAME = f"토스뱅크 {ACCOUNT_NUMBER}"
SECRET_AMOUNT = 987654
INCOME_AMOUNT = 3456789
NET_WORTH = 12345678
PRODUCT_NAME = "비밀상품저축"

SENTINELS = [
    CUSTOMER_NAME,
    CUSTOMER_EMAIL,
    PERSON_NAME,
    MERCHANT,
    MEMO,
    ACCOUNT_NUMBER,
    "1234-5678",
    PRODUCT_NAME,
    str(SECRET_AMOUNT),
    str(INCOME_AMOUNT),
    str(NET_WORTH),
]

HEADER = ["날짜", "시간", "타입", "대분류", "소분류", "내용", "금액", "화폐", "결제수단", "메모"]
PERIOD_NAME = "2026-01-01~2026-02-28.xlsx"


@dataclass(frozen=True)
class AssetRow:
    group: str
    product: str
    amount: int


ROWS: list[list] = [
    [datetime(2026, 1, 3), time(12, 30, 15), "지출", "식비", "카페", MERCHANT, -SECRET_AMOUNT, "KRW", CARD_NAME, MEMO],
    [datetime(2026, 1, 3), time(12, 30, 15), "지출", "식비", "카페", MERCHANT, -SECRET_AMOUNT, "KRW", CARD_NAME, None],
    [datetime(2026, 1, 25), time(0, 0, 0), "수입", "급여", "미분류", "급여 입금", INCOME_AMOUNT, "KRW", BANK_ACCOUNT_NAME, None],
    [datetime(2026, 2, 10), time(9, 0, 0), "이체", "카드대금", "미분류", "신한카드 결제대금", -SECRET_AMOUNT, "KRW", BANK_ACCOUNT_NAME, None],
    [datetime(2026, 2, 11), time(8, 0, 0), "이체", "내계좌이체", "미분류", PERSON_NAME, -SECRET_AMOUNT, "KRW", BANK_ACCOUNT_NAME, None],
    [datetime(2026, 2, 12), time(8, 5, 0), "지출", "쇼핑", "온라인쇼핑", "취소된 주문", SECRET_AMOUNT, "KRW", CARD_NAME, None],
    [datetime(2026, 2, 13), time(8, 5, 0), "지출", "문화", "구독", "NETFLIX", -11, "USD", "네이버페이 머니", None],
]
ASSETS = [
    AssetRow("자유입출금 자산", BANK_ACCOUNT_NAME, NET_WORTH),
    AssetRow("저축성 자산", PRODUCT_NAME, 500),
]
LIABILITIES = [AssetRow("장기대출", "비밀대출상품", 100)]
LOANS = [{2: "장기대출", 3: "OO은행", 4: "비밀대출상품", 6: 200, 7: 100, 8: 4.5, 9: datetime(2026, 2, 1), 10: datetime(2031, 2, 1)}]


def tx(
    when: str,
    transaction_type: str,
    major: str,
    minor: str,
    description: str,
    amount: float,
    instrument: str,
    *,
    currency: str = "KRW",
    memo: str | None = None,
) -> list:
    """when은 'YYYY-MM-DD HH:MM:SS' 형식이다."""
    stamp = datetime.fromisoformat(when)
    return [
        datetime(stamp.year, stamp.month, stamp.day),
        stamp.time(),
        transaction_type,
        major,
        minor,
        description,
        amount,
        currency,
        instrument,
        memo,
    ]


def loan_row(kind: str, org: str, product: str, balance: int, rate: float | None = None) -> dict[int, object]:
    """6.대출현황의 한 행. 날짜는 비워 둔다."""
    row: dict[int, object] = {2: kind, 3: org, 4: product, 6: balance, 7: balance}
    if rate is not None:
        row[8] = rate
    return row


def _month(row: list) -> str:
    return f"{row[0].year:04d}-{row[0].month:02d}"


def _write_cashflow(sheet, start_row: int, rows: Sequence[list], months: Sequence[str]) -> int:
    sheet.cell(start_row, 2, "2.현금흐름현황")
    sheet.cell(start_row + 1, 2, "최근 1년 동안의 현금흐름을 분석합니다.")
    header = start_row + 2
    for column, label in zip((2, 3, 4), ("항목", "총계", "월평균")):
        sheet.cell(header, column, label)
    for index, month in enumerate(months):
        sheet.cell(header, 5 + index, month)

    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in rows:
        if row[2] != "이체":
            totals[(row[2], row[3], _month(row))] += row[6]

    current = header + 1

    def write_group(transaction_type: str, total_label: str) -> int:
        nonlocal current
        first = current
        labels = sorted({key[1] for key in totals if key[0] == transaction_type})
        for label in labels:
            sheet.cell(current, 2, label)
            sheet.cell(current, 3, f"=SUM(E{current}:{get_column_letter(4 + len(months))}{current})")
            sheet.cell(current, 4, f"=C{current}/{len(months)}")
            for index, month in enumerate(months):
                sheet.cell(current, 5 + index, abs(totals.get((transaction_type, label, month), 0)))
            current += 1
        sheet.cell(current, 2, total_label)
        for index in range(len(months)):
            letter = get_column_letter(5 + index)
            sheet.cell(current, 5 + index, f"=SUM({letter}{first}:{letter}{current - 1})")
        total_row = current
        current += 1
        return total_row

    income_total = write_group("수입", "월수입 총계")
    expense_total = write_group("지출", "월지출 총계")
    sheet.cell(current, 2, "순수입 총계")
    for index in range(len(months)):
        letter = get_column_letter(5 + index)
        sheet.cell(current, 5 + index, f"={letter}{income_total}-{letter}{expense_total}")
    return current + 3


def _write_balance_sheet(sheet, start_row: int, assets, liabilities) -> int:
    sheet.cell(start_row, 2, "3.재무현황")
    sheet.cell(start_row + 1, 2, "데이터를 내보낸 시점의 자산과 부채 상태를 분석합니다.")
    sheet.cell(start_row + 2, 2, "자산")
    sheet.cell(start_row + 2, 6, "부채")
    header = start_row + 3
    for column, label in zip((2, 3, 5, 6, 7, 9), ("항목", "상품명", "금액", "항목", "상품명", "금액")):
        sheet.cell(header, column, label)

    current = header + 1
    first = current
    previous = None
    for asset in assets:
        if asset.group != previous:
            sheet.cell(current, 2, asset.group)
            previous = asset.group
        sheet.cell(current, 3, asset.product)
        sheet.cell(current, 5, asset.amount)
        current += 1
    sheet.cell(current, 2, "연금 자산")  # 상품 없는 항목 행
    current += 1

    debt_row = first
    previous = None
    for debt in liabilities:
        if debt.group != previous:
            sheet.cell(debt_row, 6, debt.group)
            previous = debt.group
        sheet.cell(debt_row, 7, debt.product)
        sheet.cell(debt_row, 9, debt.amount)
        debt_row += 1

    current = max(current, debt_row)
    sheet.cell(current, 2, "총자산")
    sheet.cell(current, 5, f"=SUM(E{first}:E{current - 1})")
    sheet.cell(current, 6, "총부채")
    sheet.cell(current, 9, f"=SUM(I{first}:I{current - 1})")
    sheet.cell(current + 1, 2, "순자산")
    sheet.cell(current + 2, 2, f"=E{current}-I{current}")
    return current + 5


def _write_detail(sheet, start_row: int, title: str, note: str, columns: dict[int, str], rows: Sequence[dict[int, object]]) -> int:
    sheet.cell(start_row, 2, title)
    sheet.cell(start_row + 1, 2, note)
    header = start_row + 2
    for column, label in columns.items():
        sheet.cell(header, column, label)
    current = header + 1
    for row in rows:
        for column, value in row.items():
            sheet.cell(current, column, value)
        current += 1
    sheet.cell(current, 2, "총계")
    return current + 3


def build_xlsx(
    rows: Sequence[list] = ROWS,
    assets: Sequence[AssetRow] = ASSETS,
    liabilities: Sequence[AssetRow] = LIABILITIES,
    *,
    loans: Sequence[dict[int, object]] = LOANS,
    months: Sequence[str] | None = None,
    tamper: Callable[[openpyxl.Workbook], None] | None = None,
) -> bytes:
    workbook = openpyxl.Workbook()
    summary = workbook.active
    summary.title = "뱅샐현황"

    summary["B3"] = "1.고객정보"
    summary["B4"] = "사용자의 기본 정보에 해당하는 항목입니다."
    for column, label in zip("BCDEF", ("이름", "성별", "연령\n(만 나이)", "신용점수\n(KCB)", "이메일")):
        summary[f"{column}5"] = label
    for column, value in zip("BCDEF", (CUSTOMER_NAME, "남", 33, 777, CUSTOMER_EMAIL)):
        summary[f"{column}6"] = value

    used_months = list(months) if months else sorted({_month(row) for row in rows}) or ["2026-01"]
    next_row = _write_cashflow(summary, 9, rows, used_months)
    next_row = _write_balance_sheet(summary, next_row, assets, liabilities)
    next_row = _write_detail(
        summary,
        next_row,
        "4.보험현황",
        "사용자가 보유한 보험상품 현황을 분석합니다.",
        {2: "금융사", 3: "보험명", 5: "계약상태", 6: "총납입금", 7: "계약일자", 8: "만기일자"},
        [{2: "OO생명", 3: "OO보험", 5: "유지", 6: 1000, 7: datetime(2024, 7, 1), 8: datetime(2087, 7, 1)}],
    )
    next_row = _write_detail(
        summary,
        next_row,
        "5.투자현황",
        "사용자가 보유한 투자상품 현황을 분석합니다.",
        {2: "투자상품종류", 3: "금융사", 4: "상품명", 6: "투자원금", 7: "평가금액", 8: "수익률", 9: "가입일자", 10: "만기일자"},
        [{2: "주식", 3: "OO증권", 4: "OO전자", 6: 100, 7: 120, 8: 20.0}],
    )
    _write_detail(
        summary,
        next_row,
        "6.대출현황",
        "사용자가 보유한 대출상품 현황을 분석합니다.",
        {2: "대출종류", 3: "금융사", 4: "상품명", 6: "대출원금", 7: "대출잔액", 8: "대출금리", 9: "대출신규일", 10: "대출만기일"},
        loans,
    )

    ledger = workbook.create_sheet("가계부 내역")
    ledger.append(HEADER)
    for row in sorted(rows, key=lambda r: (r[0], r[1]), reverse=True):
        ledger.append(list(row))
    for cells in ledger.iter_rows(min_row=2):
        cells[0].number_format = "yyyy-mm-dd"
        cells[1].number_format = "hh:mm"

    if tamper:
        tamper(workbook)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def zip_aes(content: bytes, password: str, inner_name: str = PERIOD_NAME) -> bytes:
    buffer = io.BytesIO()
    with pyzipper.AESZipFile(buffer, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.writestr(inner_name, content)
    return buffer.getvalue()


def zip_plain(content: bytes, inner_name: str = PERIOD_NAME) -> bytes:
    buffer = io.BytesIO()
    with pyzipper.ZipFile(buffer, "w", compression=pyzipper.ZIP_DEFLATED) as archive:
        archive.writestr(inner_name, content)
    return buffer.getvalue()


def zip_crypto(content: bytes, password: str, work_dir: Path, inner_name: str = PERIOD_NAME) -> bytes:
    """실제 파일과 같은 ZipCrypto. 시스템 zip 명령이 없으면 FileNotFoundError."""
    executable = shutil.which("zip")
    if executable is None:
        raise FileNotFoundError("zip")
    source = work_dir / inner_name
    source.write_bytes(content)
    target = work_dir / "out.zip"
    target.unlink(missing_ok=True)
    subprocess.run([executable, "-q", "-j", "-P", password, str(target), str(source)], check=True)
    return target.read_bytes()


def encrypt_xlsx(content: bytes, password: str) -> bytes:
    buffer = io.BytesIO()
    OOXMLFile(io.BytesIO(content)).encrypt(password, buffer)
    return buffer.getvalue()
