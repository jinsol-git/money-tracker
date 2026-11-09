#!/usr/bin/env python3
"""Drive에 저장된 암호화 백업을 빈 DB로 복원한다.

    DATABASE_URL=... ~/.local/bin/uv run python scripts/restore_backup.py <백업 파일> [--dry-run]

MASTER_KEY는 터미널에서 입력받는다. 대상 DB에 데이터가 있으면 복원하지 않는다.
--dry-run은 복호화와 내용 확인만 하고 DB를 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import ConfigError, parse_master_key  # noqa: E402
from app.db import make_engine  # noqa: E402
from app.ops.backup import RestoreRefused, decrypt_payload, restore_payload  # noqa: E402
from app.security.crypto import CryptoError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        master_key = parse_master_key(getpass.getpass("MASTER_KEY (입력해도 화면에 표시되지 않습니다): "))
        payload = decrypt_payload(master_key, args.backup.read_bytes())
    except (ConfigError, CryptoError, RestoreRefused) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    print(f"백업 시각: {payload['created_at']}")
    for name, rows in payload["tables"].items():
        print(f"  {name}: {len(rows)}행")
    if args.dry_run:
        return 0

    if not os.environ.get("DATABASE_URL"):
        print("오류: DATABASE_URL이 필요합니다.", file=sys.stderr)
        return 2
    os.chdir(PROJECT_ROOT)
    command.upgrade(Config("alembic.ini"), payload["alembic_revision"])
    engine = make_engine()
    try:
        with engine.begin() as connection:
            restore_payload(connection, payload)
    except RestoreRefused as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    engine.dispose()
    # 옛 백업이어도 복원 뒤에는 지금 코드가 쓰는 스키마까지 올린다.
    command.upgrade(Config("alembic.ini"), "head")
    print("복원을 마쳤습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
