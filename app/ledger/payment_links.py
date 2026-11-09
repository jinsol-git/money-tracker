"""정기 거래를 대출, 부동산, 연금에 잇는다. 어디에 오가는 돈인지는 사용자가 정하고, 대출 금리는 납입액과 잔액으로 거꾸로 구한다.

    이자 = 기간의 납입 합계 - 그동안 줄어든 잔액
    연 금리 = 납입 한 번의 이자 × 한 해의 납입 횟수 ÷ 평균 잔액

날짜가 아니라 납입 횟수로 나눈다. 잔액은 납입할 때만 줄어들므로 내보낸 날이 납입일과 어긋나도 기간 안의 납입과
잔액 감소가 서로 맞고, 값이 흔들리지 않는다.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ledger.models import AssetItem, AssetSnapshot
from app.ledger.recurring import LOAN_WORDS, PERIODS, RENT_MARGIN
from app.ops_models import SystemState

LINKS_KEY = "payment_links"
WINDOW_DAYS = 370  # 변동금리라도 지금에 가까운 값이 되도록 최근 한 해만 본다
MAX_RATE = 30.0  # 이보다 높으면 보험료처럼 다른 돈이 섞인 것이다
RATE_TOLERANCE = 0.3  # 한 구간의 금리가 전체 금리와 이만큼 넘게 다르면 잔액이 늦게 반영된 것으로 보고 어림한다
DAYS_PER_YEAR = 365.25
MONTHLY = 12
INTEREST_WORD = "이자"  # 혼자서는 이자카야에도 있어 금융사 이름이 함께 있을 때만 믿는다
HOUSING_LOAN_WORDS = ("전세", "주택", "담보", "아파트", "주담대", "보금자리")
BANK_SUFFIX = "은행"
PENSION_WORDS = ("irp", "연금저축", "퇴직연금", "개인연금")  # 국민연금 보험료처럼 연금 자산과 상관없는 연금도 있어 좁게 둔다
LATIN_PREFIX = re.compile(r"^[a-z]+")  # KB국민은행의 거래 내용에는 흔히 국민은행으로만 적힌다


@dataclass(frozen=True)
class Link:
    key: str  # 반복 거래 묶음의 대상(recurring.target_key)
    name: str
    loan: str | None
    asset: str | None
    pension: str | None = None  # 연금 자산. 넣는 돈과 받는 돈을 모두 이을 수 있다
    direction: str = "out"  # out은 보낸 돈, in은 받은 돈이다. 받은 돈은 연금에만 잇는다

    @property
    def slot(self) -> tuple[str, str]:
        """반복 거래 묶음을 가리킨다. 같은 상대라도 보낸 돈과 받은 돈은 다른 묶음이다."""
        return self.key, self.direction


def links_of(session: Session) -> list[Link]:
    row = session.get(SystemState, LINKS_KEY)
    saved = row.value.get("links", []) if row else []
    return [
        Link(item["key"], item["name"], item.get("loan"), item.get("asset"), item.get("pension"), item.get("direction", "out"))
        for item in saved
    ]


def link_kind(link: Link) -> tuple[str, str | None]:
    """연결한 묶음에 먼저 골라 둘 (종류, 카테고리 키). 연금에 오가는 돈은 내 자산 사이의 이동이라 저축·투자다."""
    if link.pension:
        return "asset_move", None
    return "expense", link_category(bool(link.asset))


def links_value(links: Iterable[Link]) -> dict[str, list[dict]]:
    return {"links": [asdict(link) for link in links]}


@dataclass(frozen=True)
class ImpliedRate:
    rate: float | None  # 연 %. 구하지 못했으면 None이고 까닭은 reason에 있다
    reason: str | None = None  # records, payments, range
    per_payment: int | None = None  # 납입 한 번의 이자
    count: int = 0
    first: date | None = None
    last: date | None = None


def _payments_per_year(days: Sequence[date]) -> int:
    gaps = [(later - earlier).days for earlier, later in zip(days, days[1:])]
    if not gaps:
        return MONTHLY
    typical = median(gaps)
    period = min(PERIODS, key=lambda item: abs(item.days - typical))
    return round(DAYS_PER_YEAR / period.days)


def _falling_window(balances: Sequence[tuple[date, int]]) -> list[tuple[date, int]]:
    """마지막 기록부터 거슬러 올라간 구간. 잔액이 늘었으면 새로 빌린 돈이라 납입으로 설명할 수 없어 거기서 멈춘다."""
    last_day = balances[-1][0]
    window = [balances[-1]]
    for day, amount in reversed(balances[:-1]):
        if amount < window[-1][1] or (last_day - day).days > WINDOW_DAYS:
            break
        window.append((day, amount))
    window.reverse()
    return window


def implied_rate(balances: Sequence[tuple[date, int]], payments: Mapping[date, int]) -> ImpliedRate:
    """balances는 날짜순의 대출 잔액, payments는 날짜별 납입액이다."""
    if len(balances) < 2:
        return ImpliedRate(None, "records")
    if not payments:
        return ImpliedRate(None, "payments")
    window = _falling_window(balances)
    # 계좌를 연동하기 전의 잔액 감소는 납입 기록이 없어 맞출 수 없다.
    paid_days = sorted(payments)
    while len(window) > 1 and window[1][0] < paid_days[0]:
        window.pop(0)
    if len(window) < 2:
        return ImpliedRate(None, "records")
    (start_day, start), (end_day, end) = window[0], window[-1]
    inside = [day for day in paid_days if start_day < day <= end_day]
    if not inside:
        return ImpliedRate(None, "payments")
    interest = sum(payments[day] for day in inside) - (start - end)
    average = (start + end) / 2
    if interest <= 0 or average <= 0:
        return ImpliedRate(None, "range")
    per_payment = interest / len(inside)
    rate = per_payment * _payments_per_year(paid_days) / average * 100
    if rate > MAX_RATE:
        return ImpliedRate(None, "range")
    return ImpliedRate(round(rate, 2), None, round(per_payment), len(inside), inside[0], inside[-1])


@dataclass(frozen=True)
class LoanOption:
    key: str
    name: str
    org: str = ""


@dataclass(frozen=True)
class AssetOption:
    key: str
    name: str
    rent: int | None = None


@dataclass(frozen=True)
class Guess:
    loan: str | None = None
    asset: str | None = None
    pension: str | None = None

    @property
    def found(self) -> bool:
        return bool(self.loan or self.asset or self.pension)


def _squeezed(text: str) -> str:
    return "".join(text.split()).casefold()


def guess_loan(description: str, loans: Sequence[LoanOption]) -> str | None:
    """거래 내용으로 어느 대출의 납입인지 짐작한다."""
    text = _squeezed(description)
    for loan in loans:
        if _squeezed(loan.name) in text:
            return loan.key
    has_word = any(word in text for word in LOAN_WORDS)
    if has_word or INTEREST_WORD in text:
        for loan in loans:
            org = LATIN_PREFIX.sub("", _squeezed(loan.org)).removesuffix(BANK_SUFFIX)
            if org and org in text:
                return loan.key
    return loans[0].key if has_word and len(loans) == 1 else None


def guess_pension(description: str, pensions: Sequence[AssetOption]) -> str | None:
    """연금 상품 이름이 그대로 있거나, 연금 자산이 하나뿐인데 연금저축이나 IRP라는 말이 있으면 그 연금이다."""
    text = _squeezed(description)
    for option in pensions:
        if _squeezed(option.name) in text:
            return option.key
    if len(pensions) == 1 and any(word in text for word in PENSION_WORDS):
        return pensions[0].key
    return None


def guess_link(
    description: str,
    typical: int,
    loans: Sequence[LoanOption],
    assets: Sequence[AssetOption],
    pensions: Sequence[AssetOption] = (),
    direction: str = "out",
) -> Guess:
    """어디에 오가는 돈인지 짐작한다. 짐작은 먼저 골라 두기만 하고, 잇는 것은 사용자가 누른다."""
    pension = guess_pension(description, pensions)
    if pension or direction == "in":
        return Guess(pension=pension)
    loan = guess_loan(description, loans)
    asset = next((item.key for item in assets if item.rent and abs(typical - item.rent) <= item.rent * RENT_MARGIN), None)
    if asset is None and loan is not None and len(assets) == 1:
        name = next(item.name for item in loans if item.key == loan)
        if any(word in _squeezed(name + description) for word in HOUSING_LOAN_WORDS):
            asset = assets[0].key
    return Guess(loan, asset)


def pension_growth(balances: Sequence[tuple[date, int]], flows: Mapping[date, int]) -> tuple[date, date, int] | None:
    """첫 잔액 기록부터 마지막 기록까지 불어난 금액. 잔액 변화에서 그사이 넣은 돈은 빼고 받은 돈은 더한다.

    flows는 날짜마다 연금으로 들어간 돈이다. 받은 돈은 음수다.
    """
    records = sorted(balances)
    if len(records) < 2:
        return None
    (start_day, start), (end_day, end) = records[0], records[-1]
    moved = sum(amount for day, amount in flows.items() if start_day < day <= end_day)
    return start_day, end_day, end - start - moved


def link_category(has_asset: bool) -> str:
    """연결한 납입의 이자를 넣을 카테고리. 부동산에 이었으면 주거비, 그 밖에는 뱅크샐러드의 지출/금융과 같은 곳이다."""
    return "housing" if has_asset else "finance"


def detail_row_for(name: str, amount: int, rows: Sequence[Mapping]) -> Mapping | None:
    """현황 표에서 이 상품의 행. 금융 탭처럼 이름이 같은 행을, 그다음 이름이 겹치는 행을, 그다음 잔액이 같은 행을 찾는다."""
    squeezed = _squeezed(name)
    for row in rows:
        if _squeezed(row["name"]) == squeezed:
            return row
    for row in rows:
        other = _squeezed(row["name"])
        if other and squeezed and (other in squeezed or squeezed in other):
            return row
    return next((row for row in rows if row.get("balance") == amount), None)


def loan_balances(session: Session) -> dict[str, list[tuple[date, int]]]:
    """내보낸 날마다의 대출 잔액. 키는 금융 탭의 상품 키와 같다."""
    rows = session.execute(
        select(AssetSnapshot.as_of, AssetItem.group_label, AssetItem.product_name, func.sum(AssetItem.amount))
        .join(AssetItem, AssetItem.snapshot_id == AssetSnapshot.id)
        .where(AssetItem.side == "liability")
        .group_by(AssetSnapshot.as_of, AssetItem.group_label, AssetItem.product_name)
        .order_by(AssetSnapshot.as_of)
    )
    balances: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for as_of, label, product, amount in rows:
        balances[f"liability:{label}:{product}"].append((as_of, int(amount)))
    return dict(balances)


@dataclass(frozen=True)
class Payment:
    key: int  # 거래 id
    day: date
    amount: int  # 낸 돈. 양수다


@dataclass(frozen=True)
class Part:
    interest: int  # 이자. 나머지는 원금이다
    exact: bool  # 두 잔액 기록 사이에서 구했다. 아니면 금리로 어림했다


def _share(total: int, payments: Sequence[Payment]) -> dict[int, int]:
    """이자를 납입마다 똑같이 나눈다. 납입액보다 많이 주지 않고, 남는 몫은 큰 납입이 받는다."""
    shares = {}
    left = total
    ordered = sorted(payments, key=lambda item: item.amount)
    for index, payment in enumerate(ordered):
        remaining = len(ordered) - index
        give = min(payment.amount, left if remaining == 1 else left // remaining)
        shares[payment.key] = give
        left -= give
    return shares


def loan_parts(balances: Sequence[tuple[date, int]], payments: Sequence[Payment], rate: float | None) -> dict[int, Part]:
    """납입마다 든 이자.

    두 잔액 기록 사이의 이자(그 사이 납입 합계 - 잔액 감소)를 그 사이 납입에 똑같이 나눈다. 매달 내보내면 그달 이자가
    그대로 나온다. 기록 밖의 납입과, 새로 빌렸거나 잔액이 늦게 반영되어 맞지 않는 구간의 납입은 금리로 어림한다.
    잔액은 기록에서 납입을 따라 되짚는다. 첫 기록보다 앞이면 갚은 원금만큼 그때 잔액이 더 컸다.
    rate는 연 %이고, 없으면 어림하지 않는다. 기록일의 잔액에는 그날 납입이 반영되어 있다고 본다.
    """
    if not balances or not payments:
        return {}
    records = sorted(balances)
    per_year = _payments_per_year(sorted({payment.day for payment in payments}))
    parts: dict[int, Part] = {}
    for (start_day, start), (end_day, end) in zip(records, records[1:]):
        inside = [payment for payment in payments if start_day < payment.day <= end_day]
        if not inside or end > start:  # 새로 빌려 잔액이 늘었으면 납입으로 설명할 수 없다
            continue
        interest = sum(payment.amount for payment in inside) - (start - end)
        average = (start + end) / 2
        if interest < 0 or average <= 0:
            continue
        window_rate = interest / len(inside) * per_year / average * 100
        if window_rate > MAX_RATE or (rate is not None and abs(window_rate - rate) > rate * RATE_TOLERANCE):
            continue
        for key, value in _share(interest, inside).items():
            parts[key] = Part(value, True)
    if rate is None:
        return parts
    per_payment = rate / 100 / per_year
    days = [day for day, _ in records]
    pending = sorted((payment for payment in payments if payment.key not in parts), key=lambda item: item.day)
    balance = float(records[0][1])
    for payment in reversed([item for item in pending if item.day <= days[0]]):
        balance = (balance + payment.amount) / (1 + per_payment)  # 이 납입 전의 잔액
        parts[payment.key] = Part(min(round(balance * per_payment), payment.amount), False)
    record = None
    for payment in (item for item in pending if item.day > days[0]):
        index = bisect_left(days, payment.day) - 1  # 납입 전 마지막 기록
        if index != record:
            record, balance = index, float(records[index][1])
        interest = min(round(max(balance, 0) * per_payment), payment.amount)
        parts[payment.key] = Part(interest, False)
        balance -= payment.amount - interest
    return parts
