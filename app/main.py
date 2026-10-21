from fastapi import FastAPI

from app.api import health, portfolio, reconciliation, trades
from app.api.handlers import install_error_handlers

DESCRIPTION = """
Records trades from multiple venues, tracks long-only positions and reports FIFO P&L.

* Idempotent on `(venue, trade_id)`; oversells are rejected atomically.
* Unrealized P&L uses a **mock mark price** you supply via `PUT /market-prices/{symbol}`.
* Reconciliation compares against a snapshot **you supply**; it is read-only.
* No authentication: out of scope for this assignment.
"""


def create_app() -> FastAPI:
    app = FastAPI(title="Trade Recorder & P&L Service", version="0.1.0", description=DESCRIPTION)
    for module in (health, trades, portfolio, reconciliation):
        app.include_router(module.router)
    install_error_handlers(app)
    return app


app = create_app()
