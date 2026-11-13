"""capability: the two integration-bucket objects of tests/spec/
capability-record.json parsed and admitted (PHASE-E-PLAN.md E3, E8, E40),
the refusals, the expiry and the re-measure bounds."""
import json
from pathlib import Path

import pytest

import capability
from constants import BENCHMARK_MAX_SECONDS, CU_MAX_THOUSANDTHS, CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS

FIXTURE = json.loads((Path(__file__).parent / "spec" / "capability-record.json").read_text())
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


def seated(address):
    return address == VALIDATOR


def admit(capability_etny=CAPABILITY, capability_time_etny=CAPABILITY_TIME, node=NODE, requirement="dcap",
          has_validator_set=True, now=NOW, chain_id=CHAIN_ID, pox=POX, registry=REGISTRY, is_seated=seated):
    return capability.capability_record_from(capability_etny, capability_time_etny, node, requirement,
                                             has_validator_set, now, chain_id, pox, registry, is_seated)


def with_field(index, value):
    """capability.etny with field `index` of the record line replaced."""
    line, sig, platform, _ = CAPABILITY.split("\n")
    fields = line.split("|")
    fields[index] = value
    return "\n".join(["|".join(fields), sig, platform, ""])


def test_capability_etny_parses_into_the_line_the_signature_and_the_platform_line():
    parsed = capability.parse_capability_etny(CAPABILITY)
    assert parsed["line"] == FIXTURE["line"]
    assert len(parsed["fields"]) == 13
    assert parsed["sig_der_hex"] == FIXTURE["signatureDerHex"]
    assert parsed["platform_line"] == FIXTURE["capabilityEtny"]["platformLine"]


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


def test_the_fixture_record_is_admitted_with_its_values():
    record, reason = admit()
    assert reason is None
    assert record["cu"] == 2500
    assert record["cuv"] == 1
    assert record["validator"] == VALIDATOR
    assert record["nonce"] == FIXTURE["measurement"]["runNonce"]
    assert (record["t0"], record["t1"]) == (T0, T1)
    assert record["sig0"] == FIXTURE["timeStatements"]["t0"]["answer"]["sig"]
    assert record["sig1"] == FIXTURE["timeStatements"]["t1"]["answer"]["sig"]
    assert record["heap"] == "256M"
    assert record["workers"] == 4
    assert (record["block0"], record["block1"]) == (29000000, 29000015)
    assert record["sig_der_hex"] == FIXTURE["signatureDerHex"]
    assert record["line"] == FIXTURE["line"]
    assert record["network"] == FIXTURE["network"]["networkField"]
    assert record["node"] == NODE
    assert record["platform_line"] == FIXTURE["capabilityEtny"]["platformLine"]
    assert record["capability_etny"] == CAPABILITY
    assert record["capability_time_etny"] == CAPABILITY_TIME


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
    assert admit(capability_etny=with_field(10, ""))[0] is None
    assert admit(capability_etny=with_field(11, ""))[0] is None


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
    record, reason = admit(capability_etny=with_field(9, str(T0 + BENCHMARK_MAX_SECONDS + 1)))
    assert record is None and "run too long" in reason
    record, reason = admit(capability_etny=with_field(9, str(T0 - 1)))
    assert record is None and "run too long" in reason
    assert BENCHMARK_MAX_SECONDS == 300
    assert T1 - T0 <= BENCHMARK_MAX_SECONDS


def test_a_validator_that_is_not_seated_is_refused():
    record, reason = admit(is_seated=lambda address: False)
    assert record is None and "not seated" in reason


def test_cu_above_the_cap_is_refused_and_the_cap_is_admitted():
    record, reason = admit(capability_etny=with_field(4, str(CU_MAX_THOUSANDTHS + 1)))
    assert record is None and "above CU_MAX_THOUSANDTHS" in reason
    assert admit(capability_etny=with_field(4, str(CU_MAX_THOUSANDTHS)))[1] is None


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
    assert admit(capability_etny="etny-cu|v1|short\nabcd\nplatform|\n")[0] is None
    assert admit(capability_etny=with_field(0, "etny-xx"))[0] is None
    assert admit(capability_etny=with_field(4, "2.5"))[0] is None


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
    assert [capability.cancel_reason_name(code) for code in range(1, 8)] == [
        "RecordSignatureInvalid", "RecordNodeMismatch", "TimestampNotSeated", "RunTooLong",
        "RecordExpired", "CuAboveMax", "DeliveredBelowAttested"]
    assert capability.cancel_reason_name(9) == "Unknown(9)"
