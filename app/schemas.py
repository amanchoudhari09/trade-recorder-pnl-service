"""Pydantic request/response models (validation and serialization only)."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from app.money import fmt

DEFAULT_ACCOUNT = "default"


# --------------------------------------------------------------------------- primitives
class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


def _strip_upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def normalize_symbol(value: str) -> str:
    return value.strip().upper()


def normalize_venue(value: str) -> str:
    return value.strip().lower()


# Decimals are serialized as plain strings ("1.5"), never floats, so no precision is lost.
DecimalStr = Annotated[Decimal, PlainSerializer(fmt, return_type=str, when_used="json")]
# Inputs: up to 18 integer digits and 18 decimal places (matches NUMERIC(36, 18) columns).
PositiveDecimal = Annotated[Decimal, Field(gt=0, max_digits=36, decimal_places=18)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, max_digits=36, decimal_places=18)]

Symbol = Annotated[
    str,
    Field(min_length=1, max_length=64),
    AfterValidator(normalize_symbol),  # BTC-USD, btc-usd -> BTC-USD (one position, not two)
]
Venue = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(normalize_venue)]
AccountId = Annotated[str, Field(min_length=1, max_length=64)]
TradeId = Annotated[str, Field(min_length=1, max_length=128)]
SideField = Annotated[Side, BeforeValidator(_strip_upper)]


def _require_iso_string(value: object) -> object:
    # Pydantic would otherwise accept bare numbers as Unix epochs (seconds vs milliseconds
    # is ambiguous), so only ISO-8601 strings are allowed.
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string with a UTC offset")
    return value


# ISO-8601 string that includes a UTC offset ("2026-01-01T12:00:00Z" / "+02:00").
IsoTimestamp = Annotated[AwareDatetime, BeforeValidator(_require_iso_string)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- trades
class _TradeCore(_Strict):
    trade_id: TradeId = Field(description="Stable identifier assigned by the venue.")
    symbol: Symbol
    side: SideField
    quantity: PositiveDecimal
    price: PositiveDecimal = Field(description="Execution price in the symbol's quote currency.")
    timestamp: IsoTimestamp = Field(description="Execution time; ISO-8601 with a UTC offset.")


class TradeIn(_TradeCore):
    venue: Venue
    account_id: AccountId = Field(
        default=DEFAULT_ACCOUNT, description="Account/portfolio the trade belongs to."
    )

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "venue": "binance",
                    "trade_id": "T-1001",
                    "account_id": "desk-1",
                    "symbol": "BTC-USD",
                    "side": "BUY",
                    "quantity": "10",
                    "price": "100.5",
                    "timestamp": "2026-01-01T12:00:00Z",
                }
            ]
        },
    )


class TradeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Server-generated internal id; also the acceptance order.")
    venue: str
    trade_id: str
    account_id: str
    symbol: str
    side: Side
    quantity: DecimalStr
    price: DecimalStr
    timestamp: datetime
    created_at: datetime


class IngestResponse(BaseModel):
    status: Literal["accepted", "duplicate"]
    trade: TradeOut


class TradeList(BaseModel):
    items: list[TradeOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- market prices
class MarketPriceIn(_Strict):
    price: PositiveDecimal = Field(description="Mock mark price in the symbol's quote currency.")


class MarketPriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    price: DecimalStr
    updated_at: datetime


# --------------------------------------------------------------------------- positions / P&L
class LotOut(BaseModel):
    trade_id: str = Field(description="Venue trade id of the BUY that created the lot.")
    original_quantity: DecimalStr
    remaining_quantity: DecimalStr
    price: DecimalStr


class PositionOut(BaseModel):
    account_id: str
    venue: str
    symbol: str
    quantity: DecimalStr
    average_cost: DecimalStr | None = Field(
        description="Quantity-weighted cost of open lots (rounded to 18 dp); null when flat."
    )
    open_lots: list[LotOut]
    realized_pnl: DecimalStr
    mark_price: DecimalStr | None = Field(description="Supplied mark price; null if none was set.")
    unrealized_pnl: DecimalStr | None = Field(
        description="Null when there is open inventory but no mark price. 0 when flat."
    )
    total_pnl: DecimalStr | None = Field(description="realized + unrealized; null if unavailable.")


class PnlSummary(BaseModel):
    realized_pnl: DecimalStr
    unrealized_pnl: DecimalStr | None = Field(
        description="Null if any open position in scope has no mark price."
    )
    total_pnl: DecimalStr | None
    missing_mark_prices: list[str] = Field(
        description="Symbols with open inventory but no supplied mark price."
    )
    position_count: int


# --------------------------------------------------------------------------- reconciliation
class FieldDifference(BaseModel):
    field: str
    internal: str
    external: str


class TradeRecordOut(BaseModel):
    trade_id: str
    account_id: str | None
    symbol: str
    side: str
    quantity: DecimalStr
    price: DecimalStr
    timestamp: datetime


class ExternalTradeRecord(_TradeCore):
    account_id: AccountId | None = Field(
        default=None, description="Compared with the internal account only when supplied."
    )


class ReconcileTradesRequest(_Strict):
    venue: Venue = Field(description="Venue the snapshot was taken from.")
    records: list[ExternalTradeRecord] = Field(max_length=10_000)
    window_start: IsoTimestamp | None = Field(
        default=None, description="If set, only internal trades at/after this time are compared."
    )
    window_end: IsoTimestamp | None = Field(
        default=None, description="If set, only internal trades at/before this time are compared."
    )

    @model_validator(mode="after")
    def _window_is_ordered(self) -> "ReconcileTradesRequest":
        if self.window_start and self.window_end and self.window_start > self.window_end:
            raise ValueError("window_start must not be after window_end")
        return self


class DuplicateExternal(BaseModel):
    identity: str
    occurrences: int
    identical: bool = Field(description="False if the repeated records differ from each other.")


class TradeConflict(BaseModel):
    trade_id: str
    differences: list[FieldDifference]


class TradeReconciliationSummary(BaseModel):
    matched: int
    missing_internal: int
    missing_external: int
    duplicate_external: int
    conflicts: int
    has_discrepancies: bool


class TradeReconciliationReport(BaseModel):
    venue: str
    records_examined: int
    internal_trades_examined: int
    summary: TradeReconciliationSummary
    missing_internal: list[TradeRecordOut] = Field(
        description="In the external snapshot but not in the internal ledger."
    )
    missing_external: list[TradeRecordOut] = Field(
        description="In the internal ledger (within scope) but not in the external snapshot."
    )
    duplicate_external: list[DuplicateExternal]
    conflicts: list[TradeConflict]


class ExternalPosition(_Strict):
    account_id: AccountId = DEFAULT_ACCOUNT
    symbol: Symbol
    quantity: NonNegativeDecimal


class ReconcilePositionsRequest(_Strict):
    venue: Venue
    positions: list[ExternalPosition] = Field(max_length=10_000)


class PositionMismatch(BaseModel):
    account_id: str
    symbol: str
    internal_quantity: DecimalStr
    external_quantity: DecimalStr


class PositionReconciliationSummary(BaseModel):
    matched: int
    quantity_mismatches: int
    missing_internal: int
    missing_external: int
    duplicate_external: int
    has_discrepancies: bool


class PositionReconciliationReport(BaseModel):
    venue: str
    records_examined: int
    summary: PositionReconciliationSummary
    quantity_mismatches: list[PositionMismatch]
    missing_internal: list[PositionMismatch] = Field(
        description="External quantity > 0 but no internal open position."
    )
    missing_external: list[PositionMismatch] = Field(
        description="Internal open position absent from the external snapshot."
    )
    duplicate_external: list[DuplicateExternal]


# --------------------------------------------------------------------------- errors
class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
