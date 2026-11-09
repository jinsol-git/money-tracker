"""설정 화면, 메일 수신 API, 백업."""

from __future__ import annotations

import base64
import json
from datetime import date

import pytest
from sqlalchemy import create_engine, event, make_url, select, text

from app.db import normalize_url
from app.ingest.importer import MAX_QUARANTINED_FILES
from app.ingest.mail_auth import rejection_reason, signature_is_valid
from app.ledger.models import AssetSnapshot, ImportBatch, Txn
from app.ops import backup
from app.ops_models import AuditLog, AuthSession, Owner, SecretValue
from app.security.crypto import CryptoError
from app.settings.secrets import secret_versions
from fixtures import make_export as fx
from web_support import GOOD_AUTH_RESULTS, HMAC_KEY, OWNER_EMAIL, Browser, make_settings

FILE_PASSWORD = "local-test-file-password"
REAL_SENDER = "banksalad <export-noreply@banksalad.com>"
ROWS = [
    fx.tx("2026-02-01 09:00:00", "수입", "급여", "미분류", "회사", 1_000_000, "입출금통장"),
    fx.tx("2026-02-02 10:00:00", "지출", "식비", "한식", "밥집", -30_000, "어떤 삼성카드"),
]
ASSETS = [fx.AssetRow("자유입출금 자산", "입출금통장", 1_000_000)]


def export_zip(password: str = FILE_PASSWORD, rows=ROWS, inner="2026-01-01~2026-02-28.xlsx", tamper=None) -> bytes:
    return fx.zip_aes(fx.build_xlsx(rows=rows, assets=ASSETS, liabilities=[], tamper=tamper), password, inner)


def mail_payload(
    data: bytes,
    message_id: str = "m1",
    auth_results: str = GOOD_AUTH_RESULTS,
    to: list[str] | None = None,
    sender: list[str] | None = None,
) -> dict:
    return {
        "message_id": message_id,
        "auth_results": auth_results,
        "to_headers": [OWNER_EMAIL] if to is None else to,
        "from_headers": [REAL_SENDER] if sender is None else sender,
        "attachment": base64.urlsafe_b64encode(data).rstrip(b"=").decode(),
    }


def set_password(browser, value: str = FILE_PASSWORD, **extra):
    return browser.post("/settings/file-password", form_page="/settings", file_password=value, **extra)


# --- 메일 인증 판별


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (GOOD_AUTH_RESULTS, True),
        ("mx.google.com; dkim=fail header.i=@banksalad.com; dmarc=pass header.from=banksalad.com", False),
        ("mx.google.com; dkim=pass header.i=@banksalad.com; dmarc=fail header.from=banksalad.com", False),
        ("mx.google.com; dkim=pass header.i=@banksalad.com", False),
        ("mx.google.com; dmarc=pass header.from=banksalad.com", False),
        ("mx.google.com; dkim=pass header.i=@evil.com; dmarc=pass header.from=evil.com", False),
        ("mx.google.com; dkim=pass header.i=@banksalad.com.evil.com; dmarc=pass header.from=banksalad.com.evil.com", False),
        ("mx.google.com; dkim=pass header.i=@notbanksalad.com; dmarc=pass header.from=notbanksalad.com", False),
        ("evil.example.com; dkim=pass header.i=@banksalad.com; dmarc=pass header.from=banksalad.com", False),
        ("mx.google.com.evil.com; dkim=pass header.i=@banksalad.com; dmarc=pass header.from=banksalad.com", False),
        ("mx.google.com; spf=pass (dkim=pass header.i=@banksalad.com) smtp.mailfrom=x; dmarc=pass header.from=banksalad.com", False),
        ("", False),
        ("mx.google.com", False),
        # 발신자가 정할 수 있는 따옴표·주석 안에 결과를 숨겨 넣는 공격
        ('mx.google.com; spf=pass smtp.mailfrom="a;dkim=pass header.i=@banksalad.com;dmarc=pass header.from=banksalad.com;"@evil', False),
        ("mx.google.com; spf=pass (x; dkim=pass header.i=@banksalad.com; dmarc=pass header.from=banksalad.com; y) smtp.mailfrom=z", False),
        ('mx.google.com; spf=pass smtp.mailfrom="unterminated; dkim=pass header.i=@banksalad.com; dmarc=pass header.from=banksalad.com', False),
        # 따옴표와 주석이 있어도 진짜 결과는 통과해야 한다
        ('mx.google.com; dkim=pass (2048-bit key) header.i=@banksalad.com header.b="a;b"; dmarc=pass (p=NONE) header.from=banksalad.com', True),
    ],
)
def test_mail_authentication_rules(header, expected):
    assert signature_is_valid(header) is expected


@pytest.mark.parametrize(
    ("to", "sender", "expected"),
    [
        ([OWNER_EMAIL], [REAL_SENDER], None),
        ([OWNER_EMAIL.upper()], [REAL_SENDER], None),
        ([f"홍길동 <{OWNER_EMAIL}>"], ["export-noreply@banksalad.com"], None),
        (["attacker@example.com"], [REAL_SENDER], "recipient"),
        ([OWNER_EMAIL, "attacker@example.com"], [REAL_SENDER], "recipient"),
        (["attacker@example.com", OWNER_EMAIL], [REAL_SENDER], "recipient"),
        ([f"{OWNER_EMAIL}, attacker@example.com"], [REAL_SENDER], "recipient"),
        ([], [REAL_SENDER], "recipient"),
        ([OWNER_EMAIL], ["other@banksalad.com"], "sender"),
        ([OWNER_EMAIL], ["export-noreply@banksalad.com.evil.com"], "sender"),
        ([OWNER_EMAIL], [REAL_SENDER, REAL_SENDER], "sender"),
        ([OWNER_EMAIL], [], "sender"),
    ],
)
def test_mail_must_be_addressed_to_the_owner(to, sender, expected):
    assert rejection_reason(GOOD_AUTH_RESULTS, to, sender, OWNER_EMAIL) == expected


def test_bad_signature_comes_first():
    assert rejection_reason("", [OWNER_EMAIL], [REAL_SENDER], OWNER_EMAIL) == "signature"


# --- 수신 API 서명


def test_valid_mail_is_imported_end_to_end(browser, signed_in, session):
    set_password(browser)
    response = browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["counts"]["new"] == 2
    assert len(session.scalars(select(Txn)).all()) == 2
    assert session.scalars(select(AssetSnapshot)).one().total_assets == 1_000_000


def test_same_message_twice_is_duplicate(browser, signed_in):
    set_password(browser)
    data = export_zip()
    assert browser.signed_post("/api/ingest/banksalad", mail_payload(data)).json()["status"] == "applied"
    assert browser.signed_post("/api/ingest/banksalad", mail_payload(data)).json()["status"] == "duplicate"


def test_wrong_signature_is_rejected_without_touching_the_database(browser, engine):
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        response = browser.signed_post("/api/ingest/banksalad", mail_payload(b"x"), key="k" * 40)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert response.status_code == 401
    assert statements == []


@pytest.mark.parametrize("skew", [-301, 301], ids=["오래된 요청", "미래 시각"])
def test_timestamp_outside_window_is_rejected(browser, clock, skew):
    stamp = int(clock().timestamp()) + skew
    assert browser.signed_post("/api/ingest/banksalad", mail_payload(b"x"), timestamp=stamp).status_code == 401


def test_signature_covers_the_body(browser, clock):
    from app.auth.gate import sign_body

    stamp = str(int(clock().timestamp()))
    signature = sign_body(HMAC_KEY, stamp, "/api/ingest/heartbeat", b'{"message_id": "original"}')
    response = browser.client.post(
        "/api/ingest/heartbeat",
        content=b'{"message_id": "tampered"}',
        headers={"X-Ledger-Timestamp": stamp, "X-Ledger-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 401


def test_signature_cannot_be_reused_on_another_api(browser, clock):
    from app.auth.gate import sign_body

    stamp = str(int(clock().timestamp()))
    body = b"{}"
    signature = sign_body(HMAC_KEY, stamp, "/api/ingest/heartbeat", body)
    headers = {"X-Ledger-Timestamp": stamp, "X-Ledger-Signature": signature, "Content-Type": "application/json"}
    assert browser.client.post("/api/ingest/heartbeat", content=body, headers=headers).status_code == 200
    assert browser.client.post("/api/backup/export", content=body, headers=headers).status_code == 401


def test_missing_headers_are_rejected(browser):
    assert browser.client.post("/api/ingest/heartbeat", json={}).status_code == 401
    assert browser.client.post("/api/backup/export", json={}).status_code == 401


def test_session_cookie_does_not_open_signed_api(signed_in):
    browser, _, _ = signed_in
    assert browser.client.post("/api/backup/export", json={}).status_code == 401


def test_oversized_body_is_rejected(browser):
    response = browser.client.post(
        "/api/ingest/heartbeat",
        content=b"x" * 10,
        headers={"Content-Length": str(7 * 1024 * 1024), "Content-Type": "application/json"},
    )
    assert response.status_code in {400, 413, 422}


# --- 위조·악성 파일


@pytest.mark.parametrize(
    "header",
    ["mx.google.com; dkim=fail header.i=@banksalad.com; dmarc=fail header.from=banksalad.com", ""],
    ids=["DKIM 실패", "헤더 없음"],
)
def test_mail_failing_authentication_is_not_imported(browser, signed_in, session, header):
    set_password(browser)
    response = browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip(), auth_results=header))
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert session.scalars(select(Txn)).all() == []
    assert session.scalars(select(ImportBatch)).all() == []
    assert "인증되지 않은 메일 1통" in browser.client.get("/settings").text


def test_someone_elses_real_export_is_rejected(browser, signed_in, session):
    # 공격자가 자기 내보내기 메일을 서명 그대로 재전송해도 수신자가 달라 받지 않는다.
    # 비밀번호까지 같아도 장부가 오염되면 안 된다.
    set_password(browser)
    for to in (["attacker@example.com"], [OWNER_EMAIL, "attacker@example.com"]):
        body = browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip(), to=to)).json()
        assert body == {"status": "rejected", "reason": "recipient"}
    assert session.scalars(select(Txn)).all() == []


def test_rejected_mail_cannot_grow_the_database(browser, signed_in, session):
    for index in range(5):
        browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip(), f"m{index}", auth_results=""))
    assert session.scalars(select(ImportBatch)).all() == []
    assert "인증되지 않은 메일 5통" in browser.client.get("/settings").text
    assert session.scalars(select(AuditLog).where(AuditLog.event == "ingest")).all() == []


def test_mail_without_attachment_is_counted_not_imported(browser, signed_in, session):
    payload = mail_payload(b"")
    del payload["attachment"]
    assert browser.signed_post("/api/ingest/banksalad", payload).json()["reason"] == "no_attachment"
    assert session.scalars(select(ImportBatch)).all() == []


def test_unencrypted_zip_is_rejected(browser, signed_in, session):
    set_password(browser)
    plain = fx.zip_plain(fx.build_xlsx(rows=ROWS, assets=ASSETS, liabilities=[]))
    body = browser.signed_post("/api/ingest/banksalad", mail_payload(plain)).json()
    assert body["status"] == "quarantined"
    assert body["reason"] == "rejected:unencrypted"
    assert session.scalars(select(Txn)).all() == []


def test_zip_bomb_is_rejected(browser, signed_in, session):
    from app.ingest.archive import MAX_UNPACKED_BYTES

    set_password(browser)
    bomb = fx.zip_aes(b"\0" * (MAX_UNPACKED_BYTES + 1), FILE_PASSWORD)
    body = browser.signed_post("/api/ingest/banksalad", mail_payload(bomb)).json()
    assert body["reason"] == "rejected:too_large"


def test_format_change_is_quarantined(browser, signed_in, session):
    def tamper(workbook):
        workbook["가계부 내역"]["G1"] = "금액(원)"

    set_password(browser)
    body = browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip(tamper=tamper))).json()
    assert body["reason"] == "format_changed"
    assert session.scalars(select(Txn)).all() == []


def test_invalid_base64_is_a_client_error(browser, signed_in):
    payload = mail_payload(b"")
    payload["attachment"] = "not base64!!"
    assert browser.signed_post("/api/ingest/banksalad", payload).status_code == 400


def test_standard_base64_is_accepted_too(browser, signed_in):
    set_password(browser)
    payload = mail_payload(b"")
    payload["attachment"] = base64.b64encode(export_zip()).decode()
    assert browser.signed_post("/api/ingest/banksalad", payload).json()["status"] == "applied"


# --- 시간 표시


def test_times_are_shown_in_korean_time(signed_in, clock):
    browser, _, _ = signed_in  # 시계는 UTC 03:00에 멈춰 있으므로 한국 시간으로는 12:00이다
    set_password(browser)
    page = browser.client.get("/settings").text
    assert "2026-11-05 12:00" in page
    assert "2026-11-05 03:00" not in page


# --- 파일 비밀번호


def test_password_is_write_only_and_encrypted(signed_in, session):
    browser, _, _ = signed_in
    response = set_password(browser)
    assert response.headers["location"] == "/settings?msg=password_saved"

    page = browser.client.get("/settings?msg=password_saved").text
    assert FILE_PASSWORD not in page
    assert "설정됨" in page

    rows = session.scalars(select(SecretValue).where(SecretValue.name == "file_password")).all()
    assert len(rows) == 1
    assert FILE_PASSWORD.encode() not in rows[0].ciphertext
    assert secret_versions(session, make_settings(), "file_password") == [FILE_PASSWORD]

    audit = session.scalars(select(AuditLog)).all()
    assert FILE_PASSWORD not in str([entry.detail for entry in audit])


def test_only_current_and_previous_passwords_are_kept(signed_in, session):
    browser, _, _ = signed_in
    for value in ("first-pass", "second-pass", "third-pass"):
        set_password(browser, value)
    assert secret_versions(session, make_settings(), "file_password") == ["third-pass", "second-pass"]


def test_previous_password_still_opens_older_export(browser, signed_in, session):
    set_password(browser, "old-password")
    set_password(browser, "new-password")
    data = export_zip(password="old-password")
    assert browser.signed_post("/api/ingest/banksalad", mail_payload(data)).json()["status"] == "applied"


@pytest.mark.parametrize("value", ["", "abc", "x" * 129])
def test_password_length_is_validated(signed_in, session, value):
    browser, _, _ = signed_in
    assert set_password(browser, value).headers["location"] == "/settings?error=password_length"
    assert session.scalars(select(SecretValue).where(SecretValue.name == "file_password")).all() == []


def test_password_change_needs_recent_otp(signed_in, clock, session):
    browser, seed, _ = signed_in
    assert "otp_code" not in browser.client.get("/settings").text  # 방금 인증했으므로 묻지 않는다

    clock.advance(minutes=6)
    assert "otp_code" in browser.client.get("/settings").text
    assert set_password(browser, otp_code="000000").headers["location"] == "/settings?error=otp"
    assert session.scalars(select(SecretValue).where(SecretValue.name == "file_password")).all() == []

    assert set_password(browser, otp_code=browser.otp(seed)).headers["location"] == "/settings?msg=password_saved"


def test_step_up_failures_count_toward_lockout(signed_in, clock, session):
    browser, seed, _ = signed_in
    clock.advance(minutes=6)
    for _ in range(5):
        set_password(browser, otp_code="000000")
    assert session.get(Owner, 1).locked_until is not None
    assert set_password(browser, otp_code=browser.otp(seed)).headers["location"] == "/settings?error=locked"


def test_mail_waits_for_password_then_is_processed_automatically(browser, signed_in, session):
    response = browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    assert response.json()["reason"] == "wrong_password"
    assert session.scalars(select(Txn)).all() == []
    assert "비밀번호가 맞지 않음" in browser.client.get("/settings").text

    location = set_password(browser).headers["location"]
    assert location == "/settings?msg=reprocessed"
    assert len(session.scalars(select(Txn)).all()) == 2

    batch = session.scalars(select(ImportBatch).where(ImportBatch.status == "resolved")).one()
    assert batch.raw_zip is None
    assert batch.summary["resolved_by"]
    assert "처리 대기 파일" not in browser.client.get("/settings").text


def test_wrong_new_password_leaves_file_waiting(browser, signed_in, session):
    browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    assert set_password(browser, "still-wrong").headers["location"] == "/settings?msg=password_saved"
    assert session.scalars(select(ImportBatch).where(ImportBatch.status == "quarantined")).one().raw_zip is not None
    assert len(session.scalars(select(ImportBatch)).all()) == 1


# --- 직접 올리기와 다시 처리


def _upload(browser, data: bytes):
    token = browser.csrf("/settings")
    return browser.client.post(
        "/settings/upload",
        data={"csrf_token": token},
        files={"file": ("export.zip", data, "application/zip")},
    )


def test_manual_upload(browser, signed_in, session):
    set_password(browser)
    data = export_zip()
    assert _upload(browser, data).headers["location"] == "/settings?msg=upload_applied"
    assert len(session.scalars(select(Txn)).all()) == 2
    assert _upload(browser, data).headers["location"] == "/settings?msg=upload_duplicate"


def test_manual_upload_failure_goes_to_quarantine(browser, signed_in, session):
    set_password(browser)
    assert _upload(browser, export_zip(password="other")).headers["location"] == "/settings?error=upload_failed"
    assert session.scalars(select(ImportBatch)).one().reason == "wrong_password"


def test_upload_requires_csrf_token(signed_in):
    browser, _, _ = signed_in
    response = browser.client.post("/settings/upload", files={"file": ("e.zip", b"x", "application/zip")})
    assert response.status_code == 403


def test_retry_button_reprocesses_quarantined_file(browser, signed_in, session):
    browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    batch = session.scalars(select(ImportBatch)).one()

    response = browser.post(f"/settings/quarantine/{batch.id}/retry", form_page="/settings")
    assert response.headers["location"] == "/settings?msg=retry_failed"
    assert len(session.scalars(select(ImportBatch)).all()) == 1

    set_password(browser, "something-else")
    session.get(ImportBatch, batch.id).raw_zip = export_zip(password="something-else")
    response = browser.post(f"/settings/quarantine/{batch.id}/retry", form_page="/settings")
    assert response.headers["location"] == "/settings?msg=retry_applied"


def test_same_quarantined_file_does_not_create_new_rows(browser, signed_in, session):
    data = export_zip()
    for index in range(3):
        browser.signed_post("/api/ingest/banksalad", mail_payload(data, f"m{index}"))
    assert len(session.scalars(select(ImportBatch)).all()) == 1


def test_quarantined_files_are_capped(browser, signed_in, session):
    for index in range(MAX_QUARANTINED_FILES + 2):
        browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip(), f"m{index}"))
    batches = session.scalars(select(ImportBatch).order_by(ImportBatch.id)).all()
    assert sum(batch.raw_zip is not None for batch in batches) == MAX_QUARANTINED_FILES
    assert batches[-1].summary["file_dropped"] is True


def test_quarantined_item_can_be_dismissed(browser, signed_in, session):
    browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    batch = session.scalars(select(ImportBatch)).one()
    assert "처리하지 못한 파일이 1건" in browser.client.get("/").text

    response = browser.post(f"/settings/quarantine/{batch.id}/dismiss", form_page="/settings")
    assert response.headers["location"] == "/settings?msg=dismissed"
    assert batch.status == "resolved"
    assert batch.raw_zip is None
    assert "처리하지 못한 파일" not in browser.client.get("/").text
    assert browser.post(f"/settings/quarantine/{batch.id}/dismiss", form_page="/settings").status_code == 404


def test_far_future_period_is_quarantined(browser, signed_in, session):
    set_password(browser)
    data = export_zip(inner="2026-01-01~2099-12-31.xlsx")
    body = browser.signed_post("/api/ingest/banksalad", mail_payload(data)).json()
    assert body["reason"] == "format_changed"
    assert session.scalars(select(Txn)).all() == []


def test_period_ending_before_the_last_row_is_quarantined(browser, signed_in, session):
    set_password(browser)
    data = export_zip(inner="2026-01-01~2026-01-31.xlsx")
    assert browser.signed_post("/api/ingest/banksalad", mail_payload(data)).json()["reason"] == "format_changed"


def test_retry_of_unknown_batch_is_404(signed_in):
    browser, _, _ = signed_in
    assert browser.post("/settings/quarantine/999999/retry", form_page="/settings").status_code == 404


# --- 기기 관리와 OTP 재등록


def test_device_can_be_logged_out_remotely(signed_in, web_app, google, clock, session):
    browser, seed, _ = signed_in
    phone = Browser(web_app, google, clock)
    phone.client.headers["user-agent"] = "phone-browser"
    phone.login(seed)
    assert "phone-browser" in browser.client.get("/settings").text

    target = session.scalars(select(AuthSession).where(AuthSession.user_agent == "phone-browser")).one()
    response = browser.post(f"/settings/sessions/{target.id}/revoke", form_page="/settings")
    assert response.headers["location"] == "/settings?msg=session_revoked"
    assert phone.client.get("/").status_code == 401
    assert browser.client.get("/").status_code == 200


def test_own_session_cannot_be_revoked_from_device_list(signed_in, session):
    browser, _, _ = signed_in
    own = session.scalars(select(AuthSession).where(AuthSession.stage == "full")).one()
    assert browser.post(f"/settings/sessions/{own.id}/revoke", form_page="/settings").status_code == 400


def test_revoke_others_needs_recent_otp(signed_in, web_app, google, clock):
    browser, seed, _ = signed_in
    phone = Browser(web_app, google, clock)
    phone.login(seed)
    clock.advance(minutes=6)
    browser.client.get("/")
    phone.client.get("/")

    response = browser.post("/settings/sessions/revoke-others", form_page="/settings", otp_code="000000")
    assert response.headers["location"] == "/settings?error=otp"
    assert phone.client.get("/").status_code == 200

    response = browser.post("/settings/sessions/revoke-others", form_page="/settings", otp_code=browser.otp(seed))
    assert response.headers["location"] == "/settings?msg=sessions_revoked"
    assert phone.client.get("/").status_code == 401
    assert browser.client.get("/").status_code == 200


def test_otp_reset_takes_effect_only_after_new_code_is_confirmed(signed_in, web_app, google, clock):
    browser, seed, _ = signed_in
    phone = Browser(web_app, google, clock)
    phone.login(seed)

    response = browser.post("/settings/otp/reset", form_page="/settings")
    assert response.headers["location"] == "/auth/enroll"
    assert phone.client.get("/").status_code == 200  # 아직 아무것도 바뀌지 않았다
    assert browser.client.get("/settings").status_code == 200

    new_seed, _ = browser.enroll()
    assert new_seed != seed
    assert browser.client.get("/settings").status_code == 200
    assert phone.client.get("/").status_code == 401

    clock.advance(seconds=30)
    device = Browser(web_app, google, clock)
    device.google_callback()
    assert device.post("/auth/mfa", code=device.otp(seed)).headers["location"] == "/auth/mfa?error=code"
    assert device.post("/auth/mfa", code=device.otp(new_seed)).headers["location"] == "/"


def test_abandoned_otp_reset_leaves_everything_as_it_was(signed_in, web_app, google, clock):
    # 등록을 끝내지 않은 틈에 Google 로그인만으로 OTP를 바꿔칠 수 없어야 한다
    browser, seed, _ = signed_in
    browser.post("/settings/otp/reset", form_page="/settings")
    browser.client.get("/auth/enroll")

    attacker = Browser(web_app, google, clock)
    attacker.google_callback()
    assert attacker.client.get("/auth/enroll").headers["location"] == "/auth/mfa"
    assert attacker.post("/auth/enroll", form_page="/auth/mfa", code="123456").headers["location"] == "/auth/mfa"
    assert attacker.client.get("/settings", headers={"accept": "text/html"}).headers["location"] == "/auth/mfa"

    clock.advance(seconds=30)
    assert attacker.post("/auth/mfa", code=attacker.otp(seed)).headers["location"] == "/"


def test_otp_reset_needs_recent_otp(signed_in, clock):
    browser, seed, _ = signed_in
    clock.advance(minutes=6)
    assert browser.post("/settings/otp/reset", form_page="/settings", otp_code="000000").headers["location"] == "/settings?error=otp"
    assert browser.client.get("/auth/enroll").headers["location"] == "/"


def test_new_recovery_codes_replace_old_ones(signed_in, web_app, google, clock):
    browser, _, old_codes = signed_in
    response = browser.post("/settings/recovery-codes", form_page="/settings")
    assert response.status_code == 200
    assert old_codes[0] not in response.text

    device = Browser(web_app, google, clock)
    device.google_callback()
    assert device.post("/auth/recovery", form_page="/auth/mfa", code=old_codes[0]).headers["location"] == "/auth/mfa?error=recovery"


# --- 하트비트와 경고


def test_heartbeat_records_time_and_purges(browser, signed_in, session, clock):
    browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    batch = session.scalars(select(ImportBatch)).one()
    assert batch.raw_zip is not None
    batch.created_at = clock() - __import__("datetime").timedelta(days=31)
    session.flush()

    assert browser.signed_post("/api/ingest/heartbeat", {"script_version": "1"}).json() == {"ok": True}
    assert session.get(ImportBatch, batch.id).raw_zip is None
    assert "메일 수신 스크립트의 신호를 아직" not in browser.client.get("/").text


def test_heartbeat_purges_old_audit_records(browser, signed_in, clock, session):
    from datetime import timedelta

    session.add(AuditLog(at=clock() - timedelta(days=181), event="old", detail={}))
    session.add(AuditLog(at=clock() - timedelta(days=179), event="recent", detail={}))
    session.flush()
    browser.signed_post("/api/ingest/heartbeat", {"script_version": "1"})
    events = set(session.scalars(select(AuditLog.event)))
    assert "old" not in events
    assert "recent" in events


def test_warnings_cover_silent_failures(browser, signed_in, clock, session):
    set_password(browser)
    home = browser.client.get("/").text
    assert "아직 가져온 데이터가 없습니다" in home
    assert "메일 수신 스크립트의 신호를 아직" in home
    assert "아직 백업이 없습니다" in home

    browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    browser.signed_post("/api/ingest/heartbeat", {"script_version": "1"})
    browser.signed_post("/api/backup/export", {})
    session.scalars(select(ImportBatch)).one().created_at = clock()
    session.flush()
    home = browser.client.get("/").text
    assert "data-warning" not in home

    clock.advance(days=9)
    browser.google_callback()
    # 세션이 만료되었으므로 시간 경과 경고는 함수로 직접 확인한다
    from app.ops.state import system_warnings

    warnings = " ".join(warning.text for warning in system_warnings(session, clock()))
    assert "마지막 내보내기 후 9일" in warnings
    assert "신호가 9일째" in warnings
    clock.advance(days=7)
    assert "마지막 백업 후 16일" in " ".join(warning.text for warning in system_warnings(session, clock()))


# --- 백업과 복원


@pytest.fixture
def second_database(database_url, engine):
    """복원 대상 빈 DB. 같은 임베디드 서버에 만든다."""
    admin = create_engine(normalize_url(database_url), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS restore_target"))
        connection.execute(text("CREATE DATABASE restore_target"))
    target = create_engine(make_url(normalize_url(database_url)).set(database="restore_target"))
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    with target.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield target
    target.dispose()
    with admin.connect() as connection:
        connection.execute(text("DROP DATABASE IF EXISTS restore_target"))
    admin.dispose()


def _load_some_data(browser):
    set_password(browser)
    assert browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip())).json()["status"] == "applied"


def test_backup_is_encrypted_and_has_no_plain_values(browser, signed_in):
    _load_some_data(browser)
    response = browser.signed_post("/api/backup/export", {})
    assert response.status_code == 200
    assert response.content.startswith(backup.MAGIC)
    for value in ("밥집".encode(), "입출금통장".encode(), FILE_PASSWORD.encode(), b"1000000"):
        assert value not in response.content


def test_backup_round_trip(browser, signed_in, session, second_database, clock):
    _load_some_data(browser)
    blob = browser.signed_post("/api/backup/export", {}).content
    payload = backup.decrypt_payload(make_settings().master_key, blob)

    with second_database.begin() as connection:
        counts = backup.restore_payload(connection, payload)
    assert counts["txn"] == 2

    with second_database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM txn")).scalar() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM report_lines")).scalar() == 2
        assert connection.execute(text("SELECT total_assets FROM asset_snapshot")).scalar() == 1_000_000
        assert connection.execute(text("SELECT COUNT(*) FROM secret_value WHERE name = 'file_password'")).scalar() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM auth_session")).scalar() == 0
        # 시퀀스가 맞아야 복원 뒤 새 행을 넣을 수 있다
        maximum = connection.execute(text("SELECT MAX(id) FROM txn")).scalar()
        assert connection.execute(text("SELECT nextval(pg_get_serial_sequence('txn', 'id'))")).scalar() == maximum + 1


def test_backup_carries_manual_assets_and_rules(browser, signed_in, session, second_database):
    from app.ledger.models import ManualAsset, ManualAssetValue, Rule

    _load_some_data(browser)
    asset = ManualAsset(kind="real_estate", name="우리 집", tenure="own", acquired_on=date(2026, 1, 1))
    asset.values.append(ManualAssetValue(as_of=date(2026, 1, 1), amount=500_000_000))
    session.add(asset)
    session.add(Rule(name="월세", match_description_equals="집주인", match_direction="out", set_nature="expense"))
    session.commit()
    blob = browser.signed_post("/api/backup/export", {}).content
    payload = backup.decrypt_payload(make_settings().master_key, blob)
    assert "우리 집".encode() not in blob

    with second_database.begin() as connection:
        counts = backup.restore_payload(connection, payload)
    assert counts["manual_asset"] == 1 and counts["manual_asset_value"] == 1
    with second_database.connect() as connection:
        assert connection.execute(text("SELECT amount FROM manual_asset_value")).scalar() == 500_000_000
        assert connection.execute(text("SELECT match_direction FROM rule")).scalar() == "out"


def test_backup_from_before_manual_assets_still_restores(browser, signed_in, second_database):
    """옛 백업은 그 버전의 스키마에 복원한 뒤 최신으로 올린다. 그때는 새 테이블이 아직 없다."""
    from alembic import command
    from alembic.config import Config

    _load_some_data(browser)
    payload = backup.decrypt_payload(make_settings().master_key, browser.signed_post("/api/backup/export", {}).content)
    payload["alembic_revision"] = "0002"
    del payload["tables"]["manual_asset"], payload["tables"]["manual_asset_value"]
    for row in payload["tables"]["rule"]:
        del row["match_direction"]

    config = Config("alembic.ini")
    with second_database.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")
    with second_database.begin() as connection:
        backup.restore_payload(connection, payload)
    with second_database.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with second_database.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM txn")).scalar() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM manual_asset")).scalar() == 0


def test_backup_excludes_sessions_and_quarantined_files(browser, signed_in):
    browser.signed_post("/api/ingest/banksalad", mail_payload(export_zip()))
    blob = browser.signed_post("/api/backup/export", {}).content
    payload = backup.decrypt_payload(make_settings().master_key, blob)
    assert "auth_session" not in payload["tables"]
    assert all("raw_zip" not in row for row in payload["tables"]["import_batch"])


def test_backup_cannot_be_opened_with_another_key(browser, signed_in):
    _load_some_data(browser)
    blob = browser.signed_post("/api/backup/export", {}).content
    with pytest.raises(CryptoError):
        backup.decrypt_payload(bytes(32), blob)


def test_tampered_backup_is_rejected(browser, signed_in):
    _load_some_data(browser)
    blob = bytearray(browser.signed_post("/api/backup/export", {}).content)
    blob[-1] ^= 1
    with pytest.raises(CryptoError):
        backup.decrypt_payload(make_settings().master_key, bytes(blob))


def test_restore_refuses_non_empty_database(browser, signed_in, session):
    _load_some_data(browser)
    blob = browser.signed_post("/api/backup/export", {}).content
    payload = backup.decrypt_payload(make_settings().master_key, blob)
    with pytest.raises(backup.RestoreRefused):
        backup.restore_payload(session.connection(), payload)


def test_restore_refuses_other_schema_version(second_database, browser, signed_in):
    _load_some_data(browser)
    payload = backup.decrypt_payload(
        make_settings().master_key, browser.signed_post("/api/backup/export", {}).content
    )
    payload["alembic_revision"] = "9999"
    with second_database.begin() as connection, pytest.raises(backup.RestoreRefused):
        backup.restore_payload(connection, payload)


def test_oversized_backup_reports_failure(browser, signed_in, monkeypatch):
    monkeypatch.setattr(backup, "MAX_BACKUP_BYTES", 10)
    assert browser.signed_post("/api/backup/export", {}).status_code == 507


def test_backup_payload_is_json_serializable(browser, signed_in, session, clock):
    _load_some_data(browser)
    json.dumps(backup.build_payload(session, clock()))
