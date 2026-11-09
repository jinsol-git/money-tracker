"""모든 응답의 보안 헤더와, 값이 새지 않는 오류 처리."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("ledger")

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'none'",
        "frame-ancestors 'none'",
    )
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # no-referrer를 쓰면 브라우저가 같은 사이트 POST에도 Origin: null을 보내 CSRF 검사에 막힌다.
    # same-origin은 다른 사이트로는 리퍼러를 보내지 않으면서 Origin을 유지한다.
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeadersMiddleware:
    """금융 데이터가 기기에 남지 않도록 함수 응답은 전부 no-store로 내보낸다."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if not scope["path"].startswith(("/css/", "/js/", "/fonts/")):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SanitizedErrorMiddleware:
    """예외 메시지와 SQL 파라미터에 거래 내용이 섞일 수 있어, 종류와 위치만 기록하고 응답한다."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - 모든 예외를 여기서 끝낸다
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            logger.error(
                "unhandled error type=%s at=%s:%s path=%s",
                type(exc).__name__,
                Path(frame.filename).name,
                frame.lineno,
                scope["path"],
            )
            if not response_started:
                response = PlainTextResponse("일시적인 오류가 발생했습니다.", status_code=500)
                await response(scope, receive, send)
