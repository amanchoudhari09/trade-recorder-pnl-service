"""Positions, P&L and mock market prices (read model over lots / lot_matches)."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Lot, LotMatch, MarketPrice, Position, Trade
from app.money import CTX, ZERO
from app.schemas import (
    LotOut,
    PnlSummary,
    PositionOut,
    normalize_symbol,
    normalize_venue,
)
from app.services.fifo import OpenLot, average_cost, total_quantity, unrealized_pnl


def set_market_price(session: Session, symbol: str, price: Decimal) -> MarketPrice:
    """Upsert the supplied mark price for a symbol (mock input, not a market-data feed)."""
    symbol = normalize_symbol(symbol)
    with session.begin():
        session.execute(
            pg_insert(MarketPrice)
            .values(symbol=symbol, price=price)
            .on_conflict_do_update(
                index_elements=["symbol"], set_={"price": price, "updated_at": func.now()}
            )
        )
        return session.scalars(select(MarketPrice).filter_by(symbol=symbol)).one()


def list_positions(
    session: Session,
    *,
    account_id: str | None = None,
    venue: str | None = None,
    symbol: str | None = None,
) -> list[PositionOut]:
    """Positions keyed by (account_id, venue, symbol), with FIFO lots and P&L."""
    conditions = []
    if account_id:
        conditions.append(Position.account_id == account_id)
    if venue:
        conditions.append(Position.venue == normalize_venue(venue))
    if symbol:
        conditions.append(Position.symbol == normalize_symbol(symbol))

    positions = session.scalars(
        select(Position)
        .where(*conditions)
        .order_by(Position.account_id, Position.venue, Position.symbol)
    ).all()
    ids = [p.id for p in positions]

    open_lots: dict[int, list[tuple[Lot, str]]] = defaultdict(list)
    for lot, venue_trade_id in session.execute(
        select(Lot, Trade.trade_id)
        .join(Trade, Trade.id == Lot.trade_pk)
        .where(Lot.position_id.in_(ids), Lot.remaining_quantity > 0)
        .order_by(Lot.id)
    ):
        open_lots[lot.position_id].append((lot, venue_trade_id))

    realized = dict(
        session.execute(
            select(LotMatch.position_id, func.sum(LotMatch.realized_pnl))
            .where(LotMatch.position_id.in_(ids))
            .group_by(LotMatch.position_id)
        ).all()
    )
    marks = {
        mp.symbol: mp.price
        for mp in session.scalars(
            select(MarketPrice).where(MarketPrice.symbol.in_({p.symbol for p in positions}))
        )
    }
    return [
        _build_position(p, open_lots.get(p.id, []), realized.get(p.id, ZERO), marks.get(p.symbol))
        for p in positions
    ]


def pnl_summary(
    session: Session,
    *,
    account_id: str | None = None,
    venue: str | None = None,
    symbol: str | None = None,
) -> PnlSummary:
    positions = list_positions(session, account_id=account_id, venue=venue, symbol=symbol)
    missing = sorted({p.symbol for p in positions if p.unrealized_pnl is None})
    realized = CTX.add(ZERO, ZERO)
    unrealized: Decimal | None = ZERO
    for p in positions:
        realized = CTX.add(realized, p.realized_pnl)
        if unrealized is not None and p.unrealized_pnl is not None:
            unrealized = CTX.add(unrealized, p.unrealized_pnl)
    if missing:
        unrealized = None
    total = None if unrealized is None else CTX.add(realized, unrealized)
    return PnlSummary(
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_pnl=total,
        missing_mark_prices=missing,
        position_count=len(positions),
    )


def _build_position(
    position: Position,
    lots: list[tuple[Lot, str]],
    realized: Decimal,
    mark: Decimal | None,
) -> PositionOut:
    open_lots = [OpenLot(lot.id, lot.remaining_quantity, lot.price) for lot, _ in lots]
    quantity = total_quantity(open_lots)

    if quantity == 0:
        unrealized: Decimal | None = ZERO  # flat: nothing open, so nothing to mark
    elif mark is None:
        unrealized = None  # open inventory but no supplied price: unavailable, never 0
    else:
        unrealized = unrealized_pnl(open_lots, mark)

    return PositionOut(
        account_id=position.account_id,
        venue=position.venue,
        symbol=position.symbol,
        quantity=quantity,
        average_cost=average_cost(open_lots),
        open_lots=[
            LotOut(
                trade_id=venue_trade_id,
                original_quantity=lot.original_quantity,
                remaining_quantity=lot.remaining_quantity,
                price=lot.price,
            )
            for lot, venue_trade_id in lots
        ],
        realized_pnl=realized,
        mark_price=mark,
        unrealized_pnl=unrealized,
        total_pnl=None if unrealized is None else CTX.add(realized, unrealized),
    )
