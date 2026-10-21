"""Trade ingestion: idempotency, oversell check, FIFO lot consumption, all in ONE transaction.

Concurrency strategy (details in DESIGN.md):
  1. A `positions` row exists per (account, venue, symbol). We create it with
     INSERT .. ON CONFLICT DO NOTHING and then `SELECT .. FOR UPDATE` it, so every trade
     touching the same position is serialized by PostgreSQL - across processes, unlike a
     Python lock. This also covers the "first trade ever on this position" race.
  2. Idempotency is enforced by the UNIQUE (venue, trade_id) constraint. An application-level
     pre-check gives a fast, deterministic answer; the constraint is the real guarantee and
     catches the cases the pre-check cannot (e.g. the same trade id racing on two accounts).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import TradeConflictError
from app.models import Lot, LotMatch, Position, Trade
from app.money import CTX
from app.schemas import Side, TradeIn
from app.services.comparison import trade_differences
from app.services.fifo import Match, OpenLot, match_fifo

TRADE_IDENTITY_CONSTRAINT = "uq_trades_venue_trade_id"


@dataclass(frozen=True)
class IngestResult:
    trade: Trade
    duplicate: bool


def ingest_trade(session: Session, data: TradeIn) -> IngestResult:
    """Record a trade exactly once.

    Returns IngestResult(duplicate=True) for an identical resubmission, raises
    TradeConflictError for the same identity with a different payload, and
    InsufficientInventoryError for an oversell. On any error nothing is persisted.
    `session` must be fresh (no transaction begun yet).
    """
    try:
        with session.begin():  # commits on success, rolls back on any exception
            return _ingest(session, data)
    except IntegrityError as exc:
        if _constraint_name(exc) != TRADE_IDENTITY_CONSTRAINT:
            raise
    # We lost a race on the unique constraint: another transaction committed this
    # (venue, trade_id) between our pre-check and our insert. Resolve it deterministically.
    with session.begin():
        existing = _find_trade(session, data)
        if existing is None:  # pragma: no cover - the winner committed, so it must exist
            raise RuntimeError("unique violation on trade identity but no trade found")
        return _resolve_existing(existing, data)


def _ingest(session: Session, data: TradeIn) -> IngestResult:
    existing = _find_trade(session, data)
    if existing is not None:
        return _resolve_existing(existing, data)

    position = _lock_position(session, data)

    # Re-check under the lock: a concurrent identical submission may have committed while we
    # waited for the position lock.
    existing = _find_trade(session, data)
    if existing is not None:
        return _resolve_existing(existing, data)

    if data.side is Side.BUY:
        return IngestResult(_apply_buy(session, position, data), duplicate=False)
    return IngestResult(_apply_sell(session, position, data), duplicate=False)


# ------------------------------------------------------------------------- idempotency
def _find_trade(session: Session, data: TradeIn) -> Trade | None:
    return session.scalars(
        select(Trade).where(Trade.venue == data.venue, Trade.trade_id == data.trade_id)
    ).one_or_none()


def _resolve_existing(existing: Trade, data: TradeIn) -> IngestResult:
    differences = trade_differences(existing, data)
    if differences:
        raise TradeConflictError(
            f"Trade {data.trade_id!r} on venue {data.venue!r} was already recorded with "
            "different details",
            {"internal_id": existing.id, "differences": [d.model_dump() for d in differences]},
        )
    return IngestResult(existing, duplicate=True)


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


# ------------------------------------------------------------------------- locking
def _lock_position(session: Session, data: TradeIn) -> Position:
    """Create the position row if needed, then lock it until the transaction ends."""
    key = {"account_id": data.account_id, "venue": data.venue, "symbol": data.symbol}
    session.execute(
        pg_insert(Position).values(**key).on_conflict_do_nothing(constraint="uq_positions_key")
    )
    return session.scalars(select(Position).filter_by(**key).with_for_update()).one()


# ------------------------------------------------------------------------- applying
def _insert_trade(session: Session, data: TradeIn) -> Trade:
    trade = Trade(
        venue=data.venue,
        trade_id=data.trade_id,
        account_id=data.account_id,
        symbol=data.symbol,
        side=data.side.value,
        quantity=data.quantity,
        price=data.price,
        timestamp=data.timestamp,
    )
    session.add(trade)
    session.flush()  # assigns trade.id; raises IntegrityError on a duplicate identity
    return trade


def _apply_buy(session: Session, position: Position, data: TradeIn) -> Trade:
    trade = _insert_trade(session, data)
    session.add(
        Lot(
            position_id=position.id,
            trade_pk=trade.id,
            original_quantity=data.quantity,
            remaining_quantity=data.quantity,
            price=data.price,
        )
    )
    return trade


def _apply_sell(session: Session, position: Position, data: TradeIn) -> Trade:
    lots = session.scalars(
        select(Lot)
        .where(Lot.position_id == position.id, Lot.remaining_quantity > 0)
        .order_by(Lot.id)
    ).all()
    # Pure computation first: an oversell raises here, before anything is written.
    matches = match_fifo(
        [OpenLot(lot.id, lot.remaining_quantity, lot.price) for lot in lots],
        data.quantity,
        data.price,
    )
    trade = _insert_trade(session, data)
    _record_matches(session, position, trade, {lot.id: lot for lot in lots}, matches)
    return trade


def _record_matches(
    session: Session,
    position: Position,
    sell_trade: Trade,
    lots_by_id: dict[int, Lot],
    matches: list[Match],
) -> None:
    for match in matches:
        lot = lots_by_id[match.lot_id]
        lot.remaining_quantity = CTX.subtract(lot.remaining_quantity, match.quantity)
        session.add(
            LotMatch(
                position_id=position.id,
                lot_id=lot.id,
                sell_trade_pk=sell_trade.id,
                quantity=match.quantity,
                buy_price=match.buy_price,
                sell_price=match.sell_price,
                realized_pnl=match.realized_pnl,
            )
        )
