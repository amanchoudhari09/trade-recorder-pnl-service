"""C. Oversell protection and atomic rollback."""

import pytest

from tests.integration.helpers import position, post


def test_selling_exactly_the_position_succeeds_and_leaves_it_flat(client):
    post(client)
    assert post(client, trade_id="S1", side="SELL", quantity="10").status_code == 201
    pos = position(client)
    assert pos["quantity"] == "0" and pos["open_lots"] == [] and pos["average_cost"] is None


def test_selling_less_than_the_position_succeeds(client):
    post(client)
    assert (
        post(client, trade_id="S1", side="SELL", quantity="9.999999999999999999").status_code == 201
    )
    assert position(client)["quantity"] == "0.000000000000000001"


def test_selling_more_than_the_position_is_rejected_with_details(client):
    post(client)
    r = post(client, trade_id="S1", side="SELL", quantity="10.000000000000000001")
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "insufficient_inventory"
    assert error["details"] == {"requested": "10.000000000000000001", "available": "10"}
    assert "short selling is not supported" in error["message"]


def test_rejected_oversell_persists_nothing_and_leaves_inventory_untouched(client, count):
    post(client, trade_id="B1", quantity="5")
    post(client, trade_id="B2", quantity="5", price="120")
    before = client.get("/positions").json()

    assert post(client, trade_id="S1", side="SELL", quantity="11").status_code == 422

    assert client.get("/positions").json() == before
    assert (count("trades"), count("lots"), count("lot_matches")) == (2, 2, 0)
    assert client.get("/trades/binance/S1").status_code == 404


def test_retry_after_the_position_grows_is_not_blocked_by_the_rejection(client):
    post(client, trade_id="B1", quantity="5")
    assert post(client, trade_id="S1", side="SELL", quantity="8").status_code == 422
    post(client, trade_id="B2", quantity="5")
    retry = post(client, trade_id="S1", side="SELL", quantity="8")
    assert retry.status_code == 201 and retry.json()["status"] == "accepted"
    assert position(client)["quantity"] == "2"


def test_selling_a_never_traded_position_is_rejected_and_creates_no_position_row(client, count):
    r = post(client, side="SELL", quantity="1")
    assert r.status_code == 422
    assert count("trades") == 0 and count("positions") == 0  # lock row rolled back too


def test_inventory_is_isolated_by_account_venue_and_symbol(client):
    post(client, trade_id="B1", quantity="10")
    for other in ({"account_id": "a2"}, {"venue": "kraken"}, {"symbol": "ETH-USD"}):
        r = post(client, trade_id="S-x", side="SELL", quantity="1", **other)
        assert r.status_code == 422, other
    assert position(client)["quantity"] == "10"


def test_a_failure_in_the_middle_of_a_sell_rolls_everything_back(client, count, monkeypatch):
    """Atomicity: the trade row is inserted before lots are consumed; if consuming fails,
    the trade insert must not survive."""
    post(client, trade_id="B1", quantity="10")
    before = client.get("/positions").json()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure while consuming lots")

    monkeypatch.setattr("app.services.ingestion._record_matches", boom)
    failing = client.__class__(client.app, raise_server_exceptions=False)
    r = failing.post(
        "/trades",
        json={
            "venue": "binance",
            "trade_id": "S1",
            "account_id": "a1",
            "symbol": "BTC-USD",
            "side": "SELL",
            "quantity": "4",
            "price": "110",
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    monkeypatch.undo()

    assert (count("trades"), count("lot_matches")) == (1, 0)
    assert client.get("/positions").json() == before
    # and the same trade can still be recorded afterwards
    assert post(client, trade_id="S1", side="SELL", quantity="4").status_code == 201


@pytest.mark.parametrize("quantity", ["1", "5", "10"])
def test_every_sell_up_to_the_position_size_works(client, quantity):
    post(client)
    assert post(client, trade_id="S1", side="SELL", quantity=quantity).status_code == 201
