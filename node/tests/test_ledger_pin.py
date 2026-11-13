"""utils.ledger_rebuild: the ESR ledger pinned by CIDv1 == result field 6 is
rebuilt, when the staged esr.authorizations.json is ahead of the attested
ledger (a step cut by the handover window), from the session.commit files
the staged rows' .commits objects name, in row order (E26); the fixture's
withCommits step rebuilds to authorizations.afterSteps[0].content and its
CID, the fence file is never part of it, and a missing record gives a CID
that does not match, so nothing is pinned."""
import json
import os

from utils import Storage, ledger_rebuild

FIXTURE = os.path.join(os.path.dirname(__file__), "spec", "session-commits.json")
with open(FIXTURE, encoding="utf-8") as f:
    SPEC = json.load(f)

STEP = SPEC["steps"]["withCommits"]
CONTENTS = [item["content"] for item in STEP["commitFiles"]]
AFTER = SPEC["authorizations"]["afterSteps"]
LEDGER_CID = SPEC["authorizations"]["ledgerCid"]


def test_the_steps_records_rebuild_the_staged_ledger_and_its_cid():
    rebuilt = ledger_rebuild(CONTENTS)
    assert rebuilt == AFTER[0]["content"] == SPEC["authorizations"]["ledgerJson"]
    assert Storage.cidv1_raw(rebuilt.encode("utf-8")) == LEDGER_CID
    assert [n["name"] for n in STEP["commitFiles"]] == json.loads(STEP["commitsObject"]["content"])


def test_a_step_without_commits_adds_nothing():
    assert SPEC["steps"]["withoutCommits"]["commitFiles"] == []
    assert ledger_rebuild(CONTENTS + []) == AFTER[1]["content"]


def test_the_fence_file_is_not_part_of_the_ledger():
    with_fence = ledger_rebuild(CONTENTS + [SPEC["fence"]["content"]])
    assert Storage.cidv1_raw(with_fence.encode("utf-8")) != LEDGER_CID


def test_a_missing_record_gives_a_cid_that_does_not_match():
    for partial in ([CONTENTS[0]], [CONTENTS[1]], []):
        assert Storage.cidv1_raw(ledger_rebuild(partial).encode("utf-8")) != LEDGER_CID
    assert ledger_rebuild([]) == "[]"


def test_the_rebuild_is_the_compact_array_of_the_records():
    assert ledger_rebuild(["{}", "{}"]) == "[{},{}]"
    assert ledger_rebuild(["{\"a\":1}"]) == "[{\"a\":1}]"
