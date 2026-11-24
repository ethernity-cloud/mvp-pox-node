"""utils.dp_request_arguments (PHASE-E-PLAN.md E2, D.2; OD18): addDPRequestV2
on a 2.0 contract with the record's gflops (0 without one), the tip and the
fixture's two metadata strings, by the signature the PoX's VERSION() names
(uint40 gflops on "2.0", uint32 on "1.2"); _addDPRequest on a legacy
contract unchanged; the argument lists match the bundled ABI; each metadata
string within DP_METADATA_MAX_BYTES for the longest trustedzones list;
utils.metadata_key / record_key tie a request's metadata4 to the record it
carries."""
import json
from pathlib import Path

import pytest
from eth_utils import keccak
from eth_utils.abi import collapse_if_tuple

import bench_record
import capability
import config
import pox20
from utils import (DP_ADD_LEGACY, DP_ADD_V2, DP_ADD_V2_EPC, DP_METADATA_MAX_BYTES, DP_REQUEST_PROTOCOL,
                   dp_request_arguments, metadata_key, record_key)

SPEC = Path(__file__).parent / "spec"
FIXTURE = json.loads((SPEC / "capability-record.json").read_text())
PEER_ID = "12D3KooWQYhTNQdmr3ArTeUHRYzFg94BKyTkoWBDWez9kSCVe2Xo"
RUNNABLE = {"ecld-pynithy-amoy", "ecld-nodenithy-amoy"}
RECORD_IMAGE = "ecld-nodenithy-amoy"
GEO = "44.4268,26.1025"
RESOURCES = (8, 32, 255, 100)
UUID = "6f1c2b9e-0d5a-4f1e-9c2c-7a1b3d4e5f60"
TIP = 46_810_056_000_000_000


def record():
    found, reason = capability.capability_record_from(
        FIXTURE["capabilityEtny"]["content"], FIXTURE["capabilityTimeEtny"]["content"], FIXTURE["node"]["address"],
        "dcap", True, FIXTURE["measurement"]["t1"], FIXTURE["network"]["chainId"], FIXTURE["network"]["pox"],
        FIXTURE["network"]["validatorRegistry"], lambda address: True)
    assert reason is None
    return found


def signature(entry):
    return f"{entry['name']}({','.join(collapse_if_tuple(i) for i in entry['inputs'])})"


def abi_inputs(wanted):
    abi = json.loads(Path(config.abi_filepath).read_text())
    return [entry for entry in abi if entry.get("type") == "function" and signature(entry) == wanted][0]["inputs"]


@pytest.mark.parametrize("version, wanted, selector", [
    ("2.0", DP_ADD_V2_EPC, "72a6bda7"),
    ("1.2", DP_ADD_V2, "6e01d461"),
    ("", DP_ADD_V2, "6e01d461"),
])
def test_the_v2_add_is_the_signature_the_poxs_version_names(version, wanted, selector):
    rec = record()
    found, arguments = dp_request_arguments(False, RESOURCES, 24, 1, rec["gflops"] | (16 << 16), TIP, UUID, "m3",
                                            "m4", epc_forms=pox20.takes_epc_forms(version))
    assert found == wanted
    assert keccak(text=found)[:4].hex() == selector
    assert abi_inputs(found)[5] == {"internalType": wanted.split(",")[5], "name": "gflops",
                                    "type": wanted.split(",")[5]}


def test_the_gflops_argument_carries_the_epc_only_in_the_uint40_form():
    word = 250 | (16 << 16) | (65535 << 24)
    assert dp_request_arguments(False, RESOURCES, 24, 1, word, TIP, UUID, "m3", "m4", epc_forms=True)[1][5] == word
    with pytest.raises(ValueError):
        dp_request_arguments(False, RESOURCES, 24, 1, word, TIP, UUID, "m3", "m4", epc_forms=False)


def test_the_v2_arguments_for_a_record():
    rec = record()
    metadata3 = capability.metadata3(GEO, rec)
    metadata4 = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, rec)
    name, arguments = dp_request_arguments(False, RESOURCES, 24, 1, rec["gflops"], TIP, UUID, metadata3, metadata4)
    assert name == DP_ADD_V2
    assert arguments == [8, 32, 255, 100, 24, 250, TIP, UUID, "v3", FIXTURE["metadata3"]["value"],
                         FIXTURE["metadata4"]["value"]]
    assert abi_inputs(DP_ADD_V2)[5]["name"] == "gflops"
    assert DP_REQUEST_PROTOCOL == "v3"


def test_the_v2_arguments_without_a_record():
    metadata3 = capability.metadata3(GEO, None)
    metadata4 = capability.metadata4(RUNNABLE, None, PEER_ID, None)
    name, arguments = dp_request_arguments(False, RESOURCES, 24, 1, 0, TIP, UUID, metadata3, metadata4)
    assert name == DP_ADD_V2
    assert arguments[5] == 0
    assert arguments[6] == TIP
    assert arguments[9] == GEO
    assert arguments[10] == f"trustedzones=ecld-nodenithy-amoy,ecld-pynithy-amoy;ipfs={PEER_ID};startrow=v1"


def test_the_legacy_list_is_unchanged():
    metadata3 = capability.metadata3(GEO, None)
    metadata4 = capability.metadata4(RUNNABLE, None, PEER_ID, None)
    name, arguments = dp_request_arguments(True, RESOURCES, 24, 3, 250, TIP, UUID, metadata3, metadata4)
    assert name == DP_ADD_LEGACY
    assert arguments == [8, 32, 255, 100, 24, 3, UUID, "v3", metadata3, metadata4]


def test_the_lists_match_the_bundled_abi():
    rec = record()
    for legacy, epc_forms, name in ((False, False, DP_ADD_V2), (False, True, DP_ADD_V2_EPC),
                                    (True, False, DP_ADD_LEGACY)):
        found, arguments = dp_request_arguments(legacy, RESOURCES, 24, 1, rec["gflops"], TIP, UUID, "m3", "m4",
                                                epc_forms=epc_forms)
        assert found == name
        inputs = abi_inputs(name)
        assert len(arguments) == len(inputs)
        for value, declared in zip(arguments, inputs):
            if declared["type"].startswith("uint"):
                assert isinstance(value, int) and 0 <= value < 2 ** int(declared["type"][4:])
            else:
                assert declared["type"] == "string" and isinstance(value, str)


def test_each_metadata_string_is_within_the_bound_for_the_longest_trustedzones_list():
    # A network and its -unsafe twin joined run the trustedzones of both
    # stacks on both (config.join_unsafe_twins): four names. Eight names of
    # 48 characters, longer than any network entry's image name, with the
    # trustedzone record, a bench record of the widest readings beside it
    # and a 64-byte geography.
    names = {f"ecld-nodenithy-{i}-" + "x" * (48 - len(f"ecld-nodenithy-{i}-")) for i in range(8)}
    rec = record()
    bench = {"gflops": 65535, "cuv": 4, "heap": "65G", "workers": 64, "encmem": 64, "custorage": 2**53 - 1,
             "epc": 2**31 - 1, "t0": 2**63, "t1": 2**63, "block0": 2**63, "block1": 2**63,
             "validator": "0x" + "2a" * 20, "nonce": "0x" + "a8" * 32, "salt": "0x" + "82" * 32,
             "sig0": "0x" + "d5" * 65, "sig1": "0x" + "1e" * 65, "sig_der_hex": "30" + "ab" * 103}
    image = sorted(names)[0]
    metadata3 = capability.metadata3("9" * 64, rec) + bench_record.metadata3_keys(bench)
    metadata4 = capability.metadata4(names, image, PEER_ID, rec) + bench_record.metadata4_keys(bench)
    _, arguments = dp_request_arguments(False, RESOURCES, 24, 1, rec["gflops"], TIP, UUID, metadata3, metadata4,
                                        epc_forms=True)
    assert all(len(arguments[i].encode("utf-8")) <= DP_METADATA_MAX_BYTES for i in (7, 8, 9, 10))
    assert DP_METADATA_MAX_BYTES == 2048


@pytest.mark.parametrize("index", [7, 9, 10])
def test_a_metadata_string_above_the_bound_is_not_sent(index):
    strings = [UUID, "m3", "m4"]
    strings[[7, 9, 10].index(index)] = "x" * (DP_METADATA_MAX_BYTES + 1)
    with pytest.raises(ValueError):
        dp_request_arguments(False, RESOURCES, 24, 1, 250, TIP, *strings)
    strings[[7, 9, 10].index(index)] = "x" * DP_METADATA_MAX_BYTES
    assert dp_request_arguments(False, RESOURCES, 24, 1, 250, TIP, *strings)[1][index] == strings[
        [7, 9, 10].index(index)]


def test_metadata_key_reads_the_record_the_request_carries():
    rec = record()
    metadata4 = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, rec)
    assert metadata_key(metadata4, "cusig") == record_key(rec)
    assert metadata_key(metadata4, "cusig") == FIXTURE["signatureDerHex"].lower()
    assert metadata_key(metadata4, "cugflops") == "250"
    assert ";cugflops=250;cuv=4;" in metadata4
    assert metadata_key(metadata4, "cu") is None
    assert metadata_key(metadata4, "ipfs") == PEER_ID
    assert metadata_key(capability.metadata4(RUNNABLE, None, PEER_ID, None), "cusig") is None
    assert metadata_key("", "cusig") is None
    assert metadata_key(None, "cusig") is None
    assert metadata_key("a=1;cusig", "cusig") is None


def test_record_key_strips_the_prefix_and_lowers():
    assert record_key(None) is None
    assert record_key({"sig_der_hex": "0x30AB"}) == "30ab"
    assert record_key({"sig_der_hex": "30AB"}) == "30ab"
    assert record_key(record()) == FIXTURE["signatureDerHex"].lower()
