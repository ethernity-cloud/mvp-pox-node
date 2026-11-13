"""gas_ledger.OrderGasLedger.plan under ESR_LEDGER_CAP: the plan is cut at the
order's room (relay_room) and the names left beyond it are counted in
beyond_cap, which the agent logs once per order; relay_plan gives the same
cut list; a plan within the room leaves beyond_cap at 0."""
from constants import ESR_LEDGER_CAP
from gas_ledger import OrderGasLedger, relay_candidates, relay_plan, relay_room

NAMES = [f"esr.commit.{'a' * 16}.{i}.json" for i in range(ESR_LEDGER_CAP + 5)]


def test_the_room_is_the_cap_less_the_names_relayed():
    assert relay_room(set()) == ESR_LEDGER_CAP
    assert relay_room(set(NAMES[:10])) == ESR_LEDGER_CAP - 10
    assert relay_room(set(NAMES)) == 0


def test_the_plan_is_cut_at_the_room_and_the_rest_counted():
    ledger = OrderGasLedger(9, None, None, 10**17)
    plan = ledger.plan(NAMES)
    assert plan == relay_plan(NAMES, {}, [], set()) == NAMES[:ESR_LEDGER_CAP]
    assert ledger.beyond_cap == 5
    assert relay_candidates(NAMES, {}, [], set()) == NAMES
    assert ledger.cap_logged is False


def test_a_plan_within_the_room_leaves_nothing_beyond():
    ledger = OrderGasLedger(9, None, None, 10**17)
    assert ledger.plan(NAMES[:3]) == NAMES[:3]
    assert ledger.beyond_cap == 0


def test_the_relayed_names_take_room_and_the_count_follows():
    ledger = OrderGasLedger(9, None, None, 10**17)
    ledger.skip(NAMES[:ESR_LEDGER_CAP - 1])
    assert ledger.plan(NAMES) == [NAMES[ESR_LEDGER_CAP - 1]]
    assert ledger.beyond_cap == 5
    ledger.skip([NAMES[ESR_LEDGER_CAP - 1]])
    assert ledger.plan(NAMES) == []
    assert ledger.beyond_cap == 5
