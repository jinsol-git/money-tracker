"""화면. 숫자는 모두 reports를 거쳐 화면과 챗이 같은 값을 본다."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import chart_widgets, charts
from app.charts import Bar
from app.deps import get_db, now_of, render
from app.format import back_link, compact_won, kst_today, link, month_end, previous_month, valid_month, won
from app.ledger import labels, reports
from app.ledger.changes import (
    CATEGORY_KIND_OF_NATURE,
    CHANGE_ERRORS,
    ChangeError,
    apply_change,
    recent_changes,
    undo_change_set,
)
from app.ledger.models import NATURES, Category, ChangeSet, Instrument, ManualAsset, TxnSplit
from app.ledger.payment_links import pension_growth
from app.ledger.reclassify import save_target_rule
from app.ledger.recurring import amount_cap, target_key
from app.ops import state
from app.recurring import view as recurring_view

router = APIRouter()

PAGE_SIZE = 50
ALL_ROWS = 5000  # 묶인 거래를 뺀 뒤에도 한 쪽을 채울 수 있게 넉넉히 읽는다
CHART_TABS = ("category", "flow", "assets", "finance")
SPEND_GROUP = "spend"
NO_CATEGORY = reports.NO_CATEGORY_KEY
NATURE_GROUPS = {SPEND_GROUP: ("expense", "refund"), **{nature: (nature,) for nature in NATURES}}
NATURE_FILTERS = {SPEND_GROUP: "지출·환불", **labels.NATURE_LABELS}
# 고치기 폼이 돌아갈 수 있는 화면
BACK_PATHS = frozenset({"/review", "/transactions"})
MESSAGES = {
    "unchanged": "바뀐 내용이 없습니다.",
    "undone": "변경을 되돌렸습니다.",
    **recurring_view.MESSAGES,
}
ERRORS = {**recurring_view.ERRORS, **CHANGE_ERRORS}
GROUPED_QUEUES = frozenset({"undetermined", "uncategorized"})


@dataclass(frozen=True)
class ReviewQueue:
    title: str
    fetch: Callable[..., list[reports.LineItem]]


REVIEW_QUEUES = {
    "undetermined": ReviewQueue("미확정", reports.review_undetermined),
    "uncategorized": ReviewQueue("미분류", reports.review_uncategorized),
    "inflows": ReviewQueue("큰 입금", reports.review_large_inflows),
    "largest": ReviewQueue("큰 지출", reports.review_largest_spend),
}


def _date_or_none(value: str | None) -> str | None:
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def _int_or_none(value: str | None) -> int | None:
    # bigint 범위를 넘는 값이 DB 오류가 되지 않게 길이도 본다.
    return int(value) if value and value.isascii() and value.isdigit() and len(value) <= 18 else None


def _notices(db: Session, changed: str | None, msg: str | None, error: str | None) -> dict[str, object]:
    change_id = _int_or_none(changed)
    change = db.get(ChangeSet, change_id) if change_id else None
    return {
        "change_notice": change if change is not None and change.undone_at is None else None,
        "message": MESSAGES.get(msg or ""),
        "error": ERRORS.get(error or ""),
    }


def _back(value: str, **extra: object) -> RedirectResponse:
    return RedirectResponse(back_link(value, BACK_PATHS, "/review", **extra), status_code=303)


@router.get("/healthz")
def health():
    return {"ok": True}


@router.get("/chart", response_class=HTMLResponse)
def chart(request: Request, tab: str | None = None):
    return render(request, "chart.html", nav=tab if tab in CHART_TABS else CHART_TABS[0])


def _asset_chart_data(history: Sequence[reports.AssetGroupLine]) -> dict[str, object]:
    """항목별 값을 날짜마다 묶는다. 항목은 마지막 값이 큰 순서로 고정한다."""
    snapshots: dict[date, dict] = {}
    latest: dict[str, dict] = {}
    for line in history:
        key = f"{line.side}:{line.group_label}"
        snapshots.setdefault(line.as_of, {"date": str(line.as_of), "groups": {}})["groups"][key] = line.amount
        latest[key] = {
            "key": key,
            "name": line.group_label,
            "side": line.side,
            "latest": line.amount,
            "bucket": labels.BUCKET_OF_GROUP.get(line.group_label, "grow"),
        }
    groups = sorted(latest.values(), key=lambda item: (item["side"] != "asset", -item["latest"]))
    days = sorted(snapshots)
    return {
        "asset_range": {"first": str(days[0]), "last": str(days[-1])} if days else None,
        "snapshots": [snapshots[day] for day in days],
        "asset_groups": groups,
        "asset_buckets": [{"key": key, "name": name} for key, name in labels.ASSET_BUCKETS.items()],
    }


def _link_facts(links: reports.AssetLinks | None, balances: dict[str, int]) -> list[dict]:
    """부동산에 이어 둔 대출과 납입."""
    if links is None:
        return []
    facts = []
    for loan in links.loans:
        balance = balances.get(loan)
        note = f"잔액 {compact_won(balance)}원" if balance is not None else None
        facts.append({"label": "연결된 대출", "value": loan.split(":", 2)[-1], "note": note})
    for payment in links.payments:
        label = "연결된 납입" if payment.direction == "out" else "연결된 수령"
        facts.append({"label": label, "value": payment.name, "note": f"{won(payment.typical)} · {payment.times}회"})
    return facts


def _pension_links_data(
    links: dict[str, reports.PensionLinks], history: Sequence[reports.FinanceLine], manual: Sequence[ManualAsset]
) -> dict[str, dict]:
    """연금 자산에 이어 둔 납입과 수령. 키는 금융 탭의 상품 키다."""
    balances: dict[str, list[tuple[date, int]]] = {}
    for line in history:
        if line.side == "asset":
            balances.setdefault(f"asset:{line.group_label}:{line.product_name}", []).append((line.as_of, line.amount))
    product_keys = {
        f"manual:{asset.id}": f"asset:{labels.manual_group_label(asset.kind, asset.tenure, asset.other_type)}:{asset.name}"
        for asset in manual
    }
    data = {}
    for key, entry in links.items():
        shown_key = product_keys.get(key, key)
        growth = pension_growth(balances.get(shown_key, []), entry.flows)
        data[shown_key] = {
            "payments": [
                {"name": item.name, "typical": item.typical, "times": item.times, "direction": item.direction}
                for item in entry.payments
            ],
            "contributed": entry.contributed,
            "received": entry.received,
            "growth": None if growth is None else {"from": str(growth[0]), "to": str(growth[1]), "amount": growth[2]},
        }
    return data


def _manual_facts(asset: ManualAsset) -> list[dict]:
    """직접 입력 정보 칸에 적을 값."""
    held = {"label": "보유 시작", "value": f"{asset.acquired_on:%Y.%m.%d}"}
    if asset.kind == "car":
        size = labels.CAR_CLASS_LABELS.get(asset.car_class or "", "").split(" ")[0]
        origin = labels.CAR_ORIGIN_LABELS.get(asset.car_origin or "", "")
        facts = [{"label": "차종", "value": " ".join(filter(None, (size, origin))) or "-", "note": asset.name}]
        if asset.first_registered:
            facts.append({"label": "최초 등록", "value": f"{asset.first_registered:%Y.%m}", "note": "차의 나이 기준"})
        if asset.base_price:
            note = f"{asset.base_date:%Y.%m} 기준" if asset.base_date else None
            facts.append({"label": "출발 금액", "value": f"{compact_won(asset.base_price)}원", "note": note})
        return facts
    if asset.kind == "real_estate":
        kind = {"label": "종류", "value": labels.TENURE_LABELS.get(asset.tenure or "", "-")}
        if asset.tenure == "own":
            if not asset.base_price:
                return [kind, held]
            note = f"{asset.base_date:%Y.%m.%d}" if asset.base_date else None
            return [kind, {"label": "구매가", "value": f"{compact_won(asset.base_price)}원", "note": note}, held]
        rent = won(asset.monthly_rent) if asset.monthly_rent else "없음"
        return [kind, {"label": "월세", "value": rent}, held]
    # 메모는 차트로 보내지 않는다. 자산 화면에서 본다.
    return [{"label": "종류", "value": labels.OTHER_TYPE_LABELS.get(asset.other_type or "", "기타")}, held]


def _asset_products(
    items: Sequence[reports.AssetItemLine],
    manual: Sequence[ManualAsset],
    today: date,
    links: dict[str, reports.AssetLinks],
) -> dict[str, list[dict]]:
    """자산 항목마다 들어 있는 것: 마지막 내보내기의 상품과 지금 갖고 있는 직접 입력 자산."""
    balances = {f"{item.side}:{item.group_label}:{item.product_name}": item.amount for item in items if item.side == "liability"}
    products: dict[str, list[dict]] = {}
    for item in items:
        entry = {"name": item.product_name, "amount": item.amount}
        linked = _link_facts(links.get(f"{item.side}:{item.group_label}:{item.product_name}"), balances)
        if linked:
            entry["hint"] = " · ".join(f"{fact['label']} {fact['value']}" for fact in linked)
        products.setdefault(f"{item.side}:{item.group_label}", []).append(entry)
    for asset in manual:
        amount = reports.manual_value_on(asset, today)
        if amount:
            key = f"asset:{labels.manual_group_label(asset.kind, asset.tenure, asset.other_type)}"
            products.setdefault(key, []).append(
                {
                    "name": asset.name,
                    "amount": amount,
                    "href": f"/assets/manual/{asset.id}",
                    "facts": _manual_facts(asset) + _link_facts(links.get(f"manual:{asset.id}"), balances),
                }
            )
    return products


def _finance_chart_data(
    history: Sequence[reports.FinanceLine],
    investments: Sequence[reports.InvestmentPoint],
    items_on_latest: Sequence[reports.AssetItemLine],
) -> dict[str, object]:
    """금융 상품을 날짜마다 묶는다. 상품과 항목은 마지막 값이 큰 순서로 고정한다."""
    instruments = {
        f"{item.side}:{item.group_label}:{item.product_name}": item.instrument_id
        for item in items_on_latest
        if item.instrument_id
    }
    snapshots: dict[date, dict] = {}
    latest: dict[str, dict] = {}
    for line in history:
        group = f"{line.side}:{line.group_label}"
        key = f"{group}:{line.product_name}"
        snapshots.setdefault(line.as_of, {"date": str(line.as_of), "groups": {}})["groups"][key] = line.amount
        latest[key] = {
            "key": key,
            "name": line.product_name,
            "side": line.side,
            "group": group,
            "group_name": line.group_label,
            "latest": line.amount,
            "instrument": instruments.get(key),
        }
    items = sorted(latest.values(), key=lambda item: (item["side"] != "asset", -item["latest"]))
    groups: dict[str, dict] = {}
    for item in items:
        entry = groups.setdefault(
            item["group"], {"key": item["group"], "name": item["group_name"], "side": item["side"], "latest": 0}
        )
        entry["latest"] += item["latest"]
    days = sorted(snapshots)
    return {
        "finance_range": {"first": str(days[0]), "last": str(days[-1])} if days else None,
        "finance_snapshots": [snapshots[day] for day in days],
        "finance_items": items,
        "finance_groups": sorted(groups.values(), key=lambda item: (item["side"] != "asset", -item["latest"])),
        "finance_investments": {
            str(point.as_of): {"principal": point.principal, "valuation": point.valuation} for point in investments
        },
    }


def _chart_options(lines: Sequence[reports.ChartLine]) -> tuple[list[dict], list[dict]]:
    """버튼 목록. 많이 쓴 순서로 고정해 숫자가 바뀌어도 버튼이 움직이지 않게 한다."""
    categories: dict[str, dict] = {}
    instruments: dict[int, dict] = {}
    for line in lines:
        key = line.category_key or NO_CATEGORY
        category = categories.setdefault(
            key, {"key": key, "name": line.category_name or "카테고리 없음", "kind": line.kind, "total": 0}
        )
        category["total"] += line.amount
        instrument = instruments.setdefault(
            line.instrument_id, {"id": line.instrument_id, "name": line.instrument_name, "total": 0}
        )
        instrument["total"] += line.amount if line.kind == "spend" else 0
    ordered = sorted(categories.values(), key=lambda item: (item["kind"] != "spend", -item["total"]))
    return ordered, sorted(instruments.values(), key=lambda item: -item["total"])


def _loan_links_data(links: reports.LoanLinks) -> dict[str, object]:
    """대출에 이어 둔 납입과, 납입액으로 구한 금리."""
    implied = links.implied
    return {
        "payments": [
            {
                "name": item.name,
                "typical": item.typical,
                "times": item.times,
                "kind": None if item.split else labels.NATURE_LABELS.get(item.nature),
            }
            for item in links.payments
        ],
        "months": [
            {"month": item.month, "interest": item.interest, "principal": item.principal, "exact": item.exact}
            for item in links.months
        ],
        "reason": implied.reason,
        "implied": None
        if implied.rate is None
        else {
            "rate": implied.rate,
            "per_payment": implied.per_payment,
            "count": implied.count,
            "first": str(implied.first),
            "last": str(implied.last),
        },
    }


@router.get("/chart/data")
def chart_data(request: Request, db: Session = Depends(get_db)):
    """브라우저에서 바로 나눠 볼 수 있는 합계. 거래 내용은 보내지 않는다. 줄은 [날짜, 수입·지출, 결제 방식, 결제 수단, 비목, 금액]이다."""
    lines = reports.chart_lines(db)
    categories, instruments = _chart_options(lines)
    as_of = reports.data_status(db).data_as_of
    today = kst_today(now_of(request))
    flow_range = None
    if lines:
        # 내보낸 날까지는 거래가 없어도 실제로 0원인 날이므로 기간에 넣는다.
        flow_range = {"first": str(lines[0].day), "last": str(max(lines[-1].day, as_of or lines[-1].day))}
    items_on_latest = reports.asset_items_on_latest(db)
    finance_lines = reports.finance_history(db)
    loan_links, asset_links, pension_links = reports.linked_payments(db, finance_lines)
    manual = reports.manual_assets(db)
    return {
        "today": str(today),
        "flow_range": flow_range,
        "channels": [{"key": key, "name": name} for key, name in labels.CHANNEL_LABELS.items()],
        "categories": categories,
        "instruments": instruments,
        "lines": [
            [str(line.day), line.kind, line.channel, line.instrument_id, line.category_key or NO_CATEGORY, line.amount]
            for line in lines
        ],
        **_asset_chart_data(reports.asset_history(db)),
        **_finance_chart_data(finance_lines, reports.investment_history(db), items_on_latest),
        "asset_products": _asset_products(items_on_latest, manual, today, asset_links),
        "finance_details": reports.finance_details(db),
        "loan_payments": {key: _loan_links_data(links) for key, links in loan_links.items()},
        "pension_links": _pension_links_data(pension_links, finance_lines, manual),
        "widgets": chart_widgets.payload(db),
    }


@router.post("/chart/widgets")
def change_chart_widgets(
    request: Request,
    db: Session = Depends(get_db),
    tab: str = Form(""),
    action: str = Form(""),
    key: str = Form(""),
    theme: str = Form(""),
    keys: list[str] = Form(default=[]),
):
    if not chart_widgets.is_valid_change(tab, action, key, theme, keys):
        raise HTTPException(status_code=404)
    layout = chart_widgets.change_layout(db, now_of(request), tab, action, key, theme, keys)
    db.commit()
    return {"layout": layout}


@router.get("/chart/places")
def chart_places(
    db: Session = Depends(get_db),
    month: str | None = None,
    start: str | None = None,
    end: str | None = None,
    category: list[str] = Query(default=[]),
    channel: list[str] = Query(default=[]),
    instrument: list[str] = Query(default=[]),
):
    """자주 쓴 곳 위젯. 거래 내용이 들어 있어 합계와 따로, 볼 때만 받는다. 고른 칸의 첫날과 끝날이나 달로 고른다."""
    first, last = _date_or_none(start), _date_or_none(end)
    days = (min(first, last), max(first, last)) if first and last else None
    picked = valid_month(month)
    if days is None and picked is None:
        raise HTTPException(status_code=404)
    places = reports.top_places(
        db,
        picked,
        categories=category,
        channels=[key for key in channel if key in labels.CHANNEL_LABELS],
        instruments=[number for number in map(_int_or_none, instrument) if number],
        days=days,
    )
    return {"places": [{"name": place.name, "amount": place.amount, "rows": place.rows} for place in places]}


@router.get("/chart/account/{instrument_id}")
def chart_account(instrument_id: int, db: Session = Depends(get_db), month: str | None = None):
    """계좌 사용처 위젯. 계좌별 돈의 흐름 화면과 같은 계산이다."""
    if db.get(Instrument, instrument_id) is None:
        raise HTTPException(status_code=404)
    picked = valid_month(month)
    trace = reports.account_trace(db, instrument_id, picked, picked)
    return {"rows": [{"name": bar.name, "amount": bar.amount} for bar in _trace_bars(trace)]}


@router.get("/spending")
def spending(month: str | None = None):
    """예전 주소. 달을 옮겨 가며 보는 화면은 홈으로 합쳤다."""
    return RedirectResponse(link("/", month=valid_month(month)), status_code=303)


@router.get("/assets")
def assets():
    """예전 주소. 자산은 차트의 자산 탭과 홈의 카드에서 본다."""
    return RedirectResponse(link("/chart", tab="assets"), status_code=303)


def _date_range(month: str | None, start: str | None, end: str | None) -> tuple[str | None, str | None]:
    """달로 들어온 주소는 그 달의 기간으로 바꾼다. 화면에는 기간만 있다. 앞뒤가 바뀐 날짜는 바로잡는다."""
    first, last = _date_or_none(start), _date_or_none(end)
    picked = valid_month(month)
    if picked and not first and not last:
        return f"{picked}-01", str(month_end(picked))
    if first and last and first > last:
        return last, first
    return first, last


def _date_presets(today: date, filters: dict[str, object]) -> list[tuple[str, str, bool]]:
    this_month = today.strftime("%Y-%m")
    presets = []
    for name, month in (("이번 달", this_month), ("지난 달", previous_month(this_month))):
        first, last = f"{month}-01", str(month_end(month))
        active = filters["start"] == first and filters["end"] == last
        presets.append((name, link("/transactions", **{**filters, "start": first, "end": last}), active))
    return presets


@router.get("/transactions", response_class=HTMLResponse)
def transactions(
    request: Request,
    db: Session = Depends(get_db),
    month: str | None = None,
    channel: str | None = None,
    category: str | None = None,
    instrument: str | None = None,
    nature: str | None = None,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: str | None = None,
    changed: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    options = recurring_view.category_options(db)
    keys = {item.key for group in options.values() for item in group}
    filters = {
        "channel": channel if channel in labels.CHANNEL_LABELS else None,
        "category": category if category in keys else None,
        "instrument": _int_or_none(instrument),
        "nature": nature if nature in NATURE_GROUPS else None,
        "q": (q or "").strip()[:100] or None,
        **dict(zip(("start", "end"), _date_range(month, start, end))),
    }
    number = max(_int_or_none(page) or 1, 1)
    lines = reports.list_lines(
        db,
        channel=filters["channel"],
        category_key=filters["category"],
        instrument_id=filters["instrument"],
        natures=NATURE_GROUPS[filters["nature"]] if filters["nature"] else None,
        query=filters["q"],
        date_from=date.fromisoformat(filters["start"]) if filters["start"] else None,
        date_to=date.fromisoformat(filters["end"]) if filters["end"] else None,
        limit=PAGE_SIZE,
        offset=(number - 1) * PAGE_SIZE,
    )
    pages = max((lines.total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    return render(
        request,
        "transactions.html",
        lines=lines,
        filters=filters,
        filtered=any(value is not None for value in filters.values()),
        more_filtered=any(filters[name] is not None for name in ("channel", "category", "instrument", "nature")),
        date_presets=_date_presets(kst_today(now_of(request)), filters),
        page=number,
        pages=pages,
        previous=link("/transactions", **filters, page=number - 1) if number > 1 else None,
        following=link("/transactions", **filters, page=number + 1) if number < pages else None,
        back=link("/transactions", **filters, page=number if number > 1 else None),
        channel_labels=labels.CHANNEL_LABELS,
        nature_filters=NATURE_FILTERS,
        nature_labels=labels.NATURE_LABELS,
        categories=options,
        category_help=recurring_view.category_help(db),
        instruments=db.scalars(select(Instrument).order_by(Instrument.display_name)).all(),
        **_notices(db, changed, msg, error),
    )


@router.get("/review", response_class=HTMLResponse)
def review(
    request: Request,
    db: Session = Depends(get_db),
    queue: str | None = None,
    changed: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    counts = reports.review_counts(db)
    if queue not in REVIEW_QUEUES:
        queue = "uncategorized" if counts.uncategorized and not counts.undetermined else "undetermined"
    active = REVIEW_QUEUES[queue]
    categories = recurring_view.category_options(db)
    groups = []
    if queue in GROUPED_QUEUES:
        found = reports.recurring_groups(db, kst_today(now_of(request)))
        groups = recurring_view.groups_for_queue(found, queue)
        grouped = {(group.key, group.direction): group for group in groups}

        def shown_in_a_group(item: reports.LineItem) -> bool:
            group = grouped.get((target_key(item.description), "out" if item.amount < 0 else "in"))
            return group is not None and item.occurred_date not in group.outliers

        # 묶음으로 보여 준 거래는 아래 목록에 다시 적지 않는다. 묶음에서 뺀 큰 금액은 여기서 따로 정한다.
        items = [item for item in active.fetch(db, ALL_ROWS) if not shown_in_a_group(item)][:PAGE_SIZE]
    else:
        items = active.fetch(db)
    return render(
        request,
        "review.html",
        queues=REVIEW_QUEUES,
        queue=queue,
        groups=groups,
        recurring=recurring_view.group_context(db, categories),
        items=items,
        counts={
            "undetermined": counts.undetermined,
            "uncategorized": counts.uncategorized,
            "inflows": counts.large_inflows,
        },
        back=link("/review", queue=queue),
        changes=recent_changes(db),
        nature_labels=labels.NATURE_LABELS,
        categories=categories,
        **_notices(db, changed, msg, error),
    )


def _change_summary(db: Session, split: TxnSplit, nature: str | None, category_id: int | None) -> str:
    parts = []
    if nature is not None:
        parts.append(f"종류 {labels.NATURE_LABELS[split.nature]} → {labels.NATURE_LABELS[nature]}")
    if category_id is not None:
        old = db.get(Category, split.category_id) if split.category_id is not None else None
        parts.append(f"카테고리 {old.name if old else '없음'} → {db.get(Category, category_id).name}")
    return f"{split.txn.description}: {', '.join(parts)}"


def _remember_for_target(
    request: Request, db: Session, split: TxnSplit, nature: str | None, category_id: int | None, back: str
) -> RedirectResponse:
    """이 거래만이 아니라 같은 상대의 거래를 모두 옮긴다. 규칙으로 남아 앞으로 들어올 거래도 같은 분류가 된다."""
    final_nature = nature or split.nature
    kind = CATEGORY_KIND_OF_NATURE.get(final_nature)
    chosen = db.get(Category, category_id) if category_id is not None else None
    if chosen is not None and kind is None:
        return _back(back, error="category_not_allowed")
    if chosen is not None and chosen.kind != kind:
        return _back(back, error="category_mismatch")
    direction = "out" if split.amount < 0 else "in"
    cap = amount_cap(direction, final_nature, abs(split.amount))
    save_target_rule(db, split.txn.description, direction, final_nature, category_id, None, cap)
    state.record(db, now_of(request), "rule_saved", rule_kind="line")
    db.commit()
    return _back(back, msg="rule_saved")


@router.post("/splits/{split_id}/change")
def change_split(
    request: Request,
    split_id: int,
    db: Session = Depends(get_db),
    nature: str = Form(""),
    category: str = Form(""),
    remember: str = Form(""),
    back: str = Form("/review"),
    choice: str = Form(""),
):
    split = db.get(TxnSplit, split_id)
    if split is None:
        raise HTTPException(status_code=404)
    if choice:  # 한 번에 고르는 버튼
        picked = recurring_view.parse_choice(db, choice)
        if picked is None:
            return _back(back, error="unknown_category")
        nature, category = picked
    if category == "internal":  # 카테고리 목록에서 고른 내 계좌 이동
        nature, category = "internal", ""
    new_nature = nature if nature and nature != split.nature else None
    new_category = _int_or_none(category)
    if new_category == split.category_id:
        new_category = None
    if new_nature is None and new_category is None:
        return _back(back, msg="unchanged")
    if new_nature is not None and new_nature not in labels.NATURE_LABELS:
        return _back(back, error="unknown_nature")
    if new_category is not None and db.get(Category, new_category) is None:
        return _back(back, error="unknown_category")
    if remember:
        return _remember_for_target(request, db, split, new_nature, new_category, back)

    summary = _change_summary(db, split, new_nature, new_category)
    try:
        change = apply_change(db, [split_id], nature=new_nature, category_id=new_category, summary=summary)
    except ChangeError as failed:
        return _back(back, error=failed.code)
    state.record(db, now_of(request), "classification_changed", change_set=change.id)
    db.commit()
    return _back(back, changed=change.id)


@router.post("/changes/{change_id}/undo")
def undo_change(request: Request, change_id: int, db: Session = Depends(get_db), back: str = Form("/review")):
    try:
        undo_change_set(db, change_id)
    except ChangeError as failed:
        return _back(back, error=failed.code)
    state.record(db, now_of(request), "classification_undone", change_set=change_id)
    db.commit()
    return _back(back, msg="undone")


def _trace_bars(trace: reports.AccountTrace) -> list[Bar]:
    rows = [
        ("바로 쓴 돈", sum(trace.direct.values()), None),
        ("카드대금", trace.card_bills, None),
        ("페이 충전", trace.pay_charges, None),
        ("저축·투자", trace.savings_investment, None),
        ("대출", trace.debt, None),
        ("내 계좌 이동", trace.own_transfer, None),
        ("어디로 갔는지 모르는 돈", trace.untracked, f"{trace.untracked_rows}건"),
    ]
    return [
        Bar(
            name=name,
            amount=amount,
            share=amount / trace.total_out if trace.total_out else None,
            width=round(amount * 100 / trace.total_out, 2) if trace.total_out else 0,
            hint=hint,
        )
        for name, amount, hint in rows
        if amount
    ]


@router.get("/accounts/{instrument_id}", response_class=HTMLResponse)
def account(request: Request, instrument_id: int, db: Session = Depends(get_db), month: str | None = None):
    instrument = db.get(Instrument, instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404)
    month = valid_month(month)
    trace = reports.account_trace(db, instrument_id, month, month)
    names = {key: name for key, name in db.execute(select(Category.key, Category.name))}
    balances = reports.instrument_balances(db, instrument_id)
    return render(
        request,
        "account.html",
        instrument=instrument,
        kind_label=labels.INSTRUMENT_KIND_LABELS[instrument.kind],
        month=month,
        months=[item.month for item in reversed(reports.monthly_summary(db))],
        trace=trace,
        bars=_trace_bars(trace),
        direct=sorted(((names.get(key, key), amount) for key, amount in trace.direct.items()), key=lambda r: -r[1]),
        balance=balances[-1] if balances else None,
        chart=charts.line_chart(balances),
        lines_link=link("/transactions", instrument=instrument_id, month=month),
    )
