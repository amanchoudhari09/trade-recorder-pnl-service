from typing import Any

from fastapi.testclient import TestClient
from httpx import Response


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "venue": "binance",
        "trade_id": "T1",
        "account_id": "a1",
        "symbol": "BTC-USD",
        "side": "BUY",
        "quantity": "10",
        "price": "100",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    return {**base, **overrides}


def post(client: TestClient, **overrides: Any) -> Response:
    return client.post("/trades", json=payload(**overrides))


def position(client: TestClient, **filters: str) -> dict[str, Any]:
    rows = client.get("/positions", params=filters).json()
    assert len(rows) == 1, rows
    return rows[0]
