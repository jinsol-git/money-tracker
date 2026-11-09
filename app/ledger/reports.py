"""집계. 화면과 챗이 같은 숫자를 보도록 report_lines 뷰 하나만 읽는다.

불변식: 실지출 합계 = 카드 + 계좌 + 페이 + 기타. 미확정 금액은 합계에 넣지 않고 따로 돌려준다.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from statistics import median

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session, selectinload

from app.ledger.labels import (
    FINANCIAL_OTHER_TYPES,
    LOAN_ESTIMATE_REASONS,
    LOAN_INTEREST_REASONS,
    LOAN_REASONS,
    PHYSICAL_ASSET_GROUPS,
    manual_group_label,
)
from app.ledger.models import AssetDetail, AssetItem, AssetSnapshot, ImportBatch, ManualAsset
from app.ledger.payment_links import ImpliedRate, implied_rate, link_kind, links_of
from app.ledger.recurring import EXPLAINED_NATURES, Suggestion, target_key
from app.ledger.recurring import Group as RecurringGroup
from app.ledger.recurring import Line as RecurringLine
from app.ledger.recurring import detect as detect_recurring

LOAN_MONTHS = 12
DETAIL_DAY = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")
NO_CATEGORY_KEY = "none"  # 차트에서 카테고리가 없는 거래를 부르는 값
UNTRACKED_REASONS = {"bill_unlinked", "bill_policy_count"}
UNDETERMINED = "nature = 'undetermined'"
LARGE_INFLOW = "amount > 0 AND nature = 'asset_move'"
UNCATEGORIZED = "category_key IN ('uncategorized_expense', 'uncategorized_income')"
LINE_COLUMNS = """txn_id, split_id, occurred_at, occurred_date, source_type, source_major, source_minor,
                   description, amount, instrument_id, instrument_name, nature, nature_source, nature_reason,
                   category_id, category_name, channel"""
LINE_FILTER = """(CAST(:month AS text) IS NULL OR month = :month)
              AND (CAST(:channel AS text) IS NULL OR channel = :channel)
              AND (CAST(:category_key AS text) IS NULL OR category_key = :category_key)
              AND (CAST(:instrument_id AS bigint) IS NULL OR instrument_id = :instrument_id)
              AND (CAST(:natures AS text[]) IS NULL OR nature = ANY(CAST(:natures AS text[])))
              AND (CAST(:pattern AS text) IS NULL OR description ILIKE :pattern ESCAPE '\\')
              AND (CAST(:date_from AS date) IS NULL OR occurred_date >= :date_from)
              AND (CAST(:date_to AS date) IS NULL OR occurred_date <= :date_to)"""


@dataclass(frozen=True)
class MonthSummary:
    month: str
    income: int
    spend_total: int
    spend_card: int
    spend_account: int
    spend_pay: int
    spend_other: int
    undetermined_in: int
    undetermined_out: int


@dataclass(frozen=True)
class DayFlow:
    income: int
    spend: int


@dataclass(frozen=True)
class CategoryLine:
    category_key: str | None
    category_name: str | None
    amount: int
    rows: int


@dataclass(frozen=True)
class InstrumentLine:
    instrument_id: int
    name: str
    kind: str
    amount: int
    rows: int


@dataclass(frozen=True)
class AssetGroupLine:
    as_of: date
    side: str  # asset 또는 liability
    group_label: str
    amount: int


@dataclass(frozen=True)
class FinanceLine:
    as_of: date
    side: str
    group_label: str
    product_name: str
    amount: int


@dataclass(frozen=True)
class InvestmentPoint:
    as_of: date
    principal: int
    valuation: int


@dataclass(frozen=True)
class AssetItemLine:
    side: str
    group_label: str
    product_name: str
    amount: int
    instrument_id: int | None


@dataclass(frozen=True)
class PlaceLine:
    name: str
    amount: int
    rows: int


@dataclass(frozen=True)
class LinkedPayment:
    name: str
    typical: int
    times: int
    nature: str | None = None
    split: bool = False  # 이자와 원금으로 나눴다
    direction: str = "out"  # out은 보낸 돈(납입), in은 받은 돈(수령)이다


@dataclass(frozen=True)
class LoanMonth:
    month: str
    interest: int
    principal: int
    exact: bool  # 두 잔액 기록 사이에서 구했다. 아니면 어림한 값이 섞였다


@dataclass(frozen=True)
class LoanLinks:
    payments: list[LinkedPayment]
    implied: ImpliedRate
    months: list[LoanMonth]  # 나눈 납입의 달마다 이자와 원금. 최근 LOAN_MONTHS달이다


@dataclass(frozen=True)
class AssetLinks:
    loans: list[str]
    payments: list[LinkedPayment]


@dataclass(frozen=True)
class PensionLinks:
    payments: list[LinkedPayment]
    contributed: int  # 연금에 넣은 돈
    received: int  # 연금에서 받은 돈
    flows: dict[date, int]  # 날짜마다 연금으로 들어간 돈. 받은 돈은 음수다


@dataclass(frozen=True)
class ChartLine:
    day: date
    kind: str  # income 또는 spend. spend의 환불은 음수다
    channel: str
    instrument_id: int
    instrument_name: str
    category_key: str | None
    category_name: str | None
    amount: int


@dataclass(frozen=True)
class AccountTrace:
    instrument_id: int
    month_from: str | None
    month_to: str | None
    total_out: int
    direct: dict[str, int]
    card_bills: int
    pay_charges: int
    savings_investment: int
    debt: int
    own_transfer: int
    untracked: int
    untracked_rows: int

    @property
    def trace_rate(self) -> float | None:
        base = self.total_out - self.own_transfer
        return 1 - self.untracked / base if base else None


@dataclass(frozen=True)
class NetWorthPoint:
    as_of: date
    total_assets: int
    total_liabilities: int

    @property
    def net_worth(self) -> int:
        return self.total_assets - self.total_liabilities


@dataclass(frozen=True)
class LineItem:
    txn_id: int
    split_id: int
    occurred_at: datetime
    occurred_date: date
    source_type: str
    source_major: str
    source_minor: str
    description: str
    amount: int
    instrument_id: int
    instrument_name: str
    nature: str
    nature_source: str
    reason: str | None
    category_id: int | None
    category_name: str | None
    channel: str


@dataclass(frozen=True)
class LinePage:
    items: list[LineItem]
    total: int  # 조건에 맞는 전체 건수
    amount: int  # 조건에 맞는 전체 금액의 합. 나간 돈은 음수다


@dataclass(frozen=True)
class ReviewCounts:
    undetermined: int
    large_inflows: int
    uncategorized: int


@dataclass(frozen=True)
class DataStatus:
    last_import_at: object | None
    data_as_of: date | None
    quarantined: int


def monthly_summary(session: Session, month_from: str | None = None, month_to: str | None = None) -> list[MonthSummary]:
    rows = session.execute(
        text(
            """
            SELECT month,
                COALESCE(SUM(amount) FILTER (WHERE nature = 'income'), 0) AS income,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund')), 0) AS spend_total,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund') AND channel = 'card'), 0) AS spend_card,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund') AND channel = 'account'), 0) AS spend_account,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund') AND channel = 'pay'), 0) AS spend_pay,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund') AND channel = 'other'), 0) AS spend_other,
                COALESCE(SUM(amount) FILTER (WHERE nature = 'undetermined' AND amount > 0), 0) AS undetermined_in,
                COALESCE(-SUM(amount) FILTER (WHERE nature = 'undetermined' AND amount < 0), 0) AS undetermined_out
            FROM report_lines
            WHERE (CAST(:month_from AS text) IS NULL OR month >= :month_from)
              AND (CAST(:month_to AS text) IS NULL OR month <= :month_to)
            GROUP BY month
            ORDER BY month
            """
        ),
        {"month_from": month_from, "month_to": month_to},
    )
    return [
        MonthSummary(
            month=row.month,
            income=int(row.income),
            spend_total=int(row.spend_total),
            spend_card=int(row.spend_card),
            spend_account=int(row.spend_account),
            spend_pay=int(row.spend_pay),
            spend_other=int(row.spend_other),
            undetermined_in=int(row.undetermined_in),
            undetermined_out=int(row.undetermined_out),
        )
        for row in rows
    ]


@dataclass(frozen=True)
class SpendBridge:
    """뱅크샐러드 지출 합계에서 이 앱의 실지출까지의 차이. 두 숫자가 왜 다른지 설명한다."""

    month: str
    banksalad_total: int
    charges_removed: int  # 페이 충전: 사용할 때 이미 지출로 잡힌다
    moves_removed: int  # 저축·투자·대출 이동
    internal_removed: int  # 내 계좌 사이 이동
    other_removed: int  # 사용자가 지출이 아니라고 지정한 것 등
    unlinked_bills_added: int  # 사용 내역 없이 대금만 보이는 카드
    other_added: int  # 사용자가 지출로 지정한 이체 등
    our_total: int


def spend_bridge(session: Session) -> list[SpendBridge]:
    rows = session.execute(
        text(
            """
            SELECT month,
                COALESCE(-SUM(amount) FILTER (WHERE source_type = '지출'), 0) AS banksalad_total,
                COALESCE(-SUM(amount) FILTER (WHERE source_type = '지출' AND nature = 'funding'), 0) AS charges,
                COALESCE(-SUM(amount) FILTER (WHERE source_type = '지출' AND nature IN ('asset_move', 'debt_move')), 0) AS moves,
                COALESCE(-SUM(amount) FILTER (WHERE source_type = '지출' AND nature = 'internal'), 0) AS internal,
                COALESCE(-SUM(amount) FILTER (
                    WHERE source_type = '지출'
                      AND nature NOT IN ('expense', 'refund', 'funding', 'asset_move', 'debt_move', 'internal')
                ), 0) AS other_removed,
                COALESCE(-SUM(amount) FILTER (
                    WHERE source_type <> '지출' AND nature IN ('expense', 'refund')
                      AND nature_reason IN ('bill_unlinked', 'bill_policy_count')
                ), 0) AS bills,
                COALESCE(-SUM(amount) FILTER (
                    WHERE source_type <> '지출' AND nature IN ('expense', 'refund')
                      AND COALESCE(nature_reason, '') NOT IN ('bill_unlinked', 'bill_policy_count')
                ), 0) AS other_added,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund')), 0) AS our_total
            FROM report_lines
            GROUP BY month
            ORDER BY month
            """
        )
    )
    return [
        SpendBridge(
            month=r.month,
            banksalad_total=int(r.banksalad_total),
            charges_removed=int(r.charges),
            moves_removed=int(r.moves),
            internal_removed=int(r.internal),
            other_removed=int(r.other_removed),
            unlinked_bills_added=int(r.bills),
            other_added=int(r.other_added),
            our_total=int(r.our_total),
        )
        for r in rows
    ]


def category_breakdown(
    session: Session,
    month_from: str | None = None,
    month_to: str | None = None,
    channel: str | None = None,
) -> list[CategoryLine]:
    """실지출을 비목별로 나눈다. channel은 card, account, pay, other 중 하나다."""
    rows = session.execute(
        text(
            """
            SELECT category_key, category_name, -SUM(amount) AS amount, COUNT(*) AS rows
            FROM report_lines
            WHERE nature IN ('expense', 'refund')
              AND (CAST(:month_from AS text) IS NULL OR month >= :month_from)
              AND (CAST(:month_to AS text) IS NULL OR month <= :month_to)
              AND (CAST(:channel AS text) IS NULL OR channel = :channel)
            GROUP BY category_key, category_name
            ORDER BY amount DESC
            """
        ),
        {"month_from": month_from, "month_to": month_to, "channel": channel},
    )
    return [CategoryLine(r.category_key, r.category_name, int(r.amount), int(r.rows)) for r in rows]


def income_breakdown(session: Session, month: str) -> list[CategoryLine]:
    """그 달의 수입을 비목별로 나눈다. 더하면 monthly_summary의 수입과 같다."""
    rows = session.execute(
        text(
            """
            SELECT category_key, category_name, SUM(amount) AS amount, COUNT(*) AS rows
            FROM report_lines
            WHERE nature = 'income' AND month = :month
            GROUP BY category_key, category_name
            ORDER BY amount DESC
            """
        ),
        {"month": month},
    )
    return [CategoryLine(r.category_key, r.category_name, int(r.amount), int(r.rows)) for r in rows]


def daily_flow(session: Session, month: str) -> dict[date, DayFlow]:
    """그 달의 수입과 실지출을 날짜별로 나눈다. 더하면 monthly_summary의 수입, 지출과 같다."""
    rows = session.execute(
        text(
            """
            SELECT occurred_date,
                COALESCE(SUM(amount) FILTER (WHERE nature = 'income'), 0) AS income,
                COALESCE(-SUM(amount) FILTER (WHERE nature IN ('expense', 'refund')), 0) AS spend
            FROM report_lines
            WHERE nature IN ('income', 'expense', 'refund') AND month = :month
            GROUP BY occurred_date
            """
        ),
        {"month": month},
    )
    return {r.occurred_date: DayFlow(int(r.income), int(r.spend)) for r in rows}


def instrument_breakdown(session: Session, month: str) -> list[InstrumentLine]:
    """그 달의 실지출을 결제 수단별로 나눈다. 대금으로만 보이는 카드는 대금을 낸 계좌로 잡힌다."""
    rows = session.execute(
        text(
            """
            SELECT instrument_id, instrument_name, instrument_kind, -SUM(amount) AS amount, COUNT(*) AS rows
            FROM report_lines
            WHERE nature IN ('expense', 'refund') AND month = :month
            GROUP BY instrument_id, instrument_name, instrument_kind
            ORDER BY amount DESC
            """
        ),
        {"month": month},
    )
    return [InstrumentLine(r.instrument_id, r.instrument_name, r.instrument_kind, int(r.amount), int(r.rows)) for r in rows]


def chart_lines(session: Session) -> list[ChartLine]:
    """수입과 실지출을 날짜, 결제 방식, 결제 수단, 비목별로 나눈 합계. 더하면 monthly_summary와 같다."""
    rows = session.execute(
        text(
            """
            SELECT occurred_date,
                CASE WHEN nature = 'income' THEN 'income' ELSE 'spend' END AS kind,
                channel, instrument_id, instrument_name, category_key, category_name,
                SUM(CASE WHEN nature = 'income' THEN amount ELSE -amount END) AS amount
            FROM report_lines
            WHERE nature IN ('income', 'expense', 'refund')
            GROUP BY occurred_date, kind, channel, instrument_id, instrument_name, category_key, category_name
            ORDER BY occurred_date
            """
        )
    )
    return [
        ChartLine(
            r.occurred_date,
            r.kind,
            r.channel,
            r.instrument_id,
            r.instrument_name,
            r.category_key,
            r.category_name,
            int(r.amount),
        )
        for r in rows
    ]


def _financial_manual_assets(session: Session) -> list[ManualAsset]:
    return [asset for asset in manual_assets(session) if asset.other_type in FINANCIAL_OTHER_TYPES]


def finance_history(session: Session) -> list[FinanceLine]:
    """금융 자산과 부채를 상품별로. 실물 자산은 빼고, 직접 입력한 코인이나 주식 같은 금융 자산은 넣는다."""
    rows = session.execute(
        select(AssetSnapshot.as_of, AssetItem.side, AssetItem.group_label, AssetItem.product_name, func.sum(AssetItem.amount))
        .join(AssetItem, AssetItem.snapshot_id == AssetSnapshot.id)
        .where(AssetItem.group_label.not_in(PHYSICAL_ASSET_GROUPS))
        .group_by(AssetSnapshot.as_of, AssetItem.side, AssetItem.group_label, AssetItem.product_name)
        .order_by(AssetSnapshot.as_of)
    )
    by_snapshot: dict[date, list[tuple[str, str, str, int]]] = {}
    for as_of, side, label, product, amount in rows:
        by_snapshot.setdefault(as_of, []).append((side, label, product, int(amount)))
    snapshot_days = list(session.scalars(select(AssetSnapshot.as_of).order_by(AssetSnapshot.as_of)))
    manual = _financial_manual_assets(session)

    lines = []
    for day in _asset_days(snapshot_days, manual):
        # 내보내기가 없는 날은 그 전 기록의 상품을 그대로 쓴다
        index = bisect_right(snapshot_days, day) - 1
        for side, label, product, amount in by_snapshot.get(snapshot_days[index], []) if index >= 0 else []:
            lines.append(FinanceLine(day, side, label, product, amount))
        products: dict[tuple[str, str], int] = {}
        for asset in manual:
            key = (manual_group_label(asset.kind, asset.tenure, asset.other_type), asset.name)
            products[key] = products.get(key, 0) + manual_value_on(asset, day)
        lines.extend(FinanceLine(day, "asset", label, name, amount) for (label, name), amount in products.items() if amount)
    return lines


def investment_history(session: Session) -> list[InvestmentPoint]:
    """금융 탭의 기록마다 투자 원금과 평가금액. 금액이 숫자가 아닌 행은 뺀다."""
    rows = session.execute(
        select(AssetSnapshot.as_of, AssetDetail.fields)
        .join(AssetDetail, AssetDetail.snapshot_id == AssetSnapshot.id)
        .where(AssetDetail.section == "investment")
        .order_by(AssetSnapshot.as_of)
    )
    totals: dict[date, list[int]] = {}
    for as_of, fields in rows:
        principal, valuation = fields.get("투자원금"), fields.get("평가금액")
        if not isinstance(principal, (int, float)) or not isinstance(valuation, (int, float)):
            continue
        entry = totals.setdefault(as_of, [0, 0])
        entry[0] += round(principal)
        entry[1] += round(valuation)
    snapshot_days = list(session.scalars(select(AssetSnapshot.as_of).order_by(AssetSnapshot.as_of)))
    points = []
    for day in _asset_days(snapshot_days, _financial_manual_assets(session)):
        # 직접 입력한 값만 바뀐 날에는 그 전 내보내기의 투자 현황이 그대로다
        index = bisect_right(snapshot_days, day) - 1
        total = totals.get(snapshot_days[index]) if index >= 0 else None
        if total:
            points.append(InvestmentPoint(day, total[0], total[1]))
    return points


def _detail_day(value: object) -> str | None:
    """현황 표의 날짜. 칸에 따라 날짜이거나 글자여서 글자에서 찾는다."""
    match = DETAIL_DAY.search(value) if isinstance(value, str) else None
    if match is None:
        return None
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def _detail_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("원", "").replace("%", "").strip())
        except ValueError:
            return None
    return None


def _detail_amount(value: object) -> int | None:
    number = _detail_number(value)
    return None if number is None else round(number)


def _investment_row(fields: dict) -> dict:
    return {
        "name": fields.get("상품명") or "",
        "org": fields.get("금융사") or "",
        "type": fields.get("투자상품종류") or "",
        "principal": _detail_amount(fields.get("투자원금")),
        "valuation": _detail_amount(fields.get("평가금액")),
        "start": _detail_day(fields.get("가입일자")),
        "end": _detail_day(fields.get("만기일자")),
    }


def _loan_row(fields: dict) -> dict:
    return {
        "name": fields.get("상품명") or "",
        "org": fields.get("금융사") or "",
        "type": fields.get("대출종류") or "",
        "principal": _detail_amount(fields.get("대출원금")),
        "balance": _detail_amount(fields.get("대출잔액")),
        "rate": _detail_number(fields.get("대출금리")),
        "start": _detail_day(fields.get("대출신규일")),
        "end": _detail_day(fields.get("대출만기일")),
    }


def _insurance_row(fields: dict) -> dict:
    return {
        "name": fields.get("보험명") or "",
        "org": fields.get("금융사") or "",
        "status": fields.get("계약상태") or "",
        "paid": _detail_amount(fields.get("총납입금")),
        "start": _detail_day(fields.get("계약일자")),
        "end": _detail_day(fields.get("만기일자")),
    }


def finance_details(session: Session) -> dict[str, list[dict]]:
    """마지막 내보내기의 투자, 대출, 보험 현황. 투자 상품은 기록마다의 원금과 평가금액을 함께 준다."""
    rows = session.execute(
        select(AssetSnapshot.as_of, AssetDetail.section, AssetDetail.fields)
        .join(AssetDetail, AssetDetail.snapshot_id == AssetSnapshot.id)
        .order_by(AssetSnapshot.as_of, AssetDetail.id)
    ).all()
    latest = session.scalar(select(func.max(AssetSnapshot.as_of)))
    history: dict[tuple[str, str], dict[date, list[int]]] = {}
    details: dict[str, list[dict]] = {"investment": [], "loan": [], "insurance": []}
    builders = {"investment": _investment_row, "loan": _loan_row, "insurance": _insurance_row}
    for as_of, section, fields in rows:
        row = builders[section](fields)
        if section == "investment" and row["principal"] is not None and row["valuation"] is not None:
            point = history.setdefault((row["org"], row["name"]), {}).setdefault(as_of, [0, 0])
            point[0] += row["principal"]
            point[1] += row["valuation"]
        if as_of == latest:
            details[section].append(row)
    for row in details["investment"]:
        points = history.get((row["org"], row["name"]), {})
        row["history"] = [
            {"date": str(day), "principal": point[0], "valuation": point[1]} for day, point in sorted(points.items())
        ]
    return details


def asset_items_on_latest(session: Session) -> list[AssetItemLine]:
    """마지막 내보내기의 상품들."""
    latest = session.scalar(select(func.max(AssetSnapshot.as_of)))
    rows = session.execute(
        select(AssetItem.side, AssetItem.group_label, AssetItem.product_name, AssetItem.amount, AssetItem.instrument_id)
        .join(AssetSnapshot, AssetSnapshot.id == AssetItem.snapshot_id)
        .where(AssetSnapshot.as_of == latest)
        .order_by(AssetItem.amount.desc())
    )
    return [AssetItemLine(side, label, name, int(amount), instrument_id) for side, label, name, amount, instrument_id in rows]


def top_places(
    session: Session,
    month: str | None,
    categories: Sequence[str] = (),
    channels: Sequence[str] = (),
    instruments: Sequence[int] = (),
    limit: int = 5,
    days: tuple[str, str] | None = None,
) -> list[PlaceLine]:
    """그 달이나 그 날짜 사이에 돈을 많이 쓴 곳. days가 있으면 달보다 먼저다. 조건은 차트 화면의 조건과 같다."""
    conditions = ["nature IN ('expense', 'refund')"]
    params: dict[str, object] = {"limit": limit}
    if days:
        conditions.append("occurred_date BETWEEN CAST(:date_from AS date) AND CAST(:date_to AS date)")
        params["date_from"], params["date_to"] = days
    else:
        conditions.append("month = :month")
        params["month"] = month
    binds = []
    named = [key for key in categories if key != NO_CATEGORY_KEY]
    if named and len(named) < len(categories):
        conditions.append("(category_key IN :categories OR category_key IS NULL)")
    elif named:
        conditions.append("category_key IN :categories")
    elif categories:
        conditions.append("category_key IS NULL")
    if named:
        params["categories"] = named
        binds.append(bindparam("categories", expanding=True))
    if channels:
        conditions.append("channel IN :channels")
        params["channels"] = list(channels)
        binds.append(bindparam("channels", expanding=True))
    if instruments:
        conditions.append("instrument_id IN :instruments")
        params["instruments"] = list(instruments)
        binds.append(bindparam("instruments", expanding=True))
    statement = text(
        f"""
        SELECT description, -SUM(amount) AS amount, COUNT(*) AS rows
        FROM report_lines
        WHERE {" AND ".join(conditions)}
        GROUP BY description
        HAVING SUM(amount) < 0
        ORDER BY amount DESC, description
        LIMIT :limit
        """
    ).bindparams(*binds)
    return [PlaceLine(row.description, int(row.amount), int(row.rows)) for row in session.execute(statement, params)]


def recurring_lines(session: Session) -> list[RecurringLine]:
    """반복 거래를 찾을 재료. 합계에 들어가지 않는 미확정도 포함한다. 찾아서 분류하는 것이 목적이기 때문이다."""
    rows = session.execute(
        text(
            """
            SELECT occurred_date, description, amount, nature, nature_reason, category_key, category_name, instrument_name,
                   source_type, source_major, source_minor
            FROM report_lines
            ORDER BY occurred_date
            """
        )
    )
    return [
        RecurringLine(
            r.occurred_date,
            r.description,
            int(r.amount),
            r.nature,
            r.nature_reason,
            r.category_key,
            r.category_name,
            r.instrument_name,
            (r.source_type, r.source_major, r.source_minor),
        )
        for r in rows
    ]


def recurring_groups(session: Session, today: date) -> list[RecurringGroup]:
    """끝난 정기인지는 마지막으로 내보낸 날을 기준으로 가린다. 내보내기가 없으면 오늘을 쓴다."""
    rents = [asset.monthly_rent for asset in manual_assets(session) if asset.monthly_rent and asset.sold_on is None]
    # 연결한 묶음은 먼저 골라 둘 분류가 정해져 있다. 대출에 이었으면 금리를 알게 될 때 이자와 원금으로 나뉜다.
    linked = {link.slot: Suggestion(*link_kind(link)) for link in links_of(session)}
    return detect_recurring(recurring_lines(session), data_status(session).data_as_of or today, rents, linked)


def linked_payments(
    session: Session, history: Sequence[FinanceLine], lines: Sequence[RecurringLine] | None = None
) -> tuple[dict[str, LoanLinks], dict[str, AssetLinks], dict[str, PensionLinks]]:
    """연결한 정기 거래를 대출, 부동산, 연금별로 모은다. 한 대출에 납입이 여럿이면 합쳐서 금리를 구한다."""
    links = links_of(session)
    if not links:
        return {}, {}, {}
    paid: dict[tuple[str, str], dict[date, int]] = {link.slot: defaultdict(int) for link in links}
    natures: dict[tuple[str, str], Counter[str]] = {link.slot: Counter() for link in links}
    parts: dict[tuple[str, str], dict[str, list]] = {link.slot: {} for link in links}  # 달마다 [이자, 원금, 어림 없음]
    for line in recurring_lines(session) if lines is None else lines:
        if not line.amount or line.nature in EXPLAINED_NATURES:
            continue
        slot = (target_key(line.description), "out" if line.amount < 0 else "in")
        if slot not in paid:
            continue
        paid[slot][line.day] += abs(line.amount)
        natures[slot][line.nature] += 1
        if line.reason in LOAN_REASONS:
            month = parts[slot].setdefault(line.day.strftime("%Y-%m"), [0, 0, True])
            month[0 if line.reason in LOAN_INTEREST_REASONS else 1] += abs(line.amount)
            month[2] = month[2] and line.reason not in LOAN_ESTIMATE_REASONS
    shown = {
        link.slot: LinkedPayment(
            link.name,
            round(median(paid[link.slot].values())) if paid[link.slot] else 0,
            len(paid[link.slot]),
            natures[link.slot].most_common(1)[0][0] if natures[link.slot] else None,
            bool(parts[link.slot]),
            link.direction,
        )
        for link in links
    }
    balances: dict[str, list[tuple[date, int]]] = {}
    for item in history:
        if item.side == "liability":
            balances.setdefault(f"{item.side}:{item.group_label}:{item.product_name}", []).append((item.as_of, item.amount))

    loans: dict[str, LoanLinks] = {}
    for loan in dict.fromkeys(link.loan for link in links if link.loan):
        members = [link for link in links if link.loan == loan]
        merged: dict[date, int] = defaultdict(int)
        months: dict[str, list] = {}
        for link in members:
            for day, amount in paid[link.slot].items():
                merged[day] += amount
            for month, (interest, principal, exact) in parts[link.slot].items():
                total = months.setdefault(month, [0, 0, True])
                total[0], total[1], total[2] = total[0] + interest, total[1] + principal, total[2] and exact
        loans[loan] = LoanLinks(
            [shown[link.slot] for link in members],
            implied_rate(balances.get(loan, []), merged),
            [LoanMonth(month, *months[month]) for month in sorted(months)[-LOAN_MONTHS:]],
        )
    # 부동산과 연금 정보에는 이어 둔 거래를 함께 적는다.
    assets: dict[str, AssetLinks] = {}
    targets = [*(link.asset for link in links if link.asset), *(link.pension for link in links if link.pension)]
    for asset in dict.fromkeys(targets):
        members = [link for link in links if asset in (link.asset, link.pension)]
        assets[asset] = AssetLinks(
            list(dict.fromkeys(link.loan for link in members if link.loan)), [shown[link.slot] for link in members]
        )
    pensions: dict[str, PensionLinks] = {}
    for pension in dict.fromkeys(link.pension for link in links if link.pension):
        members = [link for link in links if link.pension == pension]
        flows: dict[date, int] = defaultdict(int)
        for link in members:
            sign = 1 if link.direction == "out" else -1
            for day, amount in paid[link.slot].items():
                flows[day] += sign * amount
        totals = {direction: sum(sum(paid[link.slot].values()) for link in members if link.direction == direction) for direction in ("out", "in")}
        pensions[pension] = PensionLinks([shown[link.slot] for link in members], totals["out"], totals["in"], dict(flows))
    return loans, assets, pensions


def account_trace(
    session: Session, instrument_id: int, month_from: str | None = None, month_to: str | None = None
) -> AccountTrace:
    """계좌에서 나간 돈을 용도별로 나눈다. 추적률은 금액 기준이다."""
    rows = session.execute(
        text(
            """
            SELECT nature, nature_reason, category_key, -SUM(amount) AS amount, COUNT(*) AS rows
            FROM report_lines
            WHERE instrument_id = :instrument_id
              AND amount < 0
              AND (CAST(:month_from AS text) IS NULL OR month >= :month_from)
              AND (CAST(:month_to AS text) IS NULL OR month <= :month_to)
            GROUP BY nature, nature_reason, category_key
            """
        ),
        {"instrument_id": instrument_id, "month_from": month_from, "month_to": month_to},
    ).all()

    direct: dict[str, int] = {}
    total = card_bills = charges = savings = debt = own = untracked = untracked_rows = 0
    for row in rows:
        amount = int(row.amount)
        total += amount
        reason = row.nature_reason or ""
        if row.nature == "undetermined" or reason in UNTRACKED_REASONS or row.category_key == "uncategorized_expense":
            untracked += amount
            untracked_rows += int(row.rows)
        elif row.nature == "expense":
            direct[row.category_key] = direct.get(row.category_key, 0) + amount
        elif row.nature == "funding" and reason.startswith("bill"):
            card_bills += amount
        elif row.nature == "funding":
            charges += amount
        elif row.nature == "asset_move":
            savings += amount
        elif row.nature == "debt_move":
            debt += amount
        elif row.nature == "internal":
            own += amount
        else:
            untracked += amount
            untracked_rows += int(row.rows)
    return AccountTrace(
        instrument_id=instrument_id,
        month_from=month_from,
        month_to=month_to,
        total_out=total,
        direct=direct,
        card_bills=card_bills,
        pay_charges=charges,
        savings_investment=savings,
        debt=debt,
        own_transfer=own,
        untracked=untracked,
        untracked_rows=untracked_rows,
    )


def manual_assets(session: Session) -> list[ManualAsset]:
    return list(session.scalars(select(ManualAsset).options(selectinload(ManualAsset.values)).order_by(ManualAsset.id)))


def manual_value_on(asset: ManualAsset, day: date) -> int:
    """그날 갖고 있던 자산의 값. 처음 입력한 날보다 앞선 날은 그 값을 그대로 쓴다."""
    if day < asset.acquired_on or (asset.sold_on is not None and day >= asset.sold_on):
        return 0
    amount = asset.values[0].amount if asset.values else 0
    for value in asset.values:
        if value.as_of > day:
            break
        amount = value.amount
    return amount


def _asset_days(snapshot_days: Sequence[date], manual: Sequence[ManualAsset]) -> list[date]:
    """그래프의 점이 찍히는 날. 내보내기를 받은 날과, 직접 입력한 값이 바뀐 날이다.

    첫 기록 이전에는 은행 자산을 알 수 없어 직접 입력한 값만으로 점을 찍지 않는다.
    """
    days = set(snapshot_days)
    first = min(days) if days else None
    for asset in manual:
        for day in [value.as_of for value in asset.values] + ([asset.sold_on] if asset.sold_on else []):
            if first is None or day > first:
                days.add(day)
    return sorted(days)


def net_worth_series(session: Session) -> list[NetWorthPoint]:
    snapshots = list(session.scalars(select(AssetSnapshot).order_by(AssetSnapshot.as_of)))
    manual = manual_assets(session)
    snapshot_days = [snapshot.as_of for snapshot in snapshots]
    points = []
    for day in _asset_days(snapshot_days, manual):
        # 내보내기가 없는 날은 그 전 기록의 은행 자산을 그대로 쓴다
        index = bisect_right(snapshot_days, day) - 1
        base = snapshots[index] if index >= 0 else None
        points.append(
            NetWorthPoint(
                day,
                (base.total_assets if base else 0) + sum(manual_value_on(asset, day) for asset in manual),
                base.total_liabilities if base else 0,
            )
        )
    return points


def asset_history(session: Session) -> list[AssetGroupLine]:
    """그래프의 점마다 자산과 부채를 항목별로 나눈 값. 더하면 net_worth_series와 같다."""
    rows = session.execute(
        select(AssetSnapshot.as_of, AssetItem.side, AssetItem.group_label, func.sum(AssetItem.amount))
        .join(AssetItem, AssetItem.snapshot_id == AssetSnapshot.id)
        .group_by(AssetSnapshot.as_of, AssetItem.side, AssetItem.group_label)
        .order_by(AssetSnapshot.as_of)
    )
    by_snapshot: dict[date, list[tuple[str, str, int]]] = {}
    for as_of, side, label, amount in rows:
        by_snapshot.setdefault(as_of, []).append((side, label, int(amount)))
    snapshot_days = list(session.scalars(select(AssetSnapshot.as_of).order_by(AssetSnapshot.as_of)))
    manual = manual_assets(session)

    lines = []
    for day in _asset_days(snapshot_days, manual):
        index = bisect_right(snapshot_days, day) - 1
        for side, label, amount in by_snapshot.get(snapshot_days[index], []) if index >= 0 else []:
            lines.append(AssetGroupLine(day, side, label, amount))
        groups: dict[str, int] = {}
        for asset in manual:
            label = manual_group_label(asset.kind, asset.tenure, asset.other_type)
            groups[label] = groups.get(label, 0) + manual_value_on(asset, day)
        lines.extend(AssetGroupLine(day, "asset", label, amount) for label, amount in groups.items() if amount)
    return lines


def snapshot_on(session: Session, day: date) -> AssetSnapshot | None:
    """그날까지의 마지막 내보내기 기록."""
    return session.scalar(
        select(AssetSnapshot).where(AssetSnapshot.as_of <= day).order_by(AssetSnapshot.as_of.desc()).limit(1)
    )


def net_worth_on(session: Session, day: date) -> NetWorthPoint | None:
    """그날의 순자산. 내보내기가 없는 날은 그 전 기록에 그날의 직접 입력 자산을 더한다."""
    snapshot = snapshot_on(session, day)
    manual = sum(manual_value_on(asset, day) for asset in manual_assets(session))
    if snapshot is None and not manual:
        return None
    return NetWorthPoint(
        day,
        (snapshot.total_assets if snapshot else 0) + manual,
        snapshot.total_liabilities if snapshot else 0,
    )


def instrument_balances(session: Session, instrument_id: int) -> list[tuple[date, int]]:
    """결제수단별 잔액 추이. 내보낸 날짜마다 한 점이다."""
    rows = session.execute(
        select(AssetSnapshot.as_of, AssetItem.amount)
        .join(AssetItem, AssetItem.snapshot_id == AssetSnapshot.id)
        .where(AssetItem.instrument_id == instrument_id)
        .order_by(AssetSnapshot.as_of)
    )
    return [(as_of, int(amount)) for as_of, amount in rows]


def _line_item(row) -> LineItem:
    return LineItem(
        row.txn_id,
        row.split_id,
        row.occurred_at,
        row.occurred_date,
        row.source_type,
        row.source_major,
        row.source_minor,
        row.description,
        int(row.amount),
        row.instrument_id,
        row.instrument_name,
        row.nature,
        row.nature_source,
        row.nature_reason,
        row.category_id,
        row.category_name,
        row.channel,
    )


def list_lines(
    session: Session,
    *,
    month: str | None = None,
    channel: str | None = None,
    category_key: str | None = None,
    instrument_id: int | None = None,
    natures: Sequence[str] | None = None,
    query: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LinePage:
    """조건에 맞는 거래를 최신순으로 돌려준다. 합계는 지금 보이는 쪽이 아니라 조건 전체의 값이다."""
    pattern = None
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
    params = {
        "month": month,
        "channel": channel,
        "category_key": category_key,
        "instrument_id": instrument_id,
        "natures": list(natures) if natures else None,
        "pattern": pattern,
        "date_from": date_from,
        "date_to": date_to,
    }
    totals = session.execute(
        text(f"SELECT COUNT(*) AS total, COALESCE(SUM(amount), 0) AS amount FROM report_lines WHERE {LINE_FILTER}"),
        params,
    ).one()
    rows = session.execute(
        text(
            f"""
            SELECT {LINE_COLUMNS}
            FROM report_lines
            WHERE {LINE_FILTER}
            ORDER BY occurred_at DESC, split_id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {**params, "limit": limit, "offset": offset},
    )
    return LinePage([_line_item(row) for row in rows], int(totals.total), int(totals.amount))


def _review_items(session: Session, where: str, limit: int) -> list[LineItem]:
    rows = session.execute(
        text(
            f"""
            SELECT {LINE_COLUMNS}
            FROM report_lines
            WHERE {where}
            ORDER BY ABS(amount) DESC, occurred_date DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [_line_item(row) for row in rows]


def review_undetermined(session: Session, limit: int = 50) -> list[LineItem]:
    """성격을 정하지 못한 거래. 금액이 큰 것부터 본다."""
    return _review_items(session, UNDETERMINED, limit)


def review_large_inflows(session: Session, limit: int = 50) -> list[LineItem]:
    """수입이 아닌 것으로 처리한 큰 입금. 실제로는 급여일 수 있어 사람이 확인해야 한다."""
    return _review_items(session, LARGE_INFLOW, limit)


def review_largest_spend(session: Session, limit: int = 50) -> list[LineItem]:
    """금액이 큰 지출. 소수의 큰 건이 합계를 좌우하고 자동 분류가 틀리는 경우가 많다."""
    return _review_items(session, "nature = 'expense' AND nature_source <> 'user'", limit)


def review_uncategorized(session: Session, limit: int = 50) -> list[LineItem]:
    return _review_items(session, UNCATEGORIZED, limit)


def review_counts(session: Session) -> ReviewCounts:
    row = session.execute(
        text(
            f"""
            SELECT COUNT(*) FILTER (WHERE {UNDETERMINED}) AS undetermined,
                   COUNT(*) FILTER (WHERE {LARGE_INFLOW}) AS large_inflows,
                   COUNT(*) FILTER (WHERE {UNCATEGORIZED}) AS uncategorized
            FROM report_lines
            """
        )
    ).one()
    return ReviewCounts(int(row.undetermined), int(row.large_inflows), int(row.uncategorized))


def data_status(session: Session) -> DataStatus:
    latest = session.scalar(
        select(ImportBatch)
        .where(ImportBatch.status == "applied")
        .order_by(ImportBatch.period_end.desc(), ImportBatch.id.desc())
        .limit(1)
    )
    quarantined = session.scalar(
        select(func.count()).select_from(ImportBatch).where(ImportBatch.status == "quarantined")
    )
    return DataStatus(
        last_import_at=latest.created_at if latest else None,
        data_as_of=latest.period_end if latest else None,
        quarantined=quarantined or 0,
    )
