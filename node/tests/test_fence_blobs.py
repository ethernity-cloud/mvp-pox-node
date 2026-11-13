"""replay.fence_state_cids (PHASE-E-PLAN.md E35, F.7): from StateCommitted
logs and a bound, the last CID at or before the bound of every key moved
after it; a key not moved absent; a key whose first commit is after the
bound absent; two enclaves kept apart; the CIDs then go through
utils.staging_plan as an order's current state does. The fence's object
names."""
import replay
from utils import FetchDeferrals, staging_plan

ENCLAVE_A = "0x" + "aa" * 20
ENCLAVE_B = "0x" + "bb" * 20
KEY_1 = "11" * 32
KEY_2 = "22" * 32
KEY_3 = "33" * 32
BOUND = 52_647_800


def log(enclave, key, cid, version, block):
    return {'blockNumber': block, 'args': {'enclave': enclave, 'key': bytes.fromhex(key), 'cid': cid,
                                           'version': version}}


BEFORE = [
    log(ENCLAVE_A, KEY_1, 'bafy-a1-v1', 1, BOUND - 500),
    log(ENCLAVE_A, KEY_1, 'bafy-a1-v2', 2, BOUND - 100),
    log(ENCLAVE_A, KEY_2, 'bafy-a2-v1', 1, BOUND),
    log(ENCLAVE_A, KEY_3, 'bafy-a3-v1', 1, BOUND - 10),
    log(ENCLAVE_B, KEY_1, 'bafy-b1-v1', 1, BOUND - 50),
]
AFTER = [
    log(ENCLAVE_A, KEY_1, 'bafy-a1-v3', 3, BOUND + 1),
    log(ENCLAVE_A, KEY_2, 'bafy-a2-v2', 2, BOUND + 7),
    log(ENCLAVE_B, KEY_1, 'bafy-b1-v2', 2, BOUND + 3),
    log(ENCLAVE_B, KEY_2, 'bafy-b2-v1', 1, BOUND + 4),
]


def test_the_last_cid_at_or_before_the_bound_of_every_key_moved_after_it():
    cids = replay.fence_state_cids(AFTER, BEFORE, BOUND)
    assert cids == {
        (ENCLAVE_A, KEY_1): 'bafy-a1-v2',
        (ENCLAVE_A, KEY_2): 'bafy-a2-v1',
        (ENCLAVE_B, KEY_1): 'bafy-b1-v1',
    }


def test_a_key_not_moved_after_the_bound_is_absent():
    assert (ENCLAVE_A, KEY_3) not in replay.fence_state_cids(AFTER, BEFORE, BOUND)


def test_a_key_whose_first_commit_is_after_the_bound_is_absent():
    assert (ENCLAVE_B, KEY_2) not in replay.fence_state_cids(AFTER, BEFORE, BOUND)


def test_two_enclaves_are_kept_apart():
    cids = replay.fence_state_cids(AFTER, BEFORE, BOUND)
    assert cids[(ENCLAVE_A, KEY_1)] != cids[(ENCLAVE_B, KEY_1)]


def test_a_log_at_the_bound_is_before_it_and_one_misfiled_after_is_ignored():
    # The scans may overlap: a log at or before the bound in the after set is
    # not a move, and one after the bound in the before set is not a state.
    after = AFTER + [log(ENCLAVE_A, KEY_3, 'bafy-a3-v1', 1, BOUND - 10)]
    before = BEFORE + [log(ENCLAVE_A, KEY_1, 'bafy-a1-v3', 3, BOUND + 1)]
    assert replay.fence_state_cids(after, before, BOUND) == replay.fence_state_cids(AFTER, BEFORE, BOUND)


def test_keys_as_hex_strings_and_addresses_in_any_case_compare_alike():
    after = [{'blockNumber': BOUND + 1, 'args': {'enclave': ENCLAVE_A.upper(), 'key': '0x' + KEY_1, 'cid': 'x', 'version': 3}}]
    assert replay.fence_state_cids(after, BEFORE, BOUND) == {(ENCLAVE_A, KEY_1): 'bafy-a1-v2'}


def test_the_cids_go_through_the_staging_plan():
    cids = set(replay.fence_state_cids(AFTER, BEFORE, BOUND).values())
    local, remote = staging_plan(cids, lambda cid: cid == 'bafy-a2-v1', FetchDeferrals(lambda: 0.0))
    assert local == ['bafy-a2-v1']
    assert remote == ['bafy-a1-v2', 'bafy-b1-v1']


def test_the_fence_objects_the_securelock_publishes():
    assert replay.fence_objects_present(['state.fence.1111111111111111.cid', 'result.txt'])
    assert replay.fence_objects_present(['state.fence.1111111111111111.enc']) is False
    assert replay.fence_objects_present(['state.bafy.enc', 'state.1111111111111111.cid']) is False
