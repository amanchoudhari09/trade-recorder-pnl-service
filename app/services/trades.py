"""Read-side queries for trades."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models import Trade
from app.schemas import normalize_symbol, normalize_venue


def list_trades(
    session: Session,
    *,
    venue: str | None = None,
    symbol: str | None = None,
    account_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Trade], int]:
    """Trades in acceptance order, filtered and paginated. Returns (page, total matching)."""
    conditions = []
    if venue:
        conditions.append(Trade.venue == normalize_venue(venue))
    if symbol:
        conditions.append(Trade.symbol == normalize_symbol(symbol))
    if account_id:
        conditions.append(Trade.account_id == account_id)

    total = session.scalar(select(func.count()).select_from(Trade).where(*conditions)) or 0
    page = session.scalars(
        select(Trade).where(*conditions).order_by(Trade.id).limit(limit).offset(offset)
    ).all()
    return list(page), total


def get_trade(session: Session, venue: str, trade_id: str) -> Trade:
    trade = session.scalars(
        select(Trade).where(Trade.venue == normalize_venue(venue), Trade.trade_id == trade_id)
    ).one_or_none()
    if trade is None:
        raise NotFoundError(f"No trade {trade_id!r} on venue {normalize_venue(venue)!r}")
    return trade
