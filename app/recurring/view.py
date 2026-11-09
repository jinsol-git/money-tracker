"""반복 거래 묶음을 화면에 보여 줄 때 필요한 값. 검토 화면과 반복 거래 화면이 함께 쓴다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.format import link as page_link
from app.ledger import labels, reports
from app.ledger.changes import CATEGORY_KIND_OF_NATURE
from app.ledger.classify import mapped_category
from app.ledger.models import Category, Rule
from app.ledger.payment_links import AssetOption, Link, LoanOption, detail_row_for, guess_link, links_of
from app.ledger.reclassify import load_lookup
from app.ledger.recurring import Group, target_key

MESSAGES = {
    "rule_saved": "저장했습니다. 지난 거래와 앞으로 들어올 거래에 적용됩니다.",
    "rule_disabled": "규칙을 해제했습니다.",
    "link_saved": "연결했습니다.",
    "link_spend": "연결하고 지출로 분류했습니다. 지난 거래와 앞으로 들어올 거래에 적용됩니다.",
    "link_split": "연결했습니다. 이자는 지출, 원금은 대출로 나눴습니다.",
    "link_saving": "연결하고 저축·투자로 분류했습니다. 지난 거래와 앞으로 들어올 거래에 적용됩니다.",
    "link_removed": "연결을 해제했습니다.",
}
ERRORS = {
    "group": "해당 거래 묶음을 찾지 못했습니다. 새로고침한 뒤 다시 시도해 주세요.",
    "nature": "종류를 골라 주세요.",
    "category": "종류에 맞는 카테고리를 골라 주세요.",
    "new_category": "새 카테고리는 지출이나 수입 종류에만 만들 수 있습니다.",
    "empty": "바뀐 내용이 없습니다. 종류나 카테고리를 골라 주세요.",
    "link": "연결할 대출, 부동산, 연금을 찾지 못했습니다. 새로고침한 뒤 다시 시도해 주세요.",
}
KIND_ORDER = tuple(labels.RECURRING_KIND_LABELS)
CONNECT_KINDS = ("regular", "ended")
CONNECT_ROWS = 8
CONNECT_CATEGORIES = (None, "finance", "housing")  # 학원비나 보험료처럼 다른 곳에 내는 돈은 후보에서 뺀다


@dataclass(frozen=True)
class LinkTargets:
    loans: list[LoanOption]
    assets: list[AssetOption]
    pensions: list[AssetOption]

    @property
    def loan_names(self) -> dict[str, str]:
        return {item.key: item.name for item in self.loans}

    @property
    def asset_names(self) -> dict[str, str]:
        return {item.key: item.name for item in self.assets}

    @property
    def pension_names(self) -> dict[str, str]:
        return {item.key: item.name for item in self.pensions}


@dataclass(frozen=True)
class ConnectRow:
    group: Group
    asset: str | None  # 함께 이을 부동산
    guess: str | None  # 내용으로 짐작한 대출


# 검토에서 한 번에 고르는 버튼. 금액으로 방향을 알기 때문에 그 방향에 맞는 것만 둔다. 나머지는 '다른 분류'에서 고른다.
QUICK_PICKS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "out": (
        ("expense", "food"),
        ("expense", "living"),
        ("expense", "shopping"),
        ("expense", "apparel"),
        ("expense", "housing"),
        ("expense", "education"),
        ("expense", "entertainment"),
        ("expense", "beauty"),
        ("expense", "dues"),
        ("asset_move", None),  # 보증금처럼 돌려받을 돈도 여기다
        ("internal", None),
        ("ignored", None),
    ),
    "in": (
        ("income", "salary"),
        ("income", "allowance"),
        ("income", "financial_income"),
        ("income", "other_income"),
        ("refund", None),
        ("internal", None),
        ("ignored", None),
    ),
}


def parse_choice(db: Session, choice: str) -> tuple[str, str] | None:
    """버튼 값 '성격' 또는 '성격:카테고리 키'를 (성격, 카테고리 id)로 바꾼다. 없는 카테고리면 None이다."""
    nature, _, key = choice.partition(":")
    if not key:
        return nature, ""
    category_id = db.scalar(select(Category.id).where(Category.key == key))
    return None if category_id is None else (nature, str(category_id))


def category_options(db: Session) -> dict[str, list[Category]]:
    categories = db.scalars(select(Category).order_by(Category.sort_order)).all()
    return {kind: [c for c in categories if c.kind == kind] for kind in ("expense", "income")}


def category_help(db: Session) -> dict[str, object]:
    """카테고리 고르기를 돕는 값. 종류마다 고를 수 있는 카테고리 쪽, 고르지 않으면 들어갈 이름, 한 번에 고르는 버튼이다."""
    lookup = load_lookup(db)
    rows = db.execute(select(Category.id, Category.key, Category.name)).all()
    names = {category_id: name for category_id, _, name in rows}
    by_key = {key: name for _, key, name in rows}

    def auto(source: tuple[str, str, str]) -> dict[str, str]:
        return {kind: names.get(mapped_category(source, kind, "", lookup)[0], "") for kind in ("expense", "income")}

    def pick(nature: str, key: str | None) -> tuple[str, str]:
        return (f"{nature}:{key}", by_key[key]) if key else (nature, labels.NATURE_LABELS[nature])

    def pick_of(nature: str | None, key: str | None) -> tuple[str, str] | None:
        """미리 골라 둔 분류의 버튼. 카테고리가 있어야 하는 성격인데 카테고리가 없으면 버튼으로 만들지 않는다."""
        if not nature or nature == "undetermined" or nature not in labels.NATURE_LABELS:
            return None
        if key in by_key and nature in CATEGORY_KIND_OF_NATURE:
            return pick(nature, key)
        if nature in ("expense", "income"):
            return None
        return pick(nature, None)

    quick = {
        direction: [pick(nature, key) for nature, key in pairs if key is None or key in by_key]
        for direction, pairs in QUICK_PICKS.items()
    }
    return {"kinds": CATEGORY_KIND_OF_NATURE, "auto": auto, "quick": quick, "pick_of": pick_of}


def groups_for_queue(groups: Iterable[Group], queue: str) -> list[Group]:
    """검토 목록에 보여 줄 묶음. 정기, 끝난 정기, 단기, 불규칙 순서이고 같은 종류에서는 금액이 큰 것부터다."""
    chosen = [group for group in groups if getattr(group, queue)]
    return sorted(chosen, key=lambda group: (KIND_ORDER.index(group.kind), group.tentative, -group.total))


def _org_of(name: str, amount: int, rows: Sequence[dict]) -> str:
    """대출 현황 표에서 찾은 금융사."""
    row = detail_row_for(name, amount, rows)
    return row["org"] if row else ""


def link_targets(db: Session) -> LinkTargets:
    """정기 거래를 이을 수 있는 대출, 부동산, 연금. 내보낸 상품의 키는 금융 탭의 상품 키와 같다."""
    items = reports.asset_items_on_latest(db)
    rows = reports.finance_details(db)["loan"]
    loans = [
        LoanOption(f"{item.side}:{item.group_label}:{item.product_name}", item.product_name, _org_of(item.product_name, item.amount, rows))
        for item in items
        if item.side == "liability"
    ]
    assets = [
        AssetOption(f"{item.side}:{item.group_label}:{item.product_name}", item.product_name)
        for item in items
        if item.side == "asset" and item.group_label == labels.REAL_ESTATE_GROUP
    ]
    pensions = [
        AssetOption(f"{item.side}:{item.group_label}:{item.product_name}", item.product_name)
        for item in items
        if item.side == "asset" and item.group_label == labels.PENSION_GROUP
    ]
    for asset in reports.manual_assets(db):
        if asset.sold_on is not None:
            continue
        if asset.kind == "real_estate":
            assets.append(AssetOption(f"manual:{asset.id}", asset.name, asset.monthly_rent))
        elif asset.other_type == "pension":
            pensions.append(AssetOption(f"manual:{asset.id}", asset.name))
    return LinkTargets(loans, assets, pensions)


def _may_be_loan_payment(group: Group) -> bool:
    return (
        group.direction == "out"
        and group.kind in CONNECT_KINDS
        and group.suggestion.nature in ("expense", "debt_move", "undetermined")
        and group.suggestion.category_key in CONNECT_CATEGORIES
    )


def connect_card(groups: Iterable[Group], links: Iterable[Link], targets: LinkTargets, loan_key: str | None) -> dict | None:
    """금융 탭의 대출 상세에서 왔을 때 보여 준다. 고르면 바로 그 대출로 돌아간다.

    내용이 맞는 것, 매달 나가는 것, 금액이 큰 것 순서이고 이미 대출에 이은 묶음은 뺀다.
    """
    loan = next((item for item in targets.loans if item.key == loan_key), None)
    if loan is None:
        return None
    link_of = {link.key: link for link in links if link.direction == "out"}
    rows = []
    for group in groups:
        saved = link_of.get(group.key)
        if _may_be_loan_payment(group) and not (saved and saved.loan):
            guess = guess_link(group.description, group.typical_amount, targets.loans, targets.assets)
            rows.append(ConnectRow(group, saved.asset if saved else guess.asset, guess.loan))
    rows.sort(key=lambda row: (row.guess != loan.key, row.guess is not None, row.group.period != "매달", -row.group.total))
    return {"loan": loan, "back": page_link("/chart", tab="finance", focus=loan.key), "rows": rows[:CONNECT_ROWS]}


def group_context(db: Session, categories: dict[str, list[Category]]) -> dict[str, object]:
    rules = db.scalars(select(Rule).where(Rule.enabled.is_(True), Rule.match_description_equals.is_not(None)))
    targets = link_targets(db)
    return {
        "targets": targets,
        "category_help": category_help(db),
        "link_of": {link.slot: link for link in links_of(db)},
        "link_loans": targets.loan_names,
        "link_assets": targets.asset_names,
        "link_pensions": targets.pension_names,
        "link_guess": lambda group: guess_link(
            group.description, group.typical_amount, targets.loans, targets.assets, targets.pensions, group.direction
        ),
        "rule_of": {(target_key(rule.match_description_equals), rule.match_direction): rule for rule in rules},
        "suggested_ids": {category.key: category.id for group in categories.values() for category in group},
        "categories": categories,
        "nature_labels": labels.NATURE_LABELS,
        "kind_labels": labels.RECURRING_KIND_LABELS,
        "spread_labels": labels.SPREAD_LABELS,
    }
