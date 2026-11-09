"""모든 경로에 적용되는 관문. 허용 목록에 없는 경로는 정식 세션이 있어야 한다.

새 경로를 추가하면서 인증을 빠뜨려도 기본적으로 막히도록 기본 거부로 둔다.
서명이나 쿠키가 틀린 요청은 DB에 닿기 전에 거절해 무료 사용량을 소모하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import sessions
from app.config import Settings
from app.deps import get_db, now_of, settings_of

PUBLIC_PATHS = frozenset({"/healthz", "/auth/login", "/auth/google", "/auth/callback"})
SIGNED_PATHS = frozenset({"/api/ingest/banksalad", "/api/ingest/heartbeat", "/api/backup/export"})
PRE_MFA_PATHS = frozenset({"/auth/mfa", "/auth/recovery", "/auth/enroll", "/auth/logout"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_SIGNED_BODY_BYTES = 6 * 1024 * 1024
SIGNATURE_MAX_AGE_SECONDS = 300


class NotAuthenticated(Exception):
    pass


class MfaRequired(Exception):
    pass


def sign_body(key: str, timestamp: str, path: str, body: bytes) -> str:
    """경로까지 서명해 같은 본문의 서명을 다른 API에 재사용하지 못하게 한다."""
    return hmac.new(key.encode(), f"{timestamp}.{path}.".encode() + body, hashlib.sha256).hexdigest()


async def verify_signed_request(request: Request, settings: Settings, now: datetime, path: str) -> None:
    timestamp = request.headers.get("x-ledger-timestamp", "")
    signature = request.headers.get("x-ledger-signature", "")
    declared = request.headers.get("content-length", "0")
    if declared.isdigit() and int(declared) > MAX_SIGNED_BODY_BYTES:
        raise HTTPException(status_code=413)
    body = await request.body()
    if len(body) > MAX_SIGNED_BODY_BYTES:
        raise HTTPException(status_code=413)
    if not timestamp.isdigit() or abs(now.timestamp() - int(timestamp)) > SIGNATURE_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401)
    expected = sign_body(settings.ingest_hmac_key, timestamp, path, body)
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401)


def _same_origin(request: Request, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is not None:
        base = urlsplit(settings.base_url)
        return origin == f"{base.scheme}://{base.netloc}"
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in {"same-origin", "none"}
    return True  # 브라우저가 아니면 토큰 검사에 맡긴다


async def verify_csrf(request: Request, settings: Settings, record) -> None:
    if not _same_origin(request, settings):
        raise HTTPException(status_code=403)
    token = request.headers.get("x-csrf-token")
    if token is None:
        form = await request.form()
        token = form.get("csrf_token")
    if not isinstance(token, str) or not hmac.compare_digest(token, sessions.csrf_token(settings, record)):
        raise HTTPException(status_code=403)


async def gate(request: Request, db: Session = Depends(get_db)) -> None:
    settings = settings_of(request)
    now = now_of(request)
    # request.url.path는 Host 헤더로 조립되어 'Host: x/healthz?' 같은 값으로 속일 수 있다.
    # 라우터가 실제로 쓰는 scope의 경로를 봐야 관문과 라우팅이 같은 경로를 판단한다.
    path = request.scope["path"]

    if path in PUBLIC_PATHS:
        return
    if path in SIGNED_PATHS:
        await verify_signed_request(request, settings, now, path)
        return

    cookie = request.cookies.get(settings.session_cookie)
    record = sessions.resolve(db, settings, cookie, now) if cookie else None
    if record is None:
        raise NotAuthenticated
    request.state.auth = record
    request.state.db = db

    if record.stage != "full" and path not in PRE_MFA_PATHS:
        raise MfaRequired
    if request.method not in SAFE_METHODS:
        await verify_csrf(request, settings, record)
    if now - record.last_seen_at >= sessions.TOUCH_INTERVAL:
        record.last_seen_at = now
        db.commit()
