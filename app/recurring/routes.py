"""반복 거래: 같은 상대와의 거래를 정기성과 단기성으로 나누고, 분류를 정하면 규칙으로 남긴다."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_db, now_of, render
from app.format import back_link, kst_today
from app.ledger import labels, reports
from app.ledger.changes import CATEGORY_KIND_OF_NATURE
from app.ledger.models import NATURES, Category, Rule
from app.ledger.labels import LOAN_REASONS
from app.ledger.payment_links import LINKS_KEY, Link, link_kind, links_of, links_value
from app.ledger.reclassify import reclassify, save_target_rule
from app.ledger.recurring import amount_cap, target_key
from app.ops import state
from app.recurring import view as recurring_view

router = APIRouter()

MAX_CATEGORY_NAME = 30
CUSTOM_CATEGORY_ORDER = 175  # 미분류 앞에 놓는다
SIMILAR_MARGIN = 0.2
VIEWS = tuple(labels.RECURRING_KIND_LABELS)
BACK_PATHS = frozenset({"/recurring", "/review", "/chart"})
INTERNAL_CHOICE = "internal"  # 카테고리 목록 끝의 내 계좌 이동


@router.get("/recurring", response_class=HTMLResponse)
def recurring(
    request: Request,
    db: Session = Depends(get_db),
    view: str | None = None,
    loan: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    groups = reports.recurring_groups(db, kst_today(now_of(request)))
    counts = {key: sum(group.kind == key for group in groups) for key in VIEWS}
    active = view if view in VIEWS else "regular"
    rules = db.scalars(select(Rule).where(Rule.enabled.is_(True)).order_by(Rule.id.desc())).all()
    categories = recurring_view.category_options(db)
    context = recurring_view.group_context(db, categories)
    return render(
        request,
        "recurring.html",
        connect=recurring_view.connect_card(groups, links_of(db), context["targets"], loan),
        groups=[group for group in groups if group.kind == active],
        view=active,
        counts=counts,
        kind_labels=labels.RECURRING_KIND_LABELS,
        nature_labels=labels.NATURE_LABELS,
        recurring=context,
        back=f"/recurring?{urlencode({'view': active})}",
        rules=rules,
        category_names={category.id: category.name for group in categories.values() for category in group},
        message=recurring_view.MESSAGES.get(msg or ""),
        error=recurring_view.ERRORS.get(error or ""),
    )


def _new_category(db: Session, name: str, kind: str) -> Category:
    """같은 이름이 있으면 그것을 쓴다."""
    existing = db.scalar(select(Category).where(Category.name == name, Category.kind == kind))
    if existing is not None:
        return existing
    category = Category(
        key=f"custom_{secrets.token_hex(4)}", name=name, kind=kind, sort_order=CUSTOM_CATEGORY_ORDER, is_system=False
    )
    db.add(category)
    db.flush()
    return category


@router.post("/recurring/rules")
def save_rule(
    request: Request,
    db: Session = Depends(get_db),
    description: str = Form(""),
    direction: str = Form(""),
    back: str = Form("/recurring"),
    nature: str = Form(""),
    category: str = Form(""),
    new_category: str = Form(""),
    similar_only: str = Form(""),
    choice: str = Form(""),
):
    def go_back(**params: str) -> RedirectResponse:
        return RedirectResponse(back_link(back, BACK_PATHS, "/recurring", **params), status_code=303)

    if choice:  # 한 번에 고르는 버튼. 접어 둔 목록과 새 카테고리 칸보다 먼저다
        picked = recurring_view.parse_choice(db, choice)
        if picked is None:
            return go_back(error="category")
        (nature, category), new_category = picked, ""
    if category == INTERNAL_CHOICE:  # 카테고리 목록에서 고른 내 계좌 이동
        nature, category = "internal", ""
    key = target_key(description)
    groups = reports.recurring_groups(db, kst_today(now_of(request)))
    group = next((item for item in groups if item.key == key and item.direction == direction), None)
    if group is None:
        return go_back(error="group")
    if nature not in NATURES:
        return go_back(error="nature")

    kind = CATEGORY_KIND_OF_NATURE.get(nature)
    name = new_category.strip()[:MAX_CATEGORY_NAME]
    chosen = None
    if name:
        if kind is None:
            return go_back(error="new_category")
        chosen = _new_category(db, name, kind)
    elif category.isascii() and category.isdigit():
        chosen = db.get(Category, int(category))
        if chosen is None or chosen.kind != kind:
            return go_back(error="category")
    changes_nature = nature != group.nature or (group.undetermined and nature != "undetermined")
    changes_category = chosen is not None and chosen.key != group.category_key
    if not changes_nature and not changes_category:
        return go_back(error="empty")

    low, high = None, amount_cap(direction, nature, group.typical_amount)
    if similar_only:
        # 같은 상대라도 금액이 많이 다르면 다른 목적일 수 있다.
        low = int(group.typical_amount * (1 - SIMILAR_MARGIN))
        high = int(group.typical_amount * (1 + SIMILAR_MARGIN))
    save_target_rule(db, group.description, direction, nature, chosen.id if chosen else None, low, high)
    state.record(db, now_of(request), "rule_saved", rule_kind=group.kind)
    db.commit()
    return go_back(msg="rule_saved")


@router.post("/recurring/links")
def save_link(
    request: Request,
    db: Session = Depends(get_db),
    description: str = Form(""),
    direction: str = Form("out"),
    back: str = Form("/recurring"),
    loan: str = Form(""),
    asset: str = Form(""),
    pension: str = Form(""),
):
    """정기 거래를 대출, 부동산, 연금에 잇는다. 모두 비우면 연결을 푼다. 받은 돈은 연금에만 잇는다.

    아직 분류하지 않은 묶음이면 함께 분류한다. 대출과 부동산은 지출, 연금은 저축·투자다.
    대출에 이은 납입은 금리를 알면 이자와 원금으로 나뉜다.
    """

    def go_back(**params: str) -> RedirectResponse:
        return RedirectResponse(back_link(back, BACK_PATHS, "/recurring", **params), status_code=303)

    now = now_of(request)
    key = target_key(description)
    direction = direction if direction in ("out", "in") else "out"
    groups = reports.recurring_groups(db, kst_today(now))
    group = next((item for item in groups if item.key == key and item.direction == direction), None)
    if group is None:
        return go_back(error="group")
    targets = recurring_view.link_targets(db)
    if (
        (loan and loan not in targets.loan_names)
        or (asset and asset not in targets.asset_names)
        or (pension and pension not in targets.pension_names)
        or (pension and (loan or asset))  # 연금에 오가는 돈이 대출이나 집에 내는 돈일 수는 없다
        or (direction == "in" and (loan or asset))
    ):
        return go_back(error="link")
    links = [link for link in links_of(db) if link.slot != (key, direction)]
    linked = bool(loan or asset or pension)
    if linked:
        links.append(Link(key, group.description, loan or None, asset or None, pension or None, direction))
    state.set_state(db, now, LINKS_KEY, links_value(links))
    state.record(db, now, "payment_link_saved", linked=linked)
    message = "link_saved" if linked else "link_removed"
    if linked and group.needs_review:
        # 대출 납입 중 금리를 몰라 나누지 못하는 것도 이 분류를 따른다.
        nature, category_key = link_kind(links[-1])
        category = db.scalar(select(Category).where(Category.key == category_key)) if category_key else None
        cap = amount_cap(direction, nature, group.typical_amount)
        save_target_rule(db, group.description, direction, nature, category.id if category else None, None, cap)
        state.record(db, now, "rule_saved", rule_kind=group.kind)
        message = "link_saving" if nature == "asset_move" else "link_spend"
    else:
        reclassify(db)  # 대출 납입을 나누거나 한 건으로 되돌린다
    if loan and any(line.reason in LOAN_REASONS for line in reports.recurring_lines(db) if target_key(line.description) == key):
        message = "link_split"
    db.commit()
    return go_back(msg=message)


@router.post("/recurring/rules/{rule_id}/disable")
def disable_rule(request: Request, rule_id: int, db: Session = Depends(get_db), view: str = Form("regular")):
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404)
    rule.enabled = False
    reclassify(db)
    state.record(db, now_of(request), "rule_disabled")
    db.commit()
    active = view if view in VIEWS else "regular"
    return RedirectResponse(f"/recurring?{urlencode({'view': active, 'msg': 'rule_disabled'})}", status_code=303)
