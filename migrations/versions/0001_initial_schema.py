"""initial schema

Revision ID: 0001
Revises: (none)
Create Date: 2026-10-21 05:42:04.006067
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_prices",
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("price", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price > 0", name="ck_market_prices_price_positive"),
        sa.PrimaryKeyConstraint("symbol"),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("venue", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "venue", "symbol", name="uq_positions_key"),
    )
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("venue", sa.String(length=64), nullable=False),
        sa.Column("trade_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("price", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("side IN ('BUY', 'SELL')", name="ck_trades_side"),
        sa.CheckConstraint("price > 0", name="ck_trades_price_positive"),
        sa.CheckConstraint("quantity > 0", name="ck_trades_quantity_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue", "trade_id", name="uq_trades_venue_trade_id"),
    )
    op.create_index("ix_trades_account_symbol", "trades", ["account_id", "symbol"], unique=False)
    op.create_table(
        "lots",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_pk", sa.BigInteger(), nullable=False),
        sa.Column("original_quantity", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("price", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.CheckConstraint(
            "remaining_quantity <= original_quantity", name="ck_lots_remaining_le_original"
        ),
        sa.CheckConstraint("remaining_quantity >= 0", name="ck_lots_remaining_non_negative"),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trade_pk"],
            ["trades.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_pk"),
    )
    op.create_index("ix_lots_position_id", "lots", ["position_id", "id"], unique=False)
    op.create_table(
        "lot_matches",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("position_id", sa.BigInteger(), nullable=False),
        sa.Column("lot_id", sa.BigInteger(), nullable=False),
        sa.Column("sell_trade_pk", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("buy_price", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("sell_price", sa.Numeric(precision=36, scale=18), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=80, scale=36), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_lot_matches_quantity_positive"),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["lots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sell_trade_pk"],
            ["trades.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_lot_matches_position_id"), "lot_matches", ["position_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_lot_matches_position_id"), table_name="lot_matches")
    op.drop_table("lot_matches")
    op.drop_index("ix_lots_position_id", table_name="lots")
    op.drop_table("lots")
    op.drop_index("ix_trades_account_symbol", table_name="trades")
    op.drop_table("trades")
    op.drop_table("positions")
    op.drop_table("market_prices")
