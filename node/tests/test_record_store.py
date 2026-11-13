"""capability.RecordStore (PHASE-E-PLAN.md E1, E2, E8): node/<network>/
capability.json round-trips a passed test and its record, advertised() picks
the newest t1 among the runnable images' unexpired records, the probe-first
flag arms on a new record, is consumed once and survives a reload, and the
re-measure and expiry sets follow the constants."""
import json

import capability
from constants import CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS

HASH_A = "QmaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
HASH_B = "QmbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
NOW = 1_762_000_000


def record(t1, **fields):
    base = {"line": "etny-cu|v1|...", "network": "80002:0xPoX", "node": "0x" + "47" * 20, "cu": 2500, "cuv": 1,
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
