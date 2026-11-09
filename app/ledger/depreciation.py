"""자동차 감가 추정. V_t = V_0 × Π(1 − r × C_i)

r은 차급과 국산·수입에 따른 연간 상각률, C_i는 차의 i번째 해에 곱하는 보정 계수다.
실제 시세가 아니라 추정치이므로 사용자가 반영 여부를 고르고, 금액을 직접 고칠 수도 있다.
"""

from __future__ import annotations

from datetime import date

# 차급과 제조국별 상각률 구간의 가운데 값이다.
BASE_RATES = {
    ("small", "domestic"): 0.105,
    ("small", "imported"): 0.145,
    ("compact", "domestic"): 0.115,
    ("compact", "imported"): 0.155,
    ("mid", "domestic"): 0.125,
    ("mid", "imported"): 0.165,
    ("large", "domestic"): 0.145,
    ("large", "imported"): 0.19,
}
FIRST_YEAR_FACTOR = 1.65  # 신차 감가. 1.5~1.8배의 가운데 값
WARRANTY_FACTOR = 1.0  # 2~3년 차: 표준 상각
SECOND_DROP_FACTOR = 1.2  # 4~5년 차: 보증 만료와 페이스리프트로 다시 커진다
SETTLED_FACTOR = 0.8  # 6년 차 이후: 완만해진다


def age_factor(year: int) -> float:
    """차의 year번째 해에 곱하는 보정 계수."""
    if year <= 1:
        return FIRST_YEAR_FACTOR
    if year <= 3:
        return WARRANTY_FACTOR
    if year <= 5:
        return SECOND_DROP_FACTOR
    return SETTLED_FACTOR


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 2월 29일
        return day.replace(year=day.year + years, day=28)


def age_in_years(first_registered: date, target: date) -> int:
    """지금 몇 년 차인지. 출고한 해가 1년 차다."""
    year = 1
    while _add_years(first_registered, year) <= target:
        year += 1
    return year


def depreciated_value(
    price: int, car_class: str, car_origin: str, first_registered: date, base_date: date, target: date
) -> int:
    """base_date의 금액 price가 target까지 줄어든 값. 천 원 단위로 반올림한다.

    계수는 차의 나이를 따르므로, 중고차면 산 시점 이전의 해는 곱하지 않는다.
    해의 일부만 지났으면 지난 비율만큼만 줄인다.
    """
    if target <= base_date:
        return price
    rate = BASE_RATES[(car_class, car_origin)]
    value = float(price)
    year = 1
    start = first_registered
    while start < target:
        end = _add_years(first_registered, year)
        from_day = max(start, base_date)
        to_day = min(end, target)
        if to_day > from_day:
            portion = (to_day - from_day).days / (end - start).days
            value *= max(1 - rate * age_factor(year) * portion, 0)
        start = end
        year += 1
    return int(round(value, -3))
