"""An order's ESR state staging writes the objects pinned here first and fetches
the rest from the network afterwards, and a CID no node served is not fetched
again for a while (bloxberg testnet orders 2026-2030: one such CID held every
order's staging for the whole 119 s fetch budget, so the objects pinned here
were staged after the securelock had read, and the commit relay of order 2026
started late enough to time out)."""
import utils
from utils import ESR_FETCH_DEFER_MAX_SECONDS, ESR_FETCH_DEFER_SECONDS, FetchDeferrals, staging_plan


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_pinned_cids_come_first_and_the_rest_are_fetched():
    local, remote = staging_plan({"bafy-c", "bafy-a", "bafy-b"}, lambda cid: cid != "bafy-b", FetchDeferrals(Clock()))
    assert local == ["bafy-a", "bafy-c"]
    assert remote == ["bafy-b"]


def test_a_cid_that_was_not_served_is_deferred_and_then_retried():
    clock = Clock()
    deferrals = FetchDeferrals(clock)
    assert deferrals.failed("bafy-dead") == ESR_FETCH_DEFER_SECONDS
    assert staging_plan({"bafy-dead", "bafy-new"}, lambda cid: False, deferrals) == ([], ["bafy-new"])
    clock.t += ESR_FETCH_DEFER_SECONDS
    assert staging_plan({"bafy-dead"}, lambda cid: False, deferrals) == ([], ["bafy-dead"])


def test_the_wait_doubles_per_failure_up_to_the_maximum():
    deferrals = FetchDeferrals(Clock())
    waits = [deferrals.failed("bafy-dead") for _ in range(8)]
    assert waits[:3] == [ESR_FETCH_DEFER_SECONDS, 2 * ESR_FETCH_DEFER_SECONDS, 4 * ESR_FETCH_DEFER_SECONDS]
    assert waits[-1] == ESR_FETCH_DEFER_MAX_SECONDS
    assert all(w <= ESR_FETCH_DEFER_MAX_SECONDS for w in waits)


def test_a_served_cid_is_fetched_at_once_and_its_next_failure_waits_the_first_wait_again():
    deferrals = FetchDeferrals(Clock())
    deferrals.failed("bafy-x")
    deferrals.failed("bafy-x")
    deferrals.served("bafy-x")
    assert not deferrals.deferred("bafy-x")
    assert deferrals.failed("bafy-x") == ESR_FETCH_DEFER_SECONDS


def test_a_pinned_cid_is_staged_even_while_its_fetch_is_deferred():
    deferrals = FetchDeferrals(Clock())
    deferrals.failed("bafy-a")
    assert staging_plan({"bafy-a"}, lambda cid: True, deferrals) == (["bafy-a"], [])


def test_the_deferral_clock_defaults_to_the_monotonic_clock():
    assert FetchDeferrals()._now is utils.time.monotonic
