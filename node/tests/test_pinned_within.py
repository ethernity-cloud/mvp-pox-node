"""utils.pinned_within: a state object committed on another node moments ago
is fetched from the network before an order's staging gives up on it, within
the time left of the staging's budget (bloxberg testnet order 2013: the object
order 2012 committed on another operator reached this node after the enclave
had read state). No daemon: the storage is a stand-in."""
from utils import pinned_within

CID = "bafkreiebemvaj5f37fd27hoqst3bdgvk7lkfng7hoszqyovxm5xgpixy2q"


class FakeStorage:
    def __init__(self, pinned=False, fetch_error=None):
        self.pinned = pinned
        self.fetch_error = fetch_error
        self.fetches = []

    def is_pinned(self, cid):
        return self.pinned

    def pin_add_watched(self, cid, timeout, watch, interval=10, stall=None):
        self.fetches.append((cid, timeout))
        if self.fetch_error:
            raise self.fetch_error
        self.pinned = True


def test_an_object_pinned_here_is_not_fetched():
    storage = FakeStorage(pinned=True)
    assert pinned_within(storage, CID, deadline=100, now=lambda: 0) is None
    assert storage.fetches == []


def test_an_object_not_pinned_here_is_fetched_within_the_time_left():
    storage = FakeStorage()
    assert pinned_within(storage, CID, deadline=100, now=lambda: 30) is None
    assert storage.fetches == [(CID, 70)]


def test_an_object_no_node_provides_is_reported_not_raised():
    storage = FakeStorage(fetch_error=Exception("pin/add: context deadline exceeded"))
    why = pinned_within(storage, CID, deadline=100, now=lambda: 0)
    assert "not fetched within 100 s" in why and "deadline exceeded" in why


def test_no_fetch_is_started_once_the_budget_is_spent():
    storage = FakeStorage()
    assert pinned_within(storage, CID, deadline=100, now=lambda: 99.5) == "not pinned here, and no time is left to fetch it"
    assert storage.fetches == []
