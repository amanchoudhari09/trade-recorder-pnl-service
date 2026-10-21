"""E. Reconciliation: read-only comparison against a supplied snapshot."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError

from tests.integration.helpers import post


def ext(trade_id="T1", **kw):
    base = {
        "trade_id": trade_id,
        "symbol": "BTC-USD",
        "side": "BUY",
        "quantity": "10",
        "price": "100",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    return {**base, **kw}


def reconcile(client, records, venue="binance", **extra):
    r = client.post("/reconciliation/trades", json={"venue": venue, "records": records, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_exact_match_reports_no_discrepancies(client):
    post(client, trade_id="T1")
    post(client, trade_id="T2", side="SELL", quantity="4", price="110")
    report = reconcile(client, [ext("T1"), ext("T2", side="SELL", quantity="4", price="110")])
    assert report["summary"] == {
        "matched": 2,
        "missing_internal": 0,
        "missing_external": 0,
        "duplicate_external": 0,
        "conflicts": 0,
        "has_discrepancies": False,
    }
    assert (report["records_examined"], report["internal_trades_examined"]) == (2, 2)


def test_external_trade_absent_internally_is_reported(client):
    post(client, trade_id="T1")
    report = reconcile(client, [ext("T1"), ext("GHOST", price="105")])
    assert [r["trade_id"] for r in report["missing_internal"]] == ["GHOST"]
    assert report["missing_internal"][0]["price"] == "105"
    assert report["summary"]["has_discrepancies"] is True


def test_internal_trade_absent_from_snapshot_is_reported(client):
    post(client, trade_id="T1")
    post(client, trade_id="T2")
    report = reconcile(client, [ext("T1")])
    assert [r["trade_id"] for r in report["missing_external"]] == ["T2"]
    assert report["missing_external"][0]["account_id"] == "a1"


def test_duplicate_external_identities_are_reported(client):
    post(client, trade_id="T1")
    report = reconcile(client, [ext("T1"), ext("T1"), ext("T1", quantity="11")])
    assert report["duplicate_external"] == [
        {"identity": "T1", "occurrences": 3, "identical": False}
    ]
    assert report["summary"]["has_discrepancies"] is True


def test_conflicting_details_for_same_identity_are_reported_per_field(client):
    post(client, trade_id="T1")
    report = reconcile(client, [ext("T1", quantity="9", price="101")])
    (conflict,) = report["conflicts"]
    assert conflict["trade_id"] == "T1"
    assert {(d["field"], d["internal"], d["external"]) for d in conflict["differences"]} == {
        ("quantity", "10", "9"),
        ("price", "100", "101"),
    }
    assert report["summary"]["matched"] == 0


def test_account_is_compared_only_when_the_snapshot_supplies_it(client):
    post(client, trade_id="T1")
    assert reconcile(client, [ext("T1")])["summary"]["has_discrepancies"] is False
    report = reconcile(client, [ext("T1", account_id="other")])
    assert report["conflicts"][0]["differences"][0]["field"] == "account_id"


def test_empty_snapshot_against_empty_ledger(client):
    report = reconcile(client, [])
    assert report["summary"]["has_discrepancies"] is False and report["records_examined"] == 0


def test_empty_snapshot_flags_every_internal_trade(client):
    post(client, trade_id="T1")
    report = reconcile(client, [])
    assert report["summary"]["missing_external"] == 1 and report["summary"]["has_discrepancies"]


def test_scope_is_the_snapshot_venue_only(client):
    post(client, trade_id="T1", venue="binance")
    post(client, trade_id="K1", venue="kraken")
    report = reconcile(client, [ext("T1")], venue="Binance")
    assert report["internal_trades_examined"] == 1 and not report["summary"]["has_discrepancies"]


def test_time_window_limits_which_internal_trades_are_expected(client):
    post(client, trade_id="OLD", timestamp="2025-12-01T00:00:00Z")
    post(client, trade_id="T1", timestamp="2026-01-01T00:00:00Z")
    unscoped = reconcile(client, [ext("T1")])
    assert [r["trade_id"] for r in unscoped["missing_external"]] == ["OLD"]
    scoped = reconcile(
        client, [ext("T1")], window_start="2025-12-31T00:00:00Z", window_end="2026-01-02T00:00:00Z"
    )
    assert scoped["internal_trades_examined"] == 1 and not scoped["summary"]["has_discrepancies"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"venue": "binance"},
        {"records": []},
        {"venue": "binance", "records": "nope"},
        {"venue": "binance", "records": [ext(side="HOLD")]},
        {"venue": "binance", "records": [ext(quantity="0")]},
        {"venue": "binance", "records": [ext(timestamp="2026-01-01T00:00:00")]},
        {"venue": "binance", "records": [{"trade_id": "T1"}]},
        {
            "venue": "binance",
            "records": [],
            "window_start": "2026-02-01T00:00:00Z",
            "window_end": "2026-01-01T00:00:00Z",
        },
        {"venue": "binance", "records": [], "surprise": 1},
    ],
)
def test_invalid_payloads_are_rejected_without_touching_the_ledger(client, count, body):
    post(client, trade_id="T1")
    r = client.post("/reconciliation/trades", json=body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert count("trades") == 1


def test_reconciliation_does_not_mutate_trades_or_positions(client, count):
    post(client, trade_id="T1")
    post(client, trade_id="T2", side="SELL", quantity="3")
    trades_before = client.get("/trades").json()
    positions_before = client.get("/positions").json()
    counts_before = [
        count(t) for t in ("trades", "positions", "lots", "lot_matches", "market_prices")
    ]

    # a snapshot that disagrees about everything must still change nothing
    reconcile(client, [ext("GHOST"), ext("T1", quantity="99"), ext("T1")])
    client.post(
        "/reconciliation/positions",
        json={
            "venue": "binance",
            "positions": [{"account_id": "a1", "symbol": "BTC-USD", "quantity": "1"}],
        },
    )

    assert client.get("/trades").json() == trades_before
    assert client.get("/positions").json() == positions_before
    assert [
        count(t) for t in ("trades", "positions", "lots", "lot_matches", "market_prices")
    ] == counts_before


def test_reconciliation_transaction_is_read_only_at_the_database_level(session_factory):
    """Even a bug could not write: PostgreSQL rejects writes in the reconciliation txn."""
    from app.services.reconciliation import _begin_read_only

    with session_factory() as session:
        _begin_read_only(session)
        with pytest.raises((InternalError, OperationalError, ProgrammingError)) as exc:
            session.execute(text("INSERT INTO market_prices (symbol, price) VALUES ('X', 1)"))
        assert "read-only transaction" in str(exc.value)


# ----------------------------------------------------------------------- positions
def test_position_reconciliation_report(client):
    post(client, trade_id="B1", quantity="10")
    post(client, trade_id="S1", side="SELL", quantity="3")
    post(client, trade_id="E1", symbol="ETH-USD", quantity="2")
    r = client.post(
        "/reconciliation/positions",
        json={
            "venue": "binance",
            "positions": [
                {"account_id": "a1", "symbol": "BTC-USD", "quantity": "7"},  # matches
                {"account_id": "a1", "symbol": "SOL-USD", "quantity": "5"},  # not held internally
                {"account_id": "a1", "symbol": "XRP-USD", "quantity": "0"},  # flat both sides: fine
            ],
        },
    )
    assert r.status_code == 200
    report = r.json()
    assert report["summary"] == {
        "matched": 2,
        "quantity_mismatches": 0,
        "missing_internal": 1,
        "missing_external": 1,
        "duplicate_external": 0,
        "has_discrepancies": True,
    }
    assert report["missing_internal"][0]["symbol"] == "SOL-USD"
    assert report["missing_external"][0] == {
        "account_id": "a1",
        "symbol": "ETH-USD",
        "internal_quantity": "2",
        "external_quantity": "0",
    }


def test_position_quantity_mismatch_and_duplicates(client):
    post(client, trade_id="B1", quantity="10")
    r = client.post(
        "/reconciliation/positions",
        json={
            "venue": "binance",
            "positions": [
                {"account_id": "a1", "symbol": "BTC-USD", "quantity": "9"},
                {"account_id": "a1", "symbol": "btc-usd", "quantity": "9"},
            ],
        },
    )
    report = r.json()
    assert report["quantity_mismatches"] == [
        {
            "account_id": "a1",
            "symbol": "BTC-USD",
            "internal_quantity": "10",
            "external_quantity": "9",
        }
    ]
    assert report["duplicate_external"] == [
        {"identity": "a1/BTC-USD", "occurrences": 2, "identical": True}
    ]


@pytest.mark.parametrize(
    "positions", [[{"symbol": "X", "quantity": "-1"}], [{"quantity": "1"}], "x"]
)
def test_invalid_position_payloads_rejected(client, positions):
    r = client.post("/reconciliation/positions", json={"venue": "binance", "positions": positions})
    assert r.status_code == 422
