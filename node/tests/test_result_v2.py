"""utils.result_fields reads fields 12 and 14 of the trustedzone's v5 result
string (tests/spec/result-v5.json), which _addResultToOrderV2 carries
verbatim, strictly; utils.result_send_decision holds the result while the
order's gas wait or the operator's ceiling holds it and abandons it
RESULT_SEND_GRACE_SECONDS past the deadline (E23)."""
import json
import os

import pytest

from constants import RESULT_SEND_GRACE_SECONDS
from utils import result_fields, result_send_decision

FIXTURE = os.path.join(os.path.dirname(__file__), "spec", "result-v5.json")
with open(FIXTURE, encoding="utf-8") as f:
    EXAMPLES = json.load(f)["examples"]

NOW = 1_700_000_000
END = NOW + 600
DEADLINE = END + 120


def test_the_examples_without_commits_yield_zero_and_zero():
    for name in ("plain", "session", "probed", "transferred", "replayed"):
        fields = result_fields(EXAMPLES[name]["result"])
        assert fields.version == "v5"
        assert (fields.esr_commits, fields.esr_gas_wei) == (0, 0), name


def test_the_esr_and_forced_examples_yield_the_landed_count_and_their_gas():
    for name in ("esr", "forced"):
        fields = result_fields(EXAMPLES[name]["result"])
        assert (fields.esr_commits, fields.esr_gas_wei) == (3, 27641742500000000), name
        assert fields.esr_root == "19f47a6645815eb491c9835effb168157db6ec127ab806eedac86fd0a2ea703f"
        assert fields.ledger_cid == "bafkreidspmoigauzlox6cshweg4kp2jbphgkl22q7v32jggny7gurfhjoi"


def test_the_task_codes_of_the_transfer_examples():
    assert result_fields(EXAMPLES["transferred"]["result"]).task_code == "41"
    assert result_fields(EXAMPLES["forced"]["result"]).task_code == "42"
    assert result_fields(EXAMPLES["plain"]["result"]).task_code == "0"


def test_a_v3_string_yields_zero_and_zero():
    fields = result_fields("v3:0:dffd6021:k7p2x9qv4m1zr8t3hw6n")
    assert (fields.version, fields.task_code, fields.esr_commits, fields.esr_gas_wei) == ("v3", "0", 0, 0)
    assert result_fields("v4:5:abc:chal:" + "0" * 64 + ":00:").ledger_cid == ""


def v5_with(index, value):
    parts = EXAMPLES["esr"]["result"].split(":")
    parts[index] = value
    return ":".join(parts)


def test_the_bounds_of_fields_12_and_14():
    assert result_fields(v5_with(12, "etny-esr-commits=65535")).esr_commits == 65535
    with pytest.raises(ValueError):
        result_fields(v5_with(12, "etny-esr-commits=65536"))
    assert result_fields(v5_with(14, "etny-esr-gas=" + "9" * 78)).esr_gas_wei == int("9" * 78)
    with pytest.raises(ValueError):
        result_fields(v5_with(14, "etny-esr-gas=" + "9" * 79))


def test_malformed_fields_and_other_strings_are_refused():
    for bad in (v5_with(12, "etny-esr-commits="), v5_with(12, "etny-esr-commits=-1"), v5_with(12, "3"),
                v5_with(14, "etny-esr-gas=1.5"), v5_with(14, "etny-esr-gas=٣"),
                ":".join(EXAMPLES["esr"]["result"].split(":")[:14]), EXAMPLES["esr"]["result"] + ":x",
                "v6:0:a:b", "", None, "hello"):
        with pytest.raises(ValueError):
            result_fields(bad)


def test_the_gas_wait_holds_the_result_before_the_orders_end_and_not_after():
    assert result_send_decision(NOW, END, DEADLINE, True, False, False) == 'wait'
    assert result_send_decision(END, END, DEADLINE, True, False, False) == 'send'
    assert result_send_decision(NOW, None, DEADLINE, True, False, False) == 'wait'
    assert result_send_decision(NOW, END, DEADLINE, False, False, False) == 'send'


def test_inside_the_own_handover_window_the_result_goes_at_once():
    assert result_send_decision(NOW, END, DEADLINE, True, True, True) == 'send'


def test_above_the_ceiling_the_result_waits_an_hour_past_the_deadline_then_is_abandoned():
    assert result_send_decision(NOW, END, DEADLINE, False, False, True) == 'wait'
    assert result_send_decision(DEADLINE + RESULT_SEND_GRACE_SECONDS - 1, END, DEADLINE, False, False, True) == 'wait'
    assert result_send_decision(DEADLINE + RESULT_SEND_GRACE_SECONDS, END, DEADLINE, False, False, True) == 'abandon'
    assert RESULT_SEND_GRACE_SECONDS == 3600
