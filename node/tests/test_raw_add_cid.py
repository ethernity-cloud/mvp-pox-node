"""utils.Storage: content is added in chunks of one block (RAW_BLOCK_MAX), so
content up to one block is stored under its raw CID, the CID the enclaves and
the runners compute; larger content is named by the root Kubo computes for it
(bloxberg testnet order 2011: a 1 MiB result named by its raw CID, which Kubo,
adding in 256 KiB chunks, stored under another). No daemon: the API is a
stand-in that records its calls."""
import logging

from utils import Storage

KUBO_ROOT = "bafybeicx2wulo3gq2y3ub4hvd4tn5vnnhcqqcyd3ry3oezskecvozamabe"


def storage_answering(hash_):
    storage = Storage.__new__(Storage)
    storage.logger = logging.getLogger("test")
    storage.connected = True
    storage.calls = []

    def api_call(command, params=None, files=None, **kwargs):
        storage.calls.append((command, params))
        return {"Hash": hash_}
    storage._api_call = api_call
    return storage


def test_content_up_to_one_block_is_named_locally():
    storage = storage_answering(KUBO_ROOT)
    content = b"x" * Storage.RAW_BLOCK_MAX
    assert storage.cid_of(content) == Storage.cidv1_raw(content)
    assert storage.calls == []


def test_content_above_one_block_is_named_by_kubo_without_storing_it():
    storage = storage_answering(KUBO_ROOT)
    assert storage.cid_of(b"x" * (Storage.RAW_BLOCK_MAX + 1)) == KUBO_ROOT
    command, params = storage.calls[0]
    assert command == "add"
    assert params["only-hash"] == "true" and params["pin"] == "false"
    assert params["chunker"] == "size-1048576" and params["raw-leaves"] == "true" and params["cid-version"] == "1"


def test_content_is_added_in_chunks_of_one_block():
    storage = storage_answering(KUBO_ROOT)
    storage.add_bytes_raw(b"payload")
    command, params = storage.calls[0]
    assert command == "add"
    assert params["chunker"] == "size-1048576" and params["pin"] == "true"


def test_a_result_above_one_block_is_published_under_the_cid_kubo_stores_it_under(tmp_path):
    storage = storage_answering(KUBO_ROOT)
    storage.target = str(tmp_path)
    queued = []
    storage.executor = type("Executor", (), {"submit": lambda self, fn: queued.append(fn)})()
    assert storage.pin_bytes_deferred(b"r" * (2 * Storage.RAW_BLOCK_MAX), name="result-1.txt") == KUBO_ROOT
    assert (tmp_path / "pending_pins" / KUBO_ROOT).exists()
    assert len(queued) == 1


def test_a_result_of_one_block_is_published_under_its_raw_cid(tmp_path):
    storage = storage_answering(KUBO_ROOT)
    storage.target = str(tmp_path)
    storage.executor = type("Executor", (), {"submit": lambda self, fn: None})()
    content = b"r" * 300000
    assert storage.pin_bytes_deferred(content, name="result-2.txt") == Storage.cidv1_raw(content)
    assert storage.calls == []
