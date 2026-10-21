from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one Session per request.

    `expire_on_commit=False` keeps returned ORM objects readable after the service
    layer commits, without triggering extra queries.
    """
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
