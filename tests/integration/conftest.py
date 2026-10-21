"""Fixtures for PostgreSQL-backed tests.

The suite needs a disposable database (TEST_DATABASE_URL, default: trades_test from
docker-compose). If PostgreSQL is unreachable the DB tests are SKIPPED with an explicit
reason (never reported as passed). The database name must end in "_test": every table is
truncated before each test, so any other name aborts the run.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_session
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://trader:trader@localhost:5432/trades_test"
)
TABLES = "trades, positions, lots, lot_matches, market_prices"


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/integration" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    name = make_url(TEST_URL).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"Refusing to run: TEST_DATABASE_URL database {name!r} must end in '_test'", 2)

    engine = create_engine(TEST_URL, pool_size=20, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        engine.dispose()
        pytest.skip(f"PostgreSQL not reachable at TEST_DATABASE_URL; DB tests NOT run ({name})")

    with engine.begin() as conn:  # start from nothing, then build the schema via the migration
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    yield engine
    engine.dispose()


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_URL.replace("%", "%%"))
    return config


@pytest.fixture(autouse=True)
def clean_tables(db_engine: Engine) -> None:
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def count(db_engine: Engine):
    """count("trades") -> number of rows, for asserting on persisted state."""

    def _count(table: str) -> int:
        with db_engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()

    return _count
