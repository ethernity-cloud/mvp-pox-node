"""gas_ledger.gas_wait_active holds the trustedzones' predicate (chain_fee.js
gasWaitActive, bounded as trustedzone.js.tmpl bounds it): a row, commit or
result waits while the base fee plus the pricing's priority fee is above the
order's cap, never without a base fee, under a cap of 0, inside the own
handover window or past the order's end; the fee fields of a relay outside
the wait are the cap bounded by the operator's ceiling (E22); utils.order_window
gives the end the wait is bounded by."""
from gas_ledger import capped_fee_fields, gas_wait_active
from utils import ORDER_GRACE_SECONDS, order_window

GWEI = 10**9
CAP = 100 * GWEI
PRIORITY = 30 * GWEI
NOW = 1_700_000_000
END = NOW + 3600


def test_above_the_cap_waits_and_under_it_sends():
    assert gas_wait_active(71 * GWEI, PRIORITY, CAP, NOW, END)
    assert not gas_wait_active(70 * GWEI, PRIORITY, CAP, NOW, END)
    assert not gas_wait_active(60 * GWEI, PRIORITY, CAP, NOW, END)


def test_no_base_fee_never_waits():
    assert not gas_wait_active(None, PRIORITY, CAP, NOW, END)


def test_cap_zero_never_waits():
    assert not gas_wait_active(500 * GWEI, PRIORITY, 0, NOW, END)


def test_the_own_handover_window_never_waits():
    assert not gas_wait_active(500 * GWEI, PRIORITY, CAP, NOW, END, in_window=True)


def test_past_the_orders_end_never_waits():
    assert not gas_wait_active(500 * GWEI, PRIORITY, CAP, END, END)
    assert not gas_wait_active(500 * GWEI, PRIORITY, CAP, END + 1, END)
    assert gas_wait_active(500 * GWEI, PRIORITY, CAP, END - 1, END)


def test_an_unknown_end_does_not_bound_the_wait():
    assert gas_wait_active(500 * GWEI, PRIORITY, CAP, NOW, None)


def test_the_relay_fee_is_the_cap_bounded_by_the_ceiling():
    assert capped_fee_fields(CAP, PRIORITY, 500 * GWEI) == (
        CAP, {'type': 2, 'maxFeePerGas': CAP, 'maxPriorityFeePerGas': PRIORITY})
    assert capped_fee_fields(CAP, PRIORITY, 20 * GWEI) == (
        20 * GWEI, {'type': 2, 'maxFeePerGas': 20 * GWEI, 'maxPriorityFeePerGas': 20 * GWEI})


def test_the_orders_end_is_the_placement_plus_the_duration():
    assert order_window(1_000, 2, NOW) == (8_200, 8_200 + ORDER_GRACE_SECONDS)


def test_without_a_placement_the_end_is_unknown_and_the_deadline_runs_from_now():
    assert order_window(None, 2, NOW) == (None, NOW + 7200 + ORDER_GRACE_SECONDS)
    assert order_window(0, 1, NOW) == (None, NOW + 3600 + ORDER_GRACE_SECONDS)
