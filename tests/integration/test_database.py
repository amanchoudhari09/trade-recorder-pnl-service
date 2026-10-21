"""F. Persistence: constraints, migration/model parity, exact decimal round trips."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Base, Lot, Position, Trade
from tests.integration.conftest import alembic_config
from tests.integration.helpers import post


def trade(**kw) -> Trade:
    base = dict(
        venue="v",
        trade_id="T1",
        account_id="a",
        symbol="X",
        side="BUY",
        quantity=D(1),
        price=D(1),
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return Trade(**{**base, **kw})


def test_unique_constraint_on_venue_and_trade_id_is_enforced_by_the_database(session_factory):
    with session_factory() as session:
        session.add(trade())
        session.commit()
        session.add(trade(account_id="someone-else", quantity=D(5)))
        with pytest.raises(IntegrityError) as exc:
            session.commit()
        assert exc.value.orig.diag.constraint_name == "uq_trades_venue_trade_id"


def test_same_trade_id_on_another_venue_is_allowed(session_factory):
    with session_factory() as session:
        session.add_all([trade(venue="a"), trade(venue="b")])
        session.commit()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"quantity": D(0)}, "ck_trades_quantity_positive"),
        ({"price": D(-1)}, "ck_trades_price_positive"),
        ({"side": "HOLD"}, "ck_trades_side"),
    ],
)
def test_check_constraints_reject_bad_trade_rows(session_factory, overrides, constraint):
    with session_factory() as session:
        session.add(trade(**overrides))
        with pytest.raises(IntegrityError) as exc:
            session.commit()
        assert exc.value.orig.diag.constraint_name == constraint


def test_lot_remaining_can_never_go_negative_or_exceed_original(session_factory):
    for remaining, constraint in (
        (D(-1), "ck_lots_remaining_non_negative"),
        (D(2), "ck_lots_remaining_le_original"),
    ):
        with session_factory() as session:
            session.add(trade())
            session.add(Position(account_id="a", venue="v", symbol="X"))
            session.flush()
            pos = session.query(Position).one()
            session.add(
                Lot(
                    position_id=pos.id,
                    trade_pk=session.query(Trade).one().id,
                    original_quantity=D(1),
                    remaining_quantity=remaining,
                    price=D(1),
                )
            )
            with pytest.raises(IntegrityError) as exc:
                session.commit()
            assert exc.value.orig.diag.constraint_name == constraint


def test_position_key_is_unique(session_factory):
    with session_factory() as session:
        session.add(Position(account_id="a", venue="v", symbol="X"))
        session.commit()
        session.add(Position(account_id="a", venue="v", symbol="X"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_matches_models_and_is_at_head(db_engine):
    with db_engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert current == ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_decimals_round_trip_exactly(client):
    post(client, quantity="0.123456789012345678", price="999999999999999999.000000000000000001")
    t = client.get("/trades/binance/T1").json()
    assert (t["quantity"], t["price"]) == (
        "0.123456789012345678",
        "999999999999999999.000000000000000001",
    )


def test_timestamps_are_stored_timezone_aware_in_utc_instant(client):
    post(client, timestamp="2026-01-01T02:00:00+02:00")
    from datetime import datetime as dt

    stamp = dt.fromisoformat(client.get("/trades/binance/T1").json()["timestamp"])
    assert stamp.utcoffset() is not None
    assert stamp == dt(2026, 1, 1, 0, 0, tzinfo=UTC)
