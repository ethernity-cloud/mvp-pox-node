"""utils.result_end_fields and result_fields on v6 (D175, D182;
pox-smart-contract-litvm spec/matching.md section 17): the three fields
after the fifteen of v5, landedAt, ranUntil and slClass, read on every
accepted vector of spec/matching/result-fields.json with its values and on
no refused one; a v6 result's task code, commits and gas read as a v5
one's; and the task table with the codes of 3.19 (MATCHING-DESIGN.md
7.16)."""
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
