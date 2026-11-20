"""utils.Storage.repo_size and the registered-image pin that reads it first:
Kubo answers repo/stat with 500 when leveldb removes a file while the read
walks the datastore (the bootnode mirror logged it six times on 2026-11-20);
the pin's starting read is asked up to three times before the attempt fails,
and a single read stays a single read."""
import importlib.util
import logging
from pathlib import Path

import pytest

import utils
from utils import Storage

NODE = Path(__file__).resolve().parents[1]
TREE = "QmfHxkZHuZhRZLzg7T535HCjBzS94U2qcwgbxoQTZU4m5M"
COMPOSE = "QmQeKtpTNwzXbrtJE7kBt1iUip3yVeGz5oCqAaZdyYeXBS"
RACE = ('IPFS API error: http://ipfs:5001/api/v0/repo/stat 500 - {"Message":"getting disk usage at /: '
        'lstat /data/ipfs/datastore/28594648.ldb: no such file or directory","Code":0,"Type":"error"}')


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def storage_answering(answers, sleeps):
    """A Storage whose repo/stat calls take the next of `answers` (an
    exception is raised, a number is the RepoSize) and whose sleeps are
    appended to `sleeps`; `calls` counts the repo/stat calls."""
    storage = Storage.__new__(Storage)
    storage.logger = logging.getLogger("test")
    storage.calls = 0
    queue = list(answers)

    def api_call(path, **kwargs):
        assert path == "repo/stat"
        storage.calls += 1
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"RepoSize": str(answer)}

    storage._api_call = api_call
    return storage


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(utils.time, "sleep", recorded.append)
    return recorded


def test_a_read_that_fails_twice_is_answered_by_the_third(sleeps):
    storage = storage_answering([Exception(RACE), Exception(RACE), 123456], sleeps)
    assert storage.repo_size(attempts=3) == 123456
    assert storage.calls == 3
    assert sleeps == [5, 5]


def test_three_failed_reads_raise_the_last_failure(sleeps):
    storage = storage_answering([Exception("first"), Exception("second"), Exception(RACE)], sleeps)
    with pytest.raises(Exception, match="repo/stat 500"):
        storage.repo_size(attempts=3)
    assert storage.calls == 3


def test_a_single_read_fails_at_once(sleeps):
    storage = storage_answering([Exception(RACE), 1], sleeps)
    with pytest.raises(Exception, match="repo/stat 500"):
        storage.repo_size()
    assert storage.calls == 1
    assert sleeps == []


def pin_node(agent, storage, pinned, recorded):
    """An EtnyPoXNode whose storage is `storage`, whose pins are appended to
    `pinned` and whose registered-image records to `recorded`, each also kept
    in esr_progress as the agent keeps it."""
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node.storage = storage
    node.esr_progress = {}
    storage.recursive_pins = lambda: set()
    storage.pin_add_watched = lambda cid, seconds, watch, stall=None: pinned.append(cid)

    def record(ipfs_hash, **fields):
        recorded.append(fields)
        node.esr_progress.setdefault("registered_images", {}).setdefault(ipfs_hash, {}).update(fields)

    node._EtnyPoXNode__record_registered_image = record
    return node


def test_the_pin_goes_on_past_a_failed_starting_read(agent, sleeps):
    pinned, recorded = [], []
    storage = storage_answering([Exception(RACE), 7 * 1024 ** 3], sleeps)
    node = pin_node(agent, storage, pinned, recorded)
    node._EtnyPoXNode__pin_registered_cids(TREE, {"compose": COMPOSE}, 600 * 1024 ** 2)
    assert pinned == [TREE, COMPOSE]
    assert recorded[-1] == {"pinned_cids": sorted([TREE, COMPOSE])}


def test_the_pin_fails_when_three_starting_reads_fail(agent, sleeps):
    pinned, recorded = [], []
    storage = storage_answering([Exception(RACE)] * 3, sleeps)
    node = pin_node(agent, storage, pinned, recorded)
    with pytest.raises(Exception, match="repo/stat 500"):
        node._EtnyPoXNode__pin_registered_cids(TREE, {"compose": COMPOSE}, 600 * 1024 ** 2)
    assert pinned == []
