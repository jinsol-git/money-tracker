"""DB 연결. Neon의 풀러(PgBouncer)와 서버리스 환경에 맞춰 연결을 재사용하지 않는다."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool


def normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def make_engine(url: str | None = None) -> Engine:
    resolved = url or os.environ.get("DATABASE_URL")
    if not resolved:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다.")
    return create_engine(
        normalize_url(resolved),
        poolclass=NullPool,
        hide_parameters=True,  # 오류 메시지에 거래 내용이 섞이지 않게 한다
        connect_args={"prepare_threshold": None},  # PgBouncer 트랜잭션 모드 호환
    )
