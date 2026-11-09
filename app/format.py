"""화면에 보이는 금액과 달 표기, 화면 주소."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

KST = timezone(timedelta(hours=9))
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
NOTICE_PARAMS = frozenset({"changed", "msg", "error"})


def kst_today(now: datetime) -> date:
    """거래 날짜가 한국 시간이므로 오늘도 한국 시간으로 센다. 서머타임이 없어 고정 오프셋으로 충분하다."""
    return now.astimezone(KST).date()


def won(value: int) -> str:
    return f"{value:,}원"


def signed_won(value: int) -> str:
    return f"{value:+,}원"


def compact_won(value: int) -> str:
    """축처럼 좁은 자리에 쓴다. 1,234,000은 123만, 150,000,000은 1.5억이 된다.

    억 단위는 소수 넷째 자리(만 원)까지 둔다. 눈금 간격이 좁아도 값이 같아 보이지 않게 하기 위해서다.
    """
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= 100_000_000:
        return f"{sign}{size / 100_000_000:.4f}".rstrip("0").rstrip(".") + "억"
    if size >= 10_000:
        return f"{sign}{round(size / 10_000):,}만"
    return f"{sign}{size:,}"


def month_label(month: str) -> str:
    year, number = month.split("-")
    return f"{year}년 {int(number)}월"


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def valid_month(value: str | None) -> str | None:
    return value if value and MONTH_RE.match(value) else None


def previous_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year - 1}-12" if number == 1 else f"{year}-{number - 1:02d}"


def next_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year + 1}-01" if number == 12 else f"{year}-{number + 1:02d}"


def month_end(month: str) -> date:
    year, number = (int(part) for part in month.split("-"))
    return date(year, number, calendar.monthrange(year, number)[1])


def link(path: str, **params: object) -> str:
    query = urlencode({name: value for name, value in params.items() if value not in (None, "")})
    return f"{path}?{query}" if query else path


def back_link(value: str, allowed: frozenset[str], default: str, **extra: object) -> str:
    """폼이 돌아갈 주소. 폼 값을 그대로 믿으면 외부 사이트로 보내는 데 쓰일 수 있어 정해진 화면으로만 보낸다."""
    parts = urlsplit(value)
    if parts.path in allowed:
        path = parts.path
        kept = [(name, item) for name, item in parse_qsl(parts.query) if name not in NOTICE_PARAMS]
    else:
        path, kept = default, []
    return f"{path}?{urlencode([*kept, *extra.items()])}"
