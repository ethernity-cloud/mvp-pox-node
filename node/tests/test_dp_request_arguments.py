"""utils.dp_request_arguments (PHASE-E-PLAN.md E2, D.2): addDPRequestV2 on a
1.2 contract with the record's cu (0 without one), the tip and the fixture's
two metadata strings; _addDPRequest on a legacy contract unchanged; the
argument lists match the bundled ABI; utils.metadata_key / record_key tie a
request's metadata4 to the record it carries."""
import json
from pathlib import Path

import capability
import config
from utils import DP_REQUEST_PROTOCOL, dp_request_arguments, metadata_key, record_key

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


def abi_inputs(name):
    abi = json.loads(Path(config.abi_filepath).read_text())
    return [entry for entry in abi if entry.get("type") == "function" and entry.get("name") == name][0]["inputs"]


def test_the_v2_arguments_for_a_record():
    rec = record()
    metadata3 = capability.metadata3(GEO, rec)
    metadata4 = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, rec)
    name, arguments = dp_request_arguments(False, RESOURCES, 24, 1, rec["cu"], TIP, UUID, metadata3, metadata4)
    assert name == "addDPRequestV2"
    assert arguments == [8, 32, 255, 100, 24, 2500, TIP, UUID, "v3", FIXTURE["metadata3"]["value"],
                         FIXTURE["metadata4"]["value"]]
    assert DP_REQUEST_PROTOCOL == "v3"


def test_the_v2_arguments_without_a_record():
    metadata3 = capability.metadata3(GEO, None)
    metadata4 = capability.metadata4(RUNNABLE, None, PEER_ID, None)
    name, arguments = dp_request_arguments(False, RESOURCES, 24, 1, 0, TIP, UUID, metadata3, metadata4)
    assert name == "addDPRequestV2"
    assert arguments[5] == 0
    assert arguments[6] == TIP
    assert arguments[9] == GEO
    assert arguments[10] == f"trustedzones=ecld-nodenithy-amoy,ecld-pynithy-amoy;ipfs={PEER_ID};startrow=v1"


def test_the_legacy_list_is_unchanged():
    metadata3 = capability.metadata3(GEO, None)
    metadata4 = capability.metadata4(RUNNABLE, None, PEER_ID, None)
    name, arguments = dp_request_arguments(True, RESOURCES, 24, 3, 2500, TIP, UUID, metadata3, metadata4)
    assert name == "_addDPRequest"
    assert arguments == [8, 32, 255, 100, 24, 3, UUID, "v3", metadata3, metadata4]


def test_the_lists_match_the_bundled_abi():
    rec = record()
    for legacy, name in ((False, "addDPRequestV2"), (True, "_addDPRequest")):
        found, arguments = dp_request_arguments(legacy, RESOURCES, 24, 1, rec["cu"], TIP, UUID, "m3", "m4")
        assert found == name
        inputs = abi_inputs(name)
        assert len(arguments) == len(inputs)
        for value, declared in zip(arguments, inputs):
            if declared["type"].startswith("uint"):
                assert isinstance(value, int) and 0 <= value < 2 ** int(declared["type"][4:])
            else:
                assert declared["type"] == "string" and isinstance(value, str)


def test_metadata_key_reads_the_record_the_request_carries():
    rec = record()
    metadata4 = capability.metadata4(RUNNABLE, RECORD_IMAGE, PEER_ID, rec)
    assert metadata_key(metadata4, "cusig") == record_key(rec)
    assert metadata_key(metadata4, "cusig") == FIXTURE["signatureDerHex"].lower()
    assert metadata_key(metadata4, "cu") == "2500"
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
