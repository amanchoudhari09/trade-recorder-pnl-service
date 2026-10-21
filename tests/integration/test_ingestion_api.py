import pytest

from tests.integration.helpers import payload, position, post


# ---------------------------------------------------------------- A. basic ingestion
def test_valid_buy_is_accepted_with_documented_schema(client):
    r = post(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "accepted"
    trade = body["trade"]
    assert set(trade) == {
        "id",
        "venue",
        "trade_id",
        "account_id",
        "symbol",
        "side",
        "quantity",
        "price",
        "timestamp",
        "created_at",
    }
    assert (trade["id"], trade["venue"], trade["side"]) == (1, "binance", "BUY")
    assert (trade["quantity"], trade["price"]) == ("10", "100")  # decimals are strings


def test_sell_against_sufficient_inventory_is_accepted(client):
    post(client)
    r = post(client, trade_id="T2", side="SELL", quantity="4", price="110")
    assert r.status_code == 201
    assert position(client)["quantity"] == "6"


def test_account_defaults_when_omitted(client):
    body = payload()
    del body["account_id"]
    r = client.post("/trades", json=body)
    assert r.status_code == 201 and r.json()["trade"]["account_id"] == "default"


def test_venue_symbol_and_side_are_normalized(client):
    r = post(client, venue=" Binance ", symbol="btc-usd", side="buy")
    trade = r.json()["trade"]
    assert (trade["venue"], trade["symbol"], trade["side"]) == ("binance", "BTC-USD", "BUY")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("side", "HOLD"),
        ("side", ""),
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "abc"),
        ("quantity", "NaN"),
        ("price", "0"),
        ("price", "-5"),
        ("price", "Infinity"),
        ("timestamp", "2026-01-01T00:00:00"),
        ("timestamp", "garbage"),
        ("timestamp", 1767225600),
        ("trade_id", ""),
        ("venue", ""),
        ("symbol", ""),
    ],
)
def test_invalid_input_is_rejected_with_consistent_error_shape(client, count, field, value):
    r = post(client, **{field: value})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"][-1] == field
    assert count("trades") == 0 and count("positions") == 0


def test_missing_fields_and_unknown_fields_are_rejected(client):
    assert client.post("/trades", json={}).status_code == 422
    assert post(client, fee="1").status_code == 422
    assert client.post("/trades", content=b"{not json").status_code == 422


# ---------------------------------------------------------------- B. idempotency
def test_same_trade_twice_has_one_business_effect(client, count):
    first = post(client)
    second = post(client)
    assert (first.status_code, first.json()["status"]) == (201, "accepted")
    assert (second.status_code, second.json()["status"]) == (200, "duplicate")
    assert second.json()["trade"]["id"] == first.json()["trade"]["id"]
    assert count("trades") == 1 and count("lots") == 1
    assert position(client)["quantity"] == "10"  # not 20


def test_duplicate_sell_does_not_consume_inventory_twice(client, count):
    post(client)
    post(client, trade_id="S1", side="SELL", quantity="4")
    again = post(client, trade_id="S1", side="SELL", quantity="4")
    assert again.json()["status"] == "duplicate"
    assert position(client)["quantity"] == "6"
    assert count("lot_matches") == 1


def test_same_instant_in_another_timezone_and_equal_decimals_is_still_a_duplicate(client):
    post(client, quantity="10", price="100")
    r = post(client, quantity="10.000", price="100.0", timestamp="2026-01-01T02:00:00+02:00")
    assert r.status_code == 200 and r.json()["status"] == "duplicate"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", "11"),
        ("price", "101"),
        ("side", "SELL"),
        ("symbol", "ETH-USD"),
        ("account_id", "a2"),
        ("timestamp", "2026-01-02T00:00:00Z"),
    ],
)
def test_same_identity_with_different_payload_conflicts(client, count, field, value):
    post(client)
    r = post(client, **{field: value})
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "trade_conflict"
    assert [d["field"] for d in error["details"]["differences"]] == [field]
    assert count("trades") == 1 and position(client)["quantity"] == "10"


def test_identity_is_venue_scoped_and_venue_case_insensitive(client, count):
    assert post(client, venue="binance").status_code == 201
    assert post(client, venue="kraken").status_code == 201  # same trade_id, other venue
    assert post(client, venue="BINANCE").status_code == 200  # same venue, different case
    assert count("trades") == 2


# ---------------------------------------------------------------- retrieval
def test_list_trades_filters_and_paginates(client):
    post(client, trade_id="T1")
    post(client, trade_id="T2", symbol="ETH-USD")
    post(client, trade_id="T3", venue="kraken", account_id="a2")

    everything = client.get("/trades").json()
    assert (everything["total"], [t["trade_id"] for t in everything["items"]]) == (
        3,
        ["T1", "T2", "T3"],
    )
    assert [
        t["trade_id"] for t in client.get("/trades", params={"venue": "KRAKEN"}).json()["items"]
    ] == ["T3"]
    assert [
        t["trade_id"] for t in client.get("/trades", params={"symbol": "eth-usd"}).json()["items"]
    ] == ["T2"]
    assert [
        t["trade_id"] for t in client.get("/trades", params={"account_id": "a2"}).json()["items"]
    ] == ["T3"]

    page = client.get("/trades", params={"limit": 1, "offset": 1}).json()
    assert (page["total"], page["limit"], page["offset"]) == (3, 1, 1)
    assert [t["trade_id"] for t in page["items"]] == ["T2"]
    assert client.get("/trades", params={"limit": 0}).status_code == 422
    assert client.get("/trades", params={"limit": 501}).status_code == 422


def test_get_trade_by_venue_identity(client):
    post(client, trade_id="T1")
    r = client.get("/trades/Binance/T1")
    assert r.status_code == 200 and r.json()["trade_id"] == "T1"


def test_get_trade_supports_slashes_in_venue_trade_ids(client):
    post(client, trade_id="2026/01/abc")
    assert client.get("/trades/binance/2026/01/abc").status_code == 200


def test_unknown_trade_and_unknown_route_use_the_error_shape(client):
    missing = client.get("/trades/binance/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    route = client.get("/does-not-exist")
    assert route.status_code == 404 and route.json()["error"]["code"] == "http_error"


# ---------------------------------------------------------------- health / docs
def test_liveness_and_readiness(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok", "database": "up"}


def test_readiness_reports_503_when_the_database_is_down(client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.database import get_session
    from app.main import app

    dead = create_engine(
        "postgresql+psycopg://x:y@127.0.0.1:1/none", connect_args={"connect_timeout": 2}
    )

    def broken():
        with Session(dead) as session:
            yield session

    app.dependency_overrides[get_session] = broken
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"
    assert client.get("/health").status_code == 200  # liveness unaffected
    assert client.get("/positions").json()["error"]["code"] == "database_unavailable"


def test_openapi_documents_the_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/trades",
        "/trades/{venue}/{trade_id}",
        "/positions",
        "/pnl",
        "/market-prices/{symbol}",
        "/reconciliation/trades",
        "/reconciliation/positions",
        "/health",
        "/health/ready",
    ):
        assert path in paths
    assert set(paths["/trades"]["post"]["responses"]) >= {"200", "201", "409", "422"}
