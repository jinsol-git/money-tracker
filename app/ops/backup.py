"""암호화 백업과 복원. Apps Script가 주 1회 받아 내 Google Drive에 저장한다.

Neon 무료 요금제에는 자동 백업이 없고, 1년이 지난 거래는 다시 내보낼 수도 없다.
암호화 키는 MASTER_KEY에서 파생하므로 Drive가 노출되어도 내용을 볼 수 없다.
"""

from __future__ import annotations

import base64
import gzip
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, LargeBinary, Numeric, inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.types import Date, DateTime

import app.ops_models  # noqa: F401 - 메타데이터에 운영 테이블을 등록한다
from app.config import Settings
from app.ledger.models import Base
from app.security.crypto import seal, unseal

MAGIC = b"LEDGERBK1"
PURPOSE = "backup"
MAX_BACKUP_BYTES = 4_000_000  # Vercel 응답 한도 4.5MB 안쪽
EXCLUDED_TABLES = {"auth_session"}  # 복원 후 다시 로그인하면 된다
EXCLUDED_COLUMNS = {("import_batch", "raw_zip")}  # 격리 파일은 약한 암호라 백업에 넣지 않는다


class BackupTooLarge(Exception):
    pass


class RestoreRefused(Exception):
    pass


def _tables():
    return [table for table in Base.metadata.sorted_tables if table.name not in EXCLUDED_TABLES]


def _encode(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    return value


def _decode(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Numeric):
        return Decimal(value)
    if isinstance(column.type, LargeBinary):
        return base64.b64decode(value)
    if isinstance(column.type, JSONB):
        return value
    return value


def _alembic_revision(connection: Connection) -> str | None:
    if not inspect(connection).has_table("alembic_version"):
        return None
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def build_payload(db: Session, now: datetime) -> dict[str, Any]:
    connection = db.connection()
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in _tables():
        columns = [c for c in table.columns if (table.name, c.name) not in EXCLUDED_COLUMNS]
        primary = list(table.primary_key.columns)
        rows = connection.execute(select(*columns).order_by(*primary)).mappings()
        tables[table.name] = [{key: _encode(value) for key, value in row.items()} for row in rows]
    return {
        "format": 1,
        "created_at": now.isoformat(),
        "alembic_revision": _alembic_revision(connection),
        "tables": tables,
    }


def encrypt_payload(master_key: bytes, payload: dict[str, Any]) -> bytes:
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), compresslevel=9, mtime=0)
    return MAGIC + seal(master_key, PURPOSE, compressed)


def decrypt_payload(master_key: bytes, blob: bytes) -> dict[str, Any]:
    if not blob.startswith(MAGIC):
        raise RestoreRefused("백업 파일 형식이 아닙니다.")
    return json.loads(gzip.decompress(unseal(master_key, PURPOSE, blob[len(MAGIC) :])))


def export_backup(db: Session, settings: Settings, now: datetime) -> bytes:
    blob = encrypt_payload(settings.master_key, build_payload(db, now))
    if len(blob) > MAX_BACKUP_BYTES:
        raise BackupTooLarge
    return blob


def restore_payload(connection: Connection, payload: dict[str, Any]) -> dict[str, int]:
    """빈 DB에만 복원한다. 이미 데이터가 있는 DB를 덮어쓰는 일은 하지 않는다."""
    if payload.get("format") != 1:
        raise RestoreRefused("지원하지 않는 백업 형식입니다.")
    revision = _alembic_revision(connection)
    if revision != payload.get("alembic_revision"):
        raise RestoreRefused("스키마 버전이 다릅니다. 같은 버전으로 마이그레이션한 뒤 복원해 주세요.")
    for name in ("txn", "import_batch", "owner"):
        if connection.execute(text(f"SELECT EXISTS (SELECT 1 FROM {name})")).scalar():
            raise RestoreRefused("비어 있지 않은 DB에는 복원하지 않습니다.")

    # 마이그레이션이 넣은 기본 비목은 백업의 값으로 대체한다
    connection.execute(text("DELETE FROM category_map"))
    connection.execute(text("DELETE FROM category"))

    # 옛 버전의 백업은 그 버전의 스키마로 복원하므로 이후에 생긴 테이블은 아직 없다.
    existing = set(inspect(connection).get_table_names())
    counts: dict[str, int] = {}
    for table in _tables():
        rows = payload["tables"].get(table.name, [])
        counts[table.name] = len(rows)
        if not rows:
            continue
        columns = {column.name: column for column in table.columns}
        decoded = [{key: _decode(columns[key], value) for key, value in row.items()} for row in rows]
        connection.execute(table.insert(), decoded)

    # 복원한 ID 다음부터 새 행이 들어가도록 시퀀스를 맞춘다
    for table in _tables():
        if table.name not in existing:
            continue
        for column in table.primary_key.columns:
            sequence = connection.execute(
                text("SELECT pg_get_serial_sequence(:table, :column)"),
                {"table": table.name, "column": column.name},
            ).scalar()
            if sequence:
                connection.execute(
                    text(
                        f"SELECT setval(:sequence, COALESCE((SELECT MAX({column.name}) FROM {table.name}), 1), "
                        f"(SELECT MAX({column.name}) IS NOT NULL FROM {table.name}))"
                    ),
                    {"sequence": sequence},
                )
    return counts
