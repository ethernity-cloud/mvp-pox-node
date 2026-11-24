"""bench_record: the bench record of pox-smart-contract-litvm 2f600f6
spec/bench-record.json (MATCHING-SYNC-PLAN.md B01, B04, C01): the readings
grammar of the etny-pb line, its accepted and refused fields; the region
run's fixed work; the members' bench checks of every vector in benchChecks'
order; the key sets a request carries a record under; the line a validator
rebuilds from the seventeen pb keys; the probe's two objects admitted as the
agent reads them, the closing checks of a reader holding both answers'
bodies; the keys metadata4 and metadata3 carry; the store."""
import json
from pathlib import Path

import pytest

import bench_record
import capability
import timed_record

SPEC = Path(__file__).parent / "spec"
BENCH = json.loads((SPEC / "bench-record.json").read_text())
RECORD = json.loads((SPEC / "capability-record.json").read_text())
CHAIN_ID = RECORD["network"]["chainId"]
POX = RECORD["network"]["pox"]
REGISTRY = RECORD["network"]["validatorRegistry"]
NODE = RECORD["node"]["address"]
VALIDATOR = RECORD["validator"]["address"]
PROBE_KEY = BENCH["probeKey"]["publicKeyPem"]
TRUSTEDZONE_KEY = RECORD["trustedzoneKey"]["publicKeyPem"]
CASES = BENCH["benchChecks"]["cases"]
VERIFIED_AT = next(c for c in CASES if c["name"] == "verified")["readTime"]


def seated(address):
    return address == VALIDATOR


def admit(probe_etny=BENCH["probeEtny"]["content"], probe_time_etny=BENCH["probeTimeEtny"]["content"], size=2,
          now=VERIFIED_AT, cugflops=250, probe_keys=(PROBE_KEY,)):
    return bench_record.bench_record_from(probe_etny, probe_time_etny, size, NODE, now, CHAIN_ID, POX, REGISTRY,
                                          seated, list(probe_keys), [TRUSTEDZONE_KEY], cugflops)


def test_the_line_layout_and_the_tag_are_the_fixtures():
    assert (bench_record.BENCH_TAG, bench_record.BENCH_VERSION) == (BENCH["tag"], BENCH["version"])
    assert bench_record.LINE_FIELDS == BENCH["lineFields"] == len(BENCH["line"].split("|"))
    assert bench_record.METADATA4_KEYS == tuple(BENCH["metadata4"]["keys"])
    assert bench_record.METADATA3_KEYS == tuple(BENCH["metadata3"]["keys"])
    assert [bench_record.PROBE_OBJECT.format(size=2), bench_record.PROBE_TIME_OBJECT.format(size=2)] == [
        BENCH["probeEtny"]["object"], BENCH["probeTimeEtny"]["object"]]


@pytest.mark.parametrize("field", BENCH["readings"]["accepted"])
def test_an_accepted_readings_field_reads(field):
    assert bench_record.readings_why(field) is None
    assert bench_record.parse_readings(field)["heap"] == field.split(";")[0].split("=")[1]


@pytest.mark.parametrize("case", BENCH["readings"]["refused"], ids=[c["field"] for c in BENCH["readings"]["refused"]])
def test_a_refused_readings_field_fails_the_test_it_names(case):
    assert bench_record.readings_why(case["field"]) == case["why"]
    with pytest.raises(ValueError):
        bench_record.parse_readings(case["field"])


def test_the_trustedzone_lines_two_entry_field_is_no_bench_field():
    assert bench_record.readings_why(RECORD["line"].split("|")[13]) == "entries"
    assert len(BENCH["readings"]["refused"]) == 18
    assert bench_record.readings_why("heap=3G;workers=4;encmem=2;custorage=37;epc=2147483648") == "epcAboveMax"


def test_the_region_work_is_the_fixtures():
    constants = BENCH["benchChecks"]["regionWork"]["constants"]
    assert (bench_record.TRUSTEDZONE_HEAP_MIB, bench_record.SECURELOCK_HEAP_MIB, bench_record.CHUNK_MIB,
            bench_record.PAGE_BYTES, bench_record.STRIDE_SEGMENTS, bench_record.EPC_ALLOWANCE_MIB,
            bench_record.VERSION_ARRAY_MIB_PER_GIB, bench_record.EPC_PASSES, bench_record.EPC_MIN_VISITS,
            bench_record.EPC_SLICES) == tuple(constants[k] for k in (
                "trustedzoneHeapMib", "securelockHeapMib", "chunkMib", "pageBytes", "strideSegments",
                "epcAllowanceMib", "versionArrayMibPerGib", "epcPasses", "epcMinVisits", "epcSlices"))
    m = BENCH["measurement"]
    assert str(bench_record.region_work(m["encmem"], m["workers"], m["epc"])) == \
        BENCH["benchChecks"]["regionWork"]["example"]["work"]
    for case in CASES:
        k = case["keys"]
        work = bench_record.region_work(int(k["pbencmem"]), int(k["pbworkers"]), int(k["pbepc"]))
        assert (None if work is None else str(work)) == (case["regionWork"] or {}).get("work"), case["name"]


def test_the_check_figures_are_the_fixtures_placeholders():
    p = BENCH["benchChecks"]["parameters"]
    assert bench_record.CHECKS == tuple(BENCH["benchChecks"]["order"])
    assert bench_record.SHARE_MILLI == p["shareMilli"]
    assert bench_record.FLOOR_GFLOPS_BY_ISA == p["floorGflopsByIsa"]
    assert {str(k): v for k, v in bench_record.WINDOW_BOUND_SECONDS.items()} == p["windowBoundSeconds"]
    assert bench_record.lowest_floor() == min(p["floorGflopsByIsa"].values())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_bench_checks_of_every_vector(case):
    failed = bench_record.bench_verdict(case["keys"], CHAIN_ID, POX, NODE, REGISTRY, [PROBE_KEY], [TRUSTEDZONE_KEY],
                                        seated, case["readTime"], case["cugflops"])
    assert failed == case["expected"].get("failedCheck"), case["name"]
    assert bench_record.rebuilt_line(case["keys"], CHAIN_ID, POX, NODE) == case["line"]


def test_a_bench_record_proves_nothing_beside_no_trustedzone_record():
    verified = CASES[0]
    assert bench_record.bench_verdict(verified["keys"], CHAIN_ID, POX, NODE, REGISTRY, [PROBE_KEY], [TRUSTEDZONE_KEY],
                                      seated, verified["readTime"], None) == "share"
    assert bench_record.bench_verdict(None, CHAIN_ID, POX, NODE, REGISTRY, [PROBE_KEY], [], seated,
                                      verified["readTime"], 250) == "keys"


def test_a_record_signed_for_another_node_fails_its_signature():
    verified = CASES[0]
    assert bench_record.bench_verdict(verified["keys"], CHAIN_ID, POX, "0x" + "ab" * 20, REGISTRY, [PROBE_KEY], [],
                                      seated, verified["readTime"], 250) == "signature"


@pytest.mark.parametrize("key_set", BENCH["keySets"], ids=[s["name"] for s in BENCH["keySets"]])
def test_the_key_sets_a_request_carries_its_records_under(key_set):
    trustedzone, bench = bench_record.request_records(key_set["metadata3"], key_set["metadata4"])
    assert {"benchRecord": bench is not None, "trustedzoneRecord": trustedzone is not None} == key_set["expected"]


def test_a_v1_request_carries_a_trustedzone_record_without_cus():
    legacy = RECORD["legacyV1"]
    trustedzone, bench = bench_record.request_records(legacy["metadata3"], legacy["metadata4"])
    assert trustedzone is not None and "cus" not in trustedzone and bench is None
    trustedzone, _ = bench_record.request_records(RECORD["metadata3"]["value"], RECORD["metadata4"]["value"])
    assert trustedzone["cus"] == RECORD["measurement"]["salt"]


def test_a_validator_rebuilds_the_line_from_the_requests_keys():
    _, keys = bench_record.request_records(BENCH["metadata3"]["value"], BENCH["metadata4"]["value"])
    assert bench_record.rebuilt_line(keys, CHAIN_ID, POX, NODE) == BENCH["line"] == BENCH["lineFromMetadata"]["rebuilt"]
    assert capability.verify_p384(PROBE_KEY, BENCH["line"], keys["pbsig"])


def test_the_probes_objects_make_the_fixtures_record():
    record, reason = admit()
    assert reason is None
    m = BENCH["measurement"]
    assert (record["gflops"], record["cuv"], record["heap"], record["workers"], record["encmem"], record["custorage"],
            record["epc"]) == (m["gflops"], m["cuv"], m["heap"], m["workers"], m["encmem"], m["custorage"], m["epc"])
    assert (record["nonce"], record["salt"], record["t0"], record["t1"]) == (m["n0"], m["salt"], m["t0"], m["t1"])
    assert (record["block0"], record["block1"]) == (m["block0"], m["block1"])
    assert record["line"] == BENCH["line"] and record["sig_der_hex"] == BENCH["signatureDerHex"]
    assert record["size"] == 2 and record["validator"] == VALIDATOR
    assert json.loads(json.dumps(record)) == record


def test_the_records_keys_are_the_fixtures_appended_strings():
    record, _ = admit()
    assert bench_record.metadata4_keys(record) == ";" + BENCH["metadata4"]["appended"]
    assert bench_record.metadata3_keys(record) == ";" + BENCH["metadata3"]["appended"]
    assert bench_record.bench_key(record) == BENCH["signatureDerHex"]
    assert bench_record.metadata4_keys(None) == bench_record.metadata3_keys(None) == ""


def test_the_objects_built_from_the_test_keys_make_an_admitted_record():
    probe_etny, probe_time_etny = timed_record.bench_objects()
    record, reason = admit(probe_etny, probe_time_etny)
    assert reason is None and record["line"] == BENCH["line"]
    assert probe_time_etny == BENCH["probeTimeEtny"]["content"]


@pytest.mark.parametrize("figures, refused_by", [
    ({"closing_nonce": BENCH["measurement"]["n0"]}, "statements"),
    ({"t1": BENCH["measurement"]["t0"] - 1}, "order"),
    ({"block1": BENCH["measurement"]["block0"] - 1}, "order"),
    ({"t1": BENCH["measurement"]["t0"] + 81, "block1": BENCH["measurement"]["block0"] + 41}, "windowBound"),
    ({"t1": BENCH["measurement"]["t0"] + 30, "block1": BENCH["measurement"]["block0"] + 15}, "share"),
    ({"epc": 22}, "region"),
    ({"cuv": 3}, "cuv"),
    ({"encmem": 4}, "line"),
    ({"readings": "heap=3G;workers=4;encmem=2;custorage=37"}, "line"),
    ({"node": "0x" + "ab" * 20}, "node"),
    ({"network": f"{CHAIN_ID + 1}:{POX}"}, "node"),
])
def test_the_probes_objects_are_refused_by_each_check(figures, refused_by):
    probe_etny, probe_time_etny = timed_record.bench_objects(**figures)
    record, reason = admit(probe_etny, probe_time_etny)
    assert record is None and reason.startswith(refused_by + ":"), reason


def test_a_closing_answer_equal_to_the_opening_one_is_refused():
    opening = BENCH["probeTimeEtny"]["content"].split("\n")[0]
    record, reason = admit(probe_time_etny=f"{opening}\n{opening}\n")
    assert record is None and reason.startswith("equalBodies:")


def test_a_record_of_another_size_or_key_or_without_its_time_object_is_refused():
    assert admit(size=4)[1].startswith("line:")
    edmm = BENCH["edmmProbeKey"]["publicKeyPem"]
    assert admit(probe_keys=(edmm,))[1].startswith("signature:")
    assert admit(probe_keys=())[1].startswith("signature:")
    assert admit(probe_time_etny=None)[1].startswith("statements:")
    assert admit(probe_etny=None)[1] == "no probe.2.etny"
    assert admit(now=VERIFIED_AT + 7 * 86400 + 1000)[1].startswith("expiry:")


def test_a_trustedzone_line_is_no_bench_record():
    record, reason = admit(probe_etny=RECORD["capabilityEtny"]["content"])
    assert record is None and "not tagged etny-pb|v1" in reason


def test_the_store_keeps_the_record_until_its_ttl(tmp_path):
    record, _ = admit()
    store = bench_record.BenchStore(tmp_path / "bench.json")
    assert store.record(VERIFIED_AT) is None and store.remeasure_due(VERIFIED_AT)
    store.put(record, "QmProbe", VERIFIED_AT)
    again = bench_record.BenchStore(tmp_path / "bench.json")
    assert again.record(VERIFIED_AT) == record and again.entry["image_hash"] == "QmProbe"
    assert not again.remeasure_due(VERIFIED_AT)
    assert again.record(record["t1"] + 7 * 86400) is None
    assert again.remeasure_due(VERIFIED_AT + 6 * 86400)
    again.drop()
    assert bench_record.BenchStore(tmp_path / "bench.json").record(VERIFIED_AT) is None
