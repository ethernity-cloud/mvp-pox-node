"""Cancel reason 10, EnclaveMemoryAboveRecord (D147; owner's decision of
2026-11-23): the validators cancel an offer that states more enclave memory
than its capability record shows. capability.cancel_disposition gives it
'memory'; it sets no RecordMissing hold, which is reason 9's alone; the
enclave memory the cancelled request stated is refused under the record it
carried (capability.EnclaveMemoryBook, kept in broker_cursor.json) and
never stated again on that record; the next request states the size the
record states below it (capability.offer_enclave_memory), and when the
record states none, or none below it, the record is dropped and its
integration test, the probe, runs again. etny-node.py
__dp_request_cancelled applies it."""
import importlib.util
import logging
from pathlib import Path

import pytest

import capability

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


@pytest.mark.parametrize("stated, record_size, after", [
    (8, 4, (4, False)),
    (8, 2, (2, False)),
    (8, None, (None, True)),
    (8, 8, (None, True)),
    (8, 16, (None, True)),
])
def test_the_size_after_a_reason_10_cancel(stated, record_size, after):
    assert capability.enclave_memory_after_cancel(stated, record_size) == after


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
    assert capability.offer_enclave_memory(setting, record_size, refused) == size


def test_the_book_keeps_what_each_request_stated_and_the_refusals_across_a_restart(tmp_path):
    path = tmp_path / "broker_cursor.json"
    book = capability.EnclaveMemoryBook(path)
    assert book.stated(7) == 0
    book.record_stated(7, 8)
    assert book.refused(KEY) is None and book.refused(None) is None
    book.refuse(KEY, 8)
    book.refuse(KEY, 16)
    book.refuse(None, 4)
    again = capability.EnclaveMemoryBook(path)
    assert again.stated(7) == 8 and again.stated(8) == 0
    assert again.refused(KEY) == 8
    # The RecordMissing hold beside it in the same file is untouched.
    assert capability.RecordMissingHold(path).held is False


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
    node._EtnyPoXNode__enclave_memory = capability.EnclaveMemoryBook(tmp_path / "broker_cursor.json")
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
                                           book.refused(KEY)) == 4
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
