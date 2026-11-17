"""capability.RecordStore (PHASE-E-PLAN.md E1, E2, E8): node/<network>/
capability.json round-trips a passed test and its record, advertised() picks
the newest t1 among the runnable images' unexpired records, the probe-first
flag arms on a new record, is consumed once and survives a reload, the
re-measure and expiry sets follow the constants, and a stored record below
CU_VERSION_MIN or without gflops is dropped at load, its passed test kept
with retest_pending set in the file until the next put."""
import json

import capability
from constants import CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS, CU_VERSION_MIN

HASH_A = "QmaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
HASH_B = "QmbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
NOW = 1_762_000_000


def record(t1, **fields):
    base = {"line": "etny-cu|v1|...", "network": "80002:0xPoX", "node": "0x" + "47" * 20, "gflops": 250, "cuv": 4,
            "validator": "0x" + "2a" * 20, "nonce": "0x" + "f9" * 32, "t0": t1 - 30, "t1": t1,
            "sig0": "0x" + "32" * 65, "sig1": "0x" + "00" * 65, "heap": "256M", "workers": 4,
            "sig_der_hex": "3065", "platform_line": "platform|cpu=x|cores=8|mhz=3700", "block0": 1, "block1": 16,
            "capability_etny": "...", "capability_time_etny": "..."}
    base.update(fields)
    return base


def test_an_absent_file_is_an_empty_store(tmp_path):
    store = capability.RecordStore(tmp_path / "net" / "capability.json")
    assert store.entries == {}
    assert store.advertised({"a"}, NOW) is None


def test_a_put_round_trips_through_the_file(tmp_path):
    path = tmp_path / "capability.json"
    store = capability.RecordStore(path)
    store.put("a-tz", HASH_A, NOW, record(NOW - 100))
    on_disk = json.loads(path.read_text())
    assert on_disk == {"a-tz": {"image_hash": HASH_A, "passed_at": NOW, "record": record(NOW - 100),
                                "probe_first_pending": True}}
    reloaded = capability.RecordStore(path)
    assert reloaded.entries == on_disk
    assert reloaded.get("a-tz")["image_hash"] == HASH_A
    assert not path.with_suffix(".json.tmp").exists()


def test_a_passed_test_without_a_record_is_kept_with_record_null(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, NOW, None)
    assert store.get("a-tz") == {"image_hash": HASH_A, "passed_at": NOW, "record": None, "probe_first_pending": False}
    assert store.advertised({"a-tz"}, NOW) is None


def test_advertised_picks_the_newest_t1_among_the_runnable_images(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, NOW, record(NOW - 500))
    store.put("b-tz", HASH_B, NOW, record(NOW - 100))
    assert store.advertised({"a-tz", "b-tz"}, NOW) == ("b-tz", record(NOW - 100))
    assert store.advertised({"a-tz"}, NOW) == ("a-tz", record(NOW - 500))
    assert store.advertised(set(), NOW) is None


def test_an_expired_record_is_not_advertised(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, NOW, record(NOW - CU_TTL_SECONDS))
    store.put("b-tz", HASH_B, NOW, record(NOW - CU_TTL_SECONDS - 1))
    assert store.advertised({"a-tz", "b-tz"}, NOW) is None
    assert store.expired(NOW) == {"a-tz", "b-tz"}
    assert store.advertised({"a-tz", "b-tz"}, NOW - 1) == ("a-tz", record(NOW - CU_TTL_SECONDS))


def test_the_probe_flag_arms_on_a_record_and_is_consumed_once(tmp_path):
    path = tmp_path / "capability.json"
    store = capability.RecordStore(path)
    store.put("a-tz", HASH_A, NOW, record(NOW))
    assert store.probe_first_pending("a-tz")
    assert capability.RecordStore(path).probe_first_pending("a-tz")
    assert store.consume_probe_first("a-tz") is True
    assert not store.probe_first_pending("a-tz")
    assert store.consume_probe_first("a-tz") is False
    assert not capability.RecordStore(path).probe_first_pending("a-tz")


def test_a_new_record_arms_the_flag_again(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, NOW, record(NOW))
    store.consume_probe_first("a-tz")
    store.put("a-tz", HASH_B, NOW + 10, record(NOW + 10))
    assert store.probe_first_pending("a-tz")


def test_a_pass_without_a_record_does_not_arm_the_flag(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, NOW, None)
    assert not store.probe_first_pending("a-tz")
    assert store.consume_probe_first("a-tz") is False
    assert store.consume_probe_first("unknown") is False


def test_drop_forgets_the_test_and_drop_record_keeps_it(tmp_path):
    path = tmp_path / "capability.json"
    store = capability.RecordStore(path)
    store.put("a-tz", HASH_A, NOW, record(NOW))
    store.put("b-tz", HASH_B, NOW, record(NOW))
    store.drop("a-tz")
    assert "a-tz" not in store.entries
    store.drop_record("b-tz")
    assert store.get("b-tz") == {"image_hash": HASH_B, "passed_at": NOW, "record": None, "probe_first_pending": False}
    assert json.loads(path.read_text()) == store.entries
    store.drop("unknown")
    store.drop_record("unknown")


def test_remeasure_due_is_a_margin_before_the_ttl(tmp_path):
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", HASH_A, NOW, record(NOW))
    store.put("b-tz", HASH_B, NOW - 1, record(NOW))
    due_at = NOW + CU_TTL_SECONDS - CU_REMEASURE_MARGIN_SECONDS
    assert store.remeasure_due(due_at - 1) == {"b-tz"}
    assert store.remeasure_due(due_at) == {"a-tz", "b-tz"}


def test_an_unreadable_file_is_an_empty_store(tmp_path):
    path = tmp_path / "capability.json"
    path.write_text("{not json")
    store = capability.RecordStore(path)
    assert store.entries == {}
    store.put("a-tz", HASH_A, NOW, None)
    assert json.loads(path.read_text()) == store.entries


def test_the_store_creates_its_directory(tmp_path):
    path = tmp_path / "polygon_amoy" / "capability.json"
    capability.RecordStore(path).put("a-tz", HASH_A, NOW, None)
    assert path.exists()


def stored_cuv_3_record(t1):
    """A cuv 3 record as capability.json holds it: the key cu, no gflops."""
    found = record(t1, cuv=3)
    del found["gflops"]
    found["cu"] = 1100
    return found


def test_record_current_needs_gflops_and_the_minimum_version():
    assert CU_VERSION_MIN == 4
    assert capability.record_current(record(NOW))
    assert capability.record_current(record(NOW, cuv=5))
    assert not capability.record_current(record(NOW, cuv=3))
    assert not capability.record_current(stored_cuv_3_record(NOW))
    without_gflops = record(NOW)
    del without_gflops["gflops"]
    assert not capability.record_current(without_gflops)


def test_a_stored_record_below_the_minimum_version_is_dropped_at_load(tmp_path):
    path = tmp_path / "capability.json"
    without_gflops = record(NOW - 50)
    del without_gflops["gflops"]
    path.write_text(json.dumps({
        "a-tz": {"image_hash": HASH_A, "passed_at": NOW, "record": stored_cuv_3_record(NOW - 100),
                 "probe_first_pending": True},
        "b-tz": {"image_hash": HASH_B, "passed_at": NOW, "record": without_gflops, "probe_first_pending": True},
        "c-tz": {"image_hash": HASH_A, "passed_at": NOW, "record": record(NOW - 10), "probe_first_pending": True},
        "d-tz": {"image_hash": HASH_B, "passed_at": NOW, "record": None, "probe_first_pending": False},
    }))
    store = capability.RecordStore(path)
    assert store.get("a-tz") == {"image_hash": HASH_A, "passed_at": NOW, "record": None, "probe_first_pending": False,
                                 "retest_pending": True}
    assert store.get("b-tz") == {"image_hash": HASH_B, "passed_at": NOW, "record": None, "probe_first_pending": False,
                                 "retest_pending": True}
    assert [image for image in ("a-tz", "b-tz", "c-tz", "d-tz") if store.retest_pending(image)] == ["a-tz", "b-tz"]
    assert store.get("c-tz")["record"] == record(NOW - 10)
    assert store.probe_first_pending("c-tz")
    assert store.get("d-tz")["record"] is None
    assert store.advertised({"a-tz", "b-tz", "c-tz", "d-tz"}, NOW) == ("c-tz", record(NOW - 10))
    assert json.loads(path.read_text()) == store.entries
    reloaded = capability.RecordStore(path)
    assert reloaded.entries == store.entries


def test_the_re_test_stays_pending_across_a_reload_until_a_put(tmp_path):
    path = tmp_path / "capability.json"
    path.write_text(json.dumps({"a-tz": {"image_hash": HASH_A, "passed_at": NOW, "record": stored_cuv_3_record(NOW),
                                         "probe_first_pending": True}}))
    assert capability.RecordStore(path).retest_pending("a-tz")
    restarted = capability.RecordStore(path)
    assert restarted.retest_pending("a-tz")
    assert restarted.get("a-tz")["record"] is None
    restarted.drop_record("a-tz")
    assert capability.RecordStore(path).retest_pending("a-tz")
    restarted.put("a-tz", HASH_B, NOW + 900, record(NOW + 890))
    assert not restarted.retest_pending("a-tz")
    reloaded = capability.RecordStore(path)
    assert not reloaded.retest_pending("a-tz")
    assert reloaded.get("a-tz") == {"image_hash": HASH_B, "passed_at": NOW + 900, "record": record(NOW + 890),
                                    "probe_first_pending": True}


def test_a_store_of_current_records_drops_nothing_at_load(tmp_path):
    path = tmp_path / "capability.json"
    capability.RecordStore(path).put("a-tz", HASH_A, NOW, record(NOW))
    store = capability.RecordStore(path)
    assert not store.retest_pending("a-tz")
    assert not store.retest_pending("unknown")
    assert store.get("a-tz") == {"image_hash": HASH_A, "passed_at": NOW, "record": record(NOW),
                                 "probe_first_pending": True}
