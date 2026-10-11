"""utils.Storage.download and is_pinned: a CID the connected Kubo pins is read
from it; one it does not pin is pinned from the swarm (bounded) and read; the
gateway is the fallback when that fails or no Kubo is connected; a cached path
is reused and touched. is_pinned asks for recursive pins only."""
import logging
import os
import time

import pytest

from utils import PinAborted, Storage

CID = "bafkreiaonihv3zanotpc7lkt23ldjkwf2kppjq4muogrvpaagiawabjb2m"


class Cache:
    def __init__(self, held=()):
        self.held = set(held)

    def contains(self, cid):
        return cid in self.held

    def add(self, cid):
        self.held.add(cid)


class Recorder:
    """A Storage whose Kubo and gateway calls are recorded, not made."""

    def __init__(self, tmp_path, connected=True, pinned=False, gateway="https://gw.example", pin_error=None,
                 connect=False):
        self.storage = Storage.__new__(Storage)
        s = self.storage
        s.logger = logging.getLogger("test")
        s.target = str(tmp_path)
        s.cache = Cache()
        s.connected = connected
        s.gateway = gateway
        s.client_connect_url = "/ip4/127.0.0.1/tcp/5001/http"
        self.calls = []
        s.connect = lambda attempts=3: self.calls.append("connect") or connect
        s.is_pinned = lambda cid: self.calls.append("is_pinned") or pinned
        s._ensure_provider_path = lambda suspect=False: self.calls.append(f"provider_path(suspect={suspect})")

        def pin_add_watched(cid, timeout, watch, interval=10, stall=None):
            self.calls.append(f"pin_add_watched({timeout},{stall})")
            if pin_error:
                raise pin_error

        s.pin_add_watched = pin_add_watched
        s._download_from_local_ipfs = lambda cid, out: self.calls.append("local_get") or open(out, "wb").write(b"k")
        s.fetch_ipfs_content = lambda cid, output=None: self.calls.append("gateway_get") or open(output, "wb").write(b"g")
        s.add_path = lambda path: self.calls.append("add_path")
        s.pin_add = lambda cid, timeout=None: self.calls.append("pin_add")
        s.restart_ipfs_service = lambda: self.calls.append("restart_ipfs")


def test_a_pinned_cid_is_read_from_the_local_kubo(tmp_path):
    r = Recorder(tmp_path, pinned=True)
    r.storage.download(CID)
    assert r.calls == ["is_pinned", "local_get"]
    assert (tmp_path / CID).read_bytes() == b"k"
    assert r.storage.cache.contains(CID)


def test_an_unpinned_cid_is_pinned_from_the_swarm_within_the_bounds_then_read(tmp_path):
    r = Recorder(tmp_path)
    r.storage.download(CID)
    assert r.calls == ["is_pinned", "provider_path(suspect=False)",
                       f"pin_add_watched({Storage.DOWNLOAD_PIN_SECONDS},{Storage.DOWNLOAD_PIN_STALL_SECONDS})", "local_get"]
    assert r.storage.cache.contains(CID)


def test_a_failed_swarm_pin_redials_the_providers_and_falls_back_to_the_gateway(tmp_path):
    r = Recorder(tmp_path, pin_error=PinAborted("no block fetched for 120 s"))
    r.storage.download(CID)
    assert r.calls[:4] == ["is_pinned", "provider_path(suspect=False)",
                           f"pin_add_watched({Storage.DOWNLOAD_PIN_SECONDS},{Storage.DOWNLOAD_PIN_STALL_SECONDS})",
                           "provider_path(suspect=True)"]
    assert r.calls[4:] == ["gateway_get", "add_path", "pin_add"]
    assert (tmp_path / CID).read_bytes() == b"g"
    assert r.storage.cache.contains(CID)


def test_a_failed_swarm_pin_without_a_gateway_raises_without_a_daemon_restart(tmp_path):
    r = Recorder(tmp_path, gateway=None, pin_error=PinAborted("no block fetched for 120 s"))
    with pytest.raises(PinAborted):
        r.storage.download(CID)
    assert r.calls[-1] == "provider_path(suspect=True)"
    assert "restart_ipfs" not in r.calls and "gateway_get" not in r.calls
    assert not r.storage.cache.contains(CID)


def test_a_failed_local_read_restarts_the_local_daemon_then_falls_back_to_the_gateway(tmp_path):
    r = Recorder(tmp_path, pinned=True)

    def failing_get(cid, out):
        r.calls.append("local_get")
        raise Exception("ipfs get: connection reset")

    r.storage._download_from_local_ipfs = failing_get
    r.storage.download(CID)
    assert r.calls == ["is_pinned", "local_get", "restart_ipfs", "gateway_get", "add_path", "pin_add"]
    assert (tmp_path / CID).read_bytes() == b"g"


def test_a_failed_local_read_without_a_gateway_restarts_the_local_daemon_and_raises(tmp_path):
    r = Recorder(tmp_path, pinned=True, gateway=None)

    def failing_get(cid, out):
        r.calls.append("local_get")
        raise Exception("ipfs get: connection reset")

    r.storage._download_from_local_ipfs = failing_get
    with pytest.raises(Exception, match="connection reset"):
        r.storage.download(CID)
    assert r.calls == ["is_pinned", "local_get", "restart_ipfs"]


def test_without_a_kubo_the_gateway_is_used(tmp_path):
    r = Recorder(tmp_path, connected=False)
    r.storage.download(CID)
    assert r.calls == ["connect", "gateway_get"]
    assert r.storage.cache.contains(CID)


def test_without_a_kubo_or_a_gateway_the_download_raises(tmp_path):
    r = Recorder(tmp_path, connected=False, gateway=None)
    with pytest.raises(Exception, match="no IPFS connection and no gateway"):
        r.storage.download(CID)
    assert r.calls == ["connect"]


def test_a_cached_path_is_reused_and_touched(tmp_path):
    r = Recorder(tmp_path)
    path = tmp_path / CID
    path.write_bytes(b"c")
    os.utime(path, (1_000.0, 1_000.0))
    r.storage.cache.add(CID)
    r.storage.download(CID)
    assert r.calls == []
    assert time.time() - os.path.getmtime(path) < 60


def test_a_cached_cid_whose_path_is_gone_is_fetched_again(tmp_path):
    r = Recorder(tmp_path, pinned=True)
    r.storage.cache.add(CID)
    r.storage.download(CID)
    assert r.calls == ["is_pinned", "local_get"]


def pin_status_storage(answer):
    """A Storage whose pin/ls answers `answer` (a value) or raises it (an
    exception), recording the call's parameters."""
    s = Storage.__new__(Storage)
    s.logger = logging.getLogger("test")
    s.connected = True
    calls = []

    def api_call(command, params=None, timeout=None, **kwargs):
        calls.append((command, params, timeout))
        if isinstance(answer, Exception):
            raise answer
        return answer

    s._api_call = api_call
    return s, calls


def test_is_pinned_asks_for_recursive_pins_only_with_a_bound():
    s, calls = pin_status_storage({"Keys": {CID: {"Type": "recursive"}}})
    assert s.is_pinned(CID) is True
    assert calls == [("pin/ls", {"arg": CID, "type": "recursive"}, Storage.PIN_STATUS_SECONDS)]


def test_is_pinned_is_false_for_a_cid_kubo_does_not_pin_recursively():
    s, _ = pin_status_storage(Exception(f"IPFS API error: pin/ls 500 - path '{CID}' is not pinned"))
    assert s.is_pinned(CID) is False


def test_is_pinned_is_false_when_the_check_fails_and_the_client_stays_connected():
    s, _ = pin_status_storage(Exception("read timed out"))
    assert s.is_pinned(CID) is False
    assert s.connected is True


def test_is_pinned_is_false_without_a_kubo():
    s, calls = pin_status_storage({})
    s.connected = False
    assert s.is_pinned(CID) is False
    assert calls == []
