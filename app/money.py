"""Decimal helpers.

All prices, quantities and P&L are `Decimal`. Inputs are limited to 18 integer digits and
18 decimal places (see schemas.py), so every product and sum the service computes fits
comfortably inside this context's 100-digit precision and is therefore *exact*. The only
inexact operation is average cost (a division), which is rounded explicitly.
"""

from decimal import ROUND_HALF_EVEN, Context, Decimal

CTX = Context(prec=100, rounding=ROUND_HALF_EVEN)

ZERO = Decimal(0)
QUANTUM_18 = Decimal(1).scaleb(-18)  # 1e-18


def fmt(value: Decimal) -> str:
    """Plain (non-scientific) string without trailing zeros: Decimal('1.500') -> '1.5'."""
    if value == 0:
        return "0"
    return format(value.normalize(CTX), "f")
