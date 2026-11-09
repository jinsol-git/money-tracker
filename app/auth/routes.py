"""로그인 흐름: Google 로그인, OTP(또는 복구 코드), 정식 세션."""

from __future__ import annotations

import base64
import json
import logging
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.auth import otp, sessions
from app.auth.google import GoogleAuthError
from app.config import Settings
from app.deps import get_db, now_of, render, settings_of
from app.ops import state
from app.ops_models import AuthSession, Owner
from app.security.crypto import CryptoError, constant_time_equals, seal, unseal
from app.settings.secrets import current_secret, delete_secret, store_secret

router = APIRouter()
logger = logging.getLogger("ledger")

FLOW_TTL_SECONDS = 600
FLOW_PURPOSE = "oauth-flow"
ACTIVE_SEED = "totp_seed"


def _see_other(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _denied(request: Request, settings: Settings) -> HTMLResponse:
    response = render(request, "error.html", status_code=403, message="이 계정으로는 사용할 수 없습니다.")
    _clear_flow_cookie(response, settings)
    return response


def _login_failed(settings: Settings) -> RedirectResponse:
    response = _see_other("/auth/login?error=1")
    _clear_flow_cookie(response, settings)
    return response


def _write_flow_cookie(response: Response, settings: Settings, value: dict) -> None:
    sealed = seal(settings.master_key, FLOW_PURPOSE, json.dumps(value).encode())
    response.set_cookie(
        settings.flow_cookie,
        base64.urlsafe_b64encode(sealed).decode(),
        max_age=FLOW_TTL_SECONDS,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )


def _read_flow_cookie(request: Request, settings: Settings) -> dict | None:
    raw = request.cookies.get(settings.flow_cookie)
    if not raw:
        return None
    try:
        return json.loads(unseal(settings.master_key, FLOW_PURPOSE, base64.urlsafe_b64decode(raw)))
    except (CryptoError, ValueError):
        return None


def _clear_flow_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.flow_cookie, path="/", secure=settings.secure_cookies, httponly=True, samesite="lax"
    )


def _may_enroll(owner: Owner, auth: AuthSession) -> bool:
    """OTP가 아직 없을 때(첫 설정)만 Google 로그인만으로 등록할 수 있다.

    이미 OTP가 있으면 기존 OTP나 복구 코드를 확인한 세션만 바꿀 수 있고,
    새 코드를 확인하기 전까지 기존 OTP가 그대로 유효하다.
    """
    return owner.totp_enabled_at is None or auth.can_enroll


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    return render(request, "login.html", failed=error is not None)


@router.get("/auth/google")
def start_login(request: Request):
    settings = settings_of(request)
    now = now_of(request)
    flow = {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "verifier": secrets.token_urlsafe(48),
        "expires": now.timestamp() + FLOW_TTL_SECONDS,
    }
    url = request.app.state.google.authorization_url(flow["state"], flow["nonce"], flow["verifier"])
    response = _see_other(url)
    _write_flow_cookie(response, settings, flow)
    return response


@router.get("/auth/callback")
def callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    error: str | None = None,
):
    """소유자로 확인되기 전에는 DB에 닿지 않는다. 외부 요청이 무료 DB를 깨우거나 채우지 못하게 하기 위해서다."""
    settings = settings_of(request)
    now = now_of(request)
    # 이 모듈의 state(감사 기록)와 이름이 겹쳐 쿼리 값은 직접 읽는다
    returned_state = request.query_params.get("state", "")
    flow = _read_flow_cookie(request, settings)

    if flow is None or flow["expires"] <= now.timestamp() or not constant_time_equals(flow["state"], returned_state):
        return _login_failed(settings)
    if error or not code:
        return _login_failed(settings)

    google = request.app.state.google
    try:
        identity = google.verify_id_token(google.exchange_code(code, flow["verifier"]), flow["nonce"])
    except GoogleAuthError:
        logger.warning("login failed: google verification")
        return _login_failed(settings)

    if not identity.email_verified or identity.email.casefold() != settings.owner_email.casefold():
        logger.warning("login denied: not owner email")
        return _denied(request, settings)

    owner = db.get(Owner, 1)
    if owner is None:
        owner = Owner(id=1, google_sub=identity.sub, email=identity.email)
        db.add(owner)
        db.flush()
        state.record(db, now, "owner_registered")
    elif not constant_time_equals(owner.google_sub, identity.sub):
        state.record(db, now, "login_denied", reason="not_owner_sub")
        db.commit()
        return _denied(request, settings)

    _, cookie = sessions.issue(db, settings, now, request.headers.get("user-agent"))
    state.record(db, now, "google_login_ok")
    db.commit()

    response = _see_other("/auth/mfa" if owner.totp_enabled_at else "/auth/enroll")
    sessions.set_cookie(response, settings, cookie)
    _clear_flow_cookie(response, settings)
    return response


@router.get("/auth/mfa", response_class=HTMLResponse)
def mfa_page(request: Request, db: Session = Depends(get_db), error: str | None = None):
    auth = request.state.auth
    if auth.stage == "full":
        return _see_other("/")
    owner = db.get(Owner, 1)
    if owner is None or _may_enroll(owner, auth):
        return _see_other("/auth/enroll")
    return render(request, "mfa.html", error=error, wait=otp.wait_text(owner, now_of(request)))


@router.post("/auth/mfa")
def submit_otp(request: Request, db: Session = Depends(get_db), code: str = Form("")):
    settings = settings_of(request)
    now = now_of(request)
    auth = request.state.auth
    if auth.stage == "full":
        return _see_other("/")
    owner = otp.lock_owner(db)
    if owner is None or owner.totp_enabled_at is None:
        return _see_other("/auth/enroll")
    if otp.is_locked(owner, now):
        return _see_other("/auth/mfa?error=locked")

    seed = current_secret(db, settings, ACTIVE_SEED)
    step = otp.verify(seed, code.strip(), now, owner.totp_last_step) if seed else None
    if step is None:
        otp.register_failure(owner, now)
        state.record(db, now, "otp_failed", locked=otp.is_locked(owner, now))
        db.commit()
        return _see_other("/auth/mfa?error=code")

    otp.register_success(owner)
    owner.totp_last_step = step
    cookie = sessions.promote(db, settings, auth, now)
    state.record(db, now, "login_success")
    db.commit()
    response = _see_other("/")
    sessions.set_cookie(response, settings, cookie)
    return response


@router.post("/auth/recovery")
def use_recovery_code(request: Request, db: Session = Depends(get_db), code: str = Form("")):
    """복구 코드는 OTP를 대신하지 않고, 이 세션에서 OTP를 다시 등록할 자격을 준다."""
    settings = settings_of(request)
    now = now_of(request)
    auth = request.state.auth
    if auth.stage == "full":
        return _see_other("/")
    owner = otp.lock_owner(db)
    if owner is None or owner.totp_enabled_at is None:
        return _see_other("/auth/enroll")
    if otp.is_locked(owner, now):
        return _see_other("/auth/mfa?error=locked")

    if not otp.consume_recovery_code(db, settings, code, now):
        otp.register_failure(owner, now)
        state.record(db, now, "recovery_failed", locked=otp.is_locked(owner, now))
        db.commit()
        return _see_other("/auth/mfa?error=recovery")

    otp.register_success(owner)
    auth.can_enroll = True
    auth.expires_at = now + sessions.ENROLL_TIMEOUT
    state.record(db, now, "recovery_used", remaining=otp.remaining_recovery_codes(db))
    db.commit()
    return _see_other("/auth/enroll")


@router.get("/auth/enroll", response_class=HTMLResponse)
def enroll_page(request: Request, db: Session = Depends(get_db), error: str | None = None):
    settings = settings_of(request)
    now = now_of(request)
    auth = request.state.auth
    owner = db.get(Owner, 1)
    if owner is None:
        return _see_other("/auth/login")
    if not _may_enroll(owner, auth):
        return _see_other("/" if auth.stage == "full" else "/auth/mfa")

    seed = sessions.pending_seed(settings, auth)
    if seed is None:
        seed = otp.new_seed()
        sessions.set_pending_seed(settings, auth, seed)
        db.commit()
    return render(
        request,
        "enroll.html",
        qr=otp.qr_svg(otp.provisioning_uri(seed, settings.owner_email)),
        secret=seed,
        error=error,
        wait=otp.wait_text(owner, now),
    )


@router.post("/auth/enroll", response_class=HTMLResponse)
def complete_enrollment(request: Request, db: Session = Depends(get_db), code: str = Form("")):
    settings = settings_of(request)
    now = now_of(request)
    auth = request.state.auth
    owner = otp.lock_owner(db)
    if owner is None:
        return _see_other("/auth/login")
    if not _may_enroll(owner, auth):
        return _see_other("/" if auth.stage == "full" else "/auth/mfa")
    if otp.is_locked(owner, now):
        return _see_other("/auth/enroll?error=locked")

    seed = sessions.pending_seed(settings, auth)
    step = otp.verify(seed, code.strip(), now, None) if seed else None
    if step is None:
        otp.register_failure(owner, now)
        state.record(db, now, "otp_enroll_failed")
        db.commit()
        return _see_other("/auth/enroll?error=code")

    # 새 코드를 확인한 이 시점에야 기존 OTP를 바꾼다
    delete_secret(db, ACTIVE_SEED)
    store_secret(db, settings, now, ACTIVE_SEED, seed)
    otp.register_success(owner)
    owner.totp_enabled_at = now
    owner.totp_last_step = step
    auth.pending_totp = None
    auth.can_enroll = False
    codes = otp.issue_recovery_codes(db, settings, now)
    sessions.revoke_others(db, auth.id, now)
    cookie = sessions.promote(db, settings, auth, now)
    state.record(db, now, "otp_enrolled")
    db.commit()

    response = render(request, "recovery_codes.html", codes=codes)
    sessions.set_cookie(response, settings, cookie)
    return response


@router.post("/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    settings = settings_of(request)
    now = now_of(request)
    sessions.revoke(request.state.auth, now)
    state.record(db, now, "logout")
    db.commit()
    response = _see_other("/auth/login")
    sessions.clear_cookie(response, settings)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return response
