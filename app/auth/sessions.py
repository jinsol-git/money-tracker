"""서버 저장 세션. 쿠키에는 임의 토큰과 서명만 담고, DB에는 토큰의 해시만 저장한다."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.ops_models import AuthSession
from app.security.crypto import constant_time_equals, keyed_hash, seal, unseal

IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=12)
PRE_MFA_TIMEOUT = timedelta(minutes=5)
ENROLL_TIMEOUT = timedelta(minutes=15)
STEP_UP_WINDOW = timedelta(minutes=5)
TOUCH_INTERVAL = timedelta(seconds=60)
SIGNATURE_LENGTH = 32
PENDING_SEED_PURPOSE = "session-pending-totp"


def _signature(settings: Settings, token: str) -> str:
    return keyed_hash(settings.master_key, "session-cookie", token.encode())[:SIGNATURE_LENGTH]


def _token_hash(settings: Settings, token: str) -> str:
    return keyed_hash(settings.master_key, "session-store", token.encode())


def _new_cookie(settings: Settings) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return f"{token}.{_signature(settings, token)}", _token_hash(settings, token)


def issue(db: Session, settings: Settings, now: datetime, user_agent: str | None) -> tuple[AuthSession, str]:
    """Google 로그인 직후의 임시 세션. OTP 화면만 쓸 수 있다."""
    cookie, token_hash = _new_cookie(settings)
    record = AuthSession(
        token_hash=token_hash,
        stage="pre_mfa",
        created_at=now,
        last_seen_at=now,
        expires_at=now + PRE_MFA_TIMEOUT,
        user_agent=(user_agent or "")[:200] or None,
    )
    db.add(record)
    db.flush()
    return record, cookie


def resolve(db: Session, settings: Settings, cookie: str, now: datetime) -> AuthSession | None:
    """서명이 틀린 쿠키는 DB에 묻기 전에 거절한다."""
    token, separator, signature = cookie.partition(".")
    if not separator or not constant_time_equals(signature, _signature(settings, token)):
        return None
    record = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(settings, token)))
    if record is None or record.revoked_at is not None or record.expires_at <= now:
        return None
    if record.stage == "full" and record.last_seen_at + IDLE_TIMEOUT <= now:
        return None
    return record


def promote(db: Session, settings: Settings, record: AuthSession, now: datetime) -> str:
    """OTP를 통과하면 토큰을 새로 발급한다. 이전 토큰이 샜어도 정식 세션으로 쓰이지 않는다."""
    cookie, token_hash = _new_cookie(settings)
    record.token_hash = token_hash
    record.stage = "full"
    record.created_at = now
    record.last_seen_at = now
    record.expires_at = now + ABSOLUTE_TIMEOUT
    record.mfa_verified_at = now
    db.flush()
    return cookie


def pending_seed(settings: Settings, record: AuthSession) -> str | None:
    if record.pending_totp is None:
        return None
    return unseal(settings.master_key, PENDING_SEED_PURPOSE, record.pending_totp).decode()


def set_pending_seed(settings: Settings, record: AuthSession, seed: str) -> None:
    record.pending_totp = seal(settings.master_key, PENDING_SEED_PURPOSE, seed.encode())


def revoke(record: AuthSession, now: datetime) -> None:
    record.revoked_at = now


def revoke_others(db: Session, keep_id: int | None, now: datetime) -> int:
    query = update(AuthSession).where(AuthSession.revoked_at.is_(None)).values(revoked_at=now)
    if keep_id is not None:
        query = query.where(AuthSession.id != keep_id)
    return db.execute(query).rowcount


def active_sessions(db: Session, now: datetime) -> list[AuthSession]:
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now, AuthSession.stage == "full")
        .order_by(AuthSession.last_seen_at.desc())
    ).all()
    return [row for row in rows if row.last_seen_at + IDLE_TIMEOUT > now]


def has_fresh_mfa(record: AuthSession, now: datetime) -> bool:
    return record.mfa_verified_at is not None and now - record.mfa_verified_at < STEP_UP_WINDOW


def csrf_token(settings: Settings, record: AuthSession) -> str:
    return keyed_hash(settings.master_key, "csrf", record.token_hash.encode())


def set_cookie(response: Response, settings: Settings, value: str) -> None:
    """만료를 지정하지 않아 브라우저를 닫으면 사라진다. 서버 쪽 만료는 따로 검사한다."""
    response.set_cookie(
        settings.session_cookie,
        value,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
