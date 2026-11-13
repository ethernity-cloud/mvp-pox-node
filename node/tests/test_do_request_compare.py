"""utils.do_request_compare (PHASE-E-PLAN.md D.3, E17): on a handle with a
reader the DO requests are judged against the standing request's terms, and a
pass whose terms were not read judges none; without a reader the legacy
uint8 compare runs."""
from utils import do_request_compare, dp_terms


def terms():
    return dp_terms((2500, 46_810_056_000_000_000, False, 1_762_000_000, True))


def test_a_reader_handle_with_terms_judges_by_the_terms():
    assert do_request_compare(True, terms()) == 'terms'


def test_a_reader_handle_without_terms_skips_the_pass():
    assert do_request_compare(True, None) == 'skip'


def test_a_handle_without_a_reader_runs_the_legacy_compare():
    assert do_request_compare(False, None) == 'legacy'
    assert do_request_compare(False, terms()) == 'legacy'
