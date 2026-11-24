"""capability.metadata4 / metadata3 (PHASE-E-PLAN.md E2, C.4): the builders
reproduce the strings of tests/spec/capability-record.json byte for byte
from the parsed record, the record's trustedzone first; without a record the
three keys and the geography alone; with the bench record's pb keys after
them, the strings of tests/spec/bench-record.json, and the key of the two
records a request carries."""
import json
import re
from pathlib import Path

import bench_record
import capability
from utils import record_key

SPEC = Path(__file__).parent / "spec"
FIXTURE = json.loads((SPEC / "capability-record.json").read_text())
PEER_ID = "12D3KooWQYhTNQdmr3ArTeUHRYzFg94BKyTkoWBDWez9kSCVe2Xo"
RUNNABLE = {"ecld-pynithy-amoy", "ecld-nodenithy-amoy"}
RECORD_IMAGE = "ecld-nodenithy-amoy"
GEO = "44.4268,26.1025"


def record():
    found, reason = capability.capability_record_from(
        FIXTURE["capabilityEtny"]["content"], FIXTURE["capabilityTimeEtny"]["content"], FIXTURE["node"]["address"],
        "dcap", True, FIXTURE["measurement"]["t1"], FIXTURE["network"]["chainId"], FIXTURE["network"]["pox"],
        FIXTURE["network"]["validatorRegistry"], lambda address: True)
    assert reason is None
    return found


def test_metadata4_is_the_fixture_value():
    assert capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, record()) == FIXTURE["metadata4"]["value"]


def test_metadata4_keys_are_the_fixture_keys_in_order():
    value = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, record())
    assert [entry.partition("=")[0] for entry in value.split(";")] == FIXTURE["metadata4"]["keys"]


def test_metadata4_carries_the_records_gflops_as_cugflops():
    found = record()
    value = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, found)
    keys = dict(entry.split("=", 1) for entry in value.split(";"))
    assert keys["cugflops"] == str(found["gflops"]) == str(FIXTURE["measurement"]["gflops"])
    assert keys["cuv"] == "4"
    assert "cu" not in keys


def test_the_record_trustedzone_comes_first_and_the_rest_sorted():
    value = capability.metadata4({"c-tz", "a-tz", "b-tz"}, "b-tz", PEER_ID, record())
    assert value.startswith("trustedzones=b-tz,a-tz,c-tz;")


def test_without_a_record_metadata4_is_the_three_keys_sorted():
    assert capability.metadata4(RUNNABLE, None, PEER_ID, None) == (
        f"trustedzones=ecld-nodenithy-amoy,ecld-pynithy-amoy;ipfs={PEER_ID};startrow=v1")


def test_without_a_peer_id_the_ipfs_value_is_empty():
    assert ";ipfs=;" in capability.metadata4(RUNNABLE, None, "", None)


def test_a_record_of_an_image_not_runnable_leaves_the_order_sorted():
    value = capability.metadata4({"a-tz", "c-tz"}, "b-tz", PEER_ID, record())
    assert value.startswith("trustedzones=a-tz,c-tz;")


def test_cusig_carries_no_0x_prefix():
    prefixed = dict(record(), sig_der_hex="0x" + record()["sig_der_hex"])
    assert capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, prefixed) == FIXTURE["metadata4"]["value"]


def test_metadata3_is_the_fixture_value():
    assert capability.metadata3(GEO, record()) == FIXTURE["metadata3"]["value"]


def test_metadata3_with_the_empty_geography_is_the_fixture_value():
    assert capability.metadata3("", record()) == FIXTURE["metadata3"]["valueEmptyGeography"]
    assert capability.metadata3(None, record()) == FIXTURE["metadata3"]["valueEmptyGeography"]


def test_metadata3_keys_follow_the_geography_in_the_fixture_order():
    value = capability.metadata3(GEO, record())
    fields = value.split(";")
    assert fields[0] == GEO
    assert [entry.partition("=")[0] for entry in fields[1:]] == FIXTURE["metadata3"]["keys"]


def test_without_a_record_metadata3_is_the_geography():
    assert capability.metadata3(GEO, None) == GEO
    assert capability.metadata3("", None) == ""
    assert capability.metadata3(None, None) == ""


def test_cuat_equals_cut1_and_cub_values_are_the_statement_blocks():
    four = dict(entry.split("=", 1) for entry in capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, record()).split(";"))
    three = dict(entry.split("=", 1) for entry in capability.metadata3(GEO, record()).split(";")[1:])
    assert four["cuat"] == three["cut1"] == str(FIXTURE["measurement"]["t1"])
    assert (three["cub0"], three["cub1"]) == (str(FIXTURE["measurement"]["block0"]), str(FIXTURE["measurement"]["block1"]))


def test_no_metadata_value_contains_a_semicolon():
    found = record()
    for key in ("validator", "nonce", "sig0", "sig1", "sig_der_hex", "heap", "gflops", "cuv", "t0", "t1",
                "block0", "block1", "workers"):
        assert ";" not in str(found[key])


BENCH = json.loads((SPEC / "bench-record.json").read_text())


def bench():
    found, reason = bench_record.bench_record_from(
        BENCH["probeEtny"]["content"], BENCH["probeTimeEtny"]["content"], BENCH["probeKey"]["sizeGib"],
        FIXTURE["node"]["address"], BENCH["benchChecks"]["cases"][0]["readTime"], FIXTURE["network"]["chainId"],
        FIXTURE["network"]["pox"], FIXTURE["network"]["validatorRegistry"], lambda address: True,
        [BENCH["probeKey"]["publicKeyPem"]], [FIXTURE["trustedzoneKey"]["publicKeyPem"]], 250)
    assert reason is None
    return found


def test_the_two_records_strings_are_the_bench_fixtures():
    """MATCHING-SYNC-PLAN.md B04; OD1 A: the trustedzone record's keys, then
    the bench record's pb keys, in each string (bench-record.json at
    pox-smart-contract-litvm 2f600f6, metadata4.value and metadata3.value)."""
    four = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, record()) + bench_record.metadata4_keys(bench())
    three = capability.metadata3(GEO, record()) + bench_record.metadata3_keys(bench())
    assert four == BENCH["metadata4"]["value"]
    assert three == BENCH["metadata3"]["value"]
    assert [e.partition("=")[0] for e in four.split(";")][-8:] == BENCH["metadata4"]["keys"]
    assert [e.partition("=")[0] for e in three.split(";")][-9:] == BENCH["metadata3"]["keys"]


def test_the_requests_key_names_both_records_and_either_changing_it():
    key = bench_record.request_offer_key(BENCH["metadata3"]["value"], BENCH["metadata4"]["value"])
    assert key == f"{FIXTURE['signatureDerHex']}|{bench_record.bench_key(bench())}"
    assert bench_record.split_offer_key(key) == (FIXTURE["signatureDerHex"], BENCH["signatureDerHex"])
    assert key == bench_record.offer_key(record_key(record()), bench_record.bench_key(bench()))
    one = bench_record.request_offer_key(FIXTURE["metadata3"]["value"], FIXTURE["metadata4"]["value"])
    assert one == FIXTURE["signatureDerHex"] and one != key
    assert bench_record.split_offer_key(one) == (FIXTURE["signatureDerHex"], None)
    assert bench_record.offer_key(None, "abcd") is None
    assert bench_record.request_offer_key(GEO, capability.metadata4(RUNNABLE, None, PEER_ID, None)) is None


def test_the_metadata_keys_examples_equal_the_fixture_values():
    text = (SPEC / "metadata-keys.md").read_text()
    examples = re.findall(r"```\n(.*?)\n```", text, re.S)
    assert FIXTURE["metadata4"]["value"] in examples
    assert FIXTURE["metadata3"]["value"] in examples
    assert FIXTURE["metadata3"]["valueEmptyGeography"] in examples
