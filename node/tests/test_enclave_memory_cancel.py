"""Cancel reasons 10, EnclaveMemoryAboveRecord (D147), 11, StorageAboveRecord
(the owner's decisions of 2026-11-23), 12, EpcAboveRecord (D191), and 13,
GflopsAboveRecord (OD7; MATCHING-SYNC-PLAN.md A15, A16, B04, D05): the
validators cancel an offer that states more enclave memory, more storage or
more EPC than the bench record in its DP request shows, or more GFLOPS than
its trustedzone record. capability.cancel_disposition gives 10 to 12
'memory', 'storage' and 'epc' and 13 'retest'; none sets the RecordMissing
hold, which is reason 9's alone. For 10 to 12 the figure the cancelled
request stated is refused under the bench record it carried
(capability.OfferFigureBook, kept in broker_cursor.json) and never stated
again on that record; the next request states the record's reading below
it (capability.offer_enclave_memory, offer_shape.bounded,
capability.offer_epc_units), and when the record carries none below it the
bench record is dropped and the probe runs again, the trustedzone record
standing; a request without a bench record (an -unsafe-only offer's
readings) books nothing. For 13 the GFLOPS stated is refused under the
trustedzone record, which is dropped with its passed test. etny-node.py
__dp_request_cancelled applies them."""
import importlib.util
import logging
from pathlib import Path

import pytest

import bench_record
import capability
import offer_shape

NODE = Path(__file__).resolve().parents[1]
CAS = "etny-nodenithy-testnet"
RECORD = {"gflops": 250, "cuv": 4, "t1": 1761724830, "sig_der_hex": "3065AB"}
KEY = "3065ab"
BENCH = {"gflops": 118, "cuv": 4, "heap": "10G", "workers": 4, "encmem": 8, "custorage": 400, "epc": 1024,
         "t0": 1761724900, "t1": 1761724920, "block0": 1, "block1": 11, "validator": "0x" + "2a" * 20,
         "nonce": "0x" + "a8" * 32, "salt": "0x" + "82" * 32, "sig0": "0x" + "d5" * 65, "sig1": "0x" + "1e" * 65,
         "sig_der_hex": "3066CD", "size": 8}
BENCH_KEY = "3066cd"


def test_the_record_states_an_enclave_size_only_as_a_listed_one():
    assert capability.ENCLAVE_SIZES_GIB == (1, 2, 4, 8, 16, 32, 64)
    assert capability.record_enclave_memory(None) is None
    assert capability.record_enclave_memory(RECORD) is None
    assert capability.record_enclave_memory({**RECORD, "encmem": "8"}) == 8
    assert capability.record_enclave_memory({**RECORD, "encmem": 3}) is None
    assert capability.record_enclave_memory({**RECORD, "encmem": "x"}) is None
    assert capability.record_enclave_memory(BENCH) == 8 and capability.record_storage(BENCH) == 400


@pytest.mark.parametrize("stated, record_figure, after", [
    (8, 4, (4, False)),
    (8, 2, (2, False)),
    (8, None, (None, True)),
    (8, 8, (None, True)),
    (8, 16, (None, True)),
    (480, 400, (400, False)),
])
def test_the_figure_after_a_reason_10_11_or_12_cancel(stated, record_figure, after):
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


@pytest.mark.parametrize("book_of", [capability.enclave_memory_book, capability.storage_book, capability.epc_book,
                                     capability.gflops_book])
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


def test_the_books_are_kept_apart(tmp_path):
    path = tmp_path / "broker_cursor.json"
    capability.enclave_memory_book(path).refuse(KEY, 8)
    assert capability.storage_book(path).refused(KEY) is None
    assert capability.epc_book(path).refused(KEY) is None
    assert capability.gflops_book(path).refused(KEY) is None


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


def node_with(agent, tmp_path, record, bench=None):
    """A node whose standing DP request 12 carries `record` and the bench
    record `bench` (None: none), both held."""
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    store = capability.RecordStore(tmp_path / "capability.json")
    store.put(CAS, "QmA", 1761724900, record)
    benches = bench_record.BenchStore(tmp_path / "bench.json")
    if bench is not None:
        benches.put(bench, "QmProbe", bench["t1"])
    node._EtnyPoXNode__record_store = store
    node._EtnyPoXNode__bench_store = benches
    node._EtnyPoXNode__record_missing_hold = capability.RecordMissingHold(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__enclave_memory = capability.enclave_memory_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_storage = capability.storage_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_epc = capability.epc_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_gflops = capability.gflops_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__dprequest = 12
    node._EtnyPoXNode__dprequest_record_key = bench_record.offer_key(
        KEY, bench_record.bench_key(bench) if bench is not None else None)
    node.dpreq_cache = Cache()
    return node, store, benches


def test_a_reason_10_cancel_refuses_the_size_and_the_next_request_states_the_bench_records(agent, tmp_path):
    node, store, benches = node_with(agent, tmp_path, RECORD, {**BENCH, "encmem": 4})
    node._EtnyPoXNode__enclave_memory.record_stated(12, 8)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 10) is True
    book = node._EtnyPoXNode__enclave_memory
    assert book.refused(BENCH_KEY) == 8 and book.refused(KEY) is None
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert store.get(CAS) is not None and benches.entry is not None
    assert capability.offer_enclave_memory(0, capability.record_enclave_memory(benches.entry["record"]),
                                           book.refused(BENCH_KEY), 255) == 4
    assert node.dpreq_cache.added == [12]


def test_a_reason_10_cancel_of_the_bench_records_own_size_runs_the_probe_again(agent, tmp_path):
    node, store, benches = node_with(agent, tmp_path, RECORD, BENCH)
    node._EtnyPoXNode__enclave_memory.record_stated(12, 8)
    with agent.integration_test_lock:
        agent.integration_test_passed[CAS] = 1761724900
    assert node._EtnyPoXNode__dp_request_cancelled(12, 10) is True
    assert node._EtnyPoXNode__enclave_memory.refused(BENCH_KEY) == 8
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert benches.entry is None and bench_record.BenchStore(tmp_path / "bench.json").entry is None
    # The trustedzone record and its passed test stand.
    assert store.get(CAS) is not None and CAS in agent.integration_test_passed
    agent.integration_test_passed.pop(CAS, None)


def test_a_reason_11_cancel_refuses_the_storage_and_the_next_request_states_the_bench_records(agent, tmp_path):
    node, store, benches = node_with(agent, tmp_path, RECORD, BENCH)
    node._EtnyPoXNode__offer_storage.record_stated(12, 480)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 11) is True
    assert node._EtnyPoXNode__offer_storage.refused(BENCH_KEY) == 480
    assert node._EtnyPoXNode__enclave_memory.refused(BENCH_KEY) is None
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert benches.entry is not None
    bench = benches.entry["record"]
    shape = offer_shape.bounded(offer_shape.Shape(60, 480), capability.record_enclave_memory(bench),
                                capability.record_storage(bench), node._EtnyPoXNode__offer_storage.refused(BENCH_KEY))
    assert shape == offer_shape.Shape(8, 400)
    assert node.dpreq_cache.added == [12]


def test_a_reason_11_cancel_never_states_the_refused_storage_again_on_that_record(agent, tmp_path):
    # The request stated the bench record's own reading: the bench record is
    # dropped and the probe runs again, and the refused figure stays below
    # the next.
    node, store, benches = node_with(agent, tmp_path, RECORD, BENCH)
    node._EtnyPoXNode__offer_storage.record_stated(12, 400)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 11) is True
    assert benches.entry is None and store.get(CAS) is not None
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert offer_shape.bounded(offer_shape.Shape(60, 480), None, 400, 400).storage_gib == 399


@pytest.mark.parametrize("proven, setting, refused, units", [
    (None, 0, None, 0),
    (16, 0, None, 16),
    (16, 512, None, 8),
    (16, 63, None, 0),
    (16, 0, 16, 15),
    (16, 0, 1, 0),
    (16, 2048, 10, 9),
    (70000, 0, None, 65535),
])
def test_the_epc_units_the_bench_record_the_setting_and_a_reason_12_refusal_admit(proven, setting, refused, units):
    assert capability.offer_epc_units(proven, setting, refused) == units


def test_the_record_proves_floor_epc_over_64_units():
    assert capability.EPC_UNIT_MIB == 64
    assert capability.record_epc_units(None) is None
    assert capability.record_epc_units({"epc": "1024"}) == 16
    assert capability.record_epc_units({"epc": "1087"}) == 16
    assert capability.record_epc_units({"epc": "63"}) == 0
    assert capability.record_epc_units({"epc": str(2**31 - 1)}) == 65535
    assert capability.record_epc_units(RECORD) is None


def test_a_reason_12_cancel_refuses_the_units_and_the_next_request_states_the_bench_records(agent, tmp_path):
    node, store, benches = node_with(agent, tmp_path, RECORD, BENCH)
    node._EtnyPoXNode__offer_epc.record_stated(12, 20)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 12) is True
    book = node._EtnyPoXNode__offer_epc
    assert book.refused(BENCH_KEY) == 20
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert benches.entry is not None
    proven = capability.record_epc_units(benches.entry["record"])
    assert capability.offer_epc_units(proven, 0, book.refused(BENCH_KEY)) == 16
    assert node._EtnyPoXNode__enclave_memory.refused(BENCH_KEY) is None
    assert node.dpreq_cache.added == [12]


def test_a_reason_12_cancel_never_states_the_refused_units_again_on_that_record(agent, tmp_path):
    node, store, benches = node_with(agent, tmp_path, RECORD, BENCH)
    node._EtnyPoXNode__offer_epc.record_stated(12, 16)
    assert node._EtnyPoXNode__dp_request_cancelled(12, 12) is True
    assert node._EtnyPoXNode__offer_epc.refused(BENCH_KEY) == 16
    assert benches.entry is None and store.get(CAS) is not None
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert capability.offer_epc_units(16, 0, 16) == 15


@pytest.mark.parametrize("reason", [10, 11, 12])
def test_a_cancel_of_a_request_without_a_bench_record_books_nothing(agent, tmp_path, reason):
    """D05 (OD9 (iii) A): an -unsafe-only offer states its readings, which
    the validators do not judge on 10 to 12; were such a cancel read, no
    figure is refused and nothing is dropped."""
    node, store, benches = node_with(agent, tmp_path, RECORD)
    node._EtnyPoXNode__dprequest_record_key = None
    for book in ("enclave_memory", "offer_storage", "offer_epc"):
        getattr(node, f"_EtnyPoXNode__{book}").record_stated(12, 8)
    assert node._EtnyPoXNode__dp_request_cancelled(12, reason) is True
    for book in ("enclave_memory", "offer_storage", "offer_epc"):
        assert getattr(node, f"_EtnyPoXNode__{book}").refused(None) is None
    assert store.get(CAS) is not None
    assert node._EtnyPoXNode__record_missing_hold.held is False


def test_a_reason_13_cancel_refuses_the_gflops_and_runs_the_integration_test_again(agent, tmp_path):
    node, store, benches = node_with(agent, tmp_path, RECORD, BENCH)
    node._EtnyPoXNode__offer_gflops.record_stated(12, 250)
    with agent.integration_test_lock:
        agent.integration_test_passed[CAS] = 1761724900
    assert node._EtnyPoXNode__dp_request_cancelled(12, 13) is True
    assert node._EtnyPoXNode__offer_gflops.refused(KEY) == 250
    assert node._EtnyPoXNode__offer_gflops.refused(BENCH_KEY) is None
    assert capability.offer_gflops(250, node._EtnyPoXNode__offer_gflops.refused(KEY)) == 249
    assert store.get(CAS) is None
    assert CAS not in agent.integration_test_passed
    assert agent.integration_test_retry[CAS] == (0, 0.0)
    assert node._EtnyPoXNode__record_missing_hold.held is False
    assert node.dpreq_cache.added == [12]


def test_the_offer_states_the_records_gflops_below_a_reason_13_refusal():
    assert capability.offer_gflops(250, None) == 250
    assert capability.offer_gflops(250, 250) == 249
    assert capability.offer_gflops(250, 300) == 250
    assert capability.offer_gflops(250, 0) == 0
    assert capability.offer_gflops(0, None) == 0
