"""라우터가 공통으로 쓰는 의존성과 화면 렌더링."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import sessions
from app.config import Settings
from app.ledger import reports


def get_db(request: Request) -> Iterator[Session]:
    """연결은 첫 쿼리 때 맺는다. DB를 쓰지 않는 요청이 Neon을 깨우지 않게 하기 위해서다."""
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def now_of(request: Request) -> datetime:
    return request.app.state.clock()


def _review_badge(request: Request) -> int:
    """메뉴의 검토 옆에 적을 수. 숫자 하나 때문에 화면이 멈추면 안 되므로 읽지 못하면 적지 않는다."""
    try:
        counts = reports.review_counts(request.state.db)
    except SQLAlchemyError:
        return 0
    return counts.undetermined + counts.uncategorized


def render(request: Request, template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
    auth = getattr(request.state, "auth", None)
    context.setdefault("auth", auth)
    context.setdefault("csrf_token", sessions.csrf_token(settings_of(request), auth) if auth else "")
    if auth and auth.stage == "full" and "review_badge" not in context:
        context["review_badge"] = _review_badge(request)
    return request.app.state.templates.TemplateResponse(request, template, context, status_code=status_code)
