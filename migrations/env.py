"""Alembic 실행 환경. 호출한 쪽이 연결을 넘기면 그것을 쓰고, 없으면 DATABASE_URL을 쓴다."""

from __future__ import annotations

from alembic import context

import app.ops_models  # noqa: F401 - 메타데이터에 운영 테이블을 등록한다
from app.db import make_engine
from app.ledger.models import Base

target_metadata = Base.metadata


def run() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = make_engine()
    with engine.connect() as owned:
        context.configure(connection=owned, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run()
