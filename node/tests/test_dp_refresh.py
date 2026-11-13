"""The 1.2 DP request's refresh, resume and replacement rules
(PHASE-E-PLAN.md E12-E14, E16, D.2): utils.refresh_due at the margin,
resume_admits (not recorded: cancel, expiring: refresh, else resume),
dp_request_disposition (no terms, another record or the other
accept-reserved flag: readd), and bounded_cancel with injected callables (a
non-AVAILABLE request answers at once, CANCEL_ATTEMPTS sends then
abandoned)."""
import pytest

import constants
from constants import CANCEL_ATTEMPTS, DP_REFRESH_MARGIN_SECONDS
from utils import DPTerms, bounded_cancel, dp_request_disposition, dp_terms, refresh_due, resume_admits

NOW = 1_762_000_000
KEY = "3065023100cb37"


def terms(expires_in=7200, recorded=True, accepts_reserved=True, cu=2500, tip=10**15):
    return DPTerms(cu, tip, accepts_reserved, NOW + expires_in, recorded)


def test_dp_terms_from_the_contract_answer():
    assert dp_terms((2500, 10**15, True, NOW + 7200, True)) == terms()
    assert dp_terms((0, 0, False, 0, False)) == DPTerms(0, 0, False, 0, False)


def test_refresh_due_at_the_margin():
    assert DP_REFRESH_MARGIN_SECONDS == 3600
    assert refresh_due(NOW + DP_REFRESH_MARGIN_SECONDS, NOW) is True
    assert refresh_due(NOW + DP_REFRESH_MARGIN_SECONDS + 1, NOW) is False
    assert refresh_due(NOW - 10, NOW) is True
    assert refresh_due(NOW, NOW) is True


def test_resume_admits():
    assert resume_admits(terms(recorded=False), NOW) == "cancel"
    assert resume_admits(terms(recorded=False, expires_in=10), NOW) == "cancel"
    assert resume_admits(terms(expires_in=3600), NOW) == "refresh"
    assert resume_admits(terms(expires_in=-5), NOW) == "refresh"
    assert resume_admits(terms(expires_in=3601), NOW) == "resume"
    assert resume_admits(terms(), NOW) == "resume"


def test_the_disposition_keeps_a_request_in_order():
    assert dp_request_disposition(terms(), NOW, KEY, KEY) == "keep"
    assert dp_request_disposition(terms(), NOW, None, None) == "keep"
    assert dp_request_disposition(terms(), NOW, KEY, KEY, accepts_reserved=True) == "keep"


def test_the_disposition_refreshes_a_request_near_its_expiry():
    assert dp_request_disposition(terms(expires_in=3600), NOW, KEY, KEY) == "refresh"
    assert dp_request_disposition(terms(expires_in=0), NOW, KEY, KEY) == "refresh"


def test_the_disposition_replaces_a_request_without_terms():
    assert dp_request_disposition(terms(recorded=False), NOW, KEY, KEY) == "readd"
    assert dp_request_disposition(terms(recorded=False, expires_in=10), NOW, KEY, KEY) == "readd"


def test_the_disposition_replaces_a_request_on_a_changed_record():
    assert dp_request_disposition(terms(), NOW, None, KEY) == "readd"
    assert dp_request_disposition(terms(), NOW, KEY, None) == "readd"
    assert dp_request_disposition(terms(), NOW, KEY, "another") == "readd"
    assert dp_request_disposition(terms(expires_in=10), NOW, KEY, "another") == "readd"


def test_the_disposition_replaces_a_request_on_the_other_reserved_flag():
    assert dp_request_disposition(terms(accepts_reserved=True), NOW, KEY, KEY, accepts_reserved=False) == "readd"
    assert dp_request_disposition(terms(accepts_reserved=False), NOW, KEY, KEY, accepts_reserved=True) == "readd"
    assert dp_request_disposition(terms(accepts_reserved=False), NOW, KEY, KEY, accepts_reserved=None) == "keep"
    assert dp_request_disposition(terms(accepts_reserved=False), NOW, KEY, KEY) == "keep"


class Sender:
    """Injected callables of bounded_cancel: the statuses answered in turn
    (the last one repeated), the simulations' and sends' answers in turn."""

    def __init__(self, statuses, simulations=(), sends=()):
        self.statuses = list(statuses)
        self.simulations = list(simulations)
        self.sends = list(sends)
        self.calls = []

    def available(self):
        self.calls.append("status")
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def simulate(self):
        self.calls.append("simulate")
        return self.simulations.pop(0) if self.simulations else True

    def send(self):
        self.calls.append("send")
        return self.sends.pop(0) if self.sends else False

    def wait(self):
        self.calls.append("wait")

    def run(self, **kwargs):
        return bounded_cancel(self.available, self.simulate, self.send, self.wait, **kwargs)


def test_a_non_available_request_returns_at_once():
    sender = Sender([False])
    assert sender.run() == "not-available"
    assert sender.calls == ["status"]


def test_five_attempts_then_abandoned():
    assert CANCEL_ATTEMPTS == 5
    sender = Sender([True])
    assert sender.run() == "abandoned"
    assert sender.calls.count("send") == CANCEL_ATTEMPTS
    assert sender.calls.count("simulate") == CANCEL_ATTEMPTS
    assert sender.calls.count("wait") == CANCEL_ATTEMPTS - 1
    assert sender.calls[-1] == "status"


def test_a_cancel_that_lands_ends_the_attempts():
    sender = Sender([True], sends=[False, True])
    assert sender.run() == "cancelled"
    assert sender.calls == ["status", "simulate", "send", "status", "wait", "simulate", "send"]


def test_a_refused_simulation_re_reads_the_status():
    sender = Sender([True, True, False], simulations=[False, False])
    assert sender.run() == "not-available"
    assert sender.calls == ["status", "simulate", "status", "wait", "simulate", "status"]
    assert "send" not in sender.calls


def test_a_refused_simulation_of_a_standing_request_is_tried_again():
    sender = Sender([True], simulations=[False, True], sends=[True])
    assert sender.run() == "cancelled"
    assert sender.calls == ["status", "simulate", "status", "wait", "simulate", "send"]


@pytest.mark.parametrize("attempts", [1, 2])
def test_the_attempt_count_is_a_parameter(attempts):
    sender = Sender([True])
    assert sender.run(attempts=attempts) == "abandoned"
    assert sender.calls.count("send") == attempts
    assert sender.calls.count("wait") == attempts - 1


def test_the_enclave_wait_polls_the_terms_every_minute():
    assert constants.DP_TERMS_POLL_SECONDS == 60
    assert constants.DP_TERMS_POLL_SECONDS < DP_REFRESH_MARGIN_SECONDS
