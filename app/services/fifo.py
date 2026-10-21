"""Pure FIFO inventory accounting (no database, no I/O).

Long-only: a SELL consumes the oldest open BUY lots first. Fees are out of scope.

    realized   = (sell_price - lot_price) * matched_quantity
    unrealized = (mark_price - lot_price) * remaining_quantity
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.errors import InsufficientInventoryError
from app.money import CTX, QUANTUM_18, ZERO


@dataclass(frozen=True)
class OpenLot:
    lot_id: int  # lot ids increase in acceptance order, so they define FIFO order
    remaining: Decimal
    price: Decimal


@dataclass(frozen=True)
class Match:
    lot_id: int
    quantity: Decimal
    buy_price: Decimal
    sell_price: Decimal
    realized_pnl: Decimal


def total_quantity(lots: Sequence[OpenLot]) -> Decimal:
    with _exact():
        return sum((lot.remaining for lot in lots), ZERO)


def match_fifo(lots: Sequence[OpenLot], quantity: Decimal, sell_price: Decimal) -> list[Match]:
    """Match a SELL against open lots, oldest first.

    Pure function: nothing is mutated, so a failed (oversell) attempt has no side effects.
    Raises InsufficientInventoryError if `quantity` exceeds the open quantity.
    """
    available = total_quantity(lots)
    if quantity > available:
        raise InsufficientInventoryError(quantity, available)

    matches: list[Match] = []
    to_sell = quantity
    with _exact():
        for lot in sorted(lots, key=lambda lot: lot.lot_id):
            if to_sell == 0:
                break
            taken = min(lot.remaining, to_sell)
            if taken <= 0:
                continue
            matches.append(
                Match(
                    lot_id=lot.lot_id,
                    quantity=taken,
                    buy_price=lot.price,
                    sell_price=sell_price,
                    realized_pnl=(sell_price - lot.price) * taken,
                )
            )
            to_sell -= taken
    return matches


def unrealized_pnl(lots: Sequence[OpenLot], mark_price: Decimal) -> Decimal:
    with _exact():
        return sum(((mark_price - lot.price) * lot.remaining for lot in lots), ZERO)


def average_cost(lots: Sequence[OpenLot]) -> Decimal | None:
    """Quantity-weighted average price of open lots; None if there is no open inventory.

    A division, so the result is rounded (half-even) to 18 dp. Display only: P&L is always
    computed lot by lot, never from this average.
    """
    quantity = total_quantity(lots)
    if quantity == 0:
        return None
    with _exact():
        cost = sum((lot.price * lot.remaining for lot in lots), ZERO)
        return (cost / quantity).quantize(QUANTUM_18)


def _exact():
    return localcontext(CTX)
