"""capability.metadata4 / metadata3 (PHASE-E-PLAN.md E2, C.4): the builders
reproduce the strings of tests/spec/capability-record.json byte for byte
from the parsed record, the record's trustedzone first; without a record the
three keys and the geography alone."""
import json
import re
from pathlib import Path

import capability

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
    for key in ("validator", "nonce", "sig0", "sig1", "sig_der_hex", "heap", "cu", "cuv", "t0", "t1",
                "block0", "block1", "workers"):
        assert ";" not in str(found[key])


def test_the_metadata_keys_examples_equal_the_fixture_values():
    text = (SPEC / "metadata-keys.md").read_text()
    examples = re.findall(r"```\n(.*?)\n```", text, re.S)
    assert FIXTURE["metadata4"]["value"] in examples
    assert FIXTURE["metadata3"]["value"] in examples
    assert FIXTURE["metadata3"]["valueEmptyGeography"] in examples
