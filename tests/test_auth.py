"""로그인, OTP, 세션, CSRF. 계획서 7장의 보안 검증 항목을 그대로 시험한다."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import event, select

from app.auth.gate import PRE_MFA_PATHS, PUBLIC_PATHS, SIGNED_PATHS
from app.ops_models import AuditLog, AuthSession, Owner, RecoveryCode, SecretValue
from app.security.middleware import CONTENT_SECURITY_POLICY
from app.web import ROUTERS
from web_support import CLIENT_ID, OWNER_EMAIL, OWNER_SUB, SECRET_RE, Browser

# --- 관문: 기본 거부


def _api_routes():
    return [route for router in ROUTERS for route in router.routes if isinstance(route, APIRoute)]


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_public_and_signed_lists_are_exactly_what_we_expect():
    assert PUBLIC_PATHS == {"/healthz", "/auth/login", "/auth/google", "/auth/callback"}
    assert SIGNED_PATHS == {"/api/ingest/banksalad", "/api/ingest/heartbeat", "/api/backup/export"}


def test_every_route_rejects_anonymous_requests(browser):
    checked = 0
    for route in _api_routes():
        if route.path in PUBLIC_PATHS:
            continue
        path = _concrete(route.path)
        for method in route.methods:
            response = browser.client.request(method, path)
            assert response.status_code in {303, 401}, f"{method} {path}: {response.status_code}"
            if response.status_code == 303:
                assert response.headers["location"] == "/auth/login"
            checked += 1
    assert checked >= 12


def test_new_route_is_blocked_without_any_extra_code(web_app, browser):
    @web_app.get("/new-feature")
    def new_feature():
        return {"secret": "data"}

    assert browser.client.get("/new-feature").status_code == 401


def test_pre_mfa_session_reaches_only_the_otp_screens(browser, session):
    browser.google_callback()
    checked = 0
    for route in _api_routes():
        if route.path in PUBLIC_PATHS | PRE_MFA_PATHS | SIGNED_PATHS:
            continue
        path = _concrete(route.path)
        for method in route.methods:
            response = browser.client.request(method, path, headers={"accept": "text/html"})
            assert response.status_code in {303, 401}, f"{method} {path}: {response.status_code}"
            checked += 1
    assert checked >= 8
    assert browser.client.get("/", headers={"accept": "text/html"}).headers["location"] == "/auth/mfa"


def _asgi_status(app, path: str, method: str = "GET", host: bytes = b"x/healthz?", cookie: bytes | None = None) -> int:
    """Host 값이 scope의 server까지 들어가는 런타임을 흉내 낸다. TestClient로는 만들 수 없는 요청이다."""
    headers = [(b"host", host), (b"accept", b"application/json")]
    if cookie:
        headers.append((b"cookie", cookie))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "server": (host.decode(), 443),
        "client": ("203.0.113.1", 1234),
        "state": {},
    }
    statuses = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    asyncio.run(app(scope, receive, send))
    return statuses[0]


@pytest.mark.parametrize("host", [b"x/healthz?", b"x/auth/login?", b"x/api/ingest/heartbeat?", b"evil#"])
def test_host_header_cannot_fool_the_gate(web_app, host):
    assert _asgi_status(web_app, "/settings", host=host) == 401
    assert _asgi_status(web_app, "/", host=host) == 401
    assert _asgi_status(web_app, "/settings/upload", method="POST", host=host) == 401


def test_host_header_cannot_turn_otp_screen_into_full_access(signed_in, web_app, google, clock):
    device = Browser(web_app, google, clock)
    device.google_callback()
    cookie = device.client.cookies.get("__Host-session")
    header = f"__Host-session={cookie}".encode()
    assert _asgi_status(web_app, "/settings", host=b"x/auth/mfa?", cookie=header) == 401


def test_docs_are_disabled(browser):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert browser.client.get(path).status_code == 404


def test_health_check_touches_no_data(browser):
    response = browser.client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# --- Google 로그인


def test_login_uses_pkce_and_minimal_scope(browser, google):
    query = browser.start_google()
    assert query["scope"] == "openid email"
    assert query["code_challenge_method"] == "S256"
    assert query["client_id"] == CLIENT_ID
    assert len(query["state"]) >= 32
    browser.google_callback(query)
    assert google.verifier_ok is True


def test_first_login_pins_the_owner(browser, session):
    response = browser.google_callback()
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/enroll"
    owner = session.get(Owner, 1)
    assert owner.google_sub == OWNER_SUB
    assert owner.email == OWNER_EMAIL


def test_other_account_cannot_become_owner(browser, google, session):
    google.claims = {"email": "attacker@example.com", "sub": "attacker"}
    assert browser.google_callback().status_code == 403
    assert session.get(Owner, 1) is None


def test_unverified_email_cannot_become_owner(browser, google, session):
    google.claims = {"email_verified": False}
    assert browser.google_callback().status_code == 403
    assert session.get(Owner, 1) is None


def test_after_pinning_only_the_same_google_account_works(signed_in, web_app, google, clock):
    other = Browser(web_app, google, clock)
    google.claims = {"sub": "someone-else", "email": OWNER_EMAIL}
    assert other.google_callback().status_code == 403
    assert other.client.get("/settings").status_code == 401

    # 계정 ID가 같아도 소유자 이메일이 아니면 DB에 닿기 전에 거절한다
    google.claims = {"email": "renamed@example.com"}
    assert other.google_callback().status_code == 403
    google.claims = {}
    assert other.google_callback().headers["location"] == "/auth/mfa"


def test_state_mismatch_is_rejected(browser, session):
    query = browser.start_google()
    query["state"] = "forged"
    response = browser.google_callback(query)
    assert response.headers["location"] == "/auth/login?error=1"
    assert session.get(Owner, 1) is None


def test_callback_without_flow_cookie_is_rejected(browser, session):
    query = browser.start_google()
    browser.client.cookies.clear()
    assert browser.google_callback(query).headers["location"] == "/auth/login?error=1"


def test_expired_flow_is_rejected(browser, clock):
    query = browser.start_google()
    clock.advance(minutes=11)
    assert browser.google_callback(query).headers["location"] == "/auth/login?error=1"


def test_user_cancelling_at_google_returns_to_login(browser):
    query = browser.start_google()
    response = browser.client.get(f"/auth/callback?state={query['state']}&error=access_denied")
    assert response.headers["location"] == "/auth/login?error=1"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-client"},
        {"iss": "https://evil.example.com"},
        {"exp": 1000, "iat": 900},
        {"nonce": "wrong-nonce"},
        {"sub": None},
    ],
    ids=["다른 앱용 토큰", "다른 발급자", "만료", "nonce 불일치", "sub 없음"],
)
def test_invalid_id_tokens_are_rejected(browser, google, session, claims):
    google.claims = claims
    assert browser.google_callback().headers["location"] == "/auth/login?error=1"
    assert session.get(Owner, 1) is None


def test_unsigned_and_symmetric_tokens_are_rejected(browser, google, session):
    import jwt

    query = browser.start_google()
    claims = google.default_claims()
    for token in (
        jwt.encode(claims, None, algorithm="none"),
        jwt.encode(claims, "x" * 64, algorithm="HS256", headers={"kid": google.kid}),
    ):
        google.token_override = token
        query = browser.start_google()
        assert browser.google_callback(query).headers["location"] == "/auth/login?error=1"
    assert session.get(Owner, 1) is None


def test_token_signed_by_another_key_is_rejected(browser, google, session):
    from web_support import FakeGoogle

    browser.start_google()
    impostor = FakeGoogle()
    impostor.nonce = google.nonce
    impostor.kid = google.kid
    google.token_override = impostor.mint()
    query = {"state": browser.start_google()["state"]}
    assert browser.google_callback(query).headers["location"] == "/auth/login?error=1"


def test_token_endpoint_failure_is_handled(browser, google):
    google.fail_token_endpoint = True
    assert browser.google_callback().headers["location"] == "/auth/login?error=1"


def _statements_during(engine, work) -> list[str]:
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    return captured


def test_failed_logins_never_touch_the_database(browser, google, engine):
    # 미인증 요청으로 무료 DB를 깨우거나 채울 수 없어야 한다
    query = browser.start_google()
    google.fail_token_endpoint = True
    assert _statements_during(engine, lambda: [browser.google_callback(query) for _ in range(3)]) == []

    google.fail_token_endpoint = False
    google.claims = {"email": "attacker@example.com", "sub": "attacker"}
    query = browser.start_google()
    assert _statements_during(engine, lambda: browser.google_callback(query)) == []


# --- OTP 등록


def test_enrollment_rejects_wrong_code_then_accepts_right_one(browser, session):
    browser.google_callback()
    page = browser.client.get("/auth/enroll")
    assert "<svg" in page.text

    response = browser.post("/auth/enroll", code="000000")
    assert response.headers["location"] == "/auth/enroll?error=code"
    assert session.get(Owner, 1).totp_enabled_at is None

    seed, codes = browser.enroll()
    assert len(codes) == 10
    assert session.get(Owner, 1).totp_enabled_at is not None
    assert browser.client.get("/").status_code == 200


def test_seed_is_stored_encrypted(signed_in, session):
    _, seed, _ = signed_in
    rows = session.scalars(select(SecretValue).where(SecretValue.name == "totp_seed")).all()
    assert len(rows) == 1
    assert seed.encode() not in rows[0].ciphertext
    assert session.scalars(select(AuthSession.pending_totp).where(AuthSession.pending_totp.is_not(None))).all() == []


def test_pending_seed_is_encrypted_and_per_session(browser, web_app, google, clock, session):
    browser.google_callback()
    first = SECRET_RE.search(browser.client.get("/auth/enroll").text).group(1)
    stored = session.scalars(select(AuthSession.pending_totp).where(AuthSession.pending_totp.is_not(None))).one()
    assert first.encode() not in stored

    second = Browser(web_app, google, clock)
    second.google_callback()
    assert SECRET_RE.search(second.client.get("/auth/enroll").text).group(1) != first


def test_other_sessions_seed_is_useless_after_owner_enrolls(browser, web_app, google, clock):
    # 첫 설정 중에 다른 세션이 QR을 봤더라도 소유자가 등록하면 그 시드는 쓸 수 없다
    attacker = Browser(web_app, google, clock)
    attacker.google_callback()
    attacker_seed = SECRET_RE.search(attacker.client.get("/auth/enroll").text).group(1)

    browser.google_callback()
    browser.enroll()

    late = Browser(web_app, google, clock)
    late.google_callback()
    clock.advance(seconds=30)
    assert late.post("/auth/mfa", code=late.otp(attacker_seed)).headers["location"] == "/auth/mfa?error=code"
    assert attacker.client.get("/auth/enroll", headers={"accept": "text/html"}).headers["location"] == "/auth/login"


def test_recovery_codes_are_stored_as_hashes(signed_in, session):
    _, _, codes = signed_in
    stored = session.scalars(select(RecoveryCode.code_hash)).all()
    assert len(stored) == 10
    for code in codes:
        assert code not in stored
        assert code.replace("-", "") not in stored


def test_enrollment_is_closed_once_otp_exists(signed_in, web_app, google, clock):
    other = Browser(web_app, google, clock)
    other.google_callback()
    assert other.client.get("/auth/enroll").headers["location"] == "/auth/mfa"
    assert other.client.post("/auth/enroll", data={"csrf_token": "x", "code": "1"}).status_code == 403


# --- OTP 로그인


def test_login_requires_both_google_and_otp(signed_in, web_app, google, clock):
    _, seed, _ = signed_in
    other = Browser(web_app, google, clock)
    assert other.client.get("/settings", headers={"accept": "text/html"}).headers["location"] == "/auth/login"

    other.google_callback()
    assert other.client.get("/settings", headers={"accept": "text/html"}).headers["location"] == "/auth/mfa"

    clock.advance(seconds=30)
    response = other.post("/auth/mfa", code=other.otp(seed))
    assert response.headers["location"] == "/"
    assert other.client.get("/settings").status_code == 200


def test_otp_code_cannot_be_reused(signed_in, web_app, google, clock):
    _, seed, _ = signed_in
    first = Browser(web_app, google, clock)
    first.login(seed)
    code_time_seed = first.otp(seed)

    second = Browser(web_app, google, clock)
    second.google_callback()
    response = second.post("/auth/mfa", code=code_time_seed)
    assert response.headers["location"] == "/auth/mfa?error=code"


def test_otp_allows_one_step_of_clock_drift(signed_in, web_app, google, clock):
    _, seed, _ = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    clock.advance(seconds=60)
    assert device.post("/auth/mfa", code=device.otp(seed, offset_steps=-1)).headers["location"] == "/"

    device = Browser(web_app, google, clock)
    device.google_callback()
    assert device.post("/auth/mfa", code=device.otp(seed, offset_steps=2)).headers["location"] == "/auth/mfa?error=code"


def test_repeated_failures_lock_out_even_correct_codes(signed_in, web_app, google, clock, session):
    _, seed, _ = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    clock.advance(seconds=30)
    for _ in range(3):
        assert device.post("/auth/mfa", form_page="/auth/mfa", code="000000").headers["location"] == "/auth/mfa?error=code"

    assert session.get(Owner, 1).locked_until is not None
    response = device.post("/auth/mfa", code=device.otp(seed))
    assert response.headers["location"] == "/auth/mfa?error=locked"
    assert "30초 뒤에 다시 시도" in device.client.get("/auth/mfa?error=locked").text
    clock.advance(seconds=31)
    assert device.post("/auth/mfa", code=device.otp(seed)).headers["location"] == "/"


def test_otp_wait_grows_with_each_failure(signed_in, web_app, google, clock, session):
    _, seed, _ = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    owner = session.get(Owner, 1)
    # 앞의 두 번은 오타로 보고, 그다음부터 30초, 1분, 5분, 15분, 1시간, 6시간이다
    expected = [None, None, 30, 60, 300, 900, 3600, 21600, 21600]
    for wait in expected:
        token = device.csrf("/auth/mfa")
        device.client.post("/auth/mfa", data={"csrf_token": token, "code": "000000"})
        if wait is None:
            assert owner.locked_until is None
        else:
            assert owner.locked_until == clock() + timedelta(seconds=wait)
            clock.advance(seconds=wait + 1)
            # 임시 세션은 5분이면 끝나므로 오래 기다린 뒤에는 다시 로그인한다
            device = Browser(web_app, google, clock)
            device.google_callback()
    assert owner.failed_attempts == len(expected)


def test_refused_attempts_do_not_lengthen_the_wait(signed_in, web_app, google, clock, session):
    device = Browser(web_app, google, clock)
    device.google_callback()
    for _ in range(3):
        device.post("/auth/mfa", form_page="/auth/mfa", code="000000")
    owner = session.get(Owner, 1)
    until = owner.locked_until
    for _ in range(5):
        assert device.post("/auth/mfa", form_page="/auth/mfa", code="000000").headers["location"] == "/auth/mfa?error=locked"
    assert owner.locked_until == until
    assert owner.failed_attempts == 3


def test_correct_code_resets_the_wait(signed_in, web_app, google, clock, session):
    _, seed, _ = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    for _ in range(3):
        device.post("/auth/mfa", form_page="/auth/mfa", code="000000")
    clock.advance(seconds=31)
    assert device.post("/auth/mfa", code=device.otp(seed)).headers["location"] == "/"
    owner = session.get(Owner, 1)
    assert owner.failed_attempts == 0 and owner.locked_until is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(30, "30초"), (61, "1분 1초"), (60, "1분"), (900, "15분"), (3600, "1시간"), (21600, "6시간"), (3660, "1시간 1분")],
)
def test_wait_text_reads_naturally(clock, seconds, expected):
    from app.auth import otp

    owner = Owner(id=1, google_sub="x", email="x", failed_attempts=3, locked_until=clock() + timedelta(seconds=seconds))
    assert otp.wait_text(owner, clock()) == expected
    owner.locked_until = clock() - timedelta(seconds=1)
    assert otp.wait_text(owner, clock()) == ""


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 456", "١٢٣٤٥٦"])
def test_malformed_otp_is_rejected(signed_in, web_app, google, clock, bad):
    device = Browser(web_app, google, clock)
    device.google_callback()
    assert device.post("/auth/mfa", code=bad).headers["location"] == "/auth/mfa?error=code"


def test_failures_are_audited_without_secrets(signed_in, web_app, google, clock, session):
    device = Browser(web_app, google, clock)
    device.google_callback()
    device.post("/auth/mfa", code="111111")
    events = session.scalars(select(AuditLog).where(AuditLog.event == "otp_failed")).all()
    assert len(events) == 1
    assert "111111" not in str(events[0].detail)


# --- 세션 수명과 쿠키


def test_session_cookie_attributes(browser):
    response = browser.google_callback()
    cookie = next(v for k, v in response.headers.multi_items() if k == "set-cookie" and v.startswith("__Host-session="))
    lowered = cookie.lower()
    assert "secure" in lowered
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "path=/" in lowered
    assert "domain" not in lowered
    assert "max-age" not in lowered


def test_token_rotates_when_otp_succeeds(signed_in, web_app, google, clock):
    _, seed, _ = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    temporary = device.client.cookies.get("__Host-session")
    clock.advance(seconds=30)
    device.post("/auth/mfa", code=device.otp(seed))
    assert device.client.cookies.get("__Host-session") != temporary

    stolen = Browser(web_app, google, clock)
    stolen.client.cookies.set("__Host-session", temporary)
    assert stolen.client.get("/settings").status_code == 401


def test_only_token_hash_is_stored(signed_in, session):
    browser, _, _ = signed_in
    token = browser.client.cookies.get("__Host-session").split(".")[0]
    assert token not in session.scalars(select(AuthSession.token_hash)).all()


def test_pre_mfa_session_expires_in_five_minutes(signed_in, web_app, google, clock):
    device = Browser(web_app, google, clock)
    device.google_callback()
    clock.advance(minutes=5, seconds=1)
    assert device.client.get("/auth/mfa", headers={"accept": "text/html"}).headers["location"] == "/auth/login"


def test_idle_timeout(signed_in, clock):
    browser, _, _ = signed_in
    clock.advance(minutes=29)
    assert browser.client.get("/").status_code == 200
    clock.advance(minutes=29)
    assert browser.client.get("/").status_code == 200  # 사용하면 연장된다
    clock.advance(minutes=31)
    assert browser.client.get("/").status_code == 401


def test_absolute_timeout_ignores_activity(signed_in, clock):
    browser, _, _ = signed_in
    for _ in range(24):  # 29분 × 24 = 11시간 36분
        clock.advance(minutes=29)
        assert browser.client.get("/").status_code == 200
    clock.advance(minutes=29)  # 12시간 5분
    assert browser.client.get("/").status_code == 401


def test_forged_cookie_is_rejected_without_touching_the_database(browser, engine, session):
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        browser.client.cookies.set("__Host-session", "forged-token.forged-signature")
        assert browser.client.get("/settings").status_code == 401
        browser.client.cookies.set("__Host-session", "no-separator")
        assert browser.client.get("/settings").status_code == 401
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert statements == []


def test_logout_revokes_session_and_clears_browser_data(signed_in, web_app, google, clock):
    browser, _, _ = signed_in
    cookie = browser.client.cookies.get("__Host-session")
    response = browser.post("/auth/logout", form_page="/")
    assert response.headers["location"] == "/auth/login"
    assert "cookies" in response.headers["clear-site-data"]
    assert "storage" in response.headers["clear-site-data"]

    replay = Browser(web_app, google, clock)
    replay.client.cookies.set("__Host-session", cookie)
    assert replay.client.get("/").status_code == 401


# --- CSRF


def test_state_changing_request_needs_token(signed_in):
    browser, _, _ = signed_in
    assert browser.client.post("/auth/logout").status_code == 403
    assert browser.client.post("/auth/logout", data={"csrf_token": "wrong"}).status_code == 403
    assert browser.client.get("/").status_code == 200  # 여전히 로그인 상태


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example.com"},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
    ],
    ids=["다른 Origin", "null Origin", "cross-site", "same-site"],
)
def test_cross_site_request_is_blocked_even_with_valid_token(signed_in, headers):
    browser, _, _ = signed_in
    token = browser.csrf("/")
    response = browser.client.post("/auth/logout", data={"csrf_token": token}, headers=headers)
    assert response.status_code == 403


def test_same_origin_request_passes(signed_in):
    browser, _, _ = signed_in
    token = browser.csrf("/")
    response = browser.client.post(
        "/auth/logout",
        data={"csrf_token": token},
        headers={"origin": "https://testserver", "sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 303


def test_csrf_token_header_is_accepted(signed_in):
    browser, _, _ = signed_in
    token = browser.csrf("/")
    assert browser.client.post("/auth/logout", headers={"x-csrf-token": token}).status_code == 303


# --- 복구 코드


def test_recovery_code_lets_user_reenroll(signed_in, web_app, google, clock, session):
    browser, old_seed, codes = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    response = device.post("/auth/recovery", form_page="/auth/mfa", code=codes[0])
    assert response.headers["location"] == "/auth/enroll"

    # 복구 코드만으로는 데이터에 닿지 못하고 OTP를 다시 등록해야 한다
    assert device.client.get("/settings", headers={"accept": "text/html"}).headers["location"] == "/auth/mfa"
    assert device.client.get("/auth/mfa").headers["location"] == "/auth/enroll"

    new_seed, new_codes = device.enroll()
    assert new_seed != old_seed
    assert device.client.get("/settings").status_code == 200
    assert set(new_codes).isdisjoint(codes)
    # 새 OTP를 등록하면 기존 기기는 로그아웃된다
    assert browser.client.get("/").status_code == 401


def test_otp_stays_valid_until_recovery_completes(signed_in, web_app, google, clock, session):
    # 복구 코드를 쓴 뒤 등록을 끝내지 않아도 Google 로그인만으로 OTP를 바꿔칠 수 없어야 한다
    browser, seed, codes = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    device.post("/auth/recovery", form_page="/auth/mfa", code=codes[0])

    attacker = Browser(web_app, google, clock)
    attacker.google_callback()
    assert attacker.client.get("/auth/enroll").headers["location"] == "/auth/mfa"
    assert attacker.post("/auth/enroll", form_page="/auth/mfa", code="123456").headers["location"] == "/auth/mfa"

    assert browser.client.get("/").status_code == 200
    clock.advance(seconds=30)
    assert attacker.post("/auth/mfa", code=attacker.otp(seed)).headers["location"] == "/"


def test_otp_operations_lock_the_owner_row(signed_in, web_app, google, clock, engine):
    # 동시에 보낸 코드들이 실패 횟수를 덮어써 잠금을 피하지 못해야 한다
    browser, seed, codes = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    token = device.csrf("/auth/mfa")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for path, code in (("/auth/mfa", "000000"), ("/auth/recovery", "wrong")):
            statements.clear()
            device.client.post(path, data={"csrf_token": token, "code": code})
            assert any("FROM owner" in s and "FOR UPDATE" in s for s in statements), path
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_recovery_code_is_single_use_and_old_otp_dies(signed_in, web_app, google, clock):
    _, old_seed, codes = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    device.post("/auth/recovery", form_page="/auth/mfa", code=codes[0])
    device.enroll()

    again = Browser(web_app, google, clock)
    again.google_callback()
    assert again.post("/auth/recovery", form_page="/auth/mfa", code=codes[0]).headers["location"] == "/auth/mfa?error=recovery"
    assert again.post("/auth/recovery", form_page="/auth/mfa", code=codes[1]).headers["location"] == "/auth/mfa?error=recovery"
    clock.advance(seconds=30)
    assert again.post("/auth/mfa", code=again.otp(old_seed)).headers["location"] == "/auth/mfa?error=code"


def test_recovery_code_accepts_formatting_differences(signed_in, web_app, google, clock):
    _, _, codes = signed_in
    device = Browser(web_app, google, clock)
    device.google_callback()
    sloppy = f"  {codes[0].replace('-', ' ').upper()} "
    assert device.post("/auth/recovery", form_page="/auth/mfa", code=sloppy).headers["location"] == "/auth/enroll"


def test_recovery_attempts_count_toward_lockout(signed_in, web_app, google, clock, session):
    device = Browser(web_app, google, clock)
    device.google_callback()
    for _ in range(5):
        device.post("/auth/recovery", form_page="/auth/mfa", code="wrong")
    assert session.get(Owner, 1).locked_until is not None
    _, _, codes = signed_in
    assert device.post("/auth/recovery", form_page="/auth/mfa", code=codes[0]).headers["location"] == "/auth/mfa?error=locked"


def test_otp_failures_raise_a_warning(signed_in, web_app, google, clock):
    # Google을 통과한 누군가가 OTP에서 막혔다면 Google 계정이 뚫렸다는 신호다
    browser, _, _ = signed_in
    other = Browser(web_app, google, clock)
    other.google_callback()
    other.post("/auth/mfa", code="000000")
    home = browser.client.get("/").text
    assert "OTP 입력 실패가 1회" in home
    assert "본인이 맞는지 확인이 필요합니다." in home
    assert "비밀번호를 바꾸고" not in home
    clock.advance(days=8)
    other = Browser(web_app, google, clock)
    other.login(signed_in[1])
    assert "OTP 입력 실패" not in other.client.get("/").text


def test_otp_warning_can_be_dismissed_until_the_next_failure(signed_in, web_app, google, clock, session):
    browser, _, _ = signed_in
    other = Browser(web_app, google, clock)
    other.google_callback()
    other.post("/auth/mfa", code="000000")
    assert "확인했어요" in browser.client.get("/").text

    token = browser.csrf("/")
    response = browser.client.post(
        "/settings/warnings/otp_failures_seen/dismiss", data={"csrf_token": token, "back": "/"}
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "OTP 입력 실패" not in browser.client.get("/").text
    assert "OTP 입력 실패" not in browser.client.get("/settings").text
    # 기록은 남고, 확인한 뒤의 새 실패는 다시 알린다
    assert session.scalars(select(AuditLog).where(AuditLog.event == "otp_failed")).first() is not None
    assert session.scalars(select(AuditLog).where(AuditLog.event == "warning_dismissed")).first() is not None
    clock.advance(seconds=1)
    other.post("/auth/mfa", form_page="/auth/mfa", code="000000")
    assert "OTP 입력 실패가 1회" in browser.client.get("/").text


def test_only_past_events_offer_a_dismiss_button(signed_in):
    browser, _, _ = signed_in
    html = browser.client.get("/").text
    assert "아직 백업이 없습니다" in html
    assert "확인했어요" not in html


def test_dismiss_needs_a_token_and_a_known_warning(signed_in):
    browser, _, _ = signed_in
    assert browser.client.post("/settings/warnings/otp_failures_seen/dismiss", data={}).status_code == 403
    assert browser.post("/settings/warnings/nope/dismiss", form_page="/settings").status_code == 404
    response = browser.post("/settings/warnings/otp_failures_seen/dismiss", form_page="/settings", back="https://evil.example")
    assert response.headers["location"] == "/"


# --- 보안 헤더


@pytest.mark.parametrize("path", ["/auth/login", "/healthz", "/settings", "/no-such-page"])
def test_security_headers_on_every_response(browser, path):
    headers = browser.client.get(path).headers
    assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"  # no-referrer면 실제 브라우저의 POST가 전부 막힌다
    assert headers["x-frame-options"] == "DENY"
    assert "max-age" in headers["strict-transport-security"]


def test_csp_forbids_inline_and_external_code():
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "default-src 'none'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY


def test_pages_have_no_inline_scripts_or_styles(signed_in):
    browser, _, _ = signed_in
    for path in ("/", "/settings"):
        html = browser.client.get(path).text
        assert not re.search(r"<script(?![^>]*\ssrc=)", html)
        assert "<style" not in html
        assert " style=" not in html
        assert " onclick=" not in html


def test_errors_do_not_leak_values(web_app, signed_in, caplog):
    browser, _, _ = signed_in

    @web_app.get("/explode")
    def explode():
        raise ValueError("비밀가맹점 987654원")

    with caplog.at_level(logging.DEBUG):
        response = browser.client.get("/explode")
    assert response.status_code == 500
    assert "비밀가맹점" not in response.text
    assert "비밀가맹점" not in caplog.text
    assert "ValueError" in caplog.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


def test_user_agent_is_escaped_in_device_list(web_app, google, clock):
    device = Browser(web_app, google, clock)
    device.client.headers["user-agent"] = '<script>alert("x")</script>'
    device.google_callback()
    device.enroll()
    html = device.client.get("/settings").text
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html


def test_session_timeouts_match_the_plan():
    from app.auth import sessions

    assert sessions.IDLE_TIMEOUT == timedelta(minutes=30)
    assert sessions.ABSOLUTE_TIMEOUT == timedelta(hours=12)
    assert sessions.PRE_MFA_TIMEOUT == timedelta(minutes=5)
    assert sessions.STEP_UP_WINDOW == timedelta(minutes=5)
