"""capability: the two integration-bucket objects of tests/spec/
capability-record.json (pox-smart-contract-litvm 2f600f6) parsed and
admitted (PHASE-E-PLAN.md E3, E8, E40; MATCHING-SYNC-PLAN.md C01, B01), the
refusals, the expiry and the re-measure bounds. The record is v2 (the
closing statement over n1, the salt in field 8): its closing checks run in
closingChecks' order and its GFLOPS within the rate bound of rateBound on
every network; the v1 record of legacyV1 is admitted as at 52e36e0 on a
network whose pricing names no PoxMatching and refused on one that does
(admission)."""
import importlib.util
import json
import logging
from pathlib import Path

import pytest

import capability
import timed_record
from constants import (BENCHMARK_FULL_ROUNDS, BENCHMARK_MAX_SECONDS, BENCHMARK_OPS_PER_ROUND, CU_MAX_GFLOPS,
                       CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS, CU_VERSION_MIN, RATE_ALLOWANCE_SECONDS,
                       RATE_TOLERANCE_PERCENT)

FIXTURE = json.loads((Path(__file__).parent / "spec" / "capability-record.json").read_text())
TIME_STATEMENT = json.loads((Path(__file__).parent / "spec" / "time-statement.json").read_text())
CAPABILITY = FIXTURE["capabilityEtny"]["content"]
CAPABILITY_TIME = FIXTURE["capabilityTimeEtny"]["content"]
NODE = FIXTURE["node"]["address"]
VALIDATOR = FIXTURE["validator"]["address"]
CHAIN_ID = FIXTURE["network"]["chainId"]
POX = FIXTURE["network"]["pox"]
REGISTRY = FIXTURE["network"]["validatorRegistry"]
T0 = FIXTURE["measurement"]["t0"]
T1 = FIXTURE["measurement"]["t1"]
NOW = T1 + 60
LEGACY = FIXTURE["legacyV1"]
LEGACY_CAPABILITY = f"{LEGACY['line']}\n{LEGACY['signatureDerHex']}\n{FIXTURE['capabilityEtny']['platformLine']}\n"
LEGACY_TIME = timed_record.time_object(LEGACY["timeStatements"]["t0"]["answer"], LEGACY["timeStatements"]["t1"]["answer"])


def seated(address):
    return address == VALIDATOR


def admit(capability_etny=CAPABILITY, capability_time_etny=CAPABILITY_TIME, node=NODE, requirement="dcap",
          has_validator_set=True, now=NOW, chain_id=CHAIN_ID, pox=POX, registry=REGISTRY, is_seated=seated,
          matching_network=False):
    return capability.capability_record_from(capability_etny, capability_time_etny, node, requirement,
                                             has_validator_set, now, chain_id, pox, registry, is_seated,
                                             matching_network)


def admit_built(matching_network=False, now=None, **figures):
    """admit() on the objects of timed_record.trustedzone_objects(**figures)."""
    capability_etny, capability_time_etny = timed_record.trustedzone_objects(**figures)
    t1 = figures.get("t1", T1)
    return admit(capability_etny, capability_time_etny, now=t1 + 60 if now is None else now,
                 matching_network=matching_network)


def with_field(index, value, text=CAPABILITY):
    """The capability.etny `text` with field `index` of the record line replaced."""
    line, sig, platform, _ = text.split("\n")
    fields = line.split("|")
    fields[index] = value
    return "\n".join(["|".join(fields), sig, platform, ""])


def test_the_objects_built_from_the_test_keys_are_the_fixtures():
    assert timed_record.trustedzone_objects() == (CAPABILITY, CAPABILITY_TIME)


def test_capability_etny_parses_into_the_line_the_signature_and_the_platform_line():
    parsed = capability.parse_capability_etny(CAPABILITY)
    assert parsed["line"] == FIXTURE["line"]
    assert parsed["version"] == "v2"
    assert len(parsed["fields"]) == FIXTURE["lineFields"] == 14
    assert parsed["named"]["salt"] == FIXTURE["measurement"]["salt"] == parsed["fields"][8]
    assert parsed["sig_der_hex"] == FIXTURE["signatureDerHex"]
    assert parsed["platform_line"] == FIXTURE["capabilityEtny"]["platformLine"]


def test_the_legacy_line_parses_as_v1_with_thirteen_fields():
    parsed = capability.parse_capability_etny(LEGACY_CAPABILITY)
    assert parsed["version"] == "v1" and len(parsed["fields"]) == 13
    assert "salt" not in parsed["named"] and parsed["named"]["t0"] == str(LEGACY["measurement"]["t0"])


def test_capability_time_etny_parses_into_the_two_statements():
    t0, t1 = capability.parse_capability_time_etny(CAPABILITY_TIME)
    assert t0 == FIXTURE["capabilityTimeEtny"]["t0"]
    assert t1 == FIXTURE["capabilityTimeEtny"]["t1"]


def test_each_statement_recovers_to_the_fixture_validator():
    for statement in capability.parse_capability_time_etny(CAPABILITY_TIME):
        assert capability.recover_time_statement_signer(CHAIN_ID, REGISTRY, statement) == VALIDATOR


def test_the_digest_is_the_fixture_digest():
    for key in ("t0", "t1"):
        fields = FIXTURE["timeStatements"][key]["fields"]
        digest = capability.time_statement_digest(fields["chainId"], fields["registry"], fields["node"],
                                                  fields["nonce"], fields["t"], fields["block"])
        assert "0x" + digest.hex() == FIXTURE["timeStatements"][key]["digest"]


def test_the_close_nonce_is_the_fixtures():
    c = FIXTURE["closeNonce"]
    assert capability.close_nonce(c["n0"], c["salt"], c["outcome"]) == c["n1"]
    assert c["outcome"] == f"250|4|{FIXTURE['line'].split('|')[13]}"
    close = TIME_STATEMENT["close"]
    assert capability.close_nonce(close["n0"], close["salt"], close["outcome"]) == close["n1"] == c["n1"]
    assert FIXTURE["timeStatements"]["t1"]["fields"]["nonce"] == c["n1"]


def test_the_rate_bound_is_the_fixtures():
    bound = FIXTURE["rateBound"]
    assert (BENCHMARK_FULL_ROUNDS, BENCHMARK_OPS_PER_ROUND, RATE_ALLOWANCE_SECONDS, RATE_TOLERANCE_PERCENT) == (
        bound["benchmark"]["fullRounds"], bound["benchmark"]["opsPerRound"], bound["allowanceSeconds"],
        bound["tolerancePercent"])
    assert str(capability.record_work(FIXTURE["measurement"]["workers"])) == bound["recordWork"]
    for example in bound["examples"]:
        work = capability.record_work(example["workers"])
        assert not capability.above_rate_bound(example["boundGflops"], example["window"], work)
        assert capability.above_rate_bound(example["boundGflops"] + 1, example["window"], work)
    assert not capability.above_rate_bound(10**9, RATE_ALLOWANCE_SECONDS, capability.record_work(4))


def test_the_fixture_record_is_admitted_with_its_values():
    record, reason = admit()
    assert reason is None
    assert record["version"] == "v2"
    assert record["salt"] == FIXTURE["measurement"]["salt"]
    assert record["gflops"] == 250
    assert "cu" not in record
    assert record["cuv"] == 4
    assert record["validator"] == VALIDATOR
    assert record["nonce"] == FIXTURE["measurement"]["runNonce"]
    assert (record["t0"], record["t1"]) == (T0, T1)
    assert record["sig0"] == FIXTURE["timeStatements"]["t0"]["answer"]["sig"]
    assert record["sig1"] == FIXTURE["timeStatements"]["t1"]["answer"]["sig"]
    assert record["heap"] == "256M"
    assert record["workers"] == 4
    assert (record["block0"], record["block1"]) == (FIXTURE["measurement"]["block0"], FIXTURE["measurement"]["block1"])
    assert record["sig_der_hex"] == FIXTURE["signatureDerHex"]
    assert record["line"] == FIXTURE["line"]
    assert record["network"] == FIXTURE["network"]["networkField"]
    assert record["node"] == NODE
    assert record["platform_line"] == FIXTURE["capabilityEtny"]["platformLine"]
    assert record["capability_etny"] == CAPABILITY
    assert record["capability_time_etny"] == CAPABILITY_TIME


@pytest.mark.parametrize("matching_network", [True, False])
def test_the_v2_record_is_admitted_on_every_network(matching_network):
    assert admit(matching_network=matching_network)[1] is None


def test_the_v1_record_is_admitted_where_no_poxmatching_judges_and_refused_where_one_does():
    record, reason = admit(LEGACY_CAPABILITY, LEGACY_TIME, now=LEGACY["measurement"]["t1"] + 60)
    assert reason is None and record["version"] == "v1" and record["salt"] is None
    assert (record["block0"], record["block1"]) == (LEGACY["measurement"]["block0"], LEGACY["measurement"]["block1"])
    record, reason = admit(LEGACY_CAPABILITY, LEGACY_TIME, now=LEGACY["measurement"]["t1"] + 60,
                           matching_network=True)
    assert record is None and reason.startswith("admission:")
    assert FIXTURE["admission"]["forms"][0]["matchingNetwork"].startswith("refused with reason 1")


@pytest.mark.parametrize("case", FIXTURE["closingChecks"]["cases"], ids=[c["name"] for c in FIXTURE["closingChecks"]["cases"]])
@pytest.mark.parametrize("matching_network", [True, False])
def test_the_closing_checks_of_every_vector(case, matching_network):
    """Each closingChecks vector as the agent reads it: the line of the
    case's figures and its two answers' bodies, on both kinds of network."""
    m = FIXTURE["measurement"]
    line = "|".join(["etny-cu", "v2", FIXTURE["network"]["networkField"], NODE, str(case["gflops"]), str(m["cuv"]),
                     VALIDATOR, m["runNonce"], case["salt"], str(case["t0"]), str(case["t1"]), case["sig0"],
                     case["sig1"], f"heap={m['heap']};workers={m['workers']}"])
    capability_etny = f"{line}\n{FIXTURE['signatureDerHex']}\n{FIXTURE['capabilityEtny']['platformLine']}\n"
    capability_time_etny = f"{case['openAnswer']}\n{case['closeAnswer']}\n"
    record, reason = admit(capability_etny, capability_time_etny, now=case["t1"] + 60,
                           matching_network=matching_network)
    if case["expected"]["admitted"]:
        assert reason is None and record["gflops"] == case["gflops"]
    else:
        assert record is None and reason.startswith(case["expected"]["refusedBy"] + ":"), reason


def test_the_closing_checks_cover_the_fixtures_order():
    assert FIXTURE["closingChecks"]["order"] == ["closeNonce", "times", "blocks", "equalBodies", "rateBound"]
    refused = {c["expected"].get("refusedBy") for c in FIXTURE["closingChecks"]["cases"]} - {None}
    assert refused == set(FIXTURE["closingChecks"]["order"])


def test_a_closing_body_equal_to_the_opening_one_is_refused():
    opening = CAPABILITY_TIME.split("\n")[0]
    record, reason = admit(capability_time_etny=f"{opening}\n{opening}\n")
    assert record is None and reason.startswith("equalBodies:")


def test_a_closing_statement_over_the_run_nonce_is_refused():
    capability_etny, _ = timed_record.trustedzone_objects()
    m = FIXTURE["measurement"]
    opening = timed_record.answer(m["runNonce"], T0, m["block0"])
    closing = timed_record.answer(m["runNonce"], T1, m["block1"])
    line = with_field(12, closing["sig"], capability_etny)
    record, reason = admit(line, timed_record.time_object(opening, closing))
    assert record is None and reason.startswith("closeNonce:")


def test_the_rate_bound_refuses_a_record_above_it_and_admits_one_at_it():
    record, reason = admit_built(t1=T0 + 12, block1=FIXTURE["measurement"]["block0"] + 6, gflops=231)
    assert record is None and reason.startswith("rateBound:")
    assert admit_built(t1=T0 + 12, block1=FIXTURE["measurement"]["block0"] + 6, gflops=230)[1] is None


def test_the_rate_bound_is_not_applied_to_a_v1_record():
    record, reason = admit_built(version="v1", t1=T0 + 12, gflops=6400)
    assert reason is None and record["version"] == "v1"


def test_the_record_round_trips_through_json():
    record, _ = admit()
    assert json.loads(json.dumps(record)) == record


def test_a_checksum_node_address_is_matched_lower_cased():
    record, reason = admit(node="0x470D8B388f4a0AE6bD512b74038c7e87957bb8FE")
    assert reason is None and record["node"] == NODE


def test_an_empty_validator_is_refused():
    record, reason = admit(capability_etny=with_field(6, ""))
    assert record is None and "no validator" in reason


def test_an_empty_signature_field_is_refused():
    assert admit(capability_etny=with_field(11, ""))[0] is None
    assert admit(capability_etny=with_field(12, ""))[0] is None


def test_a_salt_that_is_not_32_bytes_of_hex_is_refused():
    record, reason = admit(capability_etny=with_field(8, "0x1234"))
    assert record is None and "salt" in reason


def test_a_missing_time_object_is_refused():
    record, reason = admit(capability_time_etny=None)
    assert record is None and reason == "no capability.time.etny"


def test_a_time_object_with_one_statement_is_refused():
    first = CAPABILITY_TIME.split("\n")[0] + "\n"
    record, reason = admit(capability_time_etny=first)
    assert record is None and "statements" in reason


def test_a_time_object_that_disagrees_with_the_line_is_refused():
    swapped = "\n".join(reversed(CAPABILITY_TIME.strip().split("\n"))) + "\n"
    record, reason = admit(capability_time_etny=swapped)
    assert record is None and "does not match" in reason


def test_a_statement_signed_for_another_registry_is_refused():
    record, reason = admit(registry="0x" + "11" * 20)
    assert record is None and "recovers to" in reason


def test_a_statement_signed_for_another_chain_is_refused():
    assert admit(chain_id=CHAIN_ID + 1)[0] is None


def test_another_node_address_is_refused():
    record, reason = admit(node="0x" + "ab" * 20)
    assert record is None and "names node" in reason


def test_another_pox_contract_is_refused_and_the_address_case_is_not_compared():
    record, reason = admit(pox="0x" + "ab" * 20)
    assert record is None and "names network" in reason
    assert admit(pox=POX.lower())[1] is None
    assert admit(pox=POX.upper().replace("0X", "0x"))[1] is None
    assert FIXTURE["network"]["networkField"] == f"{CHAIN_ID}:{POX}"
    assert capability.network_field(CHAIN_ID, POX) == FIXTURE["network"]["networkField"].lower()


def test_another_chain_id_in_the_network_field_is_refused():
    record, reason = admit(capability_etny=with_field(2, f"{CHAIN_ID + 1}:{POX}"))
    assert record is None and "names network" in reason


def test_a_run_longer_than_the_benchmark_bound_is_refused():
    block0 = FIXTURE["measurement"]["block0"]
    record, reason = admit_built(t1=T0 + BENCHMARK_MAX_SECONDS + 1, block1=block0 + 200, gflops=1)
    assert record is None and "run too long" in reason
    assert admit_built(t1=T0 + BENCHMARK_MAX_SECONDS, block1=block0 + 200, gflops=1)[1] is None
    record, reason = admit_built(t1=T0 - 1)
    assert record is None and reason.startswith("times:")
    legacy_t0 = LEGACY["measurement"]["t0"]
    record, reason = admit(with_field(9, str(legacy_t0 + BENCHMARK_MAX_SECONDS + 1), LEGACY_CAPABILITY), LEGACY_TIME)
    assert record is None and "run too long" in reason
    record, reason = admit(with_field(9, str(legacy_t0 - 1), LEGACY_CAPABILITY), LEGACY_TIME)
    assert record is None and "run too long" in reason
    assert BENCHMARK_MAX_SECONDS == 300
    assert T1 - T0 <= BENCHMARK_MAX_SECONDS


def test_a_closing_block_below_the_opening_one_is_refused():
    record, reason = admit_built(block1=FIXTURE["measurement"]["block0"] - 1)
    assert record is None and reason.startswith("blocks:")


def test_a_validator_that_is_not_seated_is_refused():
    record, reason = admit(is_seated=lambda address: False)
    assert record is None and "not seated" in reason


def test_gflops_above_the_cap_is_refused_and_the_cap_is_admitted():
    t1 = T0 + RATE_ALLOWANCE_SECONDS
    record, reason = admit_built(gflops=CU_MAX_GFLOPS + 1, t1=t1)
    assert record is None and "above CU_MAX_GFLOPS" in reason
    assert admit_built(gflops=CU_MAX_GFLOPS, t1=t1)[1] is None


def test_a_record_below_the_minimum_version_is_refused():
    record, reason = admit_built(cuv=3)
    assert record is None and reason == "cuv 3 is below CU_VERSION_MIN 4"
    assert admit_built(cuv=0)[0] is None
    record, reason = admit_built(cuv=CU_VERSION_MIN)
    assert reason is None and record["cuv"] == CU_VERSION_MIN


def test_the_gflops_bound_is_checked_before_the_version():
    record, reason = admit_built(gflops=CU_MAX_GFLOPS + 1, cuv=3, t1=T0 + RATE_ALLOWANCE_SECONDS)
    assert record is None and "above CU_MAX_GFLOPS" in reason


def test_an_sgx_requirement_is_refused():
    record, reason = admit(requirement="sgx")
    assert record is None and "requires sgx" in reason


def test_a_network_without_a_validator_set_is_refused():
    record, reason = admit(has_validator_set=False)
    assert record is None and "no validator set" in reason


def test_a_record_older_than_the_ttl_is_refused():
    assert admit(now=T1 + CU_TTL_SECONDS)[1] is None
    record, reason = admit(now=T1 + CU_TTL_SECONDS + 1)
    assert record is None and "outside CU_TTL_SECONDS" in reason


def test_a_missing_capability_etny_is_refused():
    record, reason = admit(capability_etny=None)
    assert record is None and reason == "no capability.etny"


def test_a_malformed_record_line_is_refused():
    assert admit(capability_etny="etny-cu|v2|short\nabcd\nplatform|\n")[0] is None
    assert admit(capability_etny=with_field(0, "etny-xx"))[0] is None
    assert admit(capability_etny=with_field(1, "v3"))[0] is None
    assert admit(capability_etny=with_field(4, "2.5"))[0] is None


@pytest.mark.parametrize("last", ["heap=256M;workers=4;encmem=2;custorage=37;epc=1024", "heap=256M", "workers=4",
                                  "heap=;workers=4", "heap=256M;workers=04", "heap=256M;workers=x",
                                  "workers=4;heap=256M", "heap=256M;workers=4;"])
def test_a_trustedzone_line_whose_last_field_is_not_heap_and_workers_is_refused(last):
    record, reason = admit_built(last=last)
    assert record is None and ("heap=<heap>;workers=<n>" in reason or "workers is not" in reason), reason


def test_a_bench_line_is_no_trustedzone_record():
    bench = json.loads((Path(__file__).parent / "spec" / "bench-record.json").read_text())
    record, reason = admit(capability_etny=bench["probeEtny"]["content"])
    assert record is None and "not tagged etny-cu|v1, etny-cu|v2" in reason


def test_a_stored_v1_record_is_not_admitted_where_a_poxmatching_judges():
    assert capability.record_admitted_on({"version": "v2"}, True)
    assert not capability.record_admitted_on({"version": "v1"}, True)
    assert not capability.record_admitted_on({"gflops": 250}, True)
    assert capability.record_admitted_on({"gflops": 250}, False)
    assert capability.record_version({"gflops": 250}) == "v1"


def test_the_expiry_is_at_t1_plus_the_ttl():
    assert not capability.record_expired(T1, T1 + CU_TTL_SECONDS - 1)
    assert capability.record_expired(T1, T1 + CU_TTL_SECONDS)


def test_the_re_measure_is_due_a_margin_before_the_ttl():
    passed_at = 1_700_000_000
    due_at = passed_at + CU_TTL_SECONDS - CU_REMEASURE_MARGIN_SECONDS
    assert not capability.remeasure_due(passed_at, due_at - 1)
    assert capability.remeasure_due(passed_at, due_at)
    assert due_at == passed_at + CU_TTL_SECONDS - 86400


def test_the_reason_table_names_the_validators_codes():
    assert [capability.cancel_reason_name(code) for code in range(1, 14)] == [
        "RecordSignatureInvalid", "RecordNodeMismatch", "TimestampNotSeated", "RunTooLong",
        "RecordExpired", "GflopsAboveMax", "DeliveredBelowAttested", "CuVersionBelowMinimum", "RecordMissing",
        "EnclaveMemoryAboveRecord", "StorageAboveRecord", "EpcAboveRecord", "GflopsAboveRecord"]
    assert capability.cancel_reason_name(14) == "Unknown(14)"


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", Path(__file__).resolve().parents[1] / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("matching_network, dropped", [(True, True), (False, False)])
def test_a_stored_v1_record_is_dropped_at_load_where_a_poxmatching_judges(agent, monkeypatch, tmp_path,
                                                                         matching_network, dropped):
    record, _ = admit(LEGACY_CAPABILITY, LEGACY_TIME, now=LEGACY["measurement"]["t1"] + 60)
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put("a-tz", "QmA", LEGACY["measurement"]["t1"], record)
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__integration_images = ["a-tz"]
    node._EtnyPoXNode__trustedzones = {"a-tz": "dcap"}
    node._EtnyPoXNode__record_store = store
    node._EtnyPoXNode__record_missing_hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__matching_network = matching_network
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__mark_stale_integration_tests", lambda self, images: set())
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__tested_las_kept", lambda self, image, tree: True)
    monkeypatch.setattr(agent.time, "time", lambda: LEGACY["measurement"]["t1"] + 60)
    agent.integration_test_remeasure.discard("a-tz")
    node._EtnyPoXNode__load_passed_integration_tests()
    assert (store.get("a-tz")["record"] is None) is dropped
    assert ("a-tz" in agent.integration_test_remeasure) is dropped
    assert agent.integration_test_passed["a-tz"] == "QmA"
    agent.integration_test_remeasure.discard("a-tz")
    agent.integration_test_passed.pop("a-tz", None)
