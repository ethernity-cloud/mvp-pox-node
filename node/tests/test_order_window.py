"""utils: an order's window is counted from the block that placed it, so a run
resumed after a restart gets what is left of it, not the duration over again
(bloxberg testnet order 2019 of 2026-10-23: resumed at 11:12 after its 10:25
placement, it ran a second hour). The placement is kept on disk per order."""
from utils import (ORDER_GRACE_SECONDS, order_placed_at, record_order_placement,
                   remaining_order_seconds)

PLACED = 1_792_750_000


def test_the_window_is_the_duration_plus_the_grace_from_the_placement():
    assert remaining_order_seconds(PLACED, 1, now=PLACED) == 3600 + ORDER_GRACE_SECONDS
    assert remaining_order_seconds(PLACED, 2, now=PLACED + 3600) == 3600 + ORDER_GRACE_SECONDS


def test_a_resumed_run_gets_what_is_left():
    assert remaining_order_seconds(PLACED, 1, now=PLACED + 2820) == 900


def test_a_window_that_has_passed_is_negative():
    assert remaining_order_seconds(PLACED, 1, now=PLACED + 3600 + ORDER_GRACE_SECONDS + 1) == -1


def test_a_placement_is_recorded_and_read_back(tmp_path):
    record_order_placement(str(tmp_path), 2019, PLACED)
    assert order_placed_at(str(tmp_path), 2019) == PLACED
    assert (tmp_path / "2019" / "placed_at").read_text() == str(PLACED)


def test_an_order_without_a_record_has_no_placement(tmp_path):
    assert order_placed_at(str(tmp_path), 2018) is None
    (tmp_path / "2017").mkdir()
    (tmp_path / "2017" / "placed_at").write_text("not a number")
    assert order_placed_at(str(tmp_path), 2017) is None
