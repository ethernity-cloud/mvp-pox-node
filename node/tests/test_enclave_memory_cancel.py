"""Cancel reasons 10, EnclaveMemoryAboveRecord (D147), and 11,
StorageAboveRecord (the owner's decisions of 2026-11-23): the validators
cancel an offer that states more enclave memory, or more storage, than the
signed bench record in its DP request shows. capability.cancel_disposition
gives them 'memory' and 'storage'; neither sets the RecordMissing hold,
which is reason 9's alone; the figure the cancelled request stated is
refused under the record it carried (capability.OfferFigureBook, kept in
broker_cursor.json) and never stated again on that record; the next request
states the record's reading below it (capability.offer_enclave_memory,
offer_shape.bounded), and when the record carries none, or none below it,
the record is dropped and its integration test, the bench, runs again.
etny-node.py __dp_request_cancelled applies them."""
import importlib.util
import logging
from pathlib import Path

import pytest

import capability
import offer_shape

NODE = Path(__file__).resolve().parents[1]
CAS = "etny-nodenithy-testnet"
RECORD = {"gflops": 250, "cuv": 4, "t1": 1761724830, "sig_der_hex": "3065AB"}
KEY = "3065ab"


def test_the_record_states_an_enclave_size_only_as_a_listed_one():
    assert capability.ENCLAVE_SIZES_GIB == (1, 2, 4, 8, 16, 32, 64)
    assert capability.record_enclave_memory(None) is None
    assert capability.record_enclave_memory(RECORD) is None
    assert capability.record_enclave_memory({**RECORD, "encmem": "8"}) == 8
    assert capability.record_enclave_memory({**RECORD, "encmem": 3}) is None
    assert capability.record_enclave_memory({**RECORD, "encmem": "x"}) is None


@pytest.mark.parametrize("stated, record_figure, after", [
    (8, 4, (4, False)),
    (8, 2, (2, False)),
    (8, None, (None, True)),
    (8, 8, (None, True)),
    (8, 16, (None, True)),
    (480, 400, (400, False)),
])
def test_the_figure_after_a_reason_10_or_11_cancel(stated, record_figure, after):
    assert capability.figure_after_cancel(stated, record_figure) == after


@pytest.mark.parametrize("setting, record_size, refused, size", [
    (0, None, None, 0),
    (0, 8, None, 8),
    (4, 8, None, 4),
    (6, 8, None, 4),
    (0, 8, 8, 4),
    (0, 8, 2, 1),
    (0, 8, 1, 0),
    (64, 64, None, 64),
])
def test_the_offer_states_the_largest_listed_size_the_record_the_setting_and_a_refusal_admit(setting, record_size,
                                                                                            refused, size):
    assert capability.offer_enclave_memory(setting, record_size, refused, 255) == size


@pytest.mark.parametrize("book_of", [capability.enclave_memory_book, capability.storage_book])
def test_each_book_keeps_what_each_request_stated_and_the_refusals_across_a_restart(tmp_path, book_of):
    path = tmp_path / "broker_cursor.json"
    book = book_of(path)
    assert book.stated(7) == 0
    book.record_stated(7, 8)
    assert book.refused(KEY) is None and book.refused(None) is None
    book.refuse(KEY, 8)
    book.refuse(KEY, 16)
    book.refuse(None, 4)
    again = book_of(path)
    assert again.stated(7) == 8 and again.stated(8) == 0
    assert again.refused(KEY) == 8
    # The RecordMissing hold beside it in the same file is untouched.
    assert capability.RecordMissingHold(path).held is False


def test_the_two_books_are_kept_apart(tmp_path):
    path = tmp_path / "broker_cursor.json"
    capability.enclave_memory_book(path).refuse(KEY, 8)
    assert capability.storage_book(path).refused(KEY) is None


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Cache:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


def node_with(agent, tmp_path, record):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put(CAS, "QmA", 1761724900, record)
    node._EtnyPoXNode__record_store = store
    node._EtnyPoXNode__record_missing_hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__enclave_memory = capability.enclave_memory_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_storage = capability.storage_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__dprequest = 12
    node._EtnyPoXNode__dprequest_record_key = KEY
    node.dpreq_cache = Cache()
    return node, store


def test_a_reason_10_cancel_refuses_the_size_and_the_next_request_states_the_records(agent, tmp_path):
    node, store = node_with(agent, tmp_path, {**RECORD, "encmem": "4"})
    node._EtnyPoXNode__enclave_memory.record_stated(12, 8)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 10) is True
    book = node._EtnyPoXNode__enclave_memory
    assert book.refused(KEY) == 8
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert store.get(CAS) is not None
    assert capability.offer_enclave_memory(0, capability.record_enclave_memory(store.get(CAS)["record"]),
                                           book.refused(KEY), 255) == 4
    assert node.dpreq_cache.added == [12]


def test_a_reason_10_cancel_under_a_record_stating_no_size_runs_the_probe_again(agent, tmp_path):
    node, store = node_with(agent, tmp_path, RECORD)
    node._EtnyPoXNode__enclave_memory.record_stated(12, 2)
    with agent.integration_test_lock:
        agent.integration_test_passed[CAS] = 1761724900
    assert node._EtnyPoXNode__dp_request_cancelled(12, 10) is True
    assert node._EtnyPoXNode__enclave_memory.refused(KEY) == 2
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert store.get(CAS) is None
    assert CAS not in agent.integration_test_passed
    assert agent.integration_test_retry[CAS] == (0, 0.0)


def test_a_reason_11_cancel_refuses_the_storage_and_the_next_request_states_the_records(agent, tmp_path):
    node, store = node_with(agent, tmp_path, {**RECORD, "encmem": "8", "custorage": "400"})
    node._EtnyPoXNode__offer_storage.record_stated(12, 480)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 11) is True
    assert node._EtnyPoXNode__offer_storage.refused(KEY) == 480
    assert node._EtnyPoXNode__enclave_memory.refused(KEY) is None
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert store.get(CAS) is not None
    record = store.get(CAS)["record"]
    shape = offer_shape.bounded(offer_shape.Shape(60, 480), capability.record_enclave_memory(record),
                                capability.record_storage(record), node._EtnyPoXNode__offer_storage.refused(KEY))
    assert shape == offer_shape.Shape(8, 400)
    assert node.dpreq_cache.added == [12]


def test_a_reason_11_cancel_never_states_the_refused_storage_again_on_that_record(agent, tmp_path):
    # The request stated the record's own reading: the record is dropped and
    # the bench runs again, and the refused figure stays below the next.
    node, store = node_with(agent, tmp_path, {**RECORD, "custorage": "400"})
    node._EtnyPoXNode__offer_storage.record_stated(12, 400)
    with agent.integration_test_lock:
        agent.integration_test_passed[CAS] = 1761724900
    assert node._EtnyPoXNode__dp_request_cancelled(12, 11) is True
    assert store.get(CAS) is None and CAS not in agent.integration_test_passed
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert offer_shape.bounded(offer_shape.Shape(60, 480), None, 400, 400).storage_gib == 399
