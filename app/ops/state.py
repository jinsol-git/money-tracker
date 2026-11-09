"""감사 기록과 운영 상태. 비밀값과 거래 내용은 넣지 않는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ledger.models import ImportBatch
from app.ops_models import AuditLog, AuthSession, SystemState

HEARTBEAT_WARN_AFTER = timedelta(days=3)
EXPORT_WARN_AFTER = timedelta(days=8)
BACKUP_WARN_AFTER = timedelta(days=15)
EXPIRED_SESSION_RETENTION = timedelta(days=7)
AUDIT_RETENTION = timedelta(days=180)
SECURITY_WARN_WINDOW = timedelta(days=7)
OTP_FAILURE_EVENTS = ("otp_failed", "recovery_failed", "step_up_failed", "otp_enroll_failed")
OTP_FAILURES_SEEN = "otp_failures_seen"
MAIL_REJECTED = "mail_rejected"
DISMISSIBLE_WARNINGS = frozenset({OTP_FAILURES_SEEN, MAIL_REJECTED})


@dataclass(frozen=True)
class SystemWarning:
    text: str
    dismiss: str | None = None  # 지나간 사건을 알리는 경고에만 확인 버튼을 둔다
    tag: str = "확인 필요"  # 공지 맨 앞의 짧은 표시
    tone: str = "attention"  # 멈췄거나 보안과 관련되면 error


def record(db: Session, now: datetime, event: str, **detail: Any) -> None:
    db.add(AuditLog(at=now, event=event, detail=detail))


def set_state(db: Session, now: datetime, key: str, value: dict[str, Any] | None = None) -> None:
    row = db.get(SystemState, key)
    if row is None:
        db.add(SystemState(key=key, value=value or {}, updated_at=now))
    else:
        row.value = value or {}
        row.updated_at = now


def state_time(db: Session, key: str) -> datetime | None:
    row = db.get(SystemState, key)
    return row.updated_at if row else None


def count_rejected_mail(db: Session, now: datetime) -> None:
    row = db.get(SystemState, MAIL_REJECTED)
    if row is None or now - row.updated_at > SECURITY_WARN_WINDOW:
        set_state(db, now, MAIL_REJECTED, {"count": 1})
    else:
        set_state(db, now, MAIL_REJECTED, {"count": row.value.get("count", 0) + 1})


def dismiss_warning(db: Session, now: datetime, key: str) -> None:
    """확인한 뒤의 새 사건은 다시 경고한다. 사건 기록 자체는 지우지 않는다."""
    if key == OTP_FAILURES_SEEN:
        set_state(db, now, OTP_FAILURES_SEEN)
    elif key == MAIL_REJECTED:
        row = db.get(SystemState, MAIL_REJECTED)
        if row is not None:
            db.delete(row)
    record(db, now, "warning_dismissed", warning=key)


def purge_expired_sessions(db: Session, now: datetime) -> None:
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now - EXPIRED_SESSION_RETENTION))


def purge_old_audit(db: Session, now: datetime) -> None:
    db.execute(delete(AuditLog).where(AuditLog.at < now - AUDIT_RETENTION))


def system_warnings(db: Session, now: datetime) -> list[SystemWarning]:
    """조용한 고장과 침입 신호를 놓치지 않도록 화면 위쪽에 띄울 경고."""
    warnings: list[SystemWarning] = []

    since = now - SECURITY_WARN_WINDOW
    seen = state_time(db, OTP_FAILURES_SEEN)
    failures = db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.event.in_(OTP_FAILURE_EVENTS), AuditLog.at > max(since, seen) if seen else AuditLog.at > since)
    )
    if failures:
        warnings.append(
            SystemWarning(
                f"최근 7일간 OTP 입력 실패가 {failures}회 있었습니다. 본인이 맞는지 확인이 필요합니다.",
                OTP_FAILURES_SEEN,
                tag="보안",
                tone="error",
            )
        )

    rejected = db.get(SystemState, MAIL_REJECTED)
    if rejected is not None and now - rejected.updated_at <= SECURITY_WARN_WINDOW:
        warnings.append(
            SystemWarning(
                f"최근 7일간 인증되지 않은 메일 {rejected.value.get('count', 0)}통을 거부했습니다. 위조 메일일 수 있습니다.",
                MAIL_REJECTED,
                tag="보안",
                tone="error",
            )
        )

    latest = db.scalar(
        select(ImportBatch).where(ImportBatch.status == "applied").order_by(ImportBatch.created_at.desc()).limit(1)
    )
    if latest is None:
        warnings.append(SystemWarning("아직 가져온 데이터가 없습니다. 뱅크샐러드 앱에서 내보내기를 실행해 주세요.", tag="시작 전"))
    else:
        if now - latest.created_at > EXPORT_WARN_AFTER:
            warnings.append(
                SystemWarning(f"마지막 내보내기 후 {(now - latest.created_at).days}일이 지났습니다. 수치가 오래되었을 수 있습니다.", tag="오래됨")
            )
        missing = latest.summary.get("missing", 0)
        if missing:
            warnings.append(SystemWarning(f"최근 가져오기에서 거래 {missing}건이 파일에서 사라져 집계에서 뺐습니다."))

    heartbeat = state_time(db, "ingest_heartbeat")
    if heartbeat is None:
        warnings.append(SystemWarning("메일 수신 스크립트의 신호를 아직 받지 못했습니다.", tag="시작 전"))
    elif now - heartbeat > HEARTBEAT_WARN_AFTER:
        warnings.append(
            SystemWarning(
                f"메일 수신 스크립트의 신호가 {(now - heartbeat).days}일째 없습니다. Apps Script를 확인해 주세요.",
                tag="멈춤",
                tone="error",
            )
        )

    backup = state_time(db, "last_backup")
    if backup is None:
        warnings.append(SystemWarning("아직 백업이 없습니다.", tag="시작 전"))
    elif now - backup > BACKUP_WARN_AFTER:
        warnings.append(SystemWarning(f"마지막 백업 후 {(now - backup).days}일이 지났습니다.", tag="백업"))

    quarantined = db.scalar(
        select(func.count()).select_from(ImportBatch).where(ImportBatch.status == "quarantined")
    )
    if quarantined:
        warnings.append(SystemWarning(f"처리하지 못한 파일이 {quarantined}건 있습니다. 설정에서 확인해 주세요."))
    return warnings
