"""가져오기 검증과 차이 계산. DB를 쓰지 않는 순수 함수로 둔다.

verify_summary: 뱅샐현황의 대분류×월 칸과 내역 합계를 대조한다. 어긋나면 파일을 격리한다.
compute_diff: 겹치는 1년치 파일을 기존 장부와 비교해 추가·갱신·부재를 가른다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.ingest.parser import ParsedExport, SourceRow

MASS_MISSING_MIN_ROWS = 20
MASS_MISSING_RATIO = 0.5


class ReconciliationFailed(Exception):
    def __init__(self, mismatches: list[tuple[str, str, str]]):
        super().__init__(f"현금흐름 표와 내역 합계가 {len(mismatches)}칸에서 다릅니다.")
        self.mismatches = mismatches


class SuspiciousDisappearance(Exception):
    """한 결제수단의 행이 대량으로 사라졌다. 연동 해제나 파일 이상일 수 있어 사람이 확인해야 한다."""


@dataclass(frozen=True)
class SummaryCheck:
    cells_checked: int
    cells_skipped: int


def verify_summary(parsed: ParsedExport) -> SummaryCheck:
    """표의 칸은 해당 타입·대분류·월 금액 합계의 절대값과 같아야 한다.

    외화 행이 섞인 달은 원화 환산 방식을 알 수 없어 그 대분류만 건너뛴다.
    """
    totals: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    foreign: set[tuple[str, str, str]] = set()
    for row in parsed.rows:
        if row.type == "이체":
            continue
        key = (row.type, row.major, row.month)
        totals[key] += row.amount
        if row.currency != "KRW":
            foreign.add(key)

    checked = skipped = 0
    mismatches: list[tuple[str, str, str]] = []
    tables = {"수입": parsed.income_table, "지출": parsed.expense_table}
    for transaction_type, table in tables.items():
        for label, cells in table.items():
            for month, value in cells.items():
                key = (transaction_type, label, month)
                if key in foreign:
                    skipped += 1
                    continue
                checked += 1
                if abs(totals.get(key, Decimal(0))) != abs(Decimal(value)):
                    mismatches.append(key)

    # 표에 없는 대분류나 월에 금액이 있어도 어긋난 것이다
    for key, amount in totals.items():
        transaction_type, label, month = key
        if amount != 0 and key not in foreign and month not in tables[transaction_type].get(label, {}):
            mismatches.append(key)

    if mismatches:
        raise ReconciliationFailed(mismatches)
    return SummaryCheck(cells_checked=checked, cells_skipped=skipped)


def normalize_description(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip().casefold()


def key_base(row: SourceRow) -> str:
    """앱에서 바뀔 수 있는 타입·분류·메모는 키에서 뺀다."""
    parts = (
        row.occurred_at.isoformat(timespec="seconds"),
        format(row.amount.normalize(), "f"),
        row.currency,
        row.instrument,
        normalize_description(row.description),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredTxn:
    id: int
    key_base: str
    occurrence: int
    occurred_at: datetime
    type: str
    major: str
    minor: str
    description: str
    amount: Decimal
    currency: str
    instrument: str
    memo: str | None
    status: str
    user_touched: bool = False

    @property
    def occurred_date(self) -> date:
        return self.occurred_at.date()


@dataclass(frozen=True)
class NewRow:
    row: SourceRow
    key_base: str
    occurrence: int


@dataclass(frozen=True)
class MatchedRow:
    stored: StoredTxn
    row: SourceRow
    changed: dict[str, object]
    new_key_base: str
    new_occurrence: int


@dataclass
class Diff:
    new: list[NewRow] = field(default_factory=list)
    matched: list[MatchedRow] = field(default_factory=list)
    missing: list[StoredTxn] = field(default_factory=list)  # 집계에서 뺀다
    missing_kept: list[StoredTxn] = field(default_factory=list)  # 사용자가 수정해 집계에 유지한다
    is_stale: bool = False  # 이미 받은 파일보다 오래된 파일. 새 행만 받고 기존 행은 건드리지 않는다.

    @property
    def updated(self) -> list[MatchedRow]:
        return [m for m in self.matched if m.changed]

    @property
    def restored(self) -> list[MatchedRow]:
        return [] if self.is_stale else [m for m in self.matched if m.stored.status == "missing"]


def _changed_fields(stored: StoredTxn, row: SourceRow) -> dict[str, object]:
    pairs = {
        "occurred_at": (stored.occurred_at, row.occurred_at),
        "type": (stored.type, row.type),
        "major": (stored.major, row.major),
        "minor": (stored.minor, row.minor),
        "description": (stored.description, row.description),
        "memo": (stored.memo, row.memo),
    }
    return {name: new for name, (old, new) in pairs.items() if old != new}


def compute_diff(
    stored: list[StoredTxn],
    rows: list[SourceRow],
    period: tuple[date, date],
    *,
    is_stale: bool = False,
) -> Diff:
    """stored는 기존 거래 전체, rows는 새 파일의 행이다.

    is_stale이면 이미 받은 파일보다 오래된 파일이다. 옛날 값으로 되돌리면 안 되므로
    없던 행만 추가하고, 기존 행의 갱신·복귀·부재 판정은 하지 않는다.
    """
    diff = Diff(is_stale=is_stale)
    stored_by_key: dict[str, list[StoredTxn]] = defaultdict(list)
    for item in stored:
        stored_by_key[item.key_base].append(item)
    for items in stored_by_key.values():
        items.sort(key=lambda item: item.occurrence)

    # 1단계: 같은 키끼리 순번대로 짝짓는다. 동일한 행이 여러 개일 수 있다.
    rows_by_key: dict[str, list[SourceRow]] = defaultdict(list)
    for row in rows:
        rows_by_key[key_base(row)].append(row)

    unmatched_rows: list[tuple[SourceRow, str]] = []
    matched_ids: set[int] = set()
    for key, group in rows_by_key.items():
        existing = stored_by_key.get(key, [])
        for index, row in enumerate(group):
            if index < len(existing):
                item = existing[index]
                matched_ids.add(item.id)
                changed = {} if is_stale else _changed_fields(item, row)
                diff.matched.append(MatchedRow(item, row, changed, item.key_base, item.occurrence))
            else:
                unmatched_rows.append((row, key))

    start, end = period
    orphans = [
        item
        for item in stored
        if item.id not in matched_ids and start <= item.occurred_date <= end and item.status != "missing"
    ]
    restorable = [
        item
        for item in stored
        if item.id not in matched_ids and start <= item.occurred_date <= end and item.status == "missing"
    ]

    # 2단계: 내용이나 시간 중 하나만 바뀐 거래. 날짜·금액·결제수단이 양쪽 모두 유일할 때만 같은 거래로 본다.
    # 둘 다 다르면 다른 거래일 가능성이 높고, 잘못 합치면 사용자의 분류가 엉뚱한 거래로 옮겨 간다.
    def loose_key_stored(item: StoredTxn) -> tuple:
        return (item.occurred_date, item.amount, item.currency, item.instrument)

    def loose_key_row(row: SourceRow) -> tuple:
        return (row.occurred_at.date(), row.amount, row.currency, row.instrument)

    orphans_by_loose: dict[tuple, list[StoredTxn]] = defaultdict(list)
    for item in orphans + restorable:
        orphans_by_loose[loose_key_stored(item)].append(item)
    rows_by_loose: dict[tuple, list[tuple[SourceRow, str]]] = defaultdict(list)
    for pair in unmatched_rows:
        rows_by_loose[loose_key_row(pair[0])].append(pair)

    # 다른 키로 옮겨 간 거래가 있으면 순번에 빈자리가 생기므로, 개수가 아니라 최댓값 다음 번호를 쓴다.
    next_occurrence = {key: max(i.occurrence for i in items) + 1 for key, items in stored_by_key.items() if items}

    def take_occurrence(key: str) -> int:
        occurrence = next_occurrence.get(key, 0)
        next_occurrence[key] = occurrence + 1
        return occurrence

    def probably_same(item: StoredTxn, row: SourceRow) -> bool:
        same_time = item.occurred_at == row.occurred_at
        same_text = normalize_description(item.description) == normalize_description(row.description)
        return same_time or same_text

    still_unmatched: list[tuple[SourceRow, str]] = []
    for loose, pairs in rows_by_loose.items():
        candidates = orphans_by_loose.get(loose, [])
        if not is_stale and len(pairs) == 1 and len(candidates) == 1 and probably_same(candidates[0], pairs[0][0]):
            item, (row, key) = candidates[0], pairs[0]
            matched_ids.add(item.id)
            diff.matched.append(MatchedRow(item, row, _changed_fields(item, row), key, take_occurrence(key)))
        else:
            still_unmatched.extend(pairs)

    # 새 행은 같은 키의 기존 순번 다음 번호를 받는다.
    for row, key in still_unmatched:
        diff.new.append(NewRow(row, key, take_occurrence(key)))

    if is_stale:
        return diff

    # 부재 판정: 파일 기간 안에서 사라진 행. 경계일은 자료가 잘려 있을 수 있어 제외한다.
    candidates = [
        item for item in stored
        if item.id not in matched_ids and item.status != "missing" and start < item.occurred_date < end
    ]
    in_period: dict[str, int] = defaultdict(int)
    for item in stored:
        if item.status != "missing" and start < item.occurred_date < end:
            in_period[item.instrument] += 1
    lost: dict[str, int] = defaultdict(int)
    for item in candidates:
        lost[item.instrument] += 1
    for instrument, count in lost.items():
        if count >= MASS_MISSING_MIN_ROWS and count / in_period[instrument] >= MASS_MISSING_RATIO:
            raise SuspiciousDisappearance("한 결제수단의 거래가 대량으로 사라졌습니다.")

    for item in candidates:
        (diff.missing_kept if item.user_touched else diff.missing).append(item)
    return diff
