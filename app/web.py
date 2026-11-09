"""FastAPI 앱 조립. 배포 진입점은 app/main.py다."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine

from app.assets.routes import router as assets_router
from app.auth import sessions
from app.auth.gate import MfaRequired, NotAuthenticated, gate
from app.auth.google import GoogleClient
from app.auth.routes import router as auth_router
from app.config import Settings
from app.db import make_engine
from app.format import KST, compact_won, month_label, percent, signed_won, won
from app.home.routes import router as home_router
from app.ingest.api import router as ingest_router
from app.ledger.labels import reason_label, split_part
from app.pages import router as pages_router
from app.recurring.routes import router as recurring_router
from app.security.middleware import SanitizedErrorMiddleware, SecurityHeadersMiddleware
from app.settings.routes import router as settings_router

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 앱에 라우터를 등록하는 유일한 목록이다. 관문 테스트도 이 목록의 모든 경로를 검사한다.
ROUTERS = (home_router, pages_router, assets_router, recurring_router, auth_router, settings_router, ingest_router)


def format_kst(value: datetime, pattern: str = "%Y-%m-%d %H:%M") -> str:
    """DB는 UTC로 응답하므로 한국 시간으로 바꿔 표시한다. 한국은 서머타임이 없어 고정 오프셋으로 충분하다."""
    return value.astimezone(KST).strftime(pattern)


def _wants_html(request: Request) -> bool:
    """브라우저의 폼 제출도 포함한다. 세션이 끝난 화면에서 저장을 누르면 JSON 대신 로그인 화면이 나와야 한다."""
    return "text/html" in request.headers.get("accept", "")


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    clock: Callable[[], datetime] | None = None,
    google_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    application = FastAPI(
        title="Ledger",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        dependencies=[Depends(gate)],
    )
    application.state.settings = settings
    application.state.engine = engine or make_engine(settings.database_url)
    application.state.clock = clock or (lambda: datetime.now(UTC))
    application.state.google = GoogleClient(
        settings.google_client_id,
        settings.google_client_secret,
        settings.redirect_uri,
        transport=google_transport,
    )
    application.state.templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
    application.state.templates.env.filters.update(
        kst=format_kst,
        won=won,
        signed_won=signed_won,
        compact_won=compact_won,
        month_label=month_label,
        percent=percent,
        reason_label=reason_label,
        split_part=split_part,
    )

    # 나중에 추가한 미들웨어가 바깥쪽이다. 오류 응답에도 보안 헤더가 붙어야 한다.
    application.add_middleware(SanitizedErrorMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)

    @application.exception_handler(NotAuthenticated)
    def on_not_authenticated(request: Request, _exc: NotAuthenticated) -> Response:
        response: Response
        if _wants_html(request):
            response = RedirectResponse("/auth/login", status_code=303)
        else:
            response = JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if request.cookies.get(settings.session_cookie):
            sessions.clear_cookie(response, settings)
        return response

    @application.exception_handler(MfaRequired)
    def on_mfa_required(request: Request, _exc: MfaRequired) -> Response:
        if _wants_html(request):
            return RedirectResponse("/auth/mfa", status_code=303)
        return JSONResponse({"detail": "OTP 확인이 필요합니다."}, status_code=401)

    for router in ROUTERS:
        application.include_router(router)

    # Vercel은 public/을 CDN에서 직접 서비스하므로 마운트하면 안 된다. 로컬에서만 쓴다.
    if not os.environ.get("VERCEL"):
        application.mount("/css", StaticFiles(directory=PROJECT_ROOT / "public" / "css"), name="css")
        application.mount("/js", StaticFiles(directory=PROJECT_ROOT / "public" / "js"), name="js")
        application.mount("/fonts", StaticFiles(directory=PROJECT_ROOT / "public" / "fonts"), name="fonts")
    return application
