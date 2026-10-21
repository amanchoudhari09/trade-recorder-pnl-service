from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.money import fmt
from app.schemas import ReconcileTradesRequest, Side, TradeIn, TradeOut

VALID = {
    "venue": "binance",
    "trade_id": "T1",
    "symbol": "BTC-USD",
    "side": "BUY",
    "quantity": "1.5",
    "price": "100",
    "timestamp": "2026-01-01T00:00:00Z",
}


def make(**overrides):
    return TradeIn.model_validate({**VALID, **overrides})


def test_valid_trade_defaults_account():
    trade = make()
    assert trade.account_id == "default"
    assert trade.side is Side.BUY
    assert trade.quantity == D("1.5")


def test_normalization_of_venue_symbol_side():
    trade = make(venue=" Binance ", symbol="btc-usd", side="sell")
    assert (trade.venue, trade.symbol, trade.side) == ("binance", "BTC-USD", Side.SELL)


@pytest.mark.parametrize("side", ["HOLD", "", "BUYY", None, 1])
def test_invalid_side_rejected(side):
    with pytest.raises(ValidationError):
        make(side=side)


@pytest.mark.parametrize("field", ["quantity", "price"])
@pytest.mark.parametrize("value", ["0", "-1", "-0.0001", "NaN", "Infinity", "abc", None])
def test_non_positive_or_non_finite_numbers_rejected(field, value):
    with pytest.raises(ValidationError):
        make(**{field: value})


def test_more_than_18_decimal_places_rejected():
    with pytest.raises(ValidationError):
        make(quantity="0.1234567890123456789")


@pytest.mark.parametrize(
    "timestamp", ["2026-01-01T00:00:00", "2026-01-01", "not-a-date", "", None, 1767225600]
)
def test_naive_or_malformed_timestamp_rejected(timestamp):
    with pytest.raises(ValidationError):
        make(timestamp=timestamp)


def test_timestamp_with_offset_is_accepted():
    assert make(timestamp="2026-01-01T02:00:00+02:00").timestamp.utcoffset() is not None


@pytest.mark.parametrize(
    "field", ["venue", "trade_id", "symbol", "side", "quantity", "price", "timestamp"]
)
def test_required_fields(field):
    payload = {k: v for k, v in VALID.items() if k != field}
    with pytest.raises(ValidationError):
        TradeIn.model_validate(payload)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        make(fee="1")


def test_json_float_converts_without_artifacts():
    trade = TradeIn.model_validate({**VALID, "quantity": 0.1})
    assert trade.quantity == D("0.1")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (D("1.500"), "1.5"),
        (D("100"), "100"),
        (D("1E+2"), "100"),
        (D("0E-18"), "0"),
        (D("0.04"), "0.04"),
    ],
)
def test_fmt_is_plain_and_trimmed(value, text):
    assert fmt(value) == text


def test_decimals_serialize_as_strings():
    out = TradeOut(
        id=1,
        venue="v",
        trade_id="t",
        account_id="a",
        symbol="X",
        side=Side.BUY,
        quantity=D("1.500000000000000000"),
        price=D("100"),
        timestamp="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
    )
    dumped = out.model_dump(mode="json")
    assert dumped["quantity"] == "1.5" and dumped["price"] == "100"


def test_reconcile_window_must_be_ordered():
    with pytest.raises(ValidationError):
        ReconcileTradesRequest.model_validate(
            {
                "venue": "v",
                "records": [],
                "window_start": "2026-02-01T00:00:00Z",
                "window_end": "2026-01-01T00:00:00Z",
            }
        )
