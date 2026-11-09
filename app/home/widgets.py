"""홈 화면의 카드. 고른 달을 기준으로 값을 모으고, 어떤 카드를 어떤 순서로 보일지 기억한다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from app import chart_widgets, charts
from app.format import link, won
from app.ledger import labels, reports
from app.ops import state
from app.ops_models import SystemState

WIDGETS = {  # 적힌 순서가 처음 보이는 순서다
    "category": "카테고리 비중",
    "payment": "결제 수단 비중",
    "cards": "카드",
    "net_worth": "순자산",
    "accounts": "계좌·현금",
    "pay": "페이머니",
    "real_estate": "부동산",
    "car": "자동차",
    "pension": "연금",
    "finance": "금융",
    "physical": "기타 실물자산",
}
SPEND_WIDGETS = frozenset({"category", "payment", "cards"})  # 나머지는 자산 묶음이다. 비슷한 카드끼리 모아 보여 준다
SPEND_TABS = frozenset({"category", "flow"})  # 차트 위젯 중 지출 묶음에 들어가는 탭
TITLES = {**WIDGETS, **{key: widget.title for key, widget in chart_widgets.WIDGETS.items()}}  # 홈에는 모든 위젯을 넣을 수 있다
HOLDING_WIDGETS = ("accounts", "pay", "real_estate", "car", "pension", "finance", "physical")
LAYOUT_ACTIONS = frozenset({"up", "down", "hide", "show"})
LAYOUT_KEY = "home_widgets"
# 뱅크샐러드 재무현황 항목이 들어갈 카드. 적혀 있지 않은 항목(저축성, 투자성, 신탁, 보험)은 금융 카드로 간다.
WIDGET_OF_GROUP = {
    "자유입출금 자산": "accounts",
    "현금 자산": "accounts",
    "전자금융 자산": "pay",
    "부동산": "real_estate",
    "동산": "car",
    "연금 자산": "pension",
    "기타 실물 자산": "physical",
}
WIDGET_OF_MANUAL_KIND = {"car": "car", "real_estate": "real_estate", "other": "physical"}
EMPTY_TEXT = {
    "category": "이 달의 지출이 없습니다.",
    "payment": "이 달의 지출이 없습니다.",
    "net_worth": "이 시점의 자산 기록이 없습니다.",
    "accounts": "이 시점의 계좌 기록이 없습니다.",
    "cards": "이 달의 카드 사용이 없습니다.",
    "pay": "페이머니 기록이 없습니다.",
    "real_estate": "등록한 부동산이 없습니다.",
    "car": "등록한 자동차가 없습니다.",
    "pension": "연금 기록이 없습니다.",
    "finance": "이 시점의 금융 상품 기록이 없습니다.",
    "physical": "등록한 자산이 없습니다.",
}
ADD_LINKS = {
    "real_estate": ("real_estate", "부동산 추가"),
    "car": ("car", "자동차 추가"),
    "physical": ("other", "자산 추가"),
}
TOP_ROWS = 5
COLOR_SLOTS = 7  # 색을 구분할 수 있는 수. 차트 화면과 같다


def _in_spend_group(key: str) -> bool:
    widget = chart_widgets.WIDGETS.get(key)
    return widget.tab in SPEND_TABS if widget else key in SPEND_WIDGETS


@dataclass(frozen=True)
class Layout:
    visible: list[str]
    hidden: list[str]

    @property
    def spend(self) -> list[str]:
        return [key for key in self.visible if _in_spend_group(key)]

    @property
    def assets(self) -> list[str]:
        return [key for key in self.visible if not _in_spend_group(key)]

    @property
    def pool(self) -> list[tuple[str, list[str]]]:
        """넣을 수 있는 위젯. 홈 카드 다음에 차트 탭별로 묶는다."""
        groups = [("홈", [key for key in self.hidden if key in WIDGETS])]
        for tab, name in chart_widgets.TABS.items():
            groups.append(
                (name, [key for key in self.hidden if key in chart_widgets.WIDGETS and chart_widgets.WIDGETS[key].tab == tab])
            )
        return [group for group in groups if group[1]]


def load_layout(db: Session) -> Layout:
    row = db.get(SystemState, LAYOUT_KEY)
    saved = row.value if row else {}
    order = [key for key in saved.get("order", []) if key in TITLES]
    order += [key for key in WIDGETS if key not in order]  # 나중에 생긴 카드는 뒤에 붙인다
    hidden = set(saved.get("hidden", []))
    unplaced = [key for key in chart_widgets.WIDGETS if key not in order]  # 차트 위젯은 넣기 전까지 보이지 않는다
    return Layout([key for key in order if key not in hidden], [key for key in order if key in hidden] + unplaced)


def gallery() -> list[tuple[str, list[str]]]:
    """위젯 화면의 묶음. 홈 카드 다음에 차트 탭 순서다."""
    groups = [("홈", list(WIDGETS))]
    for tab, name in chart_widgets.TABS.items():
        groups.append((name, [key for key, widget in chart_widgets.WIDGETS.items() if widget.tab == tab]))
    return groups


def set_layout(db: Session, now: datetime, keys: Sequence[str]) -> None:
    """위젯 화면에서 고른 순서대로 놓는다. 고르지 않은 것은 뺀다."""
    chosen = [key for key in dict.fromkeys(keys) if key in TITLES]
    hidden = [key for key in TITLES if key not in chosen]
    state.set_state(db, now, LAYOUT_KEY, {"order": chosen + hidden, "hidden": hidden})


def change_layout(db: Session, now: datetime, key: str, action: str) -> None:
    layout = load_layout(db)
    visible, hidden = list(layout.visible), list(layout.hidden)
    if action == "hide" and key in visible:
        visible.remove(key)
        hidden.append(key)
    elif action == "show" and key in hidden:
        hidden.remove(key)
        visible.append(key)
    elif action in ("up", "down") and key in visible:
        # 묶음 안에서만 옮긴다. 묶음을 넘으면 화면에서는 아무것도 바뀌지 않기 때문이다.
        group = layout.spend if _in_spend_group(key) else layout.assets
        index = group.index(key)
        other = index - 1 if action == "up" else index + 1
        if 0 <= other < len(group):
            first, second = visible.index(key), visible.index(group[other])
            visible[first], visible[second] = visible[second], visible[first]
    state.set_state(db, now, LAYOUT_KEY, {"order": visible + hidden, "hidden": hidden})


@dataclass(frozen=True)
class Row:
    name: str
    amount: int
    href: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class Section:
    title: str | None
    total: int
    rows: list[Row]


@dataclass
class HoldingCard:
    total: int = 0
    as_of: date | None = None
    sections: list[Section] = field(default_factory=list)
    has_banksalad: bool = False
    has_manual: bool = False
    debts: list[Row] = field(default_factory=list)
    details: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def overlap(self) -> bool:
        """뱅크샐러드에 있는 실물 자산을 또 입력하면 순자산이 부풀려진다."""
        return self.has_banksalad and self.has_manual

    @property
    def debt_total(self) -> int:
        return sum(row.amount for row in self.debts)


@dataclass(frozen=True)
class CategoryCard:
    slices: list[charts.Slice]
    size: int
    total: int
    top: list[charts.Bar]
    rest: list[charts.Bar]
    link: str


@dataclass(frozen=True)
class PaymentCard:
    segments: list[charts.Segment]
    total: int
    top: list[charts.Bar]
    rest: list[charts.Bar]
    link: str


@dataclass(frozen=True)
class CardsCard:
    total: int
    rows: list[charts.Bar]


@dataclass(frozen=True)
class NetWorthCard:
    point: reports.NetWorthPoint | None
    change: int | None
    chart: charts.LineChart | None


def _bars(amounts: Sequence[tuple[str, int, str | None, str | None, str | None]]) -> list[charts.Bar]:
    """(이름, 금액, 주소, 짧은 정보, 색 칸)을 큰 금액 기준의 막대로 바꾼다. 비중은 양수의 합을 기준으로 한다."""
    total = sum(max(amount, 0) for _, amount, _, _, _ in amounts)
    largest = max((amount for _, amount, _, _, _ in amounts), default=0)
    return [
        charts.Bar(
            name=name,
            amount=amount,
            share=amount / total if total > 0 else None,
            width=round(max(amount, 0) * 100 / largest, 2) if largest > 0 else 0,
            href=href,
            hint=hint,
            slot=slot,
        )
        for name, amount, href, hint, slot in amounts
    ]


def category_slots(db: Session) -> dict[str | None, str]:
    """색은 달이 바뀌어도 카테고리를 따라간다. 전체 기간의 순위로 정해 차트 화면과 맞춘다."""
    lines = reports.category_breakdown(db)
    return {
        line.category_key: f"slot-{index + 1}" if index < COLOR_SLOTS else "rest" for index, line in enumerate(lines)
    }


def category_card(db: Session, month: str) -> CategoryCard:
    lines = reports.category_breakdown(db, month, month)
    slots = category_slots(db)
    parts = [(line.category_key or "none", line.category_name or "카테고리 없음", line.amount, slots.get(line.category_key, "rest")) for line in lines]
    rest_amount = sum(amount for _, _, amount, slot in parts if slot == "rest" and amount > 0)
    colored = [part for part in parts if part[3] != "rest"]
    grouped = [*colored, ("rest", "그 외", rest_amount, "rest")] if rest_amount else colored
    bars = _bars(
        [
            (
                line.category_name or "카테고리 없음",
                line.amount,
                link("/transactions", month=month, nature="spend", category=line.category_key),
                f"{line.rows}건",
                slots.get(line.category_key, "rest"),
            )
            for line in lines
        ]
    )
    return CategoryCard(
        slices=charts.donut(grouped),
        size=charts.DONUT_SIZE,
        total=sum(line.amount for line in lines),
        top=bars[:TOP_ROWS],
        rest=bars[TOP_ROWS:],
        link=link("/chart", tab="category", month=month),
    )


def payment_card(db: Session, month: str, summary: reports.MonthSummary, lines: Sequence[reports.InstrumentLine]) -> PaymentCard:
    bars = _bars(
        [
            (line.name, line.amount, link("/transactions", month=month, nature="spend", instrument=line.instrument_id), f"{line.rows}건", None)
            for line in lines
        ]
    )
    return PaymentCard(
        segments=charts.stack(
            [(key, label, getattr(summary, f"spend_{key}")) for key, label in labels.CHANNEL_LABELS.items()]
        ),
        total=summary.spend_total,
        top=bars[:TOP_ROWS],
        rest=bars[TOP_ROWS:],
        link=link("/chart", tab="flow", month=month),
    )


def cards_card(month: str, summary: reports.MonthSummary, lines: Sequence[reports.InstrumentLine]) -> CardsCard:
    used = [line for line in lines if line.kind in ("credit_card", "check_card")]
    amounts: list[tuple[str, int, str | None, str | None, str | None]] = [
        (line.name, line.amount, link("/transactions", month=month, nature="spend", instrument=line.instrument_id), f"{line.rows}건", None)
        for line in used
    ]
    # 사용 내역 없이 대금만 보이는 카드는 계좌에서 나간 돈으로 기록되지만 카드 지출로 센다.
    unlinked = summary.spend_card - sum(line.amount for line in used)
    if unlinked:
        amounts.append(
            (
                "사용 내역 없는 카드 대금",
                unlinked,
                link("/transactions", month=month, nature="spend", channel="card", category="card_bill_unknown"),
                None,
                None,
            )
        )
    return CardsCard(total=summary.spend_card, rows=_bars(amounts))


def net_worth_card(db: Session, as_of: date, previous_end: date) -> NetWorthCard:
    point = reports.net_worth_on(db, as_of)
    earlier = reports.net_worth_on(db, previous_end)
    series = [item for item in reports.net_worth_series(db) if item.as_of <= as_of]
    return NetWorthCard(
        point=point,
        change=point.net_worth - earlier.net_worth if point and earlier else None,
        chart=charts.line_chart([(item.as_of, item.net_worth) for item in series]),
    )


def holding_cards(db: Session, as_of: date) -> dict[str, HoldingCard]:
    """그날의 자산을 카드별로 나눈다. 모두 더하면 그날의 총자산과 같다."""
    cards = {key: HoldingCard() for key in HOLDING_WIDGETS}
    snapshot = reports.snapshot_on(db, as_of)
    if snapshot is not None:
        groups: dict[str, list] = {}
        for item in sorted(snapshot.items, key=lambda line: line.amount, reverse=True):
            if item.side == "asset":
                groups.setdefault(item.group_label, []).append(item)
            else:
                cards["finance"].debts.append(Row(item.product_name, item.amount, hint=item.group_label))
        for group_label, items in sorted(groups.items(), key=lambda pair: -sum(line.amount for line in pair[1])):
            card = cards[WIDGET_OF_GROUP.get(group_label, "finance")]
            total = sum(line.amount for line in items)
            card.sections.append(
                Section(
                    group_label,
                    total,
                    [
                        Row(line.product_name, line.amount, href=f"/accounts/{line.instrument_id}" if line.instrument_id else None)
                        for line in items
                    ],
                )
            )
            card.total += total
            card.as_of = snapshot.as_of
            card.has_banksalad = True
        finance = cards["finance"]
        if finance.debts:
            finance.as_of = snapshot.as_of
        for detail in snapshot.details:
            finance.details.setdefault(detail.section, []).append(detail.fields)

    manual_rows: dict[str, dict[str, list[Row]]] = {}
    for asset in reports.manual_assets(db):
        if asset.acquired_on > as_of:
            continue
        sold = asset.sold_on is not None and asset.sold_on <= as_of
        details = (
            "처분함" if sold else None,
            labels.TENURE_LABELS.get(asset.tenure or ""),
            f"월세 {won(asset.monthly_rent)}" if asset.monthly_rent else None,
            asset.memo,
        )
        title = labels.OTHER_TYPE_LABELS.get(asset.other_type or "", "직접 입력")
        manual_rows.setdefault(WIDGET_OF_MANUAL_KIND[asset.kind], {}).setdefault(title, []).append(
            Row(
                asset.name,
                reports.manual_value_on(asset, as_of),
                href=f"/assets/manual/{asset.id}",
                hint=" · ".join(detail for detail in details if detail) or None,
            )
        )
    for key, sections in manual_rows.items():
        card = cards[key]
        for title, rows in sorted(sections.items(), key=lambda pair: -sum(row.amount for row in pair[1])):
            total = sum(row.amount for row in rows)
            card.sections.append(Section(title, total, rows))
            card.total += total
        card.has_manual = True
    return cards


def build(
    db: Session,
    *,
    month: str,
    as_of: date,
    previous_end: date,
    summary: reports.MonthSummary,
    visible: Iterable[str],
    holdings: dict[str, HoldingCard] | None = None,
    net_worth: NetWorthCard | None = None,
) -> dict[str, object]:
    """보이는 카드의 값만 모은다. 장부를 위해 이미 계산한 값이 있으면 다시 읽지 않는다."""
    wanted = set(visible)
    built: dict[str, object] = {}
    if "category" in wanted:
        built["category"] = category_card(db, month)
    if wanted & {"payment", "cards"}:
        lines = reports.instrument_breakdown(db, month)
        if "payment" in wanted:
            built["payment"] = payment_card(db, month, summary, lines)
        if "cards" in wanted:
            built["cards"] = cards_card(month, summary, lines)
    if "net_worth" in wanted:
        built["net_worth"] = net_worth or net_worth_card(db, as_of, previous_end)
    if wanted & set(HOLDING_WIDGETS):
        for key, card in (holdings or holding_cards(db, as_of)).items():
            if key in wanted:
                built[key] = card
    return built
