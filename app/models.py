"""SQLAlchemy persistence models.

Source-of-truth layout (see DESIGN.md):
  * trades       - immutable ledger of accepted trades; `id` is the acceptance order.
  * positions    - one row per (account, venue, symbol). Holds no numbers; it exists to be
                   row-locked so concurrent trades on one position are serialized.
  * lots         - FIFO inventory: one lot per accepted BUY, with its remaining quantity.
  * lot_matches  - audit trail of which lot each SELL consumed, and the realized P&L.
Quantity and realized P&L are always derived from lots / lot_matches, never stored twice.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

QTY = Numeric(36, 18)  # quantities and prices
PNL = Numeric(80, 36)  # price-difference * quantity needs up to 36 decimal places


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("venue", "trade_id", name="uq_trades_venue_trade_id"),
        CheckConstraint("quantity > 0", name="ck_trades_quantity_positive"),
        CheckConstraint("price > 0", name="ck_trades_price_positive"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_trades_side"),
        Index("ix_trades_account_symbol", "account_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venue: Mapped[str] = mapped_column(String(64))
    trade_id: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    price: Mapped[Decimal] = mapped_column(QTY)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "venue", "symbol", name="uq_positions_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64))
    venue: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (
        CheckConstraint("remaining_quantity >= 0", name="ck_lots_remaining_non_negative"),
        CheckConstraint(
            "remaining_quantity <= original_quantity", name="ck_lots_remaining_le_original"
        ),
        Index("ix_lots_position_id", "position_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    trade_pk: Mapped[int] = mapped_column(ForeignKey("trades.id"), unique=True)
    original_quantity: Mapped[Decimal] = mapped_column(QTY)
    remaining_quantity: Mapped[Decimal] = mapped_column(QTY)
    price: Mapped[Decimal] = mapped_column(QTY)


class LotMatch(Base):
    __tablename__ = "lot_matches"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_lot_matches_quantity_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"))
    sell_trade_pk: Mapped[int] = mapped_column(ForeignKey("trades.id"))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    buy_price: Mapped[Decimal] = mapped_column(QTY)
    sell_price: Mapped[Decimal] = mapped_column(QTY)
    realized_pnl: Mapped[Decimal] = mapped_column(PNL)


class MarketPrice(Base):
    """Mock mark price supplied through the API (not a market-data feed)."""

    __tablename__ = "market_prices"
    __table_args__ = (CheckConstraint("price > 0", name="ck_market_prices_price_positive"),)

    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[Decimal] = mapped_column(QTY)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
