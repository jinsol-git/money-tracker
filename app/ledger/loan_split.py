"""연결한 대출 납입을 이자와 원금으로 나눈다. 이자는 지출이고 원금은 대출(부채 이동)이다.

한 대출에 이은 납입 묶음이 하나일 때만 나눈다. 묶음이 여럿이면 이자와 원금을 따로 내는 경우가 많아 묶음마다
분류를 정하게 둔다. 사용자가 직접 고친 거래는 나누지 않는다.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ledger import reports
from app.ledger.classify import Decision
from app.ledger.models import Txn
from app.ledger.payment_links import (
    MAX_RATE,
    Payment,
    detail_row_for,
    implied_rate,
    link_category,
    links_of,
    loan_balances,
    loan_parts,
)
from app.ledger.recurring import EXPLAINED_NATURES, target_key

TABLE_RATE_MIN = 0.3  # 이보다 작은 표의 금리는 4.5%를 0.045로 적은 것인지 알 수 없어 쓰지 않는다


@dataclass(frozen=True)
class LoanSplit:
    interest: int  # 양수. 나머지는 원금이다
    exact: bool  # 두 잔액 기록 사이에서 구했다. 아니면 금리로 어림했다
    category_key: str  # 이자를 넣을 카테고리


def touched(txn: Txn) -> bool:
    return any(split.nature_source == "user" or split.category_source == "user" for split in txn.splits)


def _table_rates(session: Session) -> dict[str, float]:
    """마지막 내보내기의 대출 현황 표에 적힌 금리. 키는 금융 탭의 상품 키와 같다."""
    rows = reports.finance_details(session)["loan"]
    rates = {}
    for item in reports.asset_items_on_latest(session):
        if item.side != "liability":
            continue
        row = detail_row_for(item.product_name, item.amount, rows)
        rate = row["rate"] if row else None
        if rate is not None and TABLE_RATE_MIN <= rate <= MAX_RATE:
            rates[f"{item.side}:{item.group_label}:{item.product_name}"] = rate
    return rates


def plan(session: Session, txns: Sequence[Txn], decisions: Mapping[int, Decision]) -> dict[int, LoanSplit]:
    """거래마다 나눌 이자. 금리를 알 수 없는 납입은 빠지고, 그 납입은 원래 분류를 따른다.

    금리는 납입액과 잔액으로 구한 값이 먼저이고, 잔액 기록이 하나뿐이면 대출 현황 표의 금리를 쓴다.
    """
    links = [link for link in links_of(session) if link.loan and link.direction == "out"]
    per_loan = Counter(link.loan for link in links)
    chosen = {link.key: link for link in links if per_loan[link.loan] == 1}
    if not chosen:
        return {}
    members: dict[str, list[Txn]] = defaultdict(list)
    for txn in txns:
        if txn.currency != "KRW" or txn.source_amount >= 0 or touched(txn):
            continue
        key = target_key(txn.description)
        if key in chosen and decisions[txn.id].nature not in EXPLAINED_NATURES:
            members[key].append(txn)
    if not members:
        return {}

    balances = loan_balances(session)
    tables = _table_rates(session)
    result: dict[int, LoanSplit] = {}
    for key, group in members.items():
        link = chosen[key]
        records = balances.get(link.loan, [])
        payments = [Payment(txn.id, txn.occurred_date, -int(txn.source_amount)) for txn in group]
        paid: dict = defaultdict(int)
        for payment in payments:
            paid[payment.day] += payment.amount
        implied = implied_rate(records, paid).rate
        rate = implied if implied is not None else tables.get(link.loan)
        category = link_category(bool(link.asset))
        for txn_id, part in loan_parts(records, payments, rate).items():
            result[txn_id] = LoanSplit(part.interest, part.exact, category)
    return result
