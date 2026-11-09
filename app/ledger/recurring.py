"""같은 상대와 반복되는 거래를 찾아 정기성인지 단기성인지 나눈다. DB를 쓰지 않는 순수 함수로 둔다.

성격은 합계를 바꾸므로 여기서는 제안만 한다. 사용자가 정하면 규칙으로 저장되어 앞으로의 거래에도 적용된다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from statistics import median

from app.ledger.labels import LOAN_REASONS

MIN_REGULAR_DAYS = 3  # 두 번으로는 우연일 수 있어 후보로만 본다
REGULAR_SHARE = 0.7  # 한두 번 건너뛰어도 정기로 본다
SHORT_SPAN_DAYS = 45  # 이 안에 몰려 있으면 단기성이다
STABLE_SPREAD = 0.1
SIMILAR_SPREAD = 0.3
OUTLIER_RATIO = 5  # 평소보다 이만큼 크면 같은 목적이 아니다. 월세를 보내는 상대에게 보낸 보증금 같은 경우다
RENT_MARGIN = 0.2  # 등록한 월세와 이만큼 차이 나도 월세로 본다
UNCATEGORIZED = {"uncategorized_expense", "uncategorized_income"}
LOAN_WORDS = ("대출", "원리금", "상환", "주담대")
# 다른 곳에서 이미 설명되는 돈이다. 카드대금과 페이 충전은 쓸 때 지출로 잡힌다.
EXPLAINED_NATURES = {"internal", "ignored"}
EXPLAINED_REASONS = {"bill_linked", "bill_reversal", "bill_policy_ignore", "charge_pair"}
DATE_WORDS = re.compile(r"\d{2,4}\s*년|\d{1,2}\s*월(?:\s*분)?|\d{1,2}\s*일|\d+")
SEPARATORS = re.compile(r"[\W_]+")


@lru_cache(maxsize=8192)
def target_key(description: str) -> str:
    """같은 상대인지 가리는 값. '10월 월세'와 '월세 11월분'처럼 날짜나 기호만 다르면 같은 상대로 본다."""
    return SEPARATORS.sub("", DATE_WORDS.sub("", description)).casefold() or description


@dataclass(frozen=True)
class Period:
    name: str
    days: float
    tolerance: float


PERIODS = (
    Period("매주", 7, 1),
    Period("격주", 14, 2),
    Period("매달", 30.4, 4),
    Period("분기마다", 91, 10),
    Period("매년", 365, 20),
)


@dataclass(frozen=True)
class Line:
    day: date
    description: str
    amount: int
    nature: str
    reason: str | None
    category_key: str | None
    category_name: str | None
    instrument_name: str
    source: tuple[str, str, str] = ("", "", "")  # 뱅크샐러드 분류: 종류, 대분류, 소분류


@dataclass(frozen=True)
class Suggestion:
    nature: str | None
    category_key: str | None = None


@dataclass(frozen=True)
class Group:
    key: str
    description: str
    direction: str  # out 또는 in
    kind: str  # regular, ended, short, irregular
    tentative: bool  # 두 번만 보여 아직 확신할 수 없다
    period: str | None
    typical_day: int | None
    times: int  # 거래가 있었던 날의 수
    rows: int
    undetermined: int
    uncategorized: int
    total: int
    typical_amount: int
    low: int
    high: int
    spread: str  # same, similar, varied
    first: date
    last: date
    nature: str
    category_key: str | None
    category_name: str | None
    instrument_name: str
    needs_review: bool
    recent: tuple[tuple[date, int], ...]
    suggestion: Suggestion
    outliers: frozenset[date] = frozenset()  # 묶음에서 뺀, 평소보다 훨씬 큰 금액이 있었던 날
    loan_split: bool = False  # 연결한 대출의 이자와 원금으로 나눈 묶음
    source: tuple[str, str, str] = ("", "", "")  # 가장 많은 뱅크샐러드 분류. 자동으로 찾을 카테고리를 미리 보여 줄 때 쓴다


# 내용에 이런 말이 있으면 그 분류를 먼저 골라 둔다.
KEYWORDS: tuple[tuple[tuple[str, ...], Suggestion], ...] = (
    (("월세", "임대", "관리비", "전세"), Suggestion("expense", "housing")),
    (("학원", "교습", "과외", "수강", "레슨", "유치원", "어린이집"), Suggestion("expense", "education")),
    (("회비", "동호회", "계모임", "곗돈", "모임", "축의", "부의", "경조"), Suggestion("expense", "dues")),
    (LOAN_WORDS, Suggestion("expense", "finance")),
    (("지역화폐", "온누리", "제로페이", "상품권"), Suggestion("funding")),
    (("적금", "저축"), Suggestion("asset_move")),
    (("보험",), Suggestion("expense", "insurance")),
    (("공과금", "도시가스", "전기요금", "수도요금", "세금"), Suggestion("expense", "finance")),
    (("통신", "요금"), Suggestion("expense", "telecom")),
    (("구독", "넷플릭스", "NETFLIX", "유튜브"), Suggestion("expense", "subscription")),
)


def _match_period(gaps: Sequence[int]) -> tuple[Period, float]:
    best = PERIODS[0]
    best_share = -1.0
    for period in PERIODS:
        share = sum(abs(gap - period.days) <= period.tolerance for gap in gaps) / len(gaps)
        if share > best_share:
            best, best_share = period, share
    return best, best_share


def _classify(days: Sequence[date], as_of: date) -> tuple[str, bool, Period | None]:
    gaps = [(later - earlier).days for earlier, later in zip(days, days[1:])]
    period, share = _match_period(gaps)
    overdue = (as_of - days[-1]).days > period.days * 1.5 + period.tolerance
    span = (days[-1] - days[0]).days
    if len(days) == 2 and share == 1 and period.days >= 28 and not overdue:
        return "regular", True, period
    # 간격이 일정해도 한때 몰린 거래는 정기가 아니다. 친구에게 며칠 간격으로 보낸 돈 같은 경우다.
    if span <= SHORT_SPAN_DAYS:
        return "short", False, None
    if len(days) >= MIN_REGULAR_DAYS and share >= REGULAR_SHARE:
        return ("ended" if overdue else "regular"), False, period
    return "irregular", False, None


def _spread(amounts: Sequence[int]) -> str:
    middle = median(amounts)
    if not middle:
        return "varied"
    width = (max(amounts) - min(amounts)) / abs(middle)
    if width <= STABLE_SPREAD:
        return "same"
    return "similar" if width <= SIMILAR_SPREAD else "varied"


def _suggest(
    description: str,
    kind: str,
    direction: str,
    nature: str,
    category_key: str | None,
    typical_amount: int,
    rents: Sequence[int],
) -> Suggestion:
    if direction == "out":
        for words, suggestion in KEYWORDS:
            if any(word.casefold() in description.casefold() for word in words):
                return suggestion
        if nature == "undetermined" and any(abs(typical_amount - rent) <= rent * RENT_MARGIN for rent in rents):
            return Suggestion("expense", "housing")
    if kind == "regular" and nature == "undetermined":
        return Suggestion("expense" if direction == "out" else "income")
    return Suggestion(nature, None if category_key in UNCATEGORIZED else category_key)


def _shown_name(descriptions: Counter[str]) -> str:
    """내용이 달마다 다르면 날짜를 뺀 이름으로 보여 준다. '9월 계모임' 대신 '계모임'이다."""
    common = descriptions.most_common(1)[0][0]
    return common if len(descriptions) == 1 else DATE_WORDS.sub("", common).strip(" ._-·()[]") or common


def amount_cap(direction: str, nature: str, typical_amount: int) -> int | None:
    """나가는 돈을 지출로 보내는 규칙의 상한. 월세 규칙에 같은 상대에게 보낸 보증금이 걸리지 않게 한다."""
    return typical_amount * OUTLIER_RATIO if direction == "out" and nature == "expense" else None


def _split_outliers(members: Sequence[Line]) -> tuple[list[Line], frozenset[date]]:
    """아직 분류하지 않은 거래 중 평소보다 훨씬 큰 금액은 묶음에서 빼서 따로 정하게 한다."""
    by_day: dict[date, int] = defaultdict(int)
    for line in members:
        by_day[line.day] += abs(line.amount)
    amounts = sorted(by_day.values())
    limit = amounts[(len(amounts) - 1) // 2] * OUTLIER_RATIO  # 이틀뿐이면 작은 쪽이 기준이다
    outliers = frozenset(line.day for line in members if line.nature == "undetermined" and by_day[line.day] > limit)
    return [line for line in members if line.day not in outliers], outliers


def detect(
    lines: Iterable[Line],
    as_of: date,
    rents: Sequence[int] = (),
    linked: Mapping[tuple[str, str], Suggestion] | None = None,
) -> list[Group]:
    """같은 상대와 방향으로 이틀 이상 나타난 거래를 묶는다. 분류가 필요한 것, 금액이 큰 것 순서다.

    rents는 직접 입력한 전월세의 월세다. 금액이 비슷한 정기 출금은 주거비로 먼저 골라 둔다.
    linked는 대출, 부동산, 연금에 이어 둔 (대상, 방향)에 먼저 골라 둘 분류다. 연결은 사용자가 정한 것이라 금액으로 추측하지 않는다.
    """
    linked = linked or {}
    grouped: dict[tuple[str, str], list[Line]] = defaultdict(list)
    for line in lines:
        if not line.amount or line.nature in EXPLAINED_NATURES or line.reason in EXPLAINED_REASONS:
            continue
        grouped[(target_key(line.description), "out" if line.amount < 0 else "in")].append(line)

    groups = []
    for (key, direction), all_members in grouped.items():
        members, outliers = _split_outliers(all_members)
        by_day: dict[date, int] = defaultdict(int)
        for line in members:
            by_day[line.day] += abs(line.amount)
        days = sorted(by_day)
        if len(days) < 2:
            continue
        kind, tentative, period = _classify(days, as_of)
        amounts = [by_day[day] for day in days]
        description = _shown_name(Counter(line.description for line in members))
        nature, category_key, category_name = Counter(
            (line.nature, line.category_key, line.category_name) for line in members
        ).most_common(1)[0][0]
        undetermined = sum(line.nature == "undetermined" for line in members)
        uncategorized = sum(line.category_key in UNCATEGORIZED for line in members)
        unpaired_charge = any(line.reason == "charge_unpaired" for line in members)
        typical = round(median(amounts))
        if unpaired_charge:
            suggestion = Suggestion("funding")
        elif (key, direction) in linked:
            # 이미 정한 분류는 그대로 보여 준다.
            undecided = nature == "undetermined" or category_key in UNCATEGORIZED
            suggestion = linked[(key, direction)] if undecided else Suggestion(nature, category_key)
        else:
            suggestion = _suggest(description, kind, direction, nature, category_key, typical, rents)
        groups.append(
            Group(
                key=key,
                description=description,
                direction=direction,
                kind=kind,
                tentative=tentative,
                period=period.name if period else None,
                typical_day=round(median(day.day for day in days)) if period and period.name == "매달" else None,
                times=len(days),
                rows=len(members),
                undetermined=undetermined,
                uncategorized=uncategorized,
                total=sum(amounts),
                typical_amount=typical,
                low=min(amounts),
                high=max(amounts),
                spread=_spread(amounts),
                first=days[0],
                last=days[-1],
                nature=nature,
                category_key=category_key,
                category_name=category_name,
                instrument_name=Counter(line.instrument_name for line in members).most_common(1)[0][0],
                needs_review=bool(undetermined or uncategorized),
                recent=tuple((day, by_day[day]) for day in reversed(days[-6:])),
                suggestion=suggestion,
                outliers=outliers,
                loan_split=any(line.reason in LOAN_REASONS for line in members),
                source=Counter(line.source for line in members).most_common(1)[0][0],
            )
        )
    return sorted(groups, key=lambda group: (not group.needs_review, -group.total))
