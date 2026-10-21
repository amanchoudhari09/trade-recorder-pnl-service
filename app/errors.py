"""Domain exceptions. Mapping to HTTP status codes lives in app/api/handlers.py."""

from decimal import Decimal
from typing import Any

from app.money import fmt


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class TradeConflictError(DomainError):
    """The same (venue, trade_id) was submitted before with a different payload."""

    code = "trade_conflict"


class InsufficientInventoryError(DomainError):
    """A SELL exceeds the available long position (short selling is not supported)."""

    code = "insufficient_inventory"

    def __init__(self, requested: Decimal, available: Decimal) -> None:
        super().__init__(
            f"Cannot sell {fmt(requested)}: only {fmt(available)} available "
            "(long-only policy; short selling is not supported)",
            {"requested": fmt(requested), "available": fmt(available)},
        )


class NotFoundError(DomainError):
    code = "not_found"
