"""Field-by-field comparison of two trade representations.

Shared by idempotency (resubmitted trade vs stored trade) and reconciliation (external
record vs internal trade), so both agree on what "same trade" means.
"""

from typing import Protocol

from app.money import fmt
from app.schemas import FieldDifference


class TradeLike(Protocol):
    account_id: str | None
    symbol: str
    side: object
    quantity: object
    price: object
    timestamp: object


def trade_differences(
    internal: TradeLike, external: TradeLike, *, compare_account: bool = True
) -> list[FieldDifference]:
    """Return the fields that differ. Decimals compare by value (100 == 100.00) and
    timestamps by instant (12:00Z == 14:00+02:00)."""
    fields = ["symbol", "side", "quantity", "price", "timestamp"]
    if compare_account:
        fields.insert(0, "account_id")
    return [
        FieldDifference(field=name, internal=_show(a), external=_show(b))
        for name in fields
        if (a := getattr(internal, name)) != (b := getattr(external, name))
    ]


def _show(value: object) -> str:
    if hasattr(value, "value"):  # Enum
        return str(value.value)
    if hasattr(value, "normalize"):  # Decimal
        return fmt(value)  # type: ignore[arg-type]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
