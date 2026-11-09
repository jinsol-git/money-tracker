"""DB 테스트는 임베디드 PostgreSQL에서 실행한다. 테스트마다 트랜잭션을 롤백한다."""

from __future__ import annotations

import os

import pgserver
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

import web_support
from app.db import make_engine
from app.deps import get_db
from app.web import create_app


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture(scope="session")
def engine(database_url):
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(Config("alembic.ini"), "head")
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    connection = engine.connect()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield db
    db.close()
    outer.rollback()
    connection.close()


@pytest.fixture
def clock():
    return web_support.FrozenClock()


@pytest.fixture
def google():
    return web_support.FakeGoogle()


@pytest.fixture
def web_app(engine, session, clock, google):
    application = create_app(
        web_support.make_settings(),
        engine=engine,
        clock=clock,
        google_transport=google.transport(),
    )
    application.dependency_overrides[get_db] = lambda: session
    return application


@pytest.fixture
def browser(web_app, google, clock):
    return web_support.Browser(web_app, google, clock)


@pytest.fixture
def signed_in(browser):
    """OTP까지 마친 기기와 시드, 복구 코드."""
    browser.google_callback()
    seed, codes = browser.enroll()
    return browser, seed, codes
