"""차트 위젯: 어떤 위젯이 있는지, 탭마다 어떻게 놓았는지를 기억한다. 그리는 일은 public/js/chart-widgets.js가 한다.

탭 화면에는 그 탭의 위젯만 넣을 수 있고, 홈에는 모두 넣을 수 있다(app/home/widgets.py).
테마는 탭의 배치를 한 번에 바꾼다. 위젯 고르기 화면은 고른 순서를 한 번에 보낸다(set).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.ops import state
from app.ops_models import SystemState

TABS = {"category": "카테고리", "flow": "수입·지출", "assets": "자산", "finance": "금융"}
THEME_NAMES = {"A": "모아 보기", "B": "항목 깊이 보기", "C": "시간으로 읽기"}
DEFAULT_THEMES = {"category": "C", "flow": "A", "assets": "A", "finance": "B"}
FULL_WIDTH = 12
LAYOUT_KEY = "chart_widgets"
LAYOUT_ACTIONS = frozenset({"add", "remove", "up", "down", "theme", "set"})


@dataclass(frozen=True)
class ChartWidget:
    key: str
    title: str
    span: int = 6  # 넓은 화면에서 차지하는 칸. 12칸이 한 줄이다
    span_m: int = FULL_WIDTH  # 태블릿에서 차지하는 칸
    side: bool = False  # 왼쪽 목록. 탭 화면에서 다른 위젯 옆에 따로 선다

    @property
    def tab(self) -> str:
        return self.key.split(".")[0]


WIDGETS = {
    widget.key: widget
    for widget in (
        ChartWidget("category.matrix", "카테고리 × 월", span=FULL_WIDTH),
        ChartWidget("category.daily", "일별"),
        ChartWidget("category.weekday", "요일별"),
        ChartWidget("category.treemap", "카테고리 비중"),
        ChartWidget("category.stack", "월별 카테고리"),
        ChartWidget("category.changes", "전달보다"),
        ChartWidget("category.payment", "결제 수단"),
        ChartWidget("category.list", "카테고리 목록", side=True),
        ChartWidget("category.item", "고른 항목", span=FULL_WIDTH),
        ChartWidget("category.places", "자주 쓴 곳"),
        ChartWidget("category.free", "차트 모양 고르기", span=FULL_WIDTH),
        ChartWidget("flow.pair", "월별 수입·지출", span=7),
        ChartWidget("flow.cumulative", "누적 지출", span=5),
        ChartWidget("flow.sankey", "돈의 흐름", span=8),
        ChartWidget("flow.payment", "결제 방식", span=4),
        ChartWidget("flow.months", "달 목록", side=True),
        ChartWidget("flow.detail", "고른 달의 돈의 흐름", span=FULL_WIDTH),
        ChartWidget("flow.income", "수입 구성", span=5),
        ChartWidget("flow.mirror", "수입과 지출", span=FULL_WIDTH),
        ChartWidget("flow.net", "수입 - 지출 누적", span=FULL_WIDTH),
        ChartWidget("flow.overlay", "누적 지출 겹쳐 보기", span=7),
        ChartWidget("flow.daily", "일별", span=5),
        ChartWidget("flow.free", "차트 모양 고르기", span=FULL_WIDTH),
        ChartWidget("assets.balance", "자산과 부채", span=7),
        ChartWidget("assets.trend", "순자산 추이", span=5),
        ChartWidget("assets.waterfall", "순자산 변화", span=7),
        ChartWidget("assets.bucket", "자산 묶음", span=5),
        ChartWidget("assets.list", "항목 목록", side=True),
        ChartWidget("assets.detail", "고른 항목", span=FULL_WIDTH),
        ChartWidget("assets.info", "고른 항목의 정보", span=FULL_WIDTH),
        ChartWidget("assets.area", "자산과 부채 추이", span=FULL_WIDTH),
        ChartWidget("assets.changes", "항목별 변화", span=FULL_WIDTH),
        ChartWidget("assets.free", "차트 모양 고르기", span=FULL_WIDTH),
        ChartWidget("finance.list", "상품 목록", side=True),
        ChartWidget("finance.detail", "고른 상품", span=FULL_WIDTH),
        ChartWidget("finance.info", "고른 상품의 정보", span=FULL_WIDTH),
        ChartWidget("finance.treemap", "상품 비중", span=7),
        ChartWidget("finance.pnl", "투자 손익", span=5),
        ChartWidget("finance.loans", "대출"),
        ChartWidget("finance.maturity", "만기"),
        ChartWidget("finance.timeline", "금융 타임라인", span=FULL_WIDTH),
        ChartWidget("finance.gain", "평가금액과 원금", span=7),
        ChartWidget("finance.returns", "상품별 수익률", span=5),
        ChartWidget("finance.free", "차트 모양 고르기", span=FULL_WIDTH),
    )
}

# 탭마다 테마의 위젯과 칸 수(넓은 화면, 태블릿). design-proposals/charts.html의 A, B, C안 그대로다.
THEMES: dict[str, dict[str, tuple[tuple[str, int, int], ...]]] = {
    "category": {
        "A": (
            ("category.treemap", 5, 5),
            ("category.stack", 7, 7),
            ("category.changes", 5, 12),
            ("category.payment", 4, 6),
            ("category.weekday", 3, 6),
        ),
        "B": (
            ("category.list", 12, 12),
            ("category.item", 12, 12),
            ("category.daily", 7, 12),
            ("category.weekday", 5, 12),
            ("category.payment", 6, 12),
            ("category.places", 6, 12),
        ),
        "C": (("category.matrix", 12, 12), ("category.daily", 8, 12), ("category.weekday", 4, 12)),
    },
    "flow": {
        "A": (("flow.pair", 7, 12), ("flow.cumulative", 5, 12), ("flow.sankey", 8, 12), ("flow.payment", 4, 12)),
        "B": (
            ("flow.months", 12, 12),
            ("flow.detail", 12, 12),
            ("flow.cumulative", 7, 12),
            ("flow.income", 5, 12),
            ("flow.payment", 12, 12),
        ),
        "C": (("flow.mirror", 12, 12), ("flow.net", 12, 12), ("flow.overlay", 7, 12), ("flow.daily", 5, 12)),
    },
    "assets": {
        "A": (("assets.balance", 7, 12), ("assets.trend", 5, 12), ("assets.waterfall", 7, 12), ("assets.bucket", 5, 12)),
        "B": (("assets.list", 12, 12), ("assets.detail", 12, 12), ("assets.info", 12, 12)),
        "C": (("assets.area", 12, 12), ("assets.changes", 12, 12)),
    },
    "finance": {
        "A": (("finance.treemap", 7, 12), ("finance.pnl", 5, 12), ("finance.loans", 6, 12), ("finance.maturity", 6, 12)),
        "B": (("finance.list", 12, 12), ("finance.detail", 12, 12), ("finance.info", 12, 12)),
        "C": (("finance.timeline", 12, 12), ("finance.gain", 7, 12), ("finance.returns", 5, 12)),
    },
}


def _theme_layout(tab: str, theme: str) -> dict:
    items = [{"key": key, "span": span, "span_m": span_m} for key, span, span_m in THEMES[tab][theme]]
    return {"theme": theme, "items": items}


def _clean_span(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and 1 <= value <= FULL_WIDTH else fallback


def _clean_layout(tab: str, saved: object) -> dict:
    """저장된 값을 믿지 않는다. 없어진 위젯이나 다른 탭의 위젯이 있으면 빼고 읽는다."""
    if not isinstance(saved, dict) or saved.get("theme") not in THEMES[tab]:
        return _theme_layout(tab, DEFAULT_THEMES[tab])
    items: list[dict] = []
    seen: set[str] = set()
    for item in saved.get("items", []):
        widget = WIDGETS.get(item.get("key")) if isinstance(item, dict) else None
        if widget is None or widget.tab != tab or widget.key in seen:
            continue
        seen.add(widget.key)
        items.append(
            {
                "key": widget.key,
                "span": _clean_span(item.get("span"), widget.span),
                "span_m": _clean_span(item.get("span_m"), widget.span_m),
            }
        )
    return {"theme": saved["theme"], "items": items}


def load_layouts(db: Session) -> dict[str, dict]:
    row = db.get(SystemState, LAYOUT_KEY)
    saved = row.value if row else {}
    return {tab: _clean_layout(tab, saved.get(tab)) for tab in TABS}


def is_valid_change(tab: str, action: str, key: str, theme: str, keys: Sequence[str] = ()) -> bool:
    if tab not in TABS or action not in LAYOUT_ACTIONS:
        return False
    if action == "theme":
        return theme in THEMES[tab]
    if action == "set":
        # 고른 순서대로 놓는다. 다른 탭의 위젯이나 같은 위젯이 두 번 오면 받지 않는다.
        widgets = [WIDGETS.get(item) for item in keys]
        return theme in THEMES[tab] and len(set(keys)) == len(keys) and all(w is not None and w.tab == tab for w in widgets)
    widget = WIDGETS.get(key)
    return widget is not None and widget.tab == tab


def change_layout(
    db: Session, now: datetime, tab: str, action: str, key: str = "", theme: str = "", chosen: Sequence[str] = ()
) -> dict:
    layouts = load_layouts(db)
    current = layouts[tab]
    items = current["items"]
    keys = [item["key"] for item in items]
    if action == "theme":
        layouts[tab] = _theme_layout(tab, theme)
    elif action == "set":
        # 테마에 있는 위젯은 그 테마의 칸 수를, 없는 위젯은 위젯의 기본 칸 수를 쓴다.
        spans = {item_key: (span, span_m) for item_key, span, span_m in THEMES[tab][theme]}
        layouts[tab] = {
            "theme": theme,
            "items": [
                {"key": item_key, "span": span, "span_m": span_m}
                for item_key in chosen
                for span, span_m in [spans.get(item_key, (WIDGETS[item_key].span, WIDGETS[item_key].span_m))]
            ],
        }
    elif action == "add" and key not in keys:
        widget = WIDGETS[key]
        items.append({"key": key, "span": widget.span, "span_m": widget.span_m})
    elif action == "remove" and key in keys:
        current["items"] = [item for item in items if item["key"] != key]
    elif action in ("up", "down") and key in keys:
        index = keys.index(key)
        other = index - 1 if action == "up" else index + 1
        if 0 <= other < len(items):
            items[index], items[other] = items[other], items[index]
    state.set_state(db, now, LAYOUT_KEY, layouts)
    return layouts[tab]


def payload(db: Session) -> dict:
    """화면이 위젯 목록과 테마를 그릴 재료."""
    return {
        "tabs": [{"key": key, "name": name} for key, name in TABS.items()],
        "catalog": [
            {"key": widget.key, "tab": widget.tab, "title": widget.title, "side": widget.side} for widget in WIDGETS.values()
        ],
        "themes": [{"key": key, "name": name} for key, name in THEME_NAMES.items()],
        "theme_items": {tab: {theme: [key for key, _, _ in items] for theme, items in themes.items()} for tab, themes in THEMES.items()},
        "layouts": load_layouts(db),
    }
