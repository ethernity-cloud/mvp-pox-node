"""utils.Storage announces a pinned block over a live connection to the
configured swarm peers: provide() dials IPFS_SWARM (the bootnode) with
swarm/connect before routing/provide, at the first announce and at every
reprovide, so a client reading the block through the bootnode gets it at once
instead of after a DHT lookup (bloxberg testnet, 2026-10-25: ten nodes behind
NAT, results read 7 to 18 minutes after their orders completed). A dial that
fails does not stop the announce."""
import logging
import threading

from utils import Storage

CID = "bafkreibobnv5ikag6zekcsihz2nsckfjg32uacwd3o6shtu3u7rxi3ot2m"
BOOTNODE = "/dns4/ipfs.ethernity.cloud/tcp/4001/p2p/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5"


class Stream:
    """A routing/provide response: one event, then the end of the body."""

    def iter_lines(self):
        return iter([b'{"Type":4}'])

    def close(self):
        pass


class Inline:
    """An executor that runs the submitted call at once."""

    def submit(self, fn, *args):
        fn(*args)


def storage_with(fail_connect=False):
    storage = Storage.__new__(Storage)
    storage.logger = logging.getLogger("test")
    storage.ipfs_swarm = BOOTNODE
    storage._recent_pins = {}
    storage._recent_lock = threading.Lock()
    storage.announce_executor = Inline()
    calls = []

    def api(command, params=None, **kwargs):
        calls.append((command, dict(params or {})))
        if command == "swarm/connect" and fail_connect:
            raise Exception("dial backoff")
        return Stream()

    storage._api_call = api
    return storage, calls


def test_a_pinned_block_is_announced_after_connecting_to_the_swarm_peers():
    storage, calls = storage_with()
    storage.provide(CID)
    assert [c[0] for c in calls] == ["swarm/connect", "routing/provide"]
    assert calls[0][1]["arg"] == BOOTNODE
    assert calls[1][1]["arg"] == CID
    assert CID in storage._recent_pins


def test_a_dial_that_fails_does_not_stop_the_announce():
    storage, calls = storage_with(fail_connect=True)
    storage.provide(CID)
    assert [c[0] for c in calls] == ["swarm/connect", "routing/provide"]


def test_each_reprovide_connects_again():
    storage, calls = storage_with()
    storage.provide(CID)
    storage._recent_pins[CID]["last"] -= Storage.REPROVIDE_EVERY + 1
    storage.reprovide_recent()
    assert [c[0] for c in calls] == ["swarm/connect", "routing/provide"] * 2
