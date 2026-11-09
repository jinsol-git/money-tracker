"""설정 화면: 파일 비밀번호, 수신 상태, 보안."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import otp, sessions
from app.auth.routes import ACTIVE_SEED
from app.deps import get_db, now_of, render, settings_of
from app.ingest.archive import MAX_ARCHIVE_BYTES
from app.ingest.importer import dismiss_batch, import_export, reprocess_batch, reprocess_wrong_password
from app.ledger.models import ImportBatch
from app.ledger.reclassify import OWN_NAMES_KEY, own_names_of, parse_own_names, reclassify
from app.ops import state
from app.ops_models import AuthSession, Owner
from app.settings.secrets import current_secret, secret_updated_at, secret_versions, store_secret

router = APIRouter()

FILE_PASSWORD = "file_password"
MESSAGES = {
    "password_saved": "파일 비밀번호를 저장했습니다.",
    "reprocessed": "저장한 비밀번호로 대기 중이던 파일을 처리했습니다.",
    "upload_applied": "파일을 가져왔습니다.",
    "upload_duplicate": "이미 가져온 파일입니다.",
    "retry_applied": "파일을 다시 처리해 장부에 반영했습니다.",
    "retry_failed": "다시 처리했지만 여전히 실패했습니다.",
    "dismissed": "처리 대기 목록에서 지웠습니다.",
    "sessions_revoked": "다른 기기를 모두 로그아웃했습니다.",
    "session_revoked": "해당 기기를 로그아웃했습니다.",
    "own_names_saved": "내 이름을 저장했습니다.",
}
ERRORS = {
    "otp": "OTP 코드가 맞지 않습니다.",
    "locked": "실패가 반복되어 잠시 잠겼습니다. 조금 뒤 다시 시도해 주세요.",
    "password_length": "비밀번호는 4자 이상 128자 이하로 입력해 주세요.",
    "upload_failed": "파일을 처리하지 못했습니다. 아래 처리 대기 목록에서 사유를 확인해 주세요.",
    "upload_empty": "파일을 선택해 주세요.",
}
QUARANTINE_REASONS = {
    "wrong_password": "비밀번호가 맞지 않음",
    "format_changed": "파일 형식이 바뀜",
    "reconciliation_failed": "뱅샐현황과 내역 합계가 다름",
    "suspicious_disappearance": "거래가 대량으로 사라짐",
}


def _see_other(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _reason_label(reason: str | None) -> str:
    if reason and reason.startswith("rejected:"):
        return "내보내기 파일이 아님"
    return QUARANTINE_REASONS.get(reason or "", reason or "알 수 없음")


class StepUpFailed(Exception):
    def __init__(self, code: str):
        self.code = code


def require_fresh_mfa(request: Request, db: Session, code: str | None) -> None:
    """민감한 작업은 최근 5분 안에 OTP를 확인했어야 한다. 아니면 이번에 입력한 코드를 확인한다."""
    settings = settings_of(request)
    now = now_of(request)
    auth = request.state.auth
    if sessions.has_fresh_mfa(auth, now):
        return
    owner = otp.lock_owner(db)
    if owner is None or otp.is_locked(owner, now):
        raise StepUpFailed("locked")
    seed = current_secret(db, settings, ACTIVE_SEED)
    step = otp.verify(seed, (code or "").strip(), now, owner.totp_last_step) if seed else None
    if step is None:
        otp.register_failure(owner, now)
        state.record(db, now, "step_up_failed", locked=otp.is_locked(owner, now))
        db.commit()
        raise StepUpFailed("otp")
    otp.register_success(owner)
    owner.totp_last_step = step
    auth.mfa_verified_at = now


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), msg: str | None = None, error: str | None = None):
    now = now_of(request)
    auth = request.state.auth
    quarantined = db.scalars(
        select(ImportBatch).where(ImportBatch.status == "quarantined").order_by(ImportBatch.id.desc()).limit(20)
    ).all()
    latest = db.scalar(
        select(ImportBatch).where(ImportBatch.status == "applied").order_by(ImportBatch.id.desc()).limit(1)
    )
    owner = db.get(Owner, 1)
    wait = otp.wait_text(owner, now) if owner is not None and error == "locked" else ""
    return render(
        request,
        "settings.html",
        message=MESSAGES.get(msg or ""),
        error=f"실패가 반복되어 잠시 잠겼습니다. {wait} 뒤에 다시 시도해 주세요." if wait else ERRORS.get(error or ""),
        warnings=state.system_warnings(db, now),
        own_names=own_names_of(db),
        file_password_updated=secret_updated_at(db, FILE_PASSWORD),
        latest_batch=latest,
        quarantined=[
            {"id": b.id, "at": b.created_at, "reason": _reason_label(b.reason), "retryable": b.raw_zip is not None}
            for b in quarantined
        ],
        heartbeat=state.state_time(db, "ingest_heartbeat"),
        last_backup=state.state_time(db, "last_backup"),
        devices=sessions.active_sessions(db, now),
        recovery_left=otp.remaining_recovery_codes(db),
        needs_otp=not sessions.has_fresh_mfa(auth, now),
    )


@router.post("/settings/warnings/{key}/dismiss")
def dismiss_warning(request: Request, key: str, db: Session = Depends(get_db), back: str = Form("/")):
    """이미 확인한 사건의 경고를 내린다. 돌아갈 곳은 경고를 보여 주는 두 화면으로 한정한다."""
    if key not in state.DISMISSIBLE_WARNINGS:
        raise HTTPException(status_code=404)
    state.dismiss_warning(db, now_of(request), key)
    db.commit()
    return _see_other("/settings" if back == "/settings" else "/")


@router.post("/settings/own-names")
def save_own_names(request: Request, db: Session = Depends(get_db), names: str = Form("")):
    """이 이름으로 오간 이체는 내 계좌 이동이다. 지난 거래에도 바로 적용한다."""
    now = now_of(request)
    kept = parse_own_names(names)
    state.set_state(db, now, OWN_NAMES_KEY, {"names": kept})
    reclassify(db)
    state.record(db, now, "own_names_saved", count=len(kept))
    db.commit()
    return _see_other("/settings?msg=own_names_saved")


@router.post("/settings/file-password")
def save_file_password(
    request: Request,
    db: Session = Depends(get_db),
    file_password: str = Form(""),
    otp_code: str = Form(""),
):
    settings = settings_of(request)
    now = now_of(request)
    try:
        require_fresh_mfa(request, db, otp_code)
    except StepUpFailed as failed:
        return _see_other(f"/settings?error={failed.code}")
    if not 4 <= len(file_password) <= 128:
        return _see_other("/settings?error=password_length")

    store_secret(db, settings, now, FILE_PASSWORD, file_password)
    state.record(db, now, "file_password_changed")
    applied = reprocess_wrong_password(db, secret_versions(db, settings, FILE_PASSWORD), today=now.date())
    db.commit()
    return _see_other("/settings?msg=reprocessed" if applied else "/settings?msg=password_saved")


@router.post("/settings/upload")
def upload_export(request: Request, db: Session = Depends(get_db), file: UploadFile = File(None)):
    """메일 수신이 끊겼을 때의 대체 경로. 메일과 같은 검증을 거치되 메일 인증만 받지 않는다."""
    settings = settings_of(request)
    now = now_of(request)
    if file is None:
        return _see_other("/settings?error=upload_empty")
    data = file.file.read(MAX_ARCHIVE_BYTES + 1)
    if not data:
        return _see_other("/settings?error=upload_empty")
    result = import_export(
        db, data, secret_versions(db, settings, FILE_PASSWORD), source="upload", today=now.date()
    )
    state.record(db, now, "upload", status=result.status, reason=result.reason)
    db.commit()
    if result.status == "applied":
        return _see_other("/settings?msg=upload_applied")
    if result.status == "duplicate":
        return _see_other("/settings?msg=upload_duplicate")
    return _see_other("/settings?error=upload_failed")


@router.post("/settings/quarantine/{batch_id}/retry")
def retry_quarantined(request: Request, batch_id: int, db: Session = Depends(get_db)):
    settings = settings_of(request)
    now = now_of(request)
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.status != "quarantined" or batch.raw_zip is None:
        raise HTTPException(status_code=404)
    applied = reprocess_batch(db, batch, secret_versions(db, settings, FILE_PASSWORD), today=now.date())
    state.record(db, now, "quarantine_retry", applied=applied)
    db.commit()
    return _see_other("/settings?msg=retry_applied" if applied else "/settings?msg=retry_failed")


@router.post("/settings/quarantine/{batch_id}/dismiss")
def dismiss_quarantined(request: Request, batch_id: int, db: Session = Depends(get_db)):
    now = now_of(request)
    batch = db.get(ImportBatch, batch_id)
    if batch is None or batch.status != "quarantined":
        raise HTTPException(status_code=404)
    dismiss_batch(db, batch)
    state.record(db, now, "quarantine_dismissed")
    db.commit()
    return _see_other("/settings?msg=dismissed")


@router.post("/settings/sessions/revoke-others")
def revoke_other_sessions(request: Request, db: Session = Depends(get_db), otp_code: str = Form("")):
    now = now_of(request)
    try:
        require_fresh_mfa(request, db, otp_code)
    except StepUpFailed as failed:
        return _see_other(f"/settings?error={failed.code}")
    count = sessions.revoke_others(db, request.state.auth.id, now)
    state.record(db, now, "sessions_revoked", count=count)
    db.commit()
    return _see_other("/settings?msg=sessions_revoked")


@router.post("/settings/sessions/{session_id}/revoke")
def revoke_session(request: Request, session_id: int, db: Session = Depends(get_db)):
    now = now_of(request)
    if session_id == request.state.auth.id:
        raise HTTPException(status_code=400)
    target = db.get(AuthSession, session_id)
    if target is None:
        raise HTTPException(status_code=404)
    sessions.revoke(target, now)
    state.record(db, now, "session_revoked")
    db.commit()
    return _see_other("/settings?msg=session_revoked")


@router.post("/settings/otp/reset")
def reset_otp(request: Request, db: Session = Depends(get_db), otp_code: str = Form("")):
    """OTP 앱을 옮길 때 쓴다. 새 코드를 확인하기 전까지는 기존 OTP가 그대로 유효하다.

    기존 OTP를 먼저 지우면 등록을 마치기 전까지 Google 로그인만으로 OTP를 바꿔칠 수 있다.
    """
    now = now_of(request)
    try:
        require_fresh_mfa(request, db, otp_code)
    except StepUpFailed as failed:
        return _see_other(f"/settings?error={failed.code}")
    auth = request.state.auth
    auth.can_enroll = True
    auth.pending_totp = None
    state.record(db, now, "otp_reset_started")
    db.commit()
    return _see_other("/auth/enroll")


@router.post("/settings/recovery-codes", response_class=HTMLResponse)
def regenerate_recovery_codes(request: Request, db: Session = Depends(get_db), otp_code: str = Form("")):
    settings = settings_of(request)
    now = now_of(request)
    try:
        require_fresh_mfa(request, db, otp_code)
    except StepUpFailed as failed:
        return _see_other(f"/settings?error={failed.code}")
    codes = otp.issue_recovery_codes(db, settings, now)
    state.record(db, now, "recovery_codes_issued")
    db.commit()
    return render(request, "recovery_codes.html", codes=codes)
