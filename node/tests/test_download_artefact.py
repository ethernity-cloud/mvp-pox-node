"""Storage.download_artefact: an order's challenge, code or input is fetched
from the gateway first, ARTEFACT_GATEWAY_ATTEMPTS times, and from the swarm
(download) only when the gateway does not serve it; a cached artefact is not
fetched at all. The Storage is built without its network setup and its fetch
methods are stand-ins."""
import logging
import os

from utils import Storage


class FakeCache:
    def __init__(self, cids=()):
        self.cids = set(cids)

    def contains(self, cid):
        return cid in self.cids

    def add(self, cid):
        self.cids.add(cid)


def storage(tmp_path, gateway="https://ipfs.ethernity.cloud", cached=()):
    s = object.__new__(Storage)
    s.target = str(tmp_path)
    s.cache = FakeCache(cached)
    s.gateway = gateway
    s.logger = logging.getLogger("test")
    s.ARTEFACT_GATEWAY_DELAY_SECONDS = 0
    s.calls = []
    s.fetch_ipfs_content = lambda cid, output=None: s.calls.append(("gateway", cid))
    s._post_download_processing = lambda cid, out_path: s.calls.append(("pinned", cid))
    s.download = lambda cid: s.calls.append(("swarm", cid))
    return s


def test_the_gateway_serves_the_artefact_and_it_is_pinned_locally(tmp_path):
    s = storage(tmp_path)
    s.download_artefact("bafkreichallenge")
    assert s.calls == [("gateway", "bafkreichallenge"), ("pinned", "bafkreichallenge")]


def test_the_swarm_is_the_fallback_after_the_gateway_attempts(tmp_path):
    s = storage(tmp_path)

    def refuse(cid, output=None):
        s.calls.append(("gateway", cid))
        raise RuntimeError("404")

    s.fetch_ipfs_content = refuse
    s.download_artefact("bafkreicode")
    assert s.calls == [("gateway", "bafkreicode")] * Storage.ARTEFACT_GATEWAY_ATTEMPTS + [("swarm", "bafkreicode")]


def test_a_cached_artefact_on_disk_is_not_fetched(tmp_path):
    s = storage(tmp_path, cached=["bafkreiinput"])
    (tmp_path / "bafkreiinput").write_text("data")
    s.download_artefact("bafkreiinput")
    assert s.calls == []


def test_without_a_gateway_the_swarm_path_is_taken_at_once(tmp_path):
    s = storage(tmp_path, gateway=None)
    s.download_artefact("bafkreichallenge")
    assert s.calls == [("swarm", "bafkreichallenge")]
