"""capability.cancel_reason_name / cancel_disposition (PHASE-E-PLAN.md E15,
C.5): the reason table of cancelDPRequestByValidators 1..10, 're-test then
re-add' for the record's faults 1..6 and 8 and for 9, each applied to a
request that carried a record (capability.cancel_reaction,
test_record_missing_hold.py), 'memory' for 10, EnclaveMemoryAboveRecord
(test_enclave_memory_cancel.py), and 're-add' for the strike reason 7 and
any code outside the table."""
import pytest

import capability


@pytest.mark.parametrize("code, name", [
    (1, "RecordSignatureInvalid"),
    (2, "RecordNodeMismatch"),
    (3, "TimestampNotSeated"),
    (4, "RunTooLong"),
    (5, "RecordExpired"),
    (6, "GflopsAboveMax"),
    (7, "DeliveredBelowAttested"),
    (8, "CuVersionBelowMinimum"),
    (9, "RecordMissing"),
    (10, "EnclaveMemoryAboveRecord"),
])
def test_the_reason_table(code, name):
    assert capability.cancel_reason_name(code) == name
    assert capability.CANCEL_REASONS[code] == name


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 8, 9])
def test_a_record_fault_re_tests_before_the_re_add(code):
    assert capability.cancel_disposition(code) == "retest"


def test_a_record_below_the_minimum_version_re_tests_then_re_adds():
    assert 8 in capability.RETEST_REASONS
    assert capability.cancel_disposition(8) == "retest"


def test_a_missing_record_is_reason_9_and_re_tests_a_record_the_request_carried():
    assert capability.REASON_RECORD_MISSING == 9
    assert capability.cancel_reason_name(capability.REASON_RECORD_MISSING) == "RecordMissing"
    assert capability.cancel_disposition(capability.REASON_RECORD_MISSING) == "retest"
    assert capability.cancel_reaction(capability.REASON_RECORD_MISSING, "3065ab") == (True, True)
    assert capability.cancel_reaction(capability.REASON_RECORD_MISSING, None) == (True, False)


def test_the_strike_reason_re_adds_without_a_re_test():
    assert capability.cancel_disposition(7) == "readd"


def test_an_enclave_memory_above_the_record_is_reason_10_and_neither_holds_nor_re_tests_by_itself():
    assert capability.REASON_ENCLAVE_MEMORY_ABOVE_RECORD == 10
    assert capability.cancel_disposition(10) == "memory"
    assert 10 not in capability.RETEST_REASONS
    assert capability.cancel_reaction(10, "3065ab") == (False, False)
    assert capability.cancel_reaction(10, None) == (False, False)


def test_a_code_outside_the_table_re_adds_without_a_re_test():
    assert capability.cancel_disposition(0) == "readd"
    assert capability.cancel_disposition(11) == "readd"
    assert capability.cancel_reason_name(0) == "Unknown(0)"
    assert capability.cancel_reason_name(11) == "Unknown(11)"
