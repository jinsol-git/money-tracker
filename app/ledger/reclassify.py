"""장부 전체에 분류 규칙을 다시 적용한다. 사용자가 직접 고친 값은 건드리지 않는다."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.ledger.classify import (
    PAY_WORDS,
    CardView,
    CategoryLookup,
    RowView,
    RuleView,
    category_for,
    classify_rows,
    company_of,
    detect_pairs,
    own_name_key,
)
from app.ledger import loan_split
from app.ledger.labels import LOAN_REASONS
from app.ledger.models import Category, CategoryMap, Instrument, Rule, Txn, TxnLink, TxnSplit
from app.ledger.recurring import target_key
from app.ops_models import SystemState

OWN_NAMES_KEY = "own_names"
MAX_OWN_NAMES = 5
MAX_OWN_NAME_LENGTH = 30
CARD_KINDS = {"credit_card", "check_card"}
VIRTUAL_CARD_SUFFIX = "카드 (대금으로만 확인)"


def load_lookup(session: Session) -> CategoryLookup:
    entries = session.scalars(select(CategoryMap)).all()
    categories = session.execute(select(Category.key, Category.id, Category.kind)).all()
    keys = {key: category_id for key, category_id, _ in categories}
    return CategoryLookup(
        map={(e.source_type, e.source_major, e.source_minor): e.category_id for e in entries},
        uncategorized_expense=keys["uncategorized_expense"],
        uncategorized_income=keys["uncategorized_income"],
        card_bill_unknown=keys["card_bill_unknown"],
        kinds={category_id: kind for _, category_id, kind in categories},
    )


def parse_own_names(text: str) -> list[str]:
    """쉼표로 나눈 이름들. 한 글자는 다른 거래에 너무 쉽게 걸려 받지 않는다."""
    names = [name.strip()[:MAX_OWN_NAME_LENGTH] for name in text.split(",")]
    kept = [name for name in names if len(own_name_key(name)) >= 2]
    return list(dict.fromkeys(kept))[:MAX_OWN_NAMES]


def own_names_of(session: Session) -> list[str]:
    row = session.get(SystemState, OWN_NAMES_KEY)
    return list(row.value.get("names", [])) if row else []


def _load_rules(session: Session) -> list[RuleView]:
    return [
        RuleView(
            id=rule.id,
            priority=rule.priority,
            description_equals=rule.match_description_equals,
            description_contains=rule.match_description_contains,
            source_type=rule.match_source_type,
            source_major=rule.match_source_major,
            source_minor=rule.match_source_minor,
            instrument_id=rule.match_instrument_id,
            amount_min=rule.match_amount_min,
            amount_max=rule.match_amount_max,
            direction=rule.match_direction,
            set_nature=rule.set_nature,
            set_category_id=rule.set_category_id,
        )
        for rule in session.scalars(select(Rule).where(Rule.enabled.is_(True)))
    ]


def save_target_rule(
    session: Session,
    description: str,
    direction: str,
    nature: str,
    category_id: int | None,
    amount_min: int | None = None,
    amount_max: int | None = None,
) -> Rule:
    """같은 상대의 거래를 정해진 분류로 보낸다. 지난 거래에 바로 적용되고 앞으로 들어올 거래에도 적용된다."""
    key = target_key(description)
    existing = session.scalars(
        select(Rule).where(Rule.match_direction == direction, Rule.match_description_equals.is_not(None))
    )
    rule = next((item for item in existing if target_key(item.match_description_equals) == key), None)
    if rule is None:
        rule = Rule(name=description, match_description_equals=description, match_direction=direction, created_from="user")
        session.add(rule)
    rule.enabled = True
    rule.match_amount_min, rule.match_amount_max = amount_min, amount_max
    rule.set_nature = nature
    rule.set_category_id = category_id
    reclassify(session)
    return rule


def _ensure_virtual_cards(session: Session, txns: list[Txn], instruments: dict[int, Instrument]) -> None:
    """대금 행에는 있지만 사용 내역이 없는 카드를 결제수단으로 등록한다. 화면에 드러나도록 하기 위해서다."""
    known = {company_of(i.source_name) for i in instruments.values() if i.kind in CARD_KINDS}
    for txn in txns:
        if txn.source_type != "이체" or txn.source_major != "카드대금":
            continue
        company = company_of(txn.description)
        if company is None or company in known:
            continue
        name = f"{company}{VIRTUAL_CARD_SUFFIX}"
        card = Instrument(
            source_name=name,
            display_name=name,
            kind="credit_card",
            kind_source="auto",
            is_virtual=True,
        )
        session.add(card)
        session.flush()
        instruments[card.id] = card
        known.add(company)


def _split_state(txn: Txn) -> list[tuple]:
    return [
        (split.amount, split.nature, split.nature_reason, split.category_id, split.channel_instrument_id)
        for split in sorted(txn.splits, key=lambda split: split.seq)
    ]


def _split_loan_payment(txn: Txn, wanted: loan_split.LoanSplit, category_id: int | None) -> int:
    """이자는 지출, 원금은 대출로 나눈다. 이자나 원금이 0원이면 한 건이다."""
    before = _split_state(txn)
    amount = int(txn.source_amount)  # 나간 돈이라 음수다
    interest = -min(wanted.interest, -amount)
    suffix = "" if wanted.exact else "_estimate"
    parts = [
        (interest, "expense", f"loan_interest{suffix}", category_id),
        (amount - interest, "debt_move", f"loan_principal{suffix}", None),
    ]
    parts = [part for part in parts if part[0]]
    current = sorted(txn.splits, key=lambda split: split.seq)
    for index, (value, nature, reason, category) in enumerate(parts):
        if index < len(current):
            split = current[index]
        else:
            split = TxnSplit(seq=index + 1, amount=value)
            txn.splits.append(split)
        split.amount = value
        split.nature, split.nature_source, split.nature_reason = nature, "rule", reason
        split.category_id, split.category_source = category, "rule"
        split.channel_instrument_id = None
    for split in current[len(parts) :]:
        txn.splits.remove(split)
    return int(before != _split_state(txn))


def _join_splits(txn: Txn) -> int:
    """나눈 대출 납입을 한 건으로 되돌린다. 금액과 분류는 뒤에서 다시 정한다."""
    first, *rest = sorted(txn.splits, key=lambda split: split.seq)
    for split in rest:
        txn.splits.remove(split)
    first.nature_source = first.category_source = "auto"
    return int(bool(rest))


def reclassify(session: Session) -> int:
    """변경한 split 수를 돌려준다."""
    session.flush()
    txns = list(
        session.scalars(
            select(Txn).where(Txn.status == "active").options(selectinload(Txn.splits), joinedload(Txn.instrument))
        )
    )
    instruments = {i.id: i for i in session.scalars(select(Instrument))}
    _ensure_virtual_cards(session, txns, instruments)

    first_use: dict[int, object] = {}
    rows: list[RowView] = []
    for txn in txns:
        amount = int(txn.source_amount) if txn.currency == "KRW" else 0
        rows.append(
            RowView(
                id=txn.id,
                when=txn.occurred_at,
                type=txn.source_type,
                major=txn.source_major,
                minor=txn.source_minor,
                description=txn.description,
                amount=amount,
                currency=txn.currency,
                instrument_id=txn.instrument_id,
                instrument_kind=txn.instrument.kind,
            )
        )
        if txn.source_type == "지출" and txn.instrument.kind in CARD_KINDS:
            current = first_use.get(txn.instrument_id)
            if current is None or txn.occurred_date < current:
                first_use[txn.instrument_id] = txn.occurred_date

    cards_by_company: dict[str, CardView] = {}
    for instrument in instruments.values():
        if instrument.kind not in CARD_KINDS:
            continue
        company = company_of(instrument.source_name)
        view = CardView(
            instrument_id=instrument.id,
            company=company,
            first_use=first_use.get(instrument.id),
            linked_from=instrument.linked_from,
            bill_policy=instrument.bill_policy,
            is_virtual=instrument.is_virtual,
        )
        # 같은 회사 카드가 여럿이면 실제 사용 내역이 있는 카드를 기준으로 삼는다.
        existing = cards_by_company.get(company) if company else None
        if company and (existing is None or (existing.is_virtual and not view.is_virtual)):
            cards_by_company[company] = view

    pairs = detect_pairs(rows)
    lookup = load_lookup(session)
    # 머니 계좌가 연결된 페이만 통장에서 보낸 돈을 충전으로 본다. 머니로 쓴 돈이 따로 잡히기 때문이다.
    balances = [i.source_name for i in instruments.values() if i.kind == "pay_balance"]
    pays = [word for word in PAY_WORDS if any(word in name for name in balances)]
    decisions = classify_rows(rows, cards_by_company.values(), _load_rules(session), lookup, pairs, own_names_of(session), pays)
    row_by_id = {row.id: row for row in rows}

    session.execute(delete(TxnLink).where(TxnLink.source == "auto"))
    links = defaultdict(list)
    links["charge"] = pairs.charge
    links["internal_transfer"] = pairs.internal
    links["refund"] = pairs.refund
    for kind, kind_pairs in links.items():
        session.add_all(TxnLink(kind=kind, from_txn_id=a, to_txn_id=b, source="auto") for a, b in kind_pairs)

    planned = loan_split.plan(session, txns, decisions)
    category_ids = {key: category_id for key, category_id in session.execute(select(Category.key, Category.id))} if planned else {}

    changed = 0
    for txn in txns:
        decision = decisions[txn.id]
        wanted = planned.get(txn.id)
        if wanted is not None:
            # 규칙이 이 상대의 지출 카테고리를 정해 두었으면 이자도 그 카테고리다.
            spent = decision.nature == "expense" and decision.category_id not in (None, lookup.uncategorized_expense)
            category_id = decision.category_id if spent else category_ids.get(wanted.category_key)
            changed += _split_loan_payment(txn, wanted, category_id)
            continue
        if not loan_split.touched(txn) and any(split.nature_reason in LOAN_REASONS for split in txn.splits):
            changed += _join_splits(txn)  # 연결을 풀었거나 금리를 알 수 없게 되었다
        if len(txn.splits) != 1:  # 사용자가 나눈 거래는 자동으로 다시 판단하지 않는다
            continue
        split = txn.splits[0]
        before = (
            split.nature,
            split.nature_reason,
            split.category_id,
            split.channel_instrument_id,
            split.amount,
        )
        split.amount = int(txn.source_amount) if txn.currency == "KRW" else 0
        if split.nature_source != "user":
            split.nature = decision.nature
            split.nature_source = decision.nature_source
            split.nature_reason = decision.nature_reason
            split.channel_instrument_id = decision.channel_instrument_id
        if split.category_source != "user":
            if split.nature == decision.nature:
                split.category_id = decision.category_id
                split.category_source = decision.category_source
            else:  # 사용자가 성격을 바꾼 거래는 바꾼 성격에 맞는 비목을 찾는다
                split.category_id, split.category_source = category_for(
                    row_by_id[txn.id], split.nature, split.nature_reason or "", lookup
                )
        after = (
            split.nature,
            split.nature_reason,
            split.category_id,
            split.channel_instrument_id,
            split.amount,
        )
        changed += before != after
    session.flush()
    return changed
