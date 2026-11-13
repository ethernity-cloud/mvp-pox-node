"""capability.cancel_reason_name / cancel_disposition (PHASE-E-PLAN.md E15,
C.5): the reason table of cancelDPRequestByValidators 1..7, 're-test then
re-add' for the record's faults 1..6 and 're-add' for the strike reason 7
and any code outside the table."""
import pytest

import capability


@pytest.mark.parametrize("code, name", [
    (1, "RecordSignatureInvalid"),
    (2, "RecordNodeMismatch"),
    (3, "TimestampNotSeated"),
    (4, "RunTooLong"),
    (5, "RecordExpired"),
    (6, "CuAboveMax"),
    (7, "DeliveredBelowAttested"),
])
def test_the_reason_table(code, name):
    assert capability.cancel_reason_name(code) == name
    assert capability.CANCEL_REASONS[code] == name


@pytest.mark.parametrize("code", range(1, 7))
def test_a_record_fault_re_tests_before_the_re_add(code):
    assert capability.cancel_disposition(code) == "retest"


def test_the_strike_reason_re_adds_without_a_re_test():
    assert capability.cancel_disposition(7) == "readd"


def test_a_code_outside_the_table_re_adds_without_a_re_test():
    assert capability.cancel_disposition(0) == "readd"
    assert capability.cancel_disposition(8) == "readd"
    assert capability.cancel_reason_name(0) == "Unknown(0)"
