"""Concurrency: real parallel database sessions (one per thread) racing through the same
service code the API uses. Threads are released together by a barrier to maximize overlap,
and each scenario is repeated in several rounds on fresh keys.

These guard the two data-integrity guarantees: the UNIQUE (venue, trade_id) constraint and
the per-position row lock. A Python lock could not make these pass across processes.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from app.errors import InsufficientInventoryError, TradeConflictError
from app.schemas import TradeIn
from app.services.ingestion import ingest_trade

ROUNDS = 5


def trade(**kw) -> TradeIn:
    base = {
        "venue": "binance",
        "trade_id": "T1",
        "account_id": "a1",
        "symbol": "BTC-USD",
        "side": "BUY",
        "quantity": "10",
        "price": "100",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    return TradeIn.model_validate({**base, **kw})


def race(session_factory, trades: list[TradeIn]) -> Counter:
    """Submit all trades at (nearly) the same instant, each in its own session/connection."""
    barrier = Barrier(len(trades))

    def submit(data: TradeIn) -> str:
        with session_factory() as session:
            barrier.wait(timeout=10)
            try:
                return "duplicate" if ingest_trade(session, data).duplicate else "accepted"
            except InsufficientInventoryError:
                return "oversell"
            except TradeConflictError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=len(trades)) as pool:
        return Counter(pool.map(submit, trades))


def scalar(db_engine, sql: str):
    with db_engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_concurrent_duplicate_submissions_apply_exactly_once(session_factory, db_engine, round_no):
    symbol = f"SYM{round_no}"
    outcome = race(session_factory, [trade(symbol=symbol, trade_id="DUP") for _ in range(8)])

    assert outcome == Counter(accepted=1, duplicate=7)
    assert scalar(db_engine, f"SELECT count(*) FROM trades WHERE symbol = '{symbol}'") == 1
    assert (
        scalar(
            db_engine,
            "SELECT sum(remaining_quantity) FROM lots l JOIN positions p ON p.id = l.position_id "
            f"WHERE p.symbol = '{symbol}'",
        )
        == 10
    )  # not 80


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_two_concurrent_sells_cannot_both_pass_on_a_stale_position(
    session_factory, db_engine, round_no
):
    symbol = f"SYM{round_no}"
    with session_factory() as session:
        ingest_trade(session, trade(symbol=symbol, trade_id="B1", quantity="10"))

    outcome = race(
        session_factory,
        [
            trade(symbol=symbol, trade_id="S1", side="SELL", quantity="6"),
            trade(symbol=symbol, trade_id="S2", side="SELL", quantity="6"),
        ],
    )

    assert outcome == Counter(accepted=1, oversell=1)
    assert scalar(db_engine, "SELECT sum(remaining_quantity) FROM lots") == 4  # never negative


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_many_concurrent_sells_accept_exactly_what_inventory_allows(
    session_factory, db_engine, round_no
):
    symbol = f"SYM{round_no}"
    with session_factory() as session:
        ingest_trade(session, trade(symbol=symbol, trade_id="B1", quantity="10"))

    outcome = race(
        session_factory,
        [trade(symbol=symbol, trade_id=f"S{i}", side="SELL", quantity="3") for i in range(10)],
    )

    assert outcome == Counter(accepted=3, oversell=7)  # 3 * 3 = 9 <= 10 < 12
    assert scalar(db_engine, "SELECT sum(remaining_quantity) FROM lots") == 1
    assert scalar(db_engine, "SELECT sum(quantity) FROM lot_matches") == 9
    assert scalar(db_engine, "SELECT count(*) FROM trades WHERE side = 'SELL'") == 3
    assert scalar(db_engine, "SELECT count(*) FROM lots WHERE remaining_quantity < 0") == 0


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_first_ever_trades_on_a_new_position_all_land_on_one_position_row(
    session_factory, db_engine, round_no
):
    symbol = f"SYM{round_no}"
    outcome = race(
        session_factory, [trade(symbol=symbol, trade_id=f"B{i}", quantity="2") for i in range(8)]
    )

    assert outcome == Counter(accepted=8)
    assert scalar(db_engine, f"SELECT count(*) FROM positions WHERE symbol = '{symbol}'") == 1
    assert scalar(db_engine, "SELECT sum(remaining_quantity) FROM lots") == 16


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_same_trade_id_racing_on_two_accounts_yields_one_winner_and_one_conflict(
    session_factory, db_engine, round_no
):
    """Different accounts => different position locks, so only the UNIQUE constraint can
    arbitrate. The loser's transaction (including its new position row) must roll back."""
    symbol = f"SYM{round_no}"
    outcome = race(
        session_factory,
        [
            trade(symbol=symbol, trade_id="SAME", account_id="a1"),
            trade(symbol=symbol, trade_id="SAME", account_id="a2"),
        ],
    )

    assert outcome == Counter(accepted=1, conflict=1)
    assert scalar(db_engine, f"SELECT count(*) FROM trades WHERE symbol = '{symbol}'") == 1
    assert scalar(db_engine, f"SELECT count(*) FROM positions WHERE symbol = '{symbol}'") == 1
    assert scalar(db_engine, "SELECT count(*) FROM lots") == 1


def test_concurrent_mixed_duplicates_and_sells_stay_consistent(session_factory, db_engine):
    with session_factory() as session:
        ingest_trade(session, trade(trade_id="B1", quantity="10"))
    trades = [
        trade(trade_id=f"S{i % 4}", side="SELL", quantity="3") for i in range(12)
    ]  # 4 distinct ids x3

    outcome = race(session_factory, trades)

    assert outcome["accepted"] == 3 and outcome["conflict"] == 0
    assert outcome["accepted"] + outcome["duplicate"] + outcome["oversell"] == 12
    assert scalar(db_engine, "SELECT sum(remaining_quantity) FROM lots") == 1
    assert scalar(db_engine, "SELECT count(*) FROM trades WHERE side = 'SELL'") == 3
