"""gas_ledger.relay_plan against tests/spec/session-commits.json: every
esr.commit.*.json (a plain task's, the fence's) is relayed as it appears, a
session.commit.<n>.<key16>.<rn>.json only once a session.output.<seq>.commits
names it and that etny-so row's receipt is in, one no object names never;
the 256 cap counts both prefixes."""
import json
import os

from constants import ESR_LEDGER_CAP
from gas_ledger import commit_file_key, relay_plan

FIXTURE = os.path.join(os.path.dirname(__file__), "spec", "session-commits.json")
with open(FIXTURE, encoding="utf-8") as f:
    SPEC = json.load(f)

FENCE = SPEC["fence"]["name"]
ORPHAN = SPEC["orphan"]["name"]
STEP = SPEC["steps"]["withCommits"]
NAMES = json.loads(STEP["commitsObject"]["content"])
BEFORE = SPEC["relay"]["beforeReceipt"]
AFTER = SPEC["relay"]["afterReceipt"]


def test_before_the_rows_receipt_only_the_fence_file_goes():
    plan = relay_plan(BEFORE["listing"], {}, [], set())
    assert plan == BEFORE["relayNow"] == [FENCE]
    assert not set(BEFORE["held"]) & set(plan)
    assert ORPHAN not in plan


def test_after_the_receipt_the_steps_files_follow_the_fence_in_the_lists_order():
    plan = relay_plan(AFTER["listing"], {3: NAMES}, [3], set())
    assert plan == AFTER["relayNow"]
    assert plan[1:] == NAMES
    assert ORPHAN not in plan


def test_a_name_already_relayed_is_left_out():
    assert relay_plan(AFTER["listing"], {3: NAMES}, [3], {FENCE}) == NAMES
    assert relay_plan(AFTER["listing"], {3: NAMES}, [3], set(AFTER["relayNow"])) == []


def test_a_row_without_a_commits_object_names_nothing():
    empty = json.loads(SPEC["steps"]["withoutCommits"]["commitsObject"]["content"])
    assert empty == []
    assert relay_plan(AFTER["listing"], {4: empty}, [4], set()) == [FENCE]


def test_a_row_whose_receipt_reverted_unlocks_nothing():
    # The names are known from the .commits object but the row is not landed.
    assert relay_plan(AFTER["listing"], {3: NAMES}, [], set()) == [FENCE]


def test_a_named_file_not_yet_staged_waits():
    listing = [n for n in AFTER["listing"] if n != NAMES[1]]
    assert relay_plan(listing, {3: NAMES}, [3], set()) == [FENCE, NAMES[0]]


def test_rows_come_in_row_order_and_keys_as_string_or_int():
    listing = AFTER["listing"] + ["session.commit.5.aaaaaaaaaaaaaaaa.0.json"]
    plan = relay_plan(listing, {"5": ["session.commit.5.aaaaaaaaaaaaaaaa.0.json"], 3: NAMES}, [5, 3], set())
    assert plan == [FENCE, *NAMES, "session.commit.5.aaaaaaaaaaaaaaaa.0.json"]


def test_fence_files_sort_by_key_then_relay_nonce():
    listing = ["esr.commit.bbbb.2.json", "esr.commit.aaaa.10.json", "esr.commit.aaaa.9.json", "esr.commit.7.json"]
    assert relay_plan(listing, {}, [], set()) == [
        "esr.commit.7.json", "esr.commit.aaaa.9.json", "esr.commit.aaaa.10.json", "esr.commit.bbbb.2.json"]


def test_the_cap_counts_both_prefixes():
    relayed = {f"esr.commit.k.{i}.json" for i in range(ESR_LEDGER_CAP - 1)}
    assert len(relay_plan(AFTER["listing"], {3: NAMES}, [3], relayed)) == 1
    relayed.add("session.commit.1.k.0.json")
    assert relay_plan(AFTER["listing"], {3: NAMES}, [3], relayed) == []


def test_the_six_and_five_part_names_give_key_and_nonce():
    assert commit_file_key(NAMES[0]) == ("2c0e8db8fb1343f0", 7)
    assert commit_file_key(FENCE) == ("2c0e8db8fb1343f0", 8)
    assert commit_file_key("esr.commit.3.json") == ("", 3)
    assert commit_file_key("esr.commit.x.json") == ("~", 0)
