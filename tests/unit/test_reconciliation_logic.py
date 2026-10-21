from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.models import Trade
from app.schemas import ExternalPosition, ExternalTradeRecord
from app.services.reconciliation import build_position_report, build_trade_report

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def internal(trade_id="T1", **kw) -> Trade:
    base = dict(
        id=1,
        venue="v",
        trade_id=trade_id,
        account_id="a1",
        symbol="BTC-USD",
        side="BUY",
        quantity=D("10"),
        price=D("100"),
        timestamp=T0,
    )
    return Trade(**{**base, **kw})


def external(trade_id="T1", **kw) -> ExternalTradeRecord:
    base = dict(
        trade_id=trade_id,
        symbol="BTC-USD",
        side="BUY",
        quantity="10",
        price="100",
        timestamp=T0.isoformat(),
    )
    return ExternalTradeRecord.model_validate({**base, **kw})


def report(inner, outer):
    return build_trade_report("v", inner, outer)


def test_exact_match_has_no_discrepancies():
    r = report([internal("T1"), internal("T2")], [external("T1"), external("T2")])
    assert r.summary.matched == 2
    assert not r.summary.has_discrepancies
    assert (r.records_examined, r.internal_trades_examined) == (2, 2)


def test_value_equal_decimals_and_same_instant_in_other_zone_match():
    other_zone = T0.astimezone(UTC.__class__(timedelta(hours=2))).isoformat()
    r = report([internal()], [external(quantity="10.000", price="100.0", timestamp=other_zone)])
    assert r.summary.matched == 1 and not r.summary.has_discrepancies


def test_external_trade_missing_internally():
    r = report([], [external("X9")])
    assert [m.trade_id for m in r.missing_internal] == ["X9"]
    assert r.summary.has_discrepancies


def test_internal_trade_missing_from_snapshot():
    r = report([internal("T1"), internal("T2")], [external("T1")])
    assert [m.trade_id for m in r.missing_external] == ["T2"]
    assert r.summary.matched == 1 and r.summary.has_discrepancies


def test_duplicate_external_identity_identical_copies():
    r = report([internal()], [external(), external()])
    assert [(d.identity, d.occurrences, d.identical) for d in r.duplicate_external] == [
        ("T1", 2, True)
    ]
    assert r.summary.matched == 1  # compared once, via the first occurrence
    assert r.summary.has_discrepancies


def test_duplicate_external_identity_with_disagreeing_copies():
    r = report([internal()], [external(), external(quantity="11")])
    assert r.duplicate_external[0].identical is False


def test_conflicting_details_list_each_differing_field():
    r = report([internal()], [external(quantity="11", price="101", side="SELL")])
    (conflict,) = r.conflicts
    assert {(d.field, d.internal, d.external) for d in conflict.differences} == {
        ("quantity", "10", "11"),
        ("price", "100", "101"),
        ("side", "BUY", "SELL"),
    }
    assert r.summary.matched == 0


def test_account_only_compared_when_supplied():
    assert report([internal()], [external()]).summary.matched == 1
    assert report([internal()], [external(account_id="a1")]).summary.matched == 1
    r = report([internal()], [external(account_id="other")])
    assert r.conflicts[0].differences[0].field == "account_id"


def test_empty_snapshot_against_empty_ledger():
    r = report([], [])
    assert not r.summary.has_discrepancies and r.records_examined == 0


def test_empty_snapshot_reports_every_internal_trade_missing():
    r = report([internal("T1"), internal("T2")], [])
    assert r.summary.missing_external == 2


# ----------------------------------------------------------------------- positions
def ext_pos(symbol="BTC-USD", qty="10", account="a1") -> ExternalPosition:
    return ExternalPosition(account_id=account, symbol=symbol, quantity=D(qty))


def test_positions_match():
    r = build_position_report("v", {("a1", "BTC-USD"): D(10)}, [ext_pos()])
    assert r.summary.matched == 1 and not r.summary.has_discrepancies


def test_position_quantity_mismatch():
    r = build_position_report("v", {("a1", "BTC-USD"): D(10)}, [ext_pos(qty="9")])
    (m,) = r.quantity_mismatches
    assert (m.internal_quantity, m.external_quantity) == (D(10), D(9))


def test_position_missing_internal_and_external():
    r = build_position_report("v", {("a1", "ETH-USD"): D(3)}, [ext_pos()])
    assert [m.symbol for m in r.missing_internal] == ["BTC-USD"]
    assert [m.symbol for m in r.missing_external] == ["ETH-USD"]


def test_flat_external_position_matches_absent_internal():
    r = build_position_report("v", {}, [ext_pos(qty="0")])
    assert r.summary.matched == 1 and not r.summary.has_discrepancies


def test_duplicate_external_positions_flagged():
    r = build_position_report("v", {("a1", "BTC-USD"): D(10)}, [ext_pos(), ext_pos(qty="5")])
    assert [(d.identity, d.identical) for d in r.duplicate_external] == [("a1/BTC-USD", False)]
