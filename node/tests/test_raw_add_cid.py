"""utils.unixfs_cid and Storage: content is named by the CID Kubo's `add
--cid-version=1 --raw-leaves` gives it with the default 256 KiB chunker, which
Storage.add_bytes_raw passes, computed from the bytes (bloxberg testnet order
2011: a 1 MiB result was published under its raw CID, which Kubo, storing it
in four blocks, did not use). No daemon: the API is a stand-in that records its
calls."""
import logging

from utils import BLOCK_SIZE, Storage, unixfs_cid


def pattern(n):
    """The first n bytes of the byte values 0..250 repeated: the files Kubo
    0.36.0 and 0.42.0 named the CIDs below, through `add --cid-version=1
    --raw-leaves`, with the default chunker and with the chunker passed."""
    unit = bytes(range(251))
    return (unit * (n // len(unit) + 1))[:n]


def storage_recording():
    storage = Storage.__new__(Storage)
    storage.logger = logging.getLogger("test")
    storage.connected = True
    storage.calls = []

    def api_call(command, params=None, files=None, **kwargs):
        storage.calls.append((command, params))
        return {"Hash": "bafkreiaaaa"}
    storage._api_call = api_call
    return storage


def test_content_up_to_one_block_is_named_by_its_raw_cid():
    content = pattern(BLOCK_SIZE)
    assert BLOCK_SIZE == 262144
    assert unixfs_cid(content) == Storage.cidv1_raw(content) == "bafkreibruh455iawsviqslif5c7uurdcfdemh22mtnytyzvnzn75kpejxy"


def test_content_above_one_block_is_named_by_its_unixfs_root():
    assert unixfs_cid(pattern(BLOCK_SIZE + 1)) == "bafybeiexg2oqkfnj56l7fcmawswqbijt5shq4b5rg6a546uwpkqqzwjioi"
    assert unixfs_cid(pattern(648000)) == "bafybeicnawjqvxvtindqftaj5acjkqb6lh5sove2uqkdrzjkwooypl56zu"
    assert unixfs_cid(pattern(1048576)) == "bafybeiedpcapwld4tkgtzwahfofgn4wex5ryysf4se6hwpmlrsh4ntnrau"
    assert unixfs_cid(pattern(2621440)) == "bafybeichq3thyi62tokqze6l4467yvja3nw7w34y7w52vn6h7lyo7qgxoy"


def test_more_blocks_than_a_node_links_make_a_second_level():
    """184330 bytes in 1 KiB blocks are 181 leaves: 174 under one node and 7
    under another, both under the root (Kubo, chunker=size-1024)."""
    assert unixfs_cid(pattern(184330), block_size=1024) == "bafybeids2yxkrcqqq6cpa7lgs6hv5xpsfn2bctwvljcrq73hhhzhszxkvy"


def test_content_is_added_with_kubos_default_chunk_size():
    storage = storage_recording()
    storage.add_bytes_raw(b"payload")
    command, params = storage.calls[0]
    assert command == "add"
    assert params["chunker"] == "size-262144" and params["raw-leaves"] == "true" and params["cid-version"] == "1"
    assert params["pin"] == "true"


def test_a_result_is_published_under_the_cid_kubo_stores_it_under(tmp_path):
    storage = storage_recording()
    storage.target = str(tmp_path)
    queued = []
    storage.executor = type("Executor", (), {"submit": lambda self, fn: queued.append(fn)})()
    content = pattern(1048576)
    assert storage.pin_bytes_deferred(content, name="result-1.txt") == "bafybeiedpcapwld4tkgtzwahfofgn4wex5ryysf4se6hwpmlrsh4ntnrau"
    assert (tmp_path / "pending_pins" / "bafybeiedpcapwld4tkgtzwahfofgn4wex5ryysf4se6hwpmlrsh4ntnrau").exists()
    assert storage.calls == []
    assert len(queued) == 1
