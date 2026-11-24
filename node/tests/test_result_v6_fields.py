"""utils.result_end_fields and result_fields on v6 (D175, D182;
pox-smart-contract-litvm 2f600f6 spec/matching.md section 17;
MATCHING-SYNC-PLAN.md C02): the three fields after the fifteen of v5,
landedAt, ranUntil and slClass, read on every accepted vector of
spec/matching/result-fields.json with its values and on no refused one; a
v6 result's task code, commits and gas read as a v5 one's; a result of
twenty fields or more (the round's writers' and every session status's) read
as one of eighteen, fields 18 and 19 not read; and the task table with the
codes of 3.19 (MATCHING-DESIGN.md 7.16)."""
import json
from pathlib import Path

import pytest

from models import ResultStatus
from utils import result_end_fields, result_fields

FIELDS = json.loads((Path(__file__).parent / "spec" / "matching" / "result-fields.json").read_text())


@pytest.mark.parametrize("case", FIELDS["accepted"], ids=[c["name"] for c in FIELDS["accepted"]])
def test_an_accepted_result_carries_its_three_fields(case):
    want = case["expected"]
    assert result_end_fields(case["result"]) == (int(want["landedAt"]), int(want["ranUntil"]), want["slClass"])
    fields = result_fields(case["result"])
    assert fields.version == "v6" and int(fields.task_code) == want["code"]


@pytest.mark.parametrize("case", FIELDS["refused"], ids=[c["name"] for c in FIELDS["refused"]])
def test_a_refused_result_carries_none_of_them(case):
    assert result_end_fields(case["result"]) is None
    assert int(case["result"].split(":")[1]) == case["expected"]["code"]


V6 = FIELDS["v6"]
TWENTY = [c for c in V6["writers"] + V6["sessionStatuses"] if len(c["result"].split(":")) >= 20]


def plain_decimal_fields(result):
    parts = result.split(":")
    return all(text.isdigit() and (text == "0" or not text.startswith("0")) for text in parts[15:18])


@pytest.mark.parametrize("case", TWENTY, ids=[c["name"] for c in TWENTY])
def test_a_result_of_twenty_fields_or_more_yields_its_code_esr_fields_and_class(case):
    """result-fields.json v6 at 2f600f6: the writers' twenty-field vectors
    and the session-status vectors are read as one of eighteen fields
    (readers, "20 or more": the agent "as for 18"): the code, the ESR
    fields and the three fields 15 to 17; fields 18 and 19, whatever they
    hold, are not read."""
    parts = case["result"].split(":")
    fields = result_fields(case["result"])
    assert fields.version == "v6" and fields.task_code == parts[1]
    assert (fields.esr_commits, fields.esr_gas_wei) == (int(parts[12].split("=")[1]), int(parts[14].split("=")[1]))
    assert plain_decimal_fields(case["result"])
    assert result_end_fields(case["result"]) == (int(parts[15]), int(parts[16]), int(parts[17]))
    eighteen = ":".join(parts[:18])
    assert result_end_fields(eighteen) == result_end_fields(case["result"])
    assert result_fields(eighteen) == fields


def test_the_twenty_field_vectors_cover_every_session_status_and_the_invalid_forms_of_fields_18_and_19():
    assert V6["writerFields"] == 20
    assert {c["status"] for c in V6["sessionStatuses"]} >= {"00", "01", "02", "03", "04", "05", "06", "07", "0f"}
    names = {c["name"] for c in TWENTY}
    assert {"close-block-empty", "close-rows-not-digit", "rows-without-block", "pair-without-session"} <= names
    readers = {r["fieldCount"]: r["agent"] for r in V6["readers"]}
    assert readers["20 or more"] == "as for 18"


def test_a_v5_result_is_read_as_before():
    v5 = next(c for c in FIELDS["refused"] if c["name"] == "v5-only")["result"]
    fields = result_fields(v5)
    assert fields.version == "v5" and int(fields.task_code) == 62 and fields.esr_commits == 0


def test_the_task_table_holds_the_codes_of_the_trustedzone_releases():
    codes = {20: "SIGNATURE_ERROR", 32: "CONFIG_ERROR", 33: "EXECUTION_TIMEOUT", 35: "SECURITY_VIOLATION",
             37: "ESR_RELAY_TIMEOUT", 39: "SESSION_RELAY_FAULT", 40: "CAS_ATTESTATION_FAULT", 43: "TERMINATED",
             44: "TRUSTEDZONE_FAILURE", 45: "PAYLOAD_DELIVERY_FAULT", 46: "INPUT_DELIVERY_FAULT",
             47: "CHAIN_UNAVAILABLE", 59: "NETWORK_UNAVAILABLE", 60: "PAYLOAD_CRASHED", 61: "CONTENT_TOO_LARGE",
             62: "SECURELOCK_SILENT", 63: "PAYLOAD_LOST"}
    for code, name in codes.items():
        assert getattr(ResultStatus, name) == code
        assert ResultStatus.name_of(code) == name
    assert ResultStatus.name_of(0) == "SUCCESS" and ResultStatus.name_of(8) == "EXECVE"
    assert ResultStatus.name_of(99) == "CODE_99"
