"""D. FIFO accounting, positions, P&L and mark prices through the API."""

from decimal import Decimal as D

from sqlalchemy import text

from tests.integration.helpers import position, post


def mark(client, symbol="BTC-USD", price="140"):
    r = client.put(f"/market-prices/{symbol}", json={"price": price})
    assert r.status_code == 200
    return r.json()


def spec_scenario(client):
    post(client, trade_id="B1", quantity="10", price="100")
    post(client, trade_id="B2", quantity="10", price="120")
    post(client, trade_id="S1", side="SELL", quantity="15", price="150")


def test_spec_scenario_exact_values(client):
    spec_scenario(client)
    mark(client, price="140")

    pos = position(client)
    assert pos["quantity"] == "5"
    assert pos["average_cost"] == "120"
    assert pos["realized_pnl"] == "650"  # 10*(150-100) + 5*(150-120)
    assert pos["mark_price"] == "140"
    assert pos["unrealized_pnl"] == "100"  # 5*(140-120)
    assert pos["total_pnl"] == "750"
    assert pos["open_lots"] == [
        {"trade_id": "B2", "original_quantity": "10", "remaining_quantity": "5", "price": "120"}
    ]

    assert client.get("/pnl").json() == {
        "realized_pnl": "650",
        "unrealized_pnl": "100",
        "total_pnl": "750",
        "missing_mark_prices": [],
        "position_count": 1,
    }


def test_oldest_lot_is_fully_consumed_before_the_next(client, db_engine):
    spec_scenario(client)
    sql = "SELECT lot_id, quantity, buy_price, realized_pnl FROM lot_matches ORDER BY id"
    with db_engine.connect() as conn:
        rows = conn.execute(text(sql)).all()
    assert [(r.lot_id, r.quantity, r.buy_price) for r in rows] == [
        (1, D(10), D(100)),
        (2, D(5), D(120)),
    ]
    assert [r.realized_pnl for r in rows] == [D(500), D(150)]


def test_missing_mark_price_is_explicit_not_zero(client):
    spec_scenario(client)
    pos = position(client)
    assert pos["mark_price"] is None
    assert pos["unrealized_pnl"] is None and pos["total_pnl"] is None
    assert pos["realized_pnl"] == "650"  # realized never needs a mark
    summary = client.get("/pnl").json()
    assert summary["realized_pnl"] == "650"
    assert summary["unrealized_pnl"] is None and summary["total_pnl"] is None
    assert summary["missing_mark_prices"] == ["BTC-USD"]


def test_updating_the_mark_price_changes_unrealized_only(client):
    spec_scenario(client)
    mark(client, price="140")
    mark(client, price="110")
    pos = position(client)
    assert (pos["realized_pnl"], pos["unrealized_pnl"], pos["total_pnl"]) == ("650", "-50", "600")


def test_mark_price_validation_and_symbol_normalization(client):
    for bad in ("0", "-1", "abc", None):
        assert client.put("/market-prices/BTC-USD", json={"price": bad}).status_code == 422
    assert mark(client, symbol="btc-usd", price="1.5")["symbol"] == "BTC-USD"


def test_sell_consuming_only_part_of_the_oldest_lot(client):
    post(client, trade_id="B1", quantity="10", price="100")
    post(client, trade_id="B2", quantity="10", price="120")
    post(client, trade_id="S1", side="SELL", quantity="4", price="130")
    mark(client, price="130")
    pos = position(client)
    assert pos["realized_pnl"] == "120"  # 4 * 30
    assert [(lot["trade_id"], lot["remaining_quantity"]) for lot in pos["open_lots"]] == [
        ("B1", "6"),
        ("B2", "10"),
    ]
    assert pos["average_cost"] == "112.5"  # (6*100 + 10*120) / 16
    assert pos["unrealized_pnl"] == "280"  # 6*30 + 10*10


def test_sell_consuming_several_lots_and_exact_closure(client):
    for i, price in enumerate(("10", "20", "30"), start=1):
        post(client, trade_id=f"B{i}", quantity="2", price=price)
    post(client, trade_id="S1", side="SELL", quantity="6", price="25")
    pos = position(client)
    assert pos["quantity"] == "0"
    assert pos["realized_pnl"] == "30"  # 2*15 + 2*5 + 2*-5
    assert pos["open_lots"] == []
    # flat position: nothing to mark, so unrealized is a real 0 even without a mark price
    assert (pos["unrealized_pnl"], pos["total_pnl"]) == ("0", "30")


def test_loss_making_trade(client):
    post(client, trade_id="B1", quantity="10", price="100")
    post(client, trade_id="S1", side="SELL", quantity="10", price="90")
    assert position(client)["realized_pnl"] == "-100"


def test_multiple_symbols_accounts_and_venues_stay_isolated(client):
    post(client, trade_id="1", symbol="BTC-USD", price="100")
    post(client, trade_id="2", symbol="BTC-USD", account_id="a2", price="200")
    post(client, trade_id="3", symbol="ETH-USD", price="10")
    post(client, trade_id="4", symbol="BTC-USD", venue="kraken", price="300")
    post(
        client, trade_id="5", side="SELL", symbol="BTC-USD", quantity="10", price="110"
    )  # a1/binance only

    positions = client.get("/positions").json()
    assert [
        (p["account_id"], p["venue"], p["symbol"], p["quantity"], p["realized_pnl"])
        for p in positions
    ] == [
        ("a1", "binance", "BTC-USD", "0", "100"),
        ("a1", "binance", "ETH-USD", "10", "0"),
        ("a1", "kraken", "BTC-USD", "10", "0"),
        ("a2", "binance", "BTC-USD", "10", "0"),
    ]
    assert [
        p["symbol"]
        for p in client.get("/positions", params={"account_id": "a1", "venue": "binance"}).json()
    ] == ["BTC-USD", "ETH-USD"]
    assert len(client.get("/positions", params={"symbol": "eth-usd"}).json()) == 1
    assert client.get("/pnl", params={"account_id": "a2"}).json()["realized_pnl"] == "0"
    assert (
        client.get(
            "/pnl", params={"account_id": "a1", "venue": "binance", "symbol": "BTC-USD"}
        ).json()["realized_pnl"]
        == "100"
    )


def test_partial_mark_coverage_makes_the_aggregate_unavailable_but_lists_what_is_missing(client):
    post(client, trade_id="1", symbol="BTC-USD", price="100")
    post(client, trade_id="2", symbol="ETH-USD", price="10")
    mark(client, symbol="BTC-USD", price="110")
    summary = client.get("/pnl").json()
    assert summary["unrealized_pnl"] is None and summary["missing_mark_prices"] == ["ETH-USD"]
    mark(client, symbol="ETH-USD", price="12")
    assert client.get("/pnl").json()["unrealized_pnl"] == "120"  # 10*10 + 10*2


def test_decimal_math_avoids_float_artifacts_end_to_end(client):
    post(client, trade_id="B1", quantity="0.1", price="0.1")
    post(client, trade_id="B2", quantity="0.2", price="0.2")
    post(client, trade_id="S1", side="SELL", quantity="0.3", price="0.3")
    assert position(client)["realized_pnl"] == "0.04"  # floats would give 0.04000000000000001


def test_json_numbers_and_strings_are_equivalent_inputs(client):
    r = client.post(
        "/trades",
        json={
            "venue": "binance",
            "trade_id": "T9",
            "symbol": "BTC-USD",
            "side": "BUY",
            "quantity": 0.1,
            "price": 100.5,
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 201
    assert (r.json()["trade"]["quantity"], r.json()["trade"]["price"]) == ("0.1", "100.5")


def test_positions_and_pnl_are_empty_without_trades(client):
    assert client.get("/positions").json() == []
    assert client.get("/pnl").json() == {
        "realized_pnl": "0",
        "unrealized_pnl": "0",
        "total_pnl": "0",
        "missing_mark_prices": [],
        "position_count": 0,
    }
