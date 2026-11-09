"""서버에서 그리는 작은 차트의 좌표 계산.

CSP가 인라인 스타일과 스크립트를 막으므로 SVG 속성만 쓴다. 가로는 %로 두어 화면 폭을 따라가고,
세로는 픽셀로 고정해 휴대폰에서 글자가 줄어들지 않게 한다. 큰 차트는 public/js에서 브라우저가 그린다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import ceil, cos, floor, log10, pi, sin

from app.format import compact_won, won

PLOT_TOP = 24  # 마지막 점 위에 금액이 들어갈 자리
PLOT_HEIGHT = 140
DONUT_SIZE = 120
DONUT_THICKNESS = 26


@dataclass(frozen=True)
class Tick:
    value: int
    label: str
    y: float


@dataclass(frozen=True)
class LinePoint:
    x: float
    y: float
    title: str


@dataclass(frozen=True)
class LineChart:
    points: list[LinePoint]
    polyline: str
    ticks: list[Tick]
    baseline: float
    height: int
    start_label: str
    end_label: str
    last_value: str


@dataclass(frozen=True)
class Segment:
    key: str
    label: str
    value: int
    x: float
    width: float
    share: float | None


@dataclass(frozen=True)
class Slice:
    key: str
    name: str
    amount: int
    share: float
    slot: str
    path: str


@dataclass(frozen=True)
class Bar:
    name: str
    amount: int
    share: float | None
    width: float
    href: str | None = None
    hint: str | None = None  # 이름 옆의 짧은 정보
    slot: str | None = None  # 차트와 같은 색을 쓸 때


def nice_step(span: int, count: int = 4) -> int:
    """눈금 간격을 1, 2, 5 × 10^n 중에서 고른다."""
    rough = max(span, 1) / count
    magnitude = 10.0 ** floor(log10(rough))
    factor = next(f for f in (1, 2, 5, 10) if rough <= f * magnitude)
    return max(int(factor * magnitude), 1)


def line_chart(series: Sequence[tuple[date, int]]) -> LineChart | None:
    """점이 둘 이상이어야 추이가 된다. 값의 범위에 맞춰 축을 잡으므로 0에서 시작하지 않을 수 있다."""
    if len(series) < 2:
        return None
    values = [value for _, value in series]
    step = nice_step(max(values) - min(values))
    bottom = floor(min(values) / step) * step
    top = ceil(max(values) / step) * step
    if top == bottom:
        top += step
    baseline = float(PLOT_TOP + PLOT_HEIGHT)

    def y_of(value: int) -> float:
        return round(baseline - PLOT_HEIGHT * (value - bottom) / (top - bottom), 1)

    first = series[0][0]
    days = (series[-1][0] - first).days or 1
    points = [
        LinePoint(round((day - first).days * 100 / days, 2), y_of(value), f"{day} {won(value)}")
        for day, value in series
    ]
    return LineChart(
        points=points,
        polyline=" ".join(f"{point.x},{point.y}" for point in points),
        ticks=[Tick(value, compact_won(value), y_of(value)) for value in range(bottom, top + 1, step)],
        baseline=baseline,
        height=PLOT_TOP + PLOT_HEIGHT + 24,
        start_label=str(first),
        end_label=str(series[-1][0]),
        last_value=compact_won(values[-1]),
    )


def stack(parts: Sequence[tuple[str, str, int]]) -> list[Segment]:
    """전체에서 각 부분이 차지하는 띠. 음수인 부분은 띠에서 빼고 금액은 그대로 둔다."""
    total = sum(max(value, 0) for _, _, value in parts)
    segments = []
    x = 0.0
    for key, label, value in parts:
        share = max(value, 0) / total if total else None
        width = round((share or 0) * 100, 2)
        segments.append(Segment(key, label, value, round(x, 2), width, share))
        x += width
    return segments


def _arc(outer: float, inner: float, center: float, start: float, end: float) -> str:
    large = 1 if end - start > pi else 0

    def at(radius: float, angle: float) -> str:
        return f"{round(center + radius * cos(angle), 2)} {round(center + radius * sin(angle), 2)}"

    return (
        f"M{at(outer, start)}A{outer} {outer} 0 {large} 1 {at(outer, end)}"
        f"L{at(inner, end)}A{inner} {inner} 0 {large} 0 {at(inner, start)}Z"
    )


def donut(parts: Sequence[tuple[str, str, int, str]], size: int = DONUT_SIZE, thickness: int = DONUT_THICKNESS) -> list[Slice]:
    """비중 원. (키, 이름, 금액, 색 칸)을 받는다. 환불이 더 많아 음수인 항목은 비중을 낼 수 없어 뺀다."""
    positive = [part for part in parts if part[2] > 0]
    total = sum(part[2] for part in positive)
    center = size / 2
    outer = center - 1
    inner = outer - thickness
    slices = []
    angle = -pi / 2
    for key, name, amount, slot in positive:
        sweep = 2 * pi * amount / total
        if sweep >= 2 * pi - 1e-6:  # 한 번의 호로는 온전한 원을 그릴 수 없어 둘로 나눈다
            path = _arc(outer, inner, center, angle, angle + pi) + _arc(outer, inner, center, angle + pi, angle + 2 * pi)
        else:
            path = _arc(outer, inner, center, angle, angle + sweep)
        slices.append(Slice(key, name, amount, amount / total, slot, path))
        angle += sweep
    return slices
