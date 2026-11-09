"""성격과 비목 판단 규칙. DB를 쓰지 않는 순수 함수로 둔다.

규칙의 근거는 docs/banksalad-format.md에 정리한 실제 파일의 행 표현이다.

- 성격은 합계를 바꾸므로 정해진 규칙과 사용자 확정으로만 정한다. LLM은 제안만 한다.
- 비목 순서: 사용자 수정, 사용자 규칙(가맹점 기억 포함), 뱅크샐러드 분류 매핑, 미분류.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from app.ledger.recurring import target_key

CHARGE_WINDOW_SECONDS = 5 * 60
TRANSFER_WINDOW_SECONDS = 10 * 60
OWN_TRANSFER_INCOME_MAJORS = frozenset({"기타수입", "용돈"})  # 급여와 이자는 내 이름으로 들어와도 수입이다

# 앞의 이름이 표시용이고 뒤의 단어가 내용·상품명에서 찾는 말이다.
CARD_COMPANIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("비씨", ("비씨", "BC", "bc")),
    ("삼성", ("삼성",)),
    ("현대", ("현대",)),
    ("신한", ("신한",)),
    ("국민", ("국민", "KB", "kb")),
    ("롯데", ("롯데",)),
    ("하나", ("하나",)),
    ("우리", ("우리",)),
    ("농협", ("농협", "NH", "nh")),
    ("씨티", ("씨티",)),
    ("카카오", ("카카오",)),
    ("토스", ("토스",)),
)

ACCOUNT_WORDS = ("통장", "계좌", "입출금", "저축", "예금", "적금", "예탁금", "저금통", "세이프박스")
RETURN_WINDOW_SECONDS = 7 * 24 * 60 * 60  # 보냈다가 돌려받은 돈으로 볼 간격
# 이보다 적게 들어온 출처 모를 돈은 계좌 인증이나 이자로 본다. 인증은 1원이 많지만 몇십 원, 몇백 원도 있다.
SMALL_DEPOSIT = 1_000

Source = tuple[str, str, str | None]  # 카테고리를 찾을 뱅크샐러드 분류(타입, 대분류, 소분류)


@dataclass(frozen=True)
class NamedParty:
    """이름만 봐도 무엇인지 아는 상대. 방향마다 (성격, 카테고리를 찾을 뱅크샐러드 분류)다."""

    words: tuple[str, ...]
    out: tuple[str, Source | None]
    into: tuple[str, Source | None]


# 뱅크샐러드가 '이체'로만 보내 무엇인지 모르는 거래에 쓴다. 앞의 것이 먼저라 쿠팡이츠가 쿠팡보다 앞에 있다.
NAMED_PARTIES: tuple[NamedParty, ...] = (
    NamedParty(
        ("쿠팡이츠", "배달의민족", "배민", "요기요", "우아한형제들"),
        ("expense", ("지출", "식비", None)),
        ("refund", ("지출", "식비", None)),
    ),
    NamedParty(
        ("쿠팡", "11번가", "G마켓", "지마켓", "옥션", "위메프", "티몬", "SSG", "쓱닷컴", "무신사", "29CM", "지그재그",
         "에이블리", "오늘의집", "컬리", "알리익스프레스", "테무"),
        ("expense", ("지출", "온라인쇼핑", None)),
        ("refund", ("지출", "온라인쇼핑", None)),
    ),
    NamedParty(("업비트", "빗썸", "코인원", "코빗", "증권"), ("asset_move", None), ("asset_move", None)),
    NamedParty(
        ("학원", "교습", "피아노", "태권도", "과외", "레슨"),
        ("expense", ("지출", "교육/학습", None)),
        ("refund", ("지출", "교육/학습", None)),
    ),
    NamedParty(("로또", "복권"), ("expense", ("지출", "문화/여가", None)), ("income", ("수입", "기타수입", None))),
    NamedParty(
        ("메리츠", "화재", "해상", "손해보험", "손보", "생명보험", "삼성생명", "한화생명", "교보생명", "흥국생명", "동양생명",
         "신한라이프", "라이나"),
        ("expense", ("지출", "금융", "보험")),
        ("refund", ("지출", "의료/건강", None)),  # 보험금은 쓴 병원비를 돌려받는 돈으로 본다
    ),
)
PAY_WORDS = ("카카오페이", "네이버페이", "토스", "페이코")  # 머니 계좌가 연결된 페이만 이름으로 정한다
ONE_WON_SOURCE: Source = ("수입", "금융수입", None)


@dataclass(frozen=True)
class RowView:
    id: int
    when: datetime
    type: str
    major: str
    minor: str
    description: str
    amount: int
    currency: str
    instrument_id: int
    instrument_kind: str

    @property
    def date(self) -> date:
        return self.when.date()


@dataclass(frozen=True)
class CardView:
    instrument_id: int
    company: str | None
    first_use: date | None
    linked_from: date | None = None
    bill_policy: str = "auto"
    is_virtual: bool = False


@dataclass(frozen=True)
class RuleView:
    id: int
    priority: int = 100
    description_equals: str | None = None
    description_contains: str | None = None
    source_type: str | None = None
    source_major: str | None = None
    source_minor: str | None = None
    instrument_id: int | None = None
    amount_min: int | None = None
    amount_max: int | None = None
    direction: str | None = None
    set_nature: str | None = None
    set_category_id: int | None = None

    def matches(self, row: RowView) -> bool:
        magnitude = abs(row.amount)
        return (
            (self.direction is None or (row.amount < 0) == (self.direction == "out"))
            # 같은 상대면 '10월 월세'로 만든 규칙이 '11월 월세'에도 적용된다.
            and (self.description_equals is None or target_key(row.description) == target_key(self.description_equals))
            and (self.description_contains is None or self.description_contains in row.description)
            and (self.source_type is None or row.type == self.source_type)
            and (self.source_major is None or row.major == self.source_major)
            and (self.source_minor is None or row.minor == self.source_minor)
            and (self.instrument_id is None or row.instrument_id == self.instrument_id)
            and (self.amount_min is None or magnitude >= self.amount_min)
            and (self.amount_max is None or magnitude <= self.amount_max)
        )


@dataclass(frozen=True)
class CategoryLookup:
    """category_map과 특수 비목. map의 키는 (타입, 대분류, 소분류 또는 None)이다."""

    map: dict[tuple[str, str, str | None], int]
    uncategorized_expense: int
    uncategorized_income: int
    card_bill_unknown: int
    kinds: dict[int, str] = field(default_factory=dict)  # 카테고리마다 지출(expense)인지 수입(income)인지


@dataclass(frozen=True)
class Decision:
    nature: str
    nature_source: str
    nature_reason: str
    category_id: int | None
    category_source: str
    channel_instrument_id: int | None


@dataclass(frozen=True)
class Pairs:
    charge: list[tuple[int, int]]  # 통장 출금과 페이 머니 입금
    internal: list[tuple[int, int]]  # 내 계좌 사이의 출금과 입금
    refund: list[tuple[int, int]]  # 원거래와 취소


def company_of(text: str) -> str | None:
    for company, aliases in CARD_COMPANIES:
        if any(alias in text for alias in aliases):
            return company
    return None


def infer_instrument_kind(name: str, asset_group: str | None, transaction_types: Iterable[str]) -> str:
    """재무현황 항목을 우선 믿고, 없으면 이름과 거래 유형으로 추정한다. 사용자가 고칠 수 있다."""
    if asset_group == "자유입출금 자산":
        return "account"
    if asset_group == "저축성 자산":
        return "savings"
    if asset_group == "현금 자산":
        return "cash"
    if "간편결제" in name:
        return "pay_usage"
    if "머니" in name or "포인트" in name:
        return "pay_balance"
    if "체크" in name:
        return "check_card"
    if "카드" in name:
        return "credit_card"
    if any(word in name for word in ACCOUNT_WORDS):
        return "account"
    types = set(transaction_types)
    if types and types <= {"지출"}:  # 지출 행만 있고 자산에도 없다면 카드일 가능성이 높다
        return "credit_card"
    return "unknown"


def own_name_key(name: str) -> str:
    """이름을 비교하는 값. 은행마다 띄어쓰기가 달라 공백은 뺀다."""
    return "".join(name.split()).casefold()


def _is_own_name(description: str, own_names: Sequence[str]) -> bool:
    text = own_name_key(description)
    return any(own_name_key(name) in text for name in own_names)


def _named(row: RowView, pays: Sequence[str]) -> tuple[str, str] | None:
    """이름으로 알아본 (성격, 사유). 통장과 연결된 페이 사이의 돈은 충전이나 내 돈 이동이다."""
    text = row.description.casefold()
    if row.instrument_kind in {"account", "savings"}:
        for word in pays:
            if word.casefold() in text:
                return ("funding", "named_charge") if row.amount < 0 else ("internal", "named_pay")
    for party in NAMED_PARTIES:
        for word in party.words:
            if word.casefold() in text:
                nature, _ = party.out if row.amount < 0 else party.into
                return nature, f"named:{word}"
    return None


def named_source(reason: str, amount: int) -> Source | None:
    """이름으로 정한 거래의 카테고리를 찾을 뱅크샐러드 분류."""
    word = reason.removeprefix("named:")
    for party in NAMED_PARTIES:
        if word in party.words:
            return (party.out if amount < 0 else party.into)[1]
    return None


def _returned_pairs(rows: Sequence[RowView]) -> dict[int, str]:
    """며칠 안에 같은 금액이 반대로 오간 거래. 같은 상대면 돌려받은 돈, '취소'면 취소된 거래다."""
    found: dict[int, str] = {}
    ordered = sorted(rows, key=lambda row: row.when)
    by_party: dict[tuple[str, int], list[RowView]] = defaultdict(list)
    for row in ordered:
        by_party[(target_key(row.description), abs(row.amount))].append(row)
    for members in by_party.values():
        waiting: list[RowView] = []
        for row in members:
            partner = next(
                (
                    other
                    for other in waiting
                    if (other.amount > 0) != (row.amount > 0)
                    and (row.when - other.when).total_seconds() <= RETURN_WINDOW_SECONDS
                ),
                None,
            )
            if partner is None:
                waiting.append(row)
                continue
            waiting.remove(partner)
            found[row.id] = found[partner.id] = "returned_pair"
    for cancel in ordered:
        if "취소" not in cancel.description or cancel.id in found:
            continue
        candidates = [
            row
            for row in ordered
            if row.id not in found
            and row.id != cancel.id
            and row.instrument_id == cancel.instrument_id
            and row.amount == -cancel.amount
            and abs((row.when - cancel.when).total_seconds()) <= RETURN_WINDOW_SECONDS
        ]
        if candidates:
            original = min(candidates, key=lambda row: abs((row.when - cancel.when).total_seconds()))
            found[cancel.id] = found[original.id] = "cancel_pair"
    return found


def _pair_up(
    outs: Sequence[RowView], ins: Sequence[RowView], window_seconds: int
) -> list[tuple[RowView, RowView]]:
    """서로가 유일한 후보일 때만 짝짓는다. 같은 초를 먼저 보고, 다음에 시간 범위를 넓힌다."""
    pairs: list[tuple[RowView, RowView]] = []
    used: set[int] = set()
    ins_by_amount: dict[int, list[RowView]] = defaultdict(list)
    for row in ins:
        ins_by_amount[abs(row.amount)].append(row)

    for seconds in (0, window_seconds):
        out_candidates: dict[int, list[RowView]] = {}
        in_candidates: dict[int, list[RowView]] = defaultdict(list)
        for out in outs:
            if out.id in used:
                continue
            found = [
                row
                for row in ins_by_amount[abs(out.amount)]
                if row.id not in used
                and row.instrument_id != out.instrument_id
                and abs((row.when - out.when).total_seconds()) <= seconds
            ]
            out_candidates[out.id] = found
            for row in found:
                in_candidates[row.id].append(out)
        for out in outs:
            found = out_candidates.get(out.id, [])
            if len(found) == 1 and len(in_candidates[found[0].id]) == 1:
                pairs.append((out, found[0]))
                used.update({out.id, found[0].id})
    return pairs


def detect_pairs(rows: Sequence[RowView]) -> Pairs:
    money_rows = [row for row in rows if row.currency == "KRW" and row.amount != 0]

    # 충전: 통장 쪽이 '지출'로 잡히고 받는 쪽은 '이체/내계좌이체'로 잡힌다.
    charge_outs = [
        row
        for row in money_rows
        if row.type == "지출" and row.amount < 0 and row.instrument_kind in {"account", "savings", "unknown"}
    ]
    charge_ins = [row for row in money_rows if row.type == "이체" and row.amount > 0 and row.major == "내계좌이체"]
    charge: list[tuple[int, int]] = []
    internal: list[tuple[int, int]] = []
    used: set[int] = set()
    for out, incoming in _pair_up(charge_outs, charge_ins, CHARGE_WINDOW_SECONDS):
        target = charge if incoming.instrument_kind == "pay_balance" else internal
        target.append((out.id, incoming.id))
        used.update({out.id, incoming.id})

    # 내부 이체: 타입이 이체인 행끼리. 카드대금은 짝이 없는 행이다.
    transfer_outs = [
        row
        for row in money_rows
        if row.type == "이체" and row.amount < 0 and row.major != "카드대금" and row.id not in used
    ]
    transfer_ins = [
        row
        for row in money_rows
        if row.type == "이체" and row.amount > 0 and row.major != "카드대금" and row.id not in used
    ]
    for out, incoming in _pair_up(transfer_outs, transfer_ins, TRANSFER_WINDOW_SECONDS):
        internal.append((out.id, incoming.id))

    # 환불: 같은 내용·금액의 가장 가까운 이전 지출. 표시용이며 합계에는 영향이 없다.
    refund: list[tuple[int, int]] = []
    origins: dict[tuple[str, int], list[RowView]] = defaultdict(list)
    for row in sorted(money_rows, key=lambda r: r.when):
        if row.type == "지출" and row.amount < 0:
            origins[(row.description, -row.amount)].append(row)
    for row in sorted(money_rows, key=lambda r: r.when):
        if row.type == "지출" and row.amount > 0:
            candidates = [c for c in origins[(row.description, row.amount)] if c.when <= row.when]
            if candidates:
                origin = candidates[-1]
                origins[(row.description, row.amount)].remove(origin)
                refund.append((origin.id, row.id))
    return Pairs(charge=charge, internal=internal, refund=refund)


def _default_nature(
    row: RowView,
    charge_outs: set[int],
    internal_ids: set[int],
    cards: dict[str, CardView],
    own_names: Sequence[str] = (),
    pays: Sequence[str] = (),
) -> tuple[str, str, int | None]:
    """(성격, 사유, 채널을 대신할 결제수단) 반환."""
    if row.amount == 0 and row.currency == "KRW":
        return "ignored", "zero_amount", None
    if row.currency != "KRW":
        return "undetermined", "foreign_currency", None

    if row.type == "수입":
        if row.major in OWN_TRANSFER_INCOME_MAJORS and _is_own_name(row.description, own_names):
            return "internal", "own_name", None
        return "income", "source_income", None

    if row.type == "지출":
        if row.id in charge_outs:
            return "funding", "charge_pair", None
        if row.id in internal_ids:
            return "internal", "transfer_pair", None
        if row.major == "금융" and row.minor == "증권/투자":
            return "asset_move", "investment_out", None
        if row.amount > 0:
            return "refund", "positive_spend", None
        if row.major == "온라인쇼핑" and "충전" in row.minor:
            # 페이로 쓸 때 다시 지출로 잡히므로 짝을 못 찾았다고 지출로 세면 두 번 계산될 수 있다.
            return "undetermined", "charge_unpaired", None
        return "expense", "source_spend", None

    # 이체
    if row.major == "카드대금":
        return _bill_nature(row, cards)
    if row.major == "내계좌이체":
        return "internal", "own_transfer", None
    if row.id in internal_ids:
        return "internal", "transfer_pair", None
    if row.amount == 1:
        return "income", "one_won", None  # 계좌를 인증할 때 들어오는 1원
    if row.major in {"저축", "투자"}:
        return "asset_move", "saving_or_investment", None
    if row.major == "현금":
        return "internal", "cash_move", None
    if _is_own_name(row.description, own_names):
        return "internal", "own_name", None
    named = _named(row, pays)
    if named:
        return named[0], named[1], None
    if 0 < row.amount < SMALL_DEPOSIT:
        return "income", "small_deposit", None
    return "undetermined", "unknown_transfer", None


def _bill_nature(row: RowView, cards: dict[str, CardView]) -> tuple[str, str, int | None]:
    """카드 사용 내역이 장부에 있는 기간의 대금은 이중 집계라 빼고, 없는 기간의 대금은 지출로 잡는다."""
    company = company_of(row.description)
    if company is None:
        return "undetermined", "bill_unknown_card", None
    card = cards.get(company)
    if card is None:
        return "expense", "bill_unlinked", None
    if row.amount > 0:
        return "funding", "bill_reversal", None
    if card.bill_policy == "ignore":
        return "funding", "bill_policy_ignore", None
    if card.bill_policy == "count":
        return "expense", "bill_policy_count", card.instrument_id
    start = card.linked_from or card.first_use
    if card.is_virtual or start is None or row.date < start:
        return "expense", "bill_unlinked", card.instrument_id
    return "funding", "bill_linked", None


def category_for(
    row: RowView, nature: str, reason: str, lookup: CategoryLookup
) -> tuple[int | None, str]:
    source: Source = (row.type, row.major, row.minor)
    if reason in {"one_won", "small_deposit"}:
        source = ONE_WON_SOURCE
    elif reason.startswith("named:"):
        source = named_source(reason, row.amount) or source
    return mapped_category(source, nature, reason, lookup)


def mapped_category(
    source: tuple[str, str, str], nature: str, reason: str, lookup: CategoryLookup
) -> tuple[int | None, str]:
    """뱅크샐러드 분류(종류, 대분류, 소분류)로 정하는 카테고리. 사용자가 카테고리를 고르지 않으면 이 값이 들어간다.

    분류표의 카테고리가 종류와 맞지 않으면 쓰지 않는다. 수입으로 들어온 거래를 지출로 바꿨는데 수입 카테고리가 붙지 않게 한다.
    """
    if nature not in {"expense", "refund", "income"}:
        return None, "auto"
    if reason in {"bill_unlinked", "bill_policy_count"}:
        return lookup.card_bill_unknown, "auto"
    wanted = "income" if nature == "income" else "expense"
    source_type, major, minor = source
    for key in ((source_type, major, minor), (source_type, major, None)):
        category = lookup.map.get(key)
        if category is not None and lookup.kinds.get(category, wanted) == wanted:
            return category, "map"
    if nature == "income":
        return lookup.uncategorized_income, "auto"
    return lookup.uncategorized_expense, "auto"


def classify_rows(
    rows: Sequence[RowView],
    cards: Iterable[CardView],
    rules: Iterable[RuleView],
    lookup: CategoryLookup,
    pairs: Pairs | None = None,
    own_names: Sequence[str] = (),
    pays: Sequence[str] = (),
) -> dict[int, Decision]:
    pairs = pairs or detect_pairs(rows)
    charge_outs = {out for out, _ in pairs.charge}
    internal_ids = {row_id for pair in pairs.internal for row_id in pair} | {incoming for _, incoming in pairs.charge}
    by_company = {card.company: card for card in cards if card.company}
    ordered_rules = sorted(rules, key=lambda rule: (rule.priority, rule.id))

    decisions: dict[int, Decision] = {}
    for row in rows:
        nature, reason, channel = _default_nature(row, charge_outs, internal_ids, by_company, own_names, pays)
        nature_source = "auto"
        category_rule_id = None
        if nature != "ignored":
            for rule in ordered_rules:
                if rule.set_nature and rule.matches(row):
                    nature, reason, channel, nature_source = rule.set_nature, f"rule:{rule.id}", None, "rule"
                    break
            for rule in ordered_rules:
                if rule.set_category_id and rule.matches(row):
                    category_rule_id = rule.set_category_id
                    break
        if category_rule_id is not None and nature in {"expense", "refund", "income"}:
            category_id, category_source = category_rule_id, "rule"
        else:
            category_id, category_source = category_for(row, nature, reason, lookup)
        decisions[row.id] = Decision(nature, nature_source, reason, category_id, category_source, channel)
    # 아직 모르는 이체끼리 며칠 안에 같은 금액이 반대로 오갔으면 돌려받았거나 취소한 돈이라 합계에서 뺀다.
    waiting = [row for row in rows if decisions[row.id].nature_source == "auto" and decisions[row.id].nature_reason == "unknown_transfer"]
    for row_id, reason in _returned_pairs(waiting).items():
        decisions[row_id] = Decision("ignored", "auto", reason, None, "auto", None)
    return decisions
