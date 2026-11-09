"""Apps Script가 호출하는 API. 관문에서 HMAC 서명을 검증한 뒤에만 여기에 닿는다.

Gmail 토큰은 서버에 없다. 서버가 뚫려도 메일함으로 번지지 않게 하기 위해서다.
Vercel 함수는 응답 후 CPU를 쓸 수 없으므로 요청 안에서 처리를 끝낸다.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, now_of, settings_of
from app.ingest.importer import import_export, purge_expired_quarantine
from app.ingest.mail_auth import rejection_reason
from app.ops import state
from app.ops.backup import BackupTooLarge, export_backup
from app.settings.routes import FILE_PASSWORD
from app.settings.secrets import secret_versions

router = APIRouter()


class IngestPayload(BaseModel):
    message_id: str = Field(min_length=1, max_length=200)
    auth_results: str = Field(max_length=8000)
    to_headers: list[str] = Field(max_length=10)
    from_headers: list[str] = Field(max_length=10)
    # 스크립트가 위조 의심이나 크기 초과로 판단하면 첨부 없이 사실만 알린다
    attachment: str | None = Field(default=None, max_length=6_000_000)


class HeartbeatPayload(BaseModel):
    script_version: str = Field(default="", max_length=40)


def decode_attachment(encoded: str) -> bytes:
    """Gmail API는 URL 안전 base64를 쓰고 패딩이 없을 수 있다."""
    normalized = encoded.strip().replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(normalized + "=" * (-len(normalized) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400) from exc


@router.post("/api/ingest/banksalad")
def ingest_mail(request: Request, payload: IngestPayload, db: Session = Depends(get_db)):
    settings = settings_of(request)
    now = now_of(request)

    reason = rejection_reason(payload.auth_results, payload.to_headers, payload.from_headers, settings.owner_email)
    if reason is not None:
        # 메일을 보내는 것만으로 행을 늘릴 수 없도록 개수만 센다
        state.count_rejected_mail(db, now)
        db.commit()
        return {"status": "rejected", "reason": reason}
    if not payload.attachment:
        state.count_rejected_mail(db, now)
        db.commit()
        return {"status": "rejected", "reason": "no_attachment"}

    result = import_export(
        db,
        decode_attachment(payload.attachment),
        secret_versions(db, settings, FILE_PASSWORD),
        source="mail",
        message_id=payload.message_id,
        today=now.date(),
    )
    state.record(db, now, "ingest", status=result.status, reason=result.reason, counts=result.counts)
    db.commit()
    return {"status": result.status, "reason": result.reason, "batch_id": result.batch_id, "counts": result.counts}


@router.post("/api/ingest/heartbeat")
def heartbeat(request: Request, payload: HeartbeatPayload, db: Session = Depends(get_db)):
    """하루 한 번 호출된다. 수신 경로가 살아 있는지 보는 신호이자 정리 작업의 계기다."""
    now = now_of(request)
    state.set_state(db, now, "ingest_heartbeat", {"script_version": payload.script_version})
    purge_expired_quarantine(db, now)
    state.purge_expired_sessions(db, now)
    state.purge_old_audit(db, now)
    db.commit()
    return {"ok": True}


@router.post("/api/backup/export")
def backup_export(request: Request, db: Session = Depends(get_db)):
    settings = settings_of(request)
    now = now_of(request)
    try:
        blob = export_backup(db, settings, now)
    except BackupTooLarge as exc:
        state.record(db, now, "backup_failed", reason="too_large")
        db.commit()
        raise HTTPException(status_code=507) from exc
    state.set_state(db, now, "last_backup", {"bytes": len(blob)})
    state.record(db, now, "backup_exported", bytes=len(blob))
    db.commit()
    return Response(content=blob, media_type="application/octet-stream")
