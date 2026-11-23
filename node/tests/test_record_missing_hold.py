"""capability.RecordMissingHold (tests/spec/metadata-keys.md, reason 9
RecordMissing): after a reason-9 cancel the agent posts no DP request without
a capability record on that network unless the request names only -unsafe
trustedzones (capability.unsafe_only, which the validators pass over), posts
one carrying the record once a record is advertised (the hold cleared), and
keeps the hold in the network's broker_cursor.json across a reload, beside
the scan cursors (utils.read_cursor, utils.write_cursor); an agent that never
saw reason 9 posts without a record. While the hold stands, a passed test of
an image that can carry a record (dcap) and whose pass holds none runs
again. capability.cancel_reaction keys the reaction to a cancel on the
record the cancelled request carried (RecordStore.image_of)."""
import json
import re
from pathlib import Path

import pytest

import capability
from utils import read_cursor, record_key, runnable_trustedzones, write_cursor

SPEC = Path(__file__).resolve().parent / "spec"
RECORD = {"gflops": 250, "cuv": 4, "t1": 1761724830, "sig_der_hex": "3065AB"}
ADVERTISED = ("etny-nodenithy-testnet", RECORD)
CAS = "etny-nodenithy-testnet"
UNSAFE = "etny-nodenithy-testnet-unsafe"
# A joined bloxberg_testnet + bloxberg_testnet_unsafe handle's trustedzones.
JOINED = {CAS: "dcap", UNSAFE: "sgx"}


def test_the_spec_table_lists_the_agents_reasons():
    text = (SPEC / "metadata-keys.md").read_text(encoding="utf-8")
    rows = dict((int(code), name) for code, name in re.findall(r"^\| (\d+) \| `(\w+)` \|", text, re.M))
    assert rows == capability.CANCEL_REASONS
    assert rows[capability.REASON_RECORD_MISSING] == "RecordMissing"


def test_an_agent_that_never_saw_reason_9_posts_without_a_record(tmp_path):
    hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    assert hold.held is False
    assert hold.disposition(None, {CAS}) == "post"
    assert hold.disposition(None, set()) == "post"
    assert hold.disposition(ADVERTISED, {CAS}) == "post"
    assert not (tmp_path / "broker_cursor.json").exists()


@pytest.mark.parametrize("runnable", [{CAS}, {CAS, UNSAFE}, set()])
def test_after_a_reason_9_cancel_no_request_naming_a_cas_trustedzone_or_none_is_posted(tmp_path, runnable):
    hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    hold.set(True)
    assert hold.disposition(None, runnable) == "hold"


@pytest.mark.parametrize("runnable", [{UNSAFE}, {UNSAFE, "etny-pynithy-testnet-unsafe"}])
def test_under_the_hold_a_request_naming_only_unsafe_trustedzones_is_posted(tmp_path, runnable):
    hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    hold.set(True)
    assert hold.disposition(None, runnable) == "post"
    assert hold.held is True


def test_unsafe_only_needs_at_least_one_name_and_every_name_to_end_in_the_suffix():
    assert capability.unsafe_only([UNSAFE])
    assert capability.unsafe_only({UNSAFE, "etny-pynithy-testnet-unsafe"})
    assert not capability.unsafe_only([])
    assert not capability.unsafe_only([CAS])
    assert not capability.unsafe_only([CAS, UNSAFE])
    assert not capability.unsafe_only(["etny-unsafe-nodenithy"])
    assert not capability.unsafe_only(["etny-nodenithy-testnet-UNSAFE"])


def test_an_sgx_only_handle_with_the_hold_posts_once_its_test_passes(tmp_path):
    """bloxberg_testnet_unsafe run alone: its offer named no trustedzone
    before its sgx test passed (trustedzones=), the validators judged it
    reason 9 and the hold was set; once the test passes, the request names
    the -unsafe trustedzone alone and is posted, and no test of it is due
    for a record."""
    trustedzones = {UNSAFE: "sgx"}
    hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    hold.set(True)
    assert hold.disposition(None, runnable_trustedzones(trustedzones, [UNSAFE], {})) == "hold"
    runnable = runnable_trustedzones(trustedzones, [UNSAFE], {UNSAFE: "QmB"})
    assert runnable == {UNSAFE}
    assert hold.disposition(None, runnable) == "post"
    assert not hold.retest_due(trustedzones[UNSAFE], None)


def test_a_joined_handle_whose_dcap_test_fails_posts_its_unsafe_offer(tmp_path):
    """A joined handle on a non-DCAP host, or a DCAP node whose dcap test
    keeps failing: with the hold set and the -unsafe test passed alone, the
    request names only the -unsafe trustedzone and is posted; once the dcap
    test passes without a record, the request would name the CAS
    trustedzone and is held, and that test is due again for a record."""
    hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    hold.set(True)
    tested = [CAS, UNSAFE]
    assert hold.disposition(None, runnable_trustedzones(JOINED, tested, {UNSAFE: "QmB"})) == "post"
    both = runnable_trustedzones(JOINED, tested, {UNSAFE: "QmB", CAS: "QmA"})
    assert both == {CAS, UNSAFE}
    assert hold.disposition(None, both) == "hold"
    assert hold.retest_due(JOINED[CAS], None)
    assert hold.disposition(ADVERTISED, both) == "clear"


def test_a_record_clears_the_hold_and_the_request_is_posted(tmp_path):
    path = tmp_path / "broker_cursor.json"
    hold = capability.RecordMissingHold(path)
    hold.set(True)
    assert hold.disposition(ADVERTISED, {CAS}) == "clear"
    hold.set(False)
    assert hold.disposition(None, {CAS}) == "post"
    assert capability.RecordMissingHold(path).held is False


def test_the_hold_survives_a_reload_of_the_state(tmp_path):
    path = tmp_path / "broker_cursor.json"
    capability.RecordMissingHold(path).set(True)
    reloaded = capability.RecordMissingHold(path)
    assert reloaded.held is True
    assert reloaded.disposition(None, {CAS}) == "hold"
    assert json.loads(path.read_text()) == {capability.RECORD_MISSING_HOLD: True}


def test_the_hold_shares_the_file_with_the_scan_cursors(tmp_path):
    path = tmp_path / "broker_cursor.json"
    write_cursor(path, "dp_request_cancelled", 52_647_001)
    write_cursor(path, "refused_acceptances", [4101])
    capability.RecordMissingHold(path).set(True)
    assert read_cursor(path, "dp_request_cancelled") == 52_647_001
    assert read_cursor(path, "refused_acceptances") == [4101]
    assert read_cursor(path, capability.RECORD_MISSING_HOLD) is True
    write_cursor(path, "dp_request_cancelled", 52_647_100)
    assert capability.RecordMissingHold(path).held is True


def test_an_absent_or_unreadable_file_holds_nothing(tmp_path):
    path = tmp_path / "broker_cursor.json"
    assert read_cursor(path, capability.RECORD_MISSING_HOLD) is None
    path.write_text("{not json")
    assert capability.RecordMissingHold(path).held is False
    path.write_text("[1, 2]")
    assert read_cursor(path, "dp_request_cancelled") is None
    write_cursor(path, "dp_request_cancelled", 7)
    assert json.loads(path.read_text()) == {"dp_request_cancelled": 7}


def test_the_hold_re_tests_a_dcap_pass_without_a_record(tmp_path):
    hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    assert not hold.retest_due("dcap", None)
    hold.set(True)
    assert hold.retest_due(capability.ADMITTED_REQUIREMENT, None)
    assert not hold.retest_due("dcap", RECORD)
    assert not hold.retest_due("sgx", None)
    assert not hold.retest_due(None, None)


@pytest.mark.parametrize("code, key, reaction", [
    (9, None, (True, False)),
    (9, "3065ab", (True, True)),
    (1, "3065ab", (False, True)),
    (6, "3065ab", (False, True)),
    (8, "3065ab", (False, True)),
    (1, None, (False, False)),
    (7, "3065ab", (False, False)),
    (7, None, (False, False)),
    (10, "3065ab", (False, False)),
    (11, "3065ab", (False, False)),
])
def test_the_cancel_reaction_follows_the_record_the_cancelled_request_carried(code, key, reaction):
    assert capability.cancel_reaction(code, key) == reaction


def test_the_record_a_cancelled_request_carried_is_found_by_its_cusig(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put(CAS, "QmA", 1761724900, RECORD)
    store.put(UNSAFE, "QmB", 1761724900, None)
    assert record_key(RECORD) == "3065ab"
    assert store.image_of("3065ab") == CAS
    assert store.image_of("3065ac") is None
    assert store.image_of(None) is None
