"""gas_ledger.OrderGasLedger: a landed etny-so row (receipt status 1) with
its session.output.<seq>.commits names unlocks the step's session.commit
files for the relay plan, a row that named none unlocks nothing, relayed
names are not listed again, and a reloaded ledger (orders/<id>/relay.json)
keeps the rows, the names, the relayed set and the counter."""
import json
import os

from gas_ledger import OrderGasLedger, OrderTerms, PricingParams

FIXTURE = os.path.join(os.path.dirname(__file__), "spec", "session-commits.json")
with open(FIXTURE, encoding="utf-8") as f:
    SPEC = json.load(f)

LISTING = SPEC["relay"]["afterReceipt"]["listing"]
FENCE = SPEC["fence"]["name"]
NAMES = json.loads(SPEC["steps"]["withCommits"]["commitsObject"]["content"])
TERMS = OrderTerms(10**15, 1000, 3 * 10**17, 100 * 10**9, 0, 10**18)
PARAMS = PricingParams(343834, 267445, 971708, 30 * 10**9)


def ledger(path=None):
    return OrderGasLedger(4101, TERMS, PARAMS, 10**17, path=path)


def test_the_rows_names_are_held_until_the_row_lands():
    held = ledger()
    assert held.plan(LISTING) == [FENCE]
    held.row_landed(3, NAMES)
    assert held.plan(LISTING) == [FENCE, *NAMES]


def test_a_row_that_named_nothing_unlocks_nothing():
    held = ledger()
    held.row_landed(4, [])
    assert held.plan(LISTING) == [FENCE]


def test_relayed_names_are_not_listed_again():
    held = ledger()
    held.row_landed(3, NAMES)
    held.charge_commits([FENCE, NAMES[0]], [10**14, 10**14])
    assert held.plan(LISTING) == [NAMES[1]]
    held.skip([NAMES[1]])
    assert held.plan(LISTING) == []


def test_the_ledger_is_reloaded_from_its_file(tmp_path):
    path = str(tmp_path / "orders" / "4101" / "relay.json")
    held = ledger(path)
    held.row_landed(3, NAMES)
    held.charge(5 * 10**14)
    held.charge_commits([FENCE], [2 * 10**14])
    held.settle([FENCE], 3 * 10**14)
    held.skip(["session.commit.9.0000000000000000.0.json"])
    reloaded = ledger(path)
    assert reloaded.rows_landed == [3]
    assert reloaded.commits_by_row == {3: NAMES}
    assert reloaded.relayed == [FENCE, "session.commit.9.0000000000000000.0.json"]
    assert reloaded.spent == 8 * 10**14
    assert reloaded.estimates == {}
    assert reloaded.plan(LISTING) == NAMES


def test_a_ledger_without_a_file_starts_empty(tmp_path):
    fresh = ledger(str(tmp_path / "relay.json"))
    assert (fresh.spent, fresh.relayed, fresh.rows_landed, fresh.commits_by_row) == (0, [], [], {})
    assert not os.path.exists(fresh.path)
