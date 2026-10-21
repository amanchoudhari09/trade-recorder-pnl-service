from decimal import Decimal as D

import pytest

from app.errors import InsufficientInventoryError
from app.money import CTX
from app.services.fifo import OpenLot, average_cost, match_fifo, total_quantity, unrealized_pnl


def lots(*pairs: tuple[str, str]) -> list[OpenLot]:
    return [OpenLot(i + 1, D(q), D(p)) for i, (q, p) in enumerate(pairs)]


def realized(matches) -> D:
    return sum((m.realized_pnl for m in matches), D(0))


def test_spec_scenario_buy10_at_100_buy10_at_120_sell15_at_150():
    inventory = lots(("10", "100"), ("10", "120"))
    matches = match_fifo(inventory, D(15), D(150))

    assert [(m.lot_id, m.quantity, m.buy_price) for m in matches] == [
        (1, D(10), D(100)),
        (2, D(5), D(120)),
    ]
    assert realized(matches) == D(650)  # 10*(150-100) + 5*(150-120)

    remaining = [OpenLot(2, D(5), D(120))]
    assert total_quantity(remaining) == D(5)
    assert average_cost(remaining) == D(120)
    assert unrealized_pnl(remaining, D(140)) == D(100)  # 5 * (140-120)
    assert realized(matches) + unrealized_pnl(remaining, D(140)) == D(750)


def test_sell_consuming_only_part_of_the_oldest_lot():
    matches = match_fifo(lots(("10", "100"), ("10", "120")), D(4), D(130))
    assert [(m.lot_id, m.quantity) for m in matches] == [(1, D(4))]
    assert realized(matches) == D(120)


def test_sell_spanning_several_lots():
    matches = match_fifo(lots(("2", "10"), ("3", "20"), ("5", "30")), D(9), D(25))
    assert [(m.lot_id, m.quantity) for m in matches] == [(1, D(2)), (2, D(3)), (3, D(4))]
    assert realized(matches) == D(2 * 15 + 3 * 5 + 4 * -5)


def test_exact_closure_of_position():
    inventory = lots(("10", "100"), ("10", "120"))
    matches = match_fifo(inventory, D(20), D(110))
    assert sum((m.quantity for m in matches), D(0)) == D(20)
    assert realized(matches) == D(10 * 10 + 10 * -10)


def test_loss_making_sell_is_negative():
    matches = match_fifo(lots(("10", "100")), D(10), D(90))
    assert realized(matches) == D(-100)


def test_unrealized_loss_when_mark_below_cost():
    assert unrealized_pnl(lots(("5", "120")), D(100)) == D(-100)


def test_oversell_raises_and_reports_quantities():
    with pytest.raises(InsufficientInventoryError) as exc:
        match_fifo(lots(("10", "100")), D("10.000000000000000001"), D(100))
    assert exc.value.details == {"requested": "10.000000000000000001", "available": "10"}


def test_sell_with_no_inventory_raises():
    with pytest.raises(InsufficientInventoryError):
        match_fifo([], D(1), D(100))


def test_lots_are_matched_by_lot_id_not_by_list_order():
    shuffled = [OpenLot(2, D(10), D(120)), OpenLot(1, D(10), D(100))]
    matches = match_fifo(shuffled, D(5), D(150))
    assert [m.lot_id for m in matches] == [1]


def test_exhausted_lots_are_skipped():
    inventory = [OpenLot(1, D(0), D(100)), OpenLot(2, D(5), D(120))]
    matches = match_fifo(inventory, D(5), D(150))
    assert [m.lot_id for m in matches] == [2]


def test_decimal_math_has_no_float_artifacts():
    # With floats: 0.1*(0.3-0.1) + 0.2*(0.3-0.2) == 0.04000000000000001
    matches = match_fifo(lots(("0.1", "0.1"), ("0.2", "0.2")), D("0.3"), D("0.3"))
    assert realized(matches) == D("0.04")
    assert str(realized(matches)).rstrip("0") in {"0.04"}


def test_exact_at_extreme_precision():
    big = D("999999999999999999.999999999999999999")  # 36 digits: the max the API accepts
    sell_price = CTX.subtract(big, 1)  # exact; the default 28-digit context would round it
    matches = match_fifo([OpenLot(1, big, big)], big, sell_price)
    assert matches[0].realized_pnl == big.copy_negate()  # (sell_price - big) * big, exactly


def test_average_cost_is_weighted_and_none_when_flat():
    assert average_cost(lots(("1", "100"), ("3", "200"))) == D("175")
    assert average_cost([]) is None


def test_match_does_not_mutate_inputs():
    inventory = lots(("10", "100"))
    match_fifo(inventory, D(3), D(100))
    assert inventory[0].remaining == D(10)
