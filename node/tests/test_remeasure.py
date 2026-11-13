"""The re-measure of a passed integration test (PHASE-E-PLAN.md E8):
utils.integration_tests_due runs a passed test again while its re-measure is
pending, utils.runnable_trustedzones and capability.RecordStore.advertised
read the standing pass unchanged until the new test's put, and a failed
re-measure waits for its backoff."""
import capability
from constants import CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS
from utils import integration_tests_due, runnable_trustedzones

HASH_A = "QmaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
HASH_B = "QmbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
PASSED_AT = 1_762_000_000
DUE_AT = PASSED_AT + CU_TTL_SECONDS - CU_REMEASURE_MARGIN_SECONDS
TRUSTEDZONES = {"a-tz": "dcap", "b-tz": "dcap", "c-tz": "sgx"}
TESTED = ["a-tz", "c-tz"]


def record(t1):
    return {"line": "etny-cu|v1|...", "network": "80002:0xPoX", "node": "0x" + "47" * 20, "cu": 2500, "cuv": 1,
            "validator": "0x" + "2a" * 20, "nonce": "0x" + "f9" * 32, "t0": t1 - 30, "t1": t1,
            "sig0": "0x" + "32" * 65, "sig1": "0x" + "00" * 65, "heap": "256M", "workers": 4,
            "sig_der_hex": "3065", "platform_line": "platform|cpu=x|cores=8|mhz=3700", "block0": 1, "block1": 16,
            "capability_etny": "...", "capability_time_etny": "..."}


def test_a_due_re_measure_leaves_the_pass_the_runnable_set_and_the_record_standing(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, PASSED_AT, record(PASSED_AT - 60))
    passed = {"a-tz": HASH_A}
    runnable = runnable_trustedzones(TRUSTEDZONES, TESTED, passed)
    assert runnable == {"a-tz", "b-tz"}
    advertised = store.advertised(runnable, DUE_AT)

    remeasure = store.remeasure_due(DUE_AT)
    assert remeasure == {"a-tz"}
    assert integration_tests_due(TESTED, passed, remeasure, {}, 0.0) == ["a-tz", "c-tz"]
    assert passed == {"a-tz": HASH_A}
    assert runnable_trustedzones(TRUSTEDZONES, TESTED, passed) == runnable
    assert store.advertised(runnable, DUE_AT) == advertised == ("a-tz", record(PASSED_AT - 60))

    store.put("a-tz", HASH_B, DUE_AT + 100, record(DUE_AT + 90))
    assert store.advertised(runnable, DUE_AT + 100) == ("a-tz", record(DUE_AT + 90))
    assert store.remeasure_due(DUE_AT + 100) == set()


def test_a_passed_test_runs_again_only_while_its_re_measure_is_pending():
    passed = {"a-tz": HASH_A}
    assert integration_tests_due(TESTED, passed, set(), {}, 0.0) == ["c-tz"]
    assert integration_tests_due(TESTED, passed, {"a-tz"}, {}, 0.0) == ["a-tz", "c-tz"]
    assert integration_tests_due(TESTED, {"a-tz": HASH_A, "c-tz": HASH_B}, set(), {}, 0.0) == []


def test_a_failed_re_measure_waits_for_its_backoff():
    passed = {"a-tz": HASH_A}
    retry = {"a-tz": (1, 600.0), "c-tz": (2, 1800.0)}
    assert integration_tests_due(TESTED, passed, {"a-tz"}, retry, 599.0) == []
    assert integration_tests_due(TESTED, passed, {"a-tz"}, retry, 600.0) == ["a-tz"]
    assert integration_tests_due(TESTED, passed, {"a-tz"}, retry, 1800.0) == ["a-tz", "c-tz"]


def test_the_runnable_set_follows_the_passed_tests():
    assert runnable_trustedzones(TRUSTEDZONES, TESTED, {}) == set()
    assert runnable_trustedzones(TRUSTEDZONES, TESTED, {"c-tz": HASH_A}) == {"c-tz"}
    assert runnable_trustedzones(TRUSTEDZONES, TESTED, {"a-tz": HASH_A, "c-tz": HASH_B}) == {"a-tz", "b-tz", "c-tz"}
    assert runnable_trustedzones(TRUSTEDZONES, TESTED, {"b-tz": HASH_A}) == set()
