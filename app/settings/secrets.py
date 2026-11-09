"""사용자가 정한 비밀값의 보관. DB에는 암호문만 있고 키는 MASTER_KEY에서 파생한다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.ops_models import SecretValue
from app.security.crypto import seal, unseal

KEEP_VERSIONS = 2  # 현재 값과 직전 값. 바꾸기 전에 내보낸 파일도 열 수 있어야 한다.


def _purpose(name: str) -> str:
    return f"secret:{name}"


def store_secret(db: Session, settings: Settings, now: datetime, name: str, value: str) -> int:
    versions = db.scalars(
        select(SecretValue).where(SecretValue.name == name).order_by(SecretValue.version.desc())
    ).all()
    version = versions[0].version + 1 if versions else 1
    db.add(
        SecretValue(
            name=name,
            version=version,
            ciphertext=seal(settings.master_key, _purpose(name), value.encode("utf-8")),
            created_at=now,
        )
    )
    for old in versions[KEEP_VERSIONS - 1 :]:
        db.delete(old)
    db.flush()
    return version


def secret_versions(db: Session, settings: Settings, name: str) -> list[str]:
    """최신 값부터 돌려준다."""
    rows = db.scalars(
        select(SecretValue).where(SecretValue.name == name).order_by(SecretValue.version.desc())
    ).all()
    return [unseal(settings.master_key, _purpose(name), row.ciphertext).decode("utf-8") for row in rows]


def current_secret(db: Session, settings: Settings, name: str) -> str | None:
    versions = secret_versions(db, settings, name)
    return versions[0] if versions else None


def secret_updated_at(db: Session, name: str) -> datetime | None:
    return db.scalar(
        select(SecretValue.created_at)
        .where(SecretValue.name == name)
        .order_by(SecretValue.version.desc())
        .limit(1)
    )


def delete_secret(db: Session, name: str) -> None:
    db.execute(delete(SecretValue).where(SecretValue.name == name))
