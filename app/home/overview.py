"""홈의 달력과 복식 장부. 합계는 reports와 같은 값이다."""

from __future__ import annotations

import calendar as month_calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app import charts
from app.format import link, month_label, previous_month, won
from app.home.widgets import HOLDING_WIDGETS, WIDGETS, HoldingCard, NetWorthCard
from app.ledger import reports

WEEKDAYS = "일월화수목금토"
DAY_LINES = 2000  # 한 달의 거래가 이보다 많아도 합계는 맞고, 목록은 거래 내역에서 이어 볼 수 있다
TREND_MONTHS = 12
INCOME = "income"
SPEND = "spend"
FLOW_NATURES = (INCOME, "expense", "refund")


@dataclass(frozen=True)
class CalendarDay:
    day: date
    income: int
    spend: int
    state: str  # active(거래 있음), none(거래 없음), unknown(아직 기록이 없는 날)
    label: str
    href: str


@dataclass(frozen=True)
class DayLine:
    name: str
    hint: str
    amount: int
    is_income: bool


@dataclass(frozen=True)
class DayDetail:
    day: date
    title: str
    income: int
    spend: int
    lines: list[DayLine]
    income_href: str
    spend_href: str


@dataclass(frozen=True)
class Calendar:
    weeks: list[list[CalendarDay | None]]
    details: list[DayDetail]
    selected: date | None


@dataclass(frozen=True)
class BookRow:
    name: str
    amount: int
    href: str | None = None
    width: float | None = None  # 금액 막대. 없으면 비중을 적는다
    share: float | None = None
    hint: str | None = None


@dataclass(frozen=True)
class BookSection:
    title: str
    rows: list[BookRow]
    total: int


@dataclass(frozen=True)
class Book:
    income: BookSection
    spend: BookSection
    left: int
    as_of: date
    assets: BookSection
    debts: BookSection
    net: int
    net_change: int | None
    chart: charts.LineChart | None


@dataclass(frozen=True)
class MonthBar:
    month: str
    height: float
    current: bool
    label: str
    href: str


def month_bars(
    months: dict[str, reports.MonthSummary], *, selected: str, first: str, last: str, editing: str | None
) -> list[MonthBar]:
    """지출 숫자 아래의 12개월 막대. 누르면 그 달로 간다. 고른 달이 언제나 보이도록 구간을 잡는다."""

    def window(end: str) -> list[str]:
        keys = [end]
        while len(keys) < TREND_MONTHS and keys[-1] > first:
            keys.append(previous_month(keys[-1]))
        return keys[::-1]

    keys = window(last)
    if selected not in keys:
        keys = window(selected)
    amounts = {key: months[key].spend_total if key in months else 0 for key in keys}
    largest = max(max(amounts.values()), 1)
    return [
        MonthBar(
            month=key,
            height=round(max(amount, 0) * 100 / largest, 2),
            current=key == selected,
            label=f"{month_label(key)} {won(amount)}",
            href=link("/", month=key, edit=editing),
        )
        for key, amount in amounts.items()
    ]


def _day_title(day: date) -> str:
    return f"{day.month}월 {day.day}일 {WEEKDAYS[(day.weekday() + 1) % 7]}요일"


def _day_link(day: date, nature: str) -> str:
    return link("/transactions", start=day.isoformat(), end=day.isoformat(), nature=nature)


def _calendar_day(day: date, flow: reports.DayFlow | None, known_until: date) -> CalendarDay:
    income = flow.income if flow else 0
    spend = flow.spend if flow else 0
    state = "active" if income or spend else "none" if day <= known_until else "unknown"
    amounts = [f"{name} {won(amount)}" for name, amount in (("수입", income), ("지출", spend)) if amount]
    described = ", ".join(amounts) or ("거래 없음" if state == "none" else "기록 없음")
    return CalendarDay(
        day=day,
        income=income,
        spend=spend,
        state=state,
        label=f"{_day_title(day)} {described}",
        href=_day_link(day, SPEND if spend else INCOME),
    )


def _details(flows: dict[date, reports.DayFlow], lines: Sequence[reports.LineItem]) -> list[DayDetail]:
    by_day: dict[date, list[DayLine]] = {}
    for line in lines:
        hint = " · ".join(part for part in (line.category_name, line.instrument_name) if part)
        is_income = line.nature == INCOME
        by_day.setdefault(line.occurred_date, []).append(
            DayLine(line.description, hint, line.amount if is_income else -line.amount, is_income)
        )
    return [
        DayDetail(
            day=day,
            title=_day_title(day),
            income=flow.income,
            spend=flow.spend,
            lines=by_day.get(day, []),
            income_href=_day_link(day, INCOME),
            spend_href=_day_link(day, SPEND),
        )
        for day, flow in sorted(flows.items())
        if flow.income or flow.spend
    ]


def calendar(db: Session, month: str, known_until: date) -> Calendar:
    """일요일에 시작하는 그 달의 달력. known_until 뒤의 날은 거래가 없는 날이 아니라 아직 모르는 날이다."""
    flows = reports.daily_flow(db, month)
    lines = reports.list_lines(db, month=month, natures=FLOW_NATURES, limit=DAY_LINES).items
    year, number = (int(part) for part in month.split("-"))
    weeks = [
        [
            _calendar_day(date(year, number, value), flows.get(date(year, number, value)), known_until) if value else None
            for value in numbers
        ]
        for numbers in month_calendar.Calendar(firstweekday=6).monthdayscalendar(year, number)
    ]
    active = [day for day, flow in flows.items() if flow.income or flow.spend]
    return Calendar(weeks, _details(flows, lines), max(active, default=None))


def _flow_rows(lines: Sequence[reports.CategoryLine], month: str, nature: str, bars: bool) -> list[BookRow]:
    largest = max((line.amount for line in lines), default=0)
    return [
        BookRow(
            name=line.category_name or "카테고리 없음",
            amount=line.amount,
            href=link("/transactions", month=month, nature=nature, category=line.category_key),
            width=round(max(line.amount, 0) * 100 / largest, 2) if bars and largest > 0 else None,
        )
        for line in lines
    ]


def book(
    db: Session,
    *,
    month: str,
    summary: reports.MonthSummary,
    as_of: date,
    holdings: dict[str, HoldingCard],
    net_worth: NetWorthCard,
    visible: Sequence[str],
) -> Book:
    """왼쪽은 그 달의 흐름, 오른쪽은 그날의 재산. 왼쪽 합계는 요약과, 오른쪽 합계는 순자산과 같다."""
    assets = sum(holdings[key].total for key in HOLDING_WIDGETS)
    debts = holdings["finance"].debts
    debt_total = holdings["finance"].debt_total
    return Book(
        income=BookSection("수입", _flow_rows(reports.income_breakdown(db, month), month, INCOME, bars=False), summary.income),
        spend=BookSection("지출", _flow_rows(reports.category_breakdown(db, month, month), month, SPEND, bars=True), summary.spend_total),
        left=summary.income - summary.spend_total,
        as_of=as_of,
        assets=BookSection(
            "자산",
            [
                BookRow(
                    name=WIDGETS[key],
                    amount=holdings[key].total,
                    href=f"#widget-{key}" if key in visible else link("/chart", tab="assets"),
                    share=holdings[key].total / assets if assets > 0 else None,
                )
                for key in HOLDING_WIDGETS
                if holdings[key].total
            ],
            assets,
        ),
        debts=BookSection("부채", [BookRow(row.name, row.amount, hint=row.hint) for row in debts], debt_total),
        net=assets - debt_total,
        net_change=net_worth.change,
        chart=net_worth.chart,
    )
