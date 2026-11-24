"""etny-node.py on a network with PoX 2.0 matching (MATCHING-DESIGN.md
section 7) and without it. __bind_matching binds a MatchingNode only when
the pricing names a matching contract and that contract's reader() names
its PoxMatchReader; a pricing with no matching(), the zero address and a
reader() of zero leave the network as today. On a matching network the
heartbeat goes to PoxMatching alone; an
unpinned request served by claims is claimed and never placed, one that is
not served by claims is placed as today (roll-out step 2), and a PoX 1.0
network keeps the rotation; the offer states its enclave memory and EPC
units above its GFLOPS from the bench record its DP request carries beside
the trustedzone record (an -unsafe-only offer its readings), and a request
of a trustedzone record without a bench record is held where the pricing
names a PoxMatching; the refresh keeps the period's tip while a claim stands; no
integration test starts while a claim can be approved, and an approved
order ends a running one; an order's objects are fetched for 10 minutes
after its approval, the validators' nodes read again between attempts;
the farm step names no payout and waits for the rewards wallet's approval."""
import importlib.util
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from web3.exceptions import ContractLogicError

import bench_record
import capability
import matching
import matching_node
import test_do_request_age_intake as intake
import utils

NODE = Path(__file__).resolve().parents[1]
ADDRESS = intake.NODE_ADDRESS
MATCHING = "0x" + "5a" * 20
READER = "0x" + "5b" * 20


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubMatching:
    """A MatchingNode as etny-node.py drives it: serve answers `served`; the
    ticks are recorded."""

    def __init__(self, served='claimed', approvable=False, standing=False, period=None, fetch_seconds=600,
                 banned=False, farm_ready=(True, None)):
        self.farm_ready = farm_ready
        self.served = served
        self.approvable = approvable
        self.standing = standing
        self.period = period
        self.calls = []
        self.fetch = fetch_seconds
        self.banned = banned
        self.chain = SimpleNamespace(request_image=lambda request_id: (b"\x6f" * 31, 0))

    def serve(self, request_id, terms, offer):
        self.calls.append(('serve', request_id))
        return self.served

    def follow(self):
        self.calls.append(('follow',))

    def heartbeat_tick(self, offer, holding, refresh):
        self.calls.append(('heartbeat', holding))

    def hold_tick(self, offer, reasons):
        self.calls.append(('hold', tuple(reasons)))

    def hold_reasons(self, *args):
        return []

    def correction_tick(self):
        self.calls.append(('correction',))

    def skip_tick(self):
        self.calls.append(('skip',))

    def wide_tick(self):
        self.calls.append(('wide',))

    def claim_approvable(self):
        return self.approvable

    def standing_claim_or_mandate(self):
        return self.standing

    def own_period(self):
        return self.period

    def is_banned(self):
        return self.banned

    def params(self):
        return SimpleNamespace(fetchTrySeconds=self.fetch)

    def payout_tick(self, setting):
        self.calls.append(('payout', setting))

    def farm_join_ready(self, farm):
        self.calls.append(('farm', farm))
        return self.farm_ready


def served_run(agent, monkeypatch, tmp_path, stub, chain, passes=None):
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__matching", stub)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__matching_offer",
                        lambda self: matching_node.Offer(intake.DP_REQUEST, True, False, 1, False))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__matching_terms", lambda self, request_id: {})
    return intake.run(agent, monkeypatch, tmp_path, chain, passes=passes)


def test_a_request_served_by_claims_is_claimed_and_never_placed(agent, monkeypatch, tmp_path):
    chain = intake.Chain([intake.NOW - intake.HOUR])
    stub = StubMatching('claimed')
    node = served_run(agent, monkeypatch, tmp_path, stub, chain, passes=3)
    assert ('serve', 0) in stub.calls
    assert intake.placed(chain) == []
    assert node.doreq_cache.get(0) == 0
    # The heartbeat went to PoxMatching: the matching tick ran, ECHeartBeat
    # was never called.
    assert ('follow',) in stub.calls and not any(e.startswith("logCall") for e in chain.events)


def test_a_request_not_served_by_claims_is_placed_as_today(agent, monkeypatch, tmp_path):
    chain = intake.Chain([intake.NOW - intake.HOUR])
    stub = StubMatching('placement')
    served_run(agent, monkeypatch, tmp_path, stub, chain)
    assert stub.calls.count(('serve', 0)) == 1
    assert intake.placed(chain) == [0]


def test_a_request_that_waits_is_neither_placed_nor_cached(agent, monkeypatch, tmp_path):
    chain = intake.Chain([intake.NOW - intake.HOUR])
    stub = StubMatching('wait')
    node = served_run(agent, monkeypatch, tmp_path, stub, chain, passes=3)
    assert stub.calls.count(('serve', 0)) >= 2
    assert intake.placed(chain) == []
    assert node.doreq_cache.get(0) is None


def test_a_pinned_request_is_placed_as_today_unless_the_node_is_banned(agent, monkeypatch, tmp_path):
    chain = intake.Chain([intake.NOW - intake.HOUR], pinned=(0,))
    stub = StubMatching('claimed')
    served_run(agent, monkeypatch, tmp_path, stub, chain)
    assert ('serve', 0) not in stub.calls
    assert intake.placed(chain) == [0]
    banned = tmp_path / "banned"
    banned.mkdir()
    chain = intake.Chain([intake.NOW - intake.HOUR], pinned=(0,))
    served_run(agent, monkeypatch, banned, StubMatching('claimed', banned=True), chain, passes=3)
    assert intake.placed(chain) == []


def test_without_matching_the_rotation_places_as_today(agent, monkeypatch, tmp_path):
    chain = intake.Chain([intake.NOW - intake.HOUR])
    assert agent.EtnyPoXNode._EtnyPoXNode__matching is None
    intake.run(agent, monkeypatch, tmp_path, chain)
    assert intake.placed(chain) == [0]
    assert "getNodesCount" in chain.events


# ------------------------------------------------------------ the binding

class View:
    """A contract at `address` whose view `name` answers `answer`, or raises
    it when it is an exception."""

    def __init__(self, name, answer, address=None):
        self.name = name
        self.answer = answer
        self.address = address

    @property
    def functions(self):
        answer = self.answer

        def call():
            if isinstance(answer, Exception):
                raise answer
            return answer
        return SimpleNamespace(**{self.name: lambda: SimpleNamespace(call=call)})


def bound(agent, monkeypatch, pointer, reader=READER):
    """The MatchingNode __bind_matching binds when the pricing's matching()
    answers `pointer` and PoxMatching's reader() answers `reader`."""
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")

    def contract(address, abi):
        if abi is matching_node.POINTER_ABI:
            return View("matching", pointer)
        if address == MATCHING:
            return View("reader", reader, address)
        return SimpleNamespace(address=address, functions=None)
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=SimpleNamespace(contract=contract), to_checksum_address=lambda a: a)
    node._EtnyPoXNode__pricing = SimpleNamespace(address="0x" + "20" * 20)
    node._EtnyPoXNode__etny = SimpleNamespace(address="0x" + "21" * 20)
    node._EtnyPoXNode__address = ADDRESS
    node._EtnyPoXNode__network = "polygon_amoy"
    node._EtnyPoXNode__network_config = intake.polygon_mainnet(monkeypatch)
    node._EtnyPoXNode__pox_epc_forms = True
    monkeypatch.setattr(agent, "retry", lambda fn, *args, **kwargs: (True, fn(*args)))
    node._EtnyPoXNode__bind_matching()
    return node._EtnyPoXNode__matching


def test_a_pricing_without_matching_leaves_the_network_as_today(agent, monkeypatch):
    assert bound(agent, monkeypatch, "0x" + "00" * 20) is None
    assert bound(agent, monkeypatch, ContractLogicError("execution reverted")) is None


def test_a_matching_contract_whose_reader_is_not_written_is_logged_and_unbound(agent, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    assert bound(agent, monkeypatch, MATCHING, reader="0x" + "00" * 20) is None
    assert any("whose reader() names no reader" in m for m in caplog.messages)


def test_the_pointer_and_the_reader_on_chain_bind_matching(agent, monkeypatch):
    m = bound(agent, monkeypatch, MATCHING)
    assert isinstance(m, matching_node.MatchingNode)
    assert m.chain.matching.address == MATCHING and m.chain.reader.address == READER
    # The PoX's VERSION() names the EPC forms: doStamp is read through the
    # PoX's own handle, the union ABI's three outputs.
    assert m.chain.epc_forms is True and m.chain.stamps is m.chain.pox
    assert "match_reader_address" not in agent.config.NetworkConfig.__dataclass_fields__
    assert m.settings.claim_spread_nodes == 8 and m.settings.claim_stand_limit == 2


# ------------------------------------------------------------- the offer

GIB = 2**30


def params_node(agent, monkeypatch, tmp_path, stub, record, memory=(64 * GIB, 60 * GIB),
                storage=(200 * GIB, 150 * GIB), bench=None, epc_mib=0):
    """A node whose host has `memory` (total, MemAvailable) and `storage`
    (total, free) bytes and the free figures of today's offer, 16 GB and
    100 GB, advertising `record` and holding the bench record `bench`; its
    pricing names a PoxMatching when `stub` is a MatchingNode stand-in."""
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    network = intake.polygon_mainnet(monkeypatch)
    node._EtnyPoXNode__network_config = network.__class__(**{**network.__dict__, "enclave_memory_gib": 0,
                                                             "epc_mib": epc_mib})
    node._EtnyPoXNode__matching = stub
    node._EtnyPoXNode__matching_network = stub is not None
    node._EtnyPoXNode__bench_store = bench_record.BenchStore(tmp_path / "bench.json")
    if bench is not None:
        node._EtnyPoXNode__bench_store.put(bench, "QmProbe", bench["t1"])
    node._EtnyPoXNode__no_bench_logged = False
    node._EtnyPoXNode__enclave_memory = capability.enclave_memory_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_storage = capability.storage_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_epc = capability.epc_book(tmp_path / "broker_cursor.json")
    node._EtnyPoXNode__offer_gflops = capability.gflops_book(tmp_path / "broker_cursor.json")
    node.cache_config = SimpleNamespace(base_path=tmp_path)
    node.storage = SimpleNamespace(peer_id=lambda: "peer")
    node._EtnyPoXNode__price = 1
    node._EtnyPoXNode__bandwidth = 100
    node._EtnyPoXNode__duration = 24
    node._EtnyPoXNode__uuid = "uuid"
    node._EtnyPoXNode__node_geo = "geo"
    node._EtnyPoXNode__pox_is_20 = True
    node._EtnyPoXNode__pox_epc_forms = True
    node._EtnyPoXNode__broker = None
    monkeypatch.setattr(agent, "HardwareInfoProvider", SimpleNamespace(
        get_number_of_cpus=lambda: 8, get_free_memory=lambda: 16, get_free_storage=lambda: 100,
        get_total_memory_bytes=lambda: memory[0], get_available_memory_bytes=lambda: memory[1],
        get_storage_bytes=lambda path: storage))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__advertised_record", lambda self: ("img", record))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__runnable_trustedzones", lambda self: {"img"})
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__current_tip", lambda self, gflops: 5)
    monkeypatch.setattr(capability, "metadata3", lambda geo, record: "m3")
    return node


RECORD = {"gflops": 250, "cuv": 4, "t1": 1761724830, "sig_der_hex": "3065AB", "heap": "2G", "workers": 4}
NOW = int(time.time())
# A bench record of a 32 GiB probe that read 120 GiB of storage and proved
# 2,048 MiB of EPC (32 units) over a 20 s window: its region's work, 32 x
# 8,192 x 4 x 8,388,608 is at least 0.1 x 250 GFLOPS over the window.
BENCH = {"gflops": 118, "cuv": 4, "heap": "34G", "workers": 4, "encmem": 32, "custorage": 120, "epc": 2048,
         "t0": NOW - 20, "t1": NOW, "block0": 1, "block1": 11, "validator": "0x" + "2a" * 20,
         "nonce": "0x" + "a8" * 32, "salt": "0x" + "82" * 32, "sig0": "0x" + "d5" * 65, "sig1": "0x" + "1e" * 65,
         "sig_der_hex": "3066CD", "size": 32}
BENCH_KEY = "3066cd"


def test_the_offer_states_the_largest_bench_size_its_record_passed_above_its_gflops(agent, monkeypatch, tmp_path):
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench={**BENCH, "encmem": 8})
    name, arguments = node._EtnyPoXNode__dp_request_params()
    assert name == utils.DP_ADD_V2_EPC and arguments[5] == 250 | (8 << 16) | (32 << 24)
    node._EtnyPoXNode__enclave_memory.refuse(BENCH_KEY, 8)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[5] == 250 | (4 << 16) | (32 << 24)


def test_the_offer_states_the_epc_units_the_bench_record_proves_lowered_by_the_setting_and_a_refusal(
        agent, monkeypatch, tmp_path):
    """MATCHING-SYNC-PLAN.md A03; OD21 A: floor(epc / 64) of the bench
    record in bits 24 to 39, at most the setting's units, below a reason-12
    refusal under the record."""
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[5] >> 24 == 32 and node._EtnyPoXNode__offer_epc_sent == 32
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH, epc_mib=640)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[5] >> 24 == 10
    node._EtnyPoXNode__offer_epc.refuse(BENCH_KEY, 8)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[5] >> 24 == 7


def test_the_dp_request_carries_the_bench_records_keys_after_the_cu_keys(agent, monkeypatch, tmp_path):
    """MATCHING-SYNC-PLAN.md B04; OD1 A: metadata4 and metadata3 each carry
    the bench record's pb keys after the trustedzone record's."""
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[10].endswith(bench_record.metadata4_keys(BENCH))
    assert ";cuworkers=4;pbgflops=118;pbcuv=4;pbsig=3066cd;" in arguments[10]
    assert arguments[9] == "m3" + bench_record.metadata3_keys(BENCH)
    assert ";encmem=" not in arguments[10] and ";custorage=" not in arguments[10]


@pytest.mark.parametrize("epc_forms, wanted", [(True, utils.DP_ADD_V2_EPC), (False, utils.DP_ADD_V2)])
def test_the_dp_add_is_bound_by_the_signature_the_poxs_version_names(agent, monkeypatch, tmp_path, epc_forms,
                                                                       wanted):
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench={**BENCH, "encmem": 8, "epc": 0})
    node._EtnyPoXNode__pox_epc_forms = epc_forms
    bound_to = []
    node._EtnyPoXNode__etny = SimpleNamespace(
        get_function_by_signature=lambda signature: (lambda *args: bound_to.append(signature) or signature))
    monkeypatch.setattr(bench_record, "share_holds", lambda bench, cugflops: True)
    fn, arguments = node._EtnyPoXNode__dp_request_function()
    assert fn == wanted and bound_to == [wanted]
    assert arguments[5] == 250 | (8 << 16)


def answering(value):
    """A contract function whose call() answers `value`, or raises it."""
    def call():
        if isinstance(value, Exception):
            raise value
        return value
    return lambda: SimpleNamespace(call=call)


@pytest.mark.parametrize("version, epc_forms", [
    ("2.0", True), ("1.2", False), (ContractLogicError("execution reverted"), False)])
def test_the_poxs_version_names_the_form_of_its_adds(agent, monkeypatch, version, epc_forms):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__replication_only = False
    node._EtnyPoXNode__w3 = None
    node._EtnyPoXNode__etny = SimpleNamespace(address="0x" + "21" * 20, functions=SimpleNamespace(
        pricingVersion=answering(2), VERSION=answering(version)))
    monkeypatch.setattr(agent, "retry", lambda fn, *args, **kwargs: (True, fn(*args)))
    monkeypatch.setattr(agent.pox20, "chain_handles",
                        lambda pox, w3, abis: {"reader": None, "pricing": None, "broker": None, "token": None})
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__read_fee_percents", lambda self: (5, 5))
    node._EtnyPoXNode__bind_pox_generation()
    assert node._EtnyPoXNode__pox_is_20 is True
    assert node._EtnyPoXNode__pox_epc_forms is epc_forms


def test_the_offer_states_the_reserved_figures_at_most_the_bench_records_readings(agent, monkeypatch, tmp_path):
    # 64 GiB of memory, 60 available: 60 GiB; 200 GiB of storage, 150 free:
    # 150 GiB; the bench record read 32 GiB allocated and 120 GiB of storage.
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[:4] == [8, 32, 120, 100]
    assert arguments[5] == 250 | (32 << 16) | (32 << 24)
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD,
                       bench={**BENCH, "encmem": 64, "custorage": 190}, storage=(200 * GIB, 195 * GIB))
    monkeypatch.setattr(bench_record, "share_holds", lambda bench, cugflops: True)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    # memory min(64 - 1, 60) = 60, the record's 64 above it; storage
    # min(200 - 20, 195) = 180, the record's 190 above it.
    assert arguments[:4] == [8, 60, 180, 100]
    assert arguments[5] == 250 | (32 << 16) | (32 << 24)


def test_a_reason_11_refusal_keeps_the_storage_below_it(agent, monkeypatch, tmp_path):
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH,
                       storage=(200 * GIB, 195 * GIB))
    node._EtnyPoXNode__offer_storage.refuse(BENCH_KEY, 120)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[2] == 119
    # A figure refused under the trustedzone record's key is not the bench
    # record's.
    node._EtnyPoXNode__offer_storage.refuse("3065ab", 100)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[2] == 119


def test_without_a_bench_record_the_request_states_no_enclave_memory_or_epc_and_is_held(agent, monkeypatch,
                                                                                       tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[:4] == [8, 60, 150, 100]
    assert arguments[5] == 250
    assert ";pb" not in arguments[10] and arguments[9] == "m3"
    assert node._EtnyPoXNode__dp_request_held() is True
    assert sum("carries no bench record" in m for m in caplog.messages) == 1


def test_a_bench_record_that_proves_nothing_beside_the_record_is_not_carried(agent, monkeypatch, tmp_path):
    """A bench record whose region's rate is below the share of the
    trustedzone record's GFLOPS (bench_record.share_holds) is judged as no
    bench record: the request carries neither its keys nor its figures."""
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), {**RECORD, "gflops": 6400}, bench=BENCH)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert ";pb" not in arguments[10] and arguments[5] == 6400


def test_an_offer_of_unsafe_trustedzones_alone_states_its_readings_and_no_gflops(agent, monkeypatch, tmp_path):
    # Weakness (d), MATCHING-SYNC-PLAN.md A19, D05 (OD9 (iii) A): an offer
    # with no capability record, as an -unsafe-only node's, states 0 in bits
    # 0 to 15 of its gflops argument (stored as 100, one CU, never above it:
    # reason 13 on a passed-over offer judges above 100) and no cu or pb
    # keys; its enclave memory is the largest listed size its memory figure
    # admits and its EPC its claim, at most the settings.
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), None)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__runnable_trustedzones",
                        lambda self: {"ecld-nodenithy-amoy-unsafe", "ecld-pynithy-amoy-unsafe"})
    monkeypatch.setattr(agent.epc_claim, "read_claim", lambda setting: (2048, "CPUID leaf 0x12"))
    name, arguments = node._EtnyPoXNode__dp_request_params()
    assert name == utils.DP_ADD_V2_EPC
    assert arguments[5] & 0xFFFF == 0
    assert arguments[5] == (32 << 16) | (32 << 24)
    assert "cugflops=" not in arguments[10] and "cusig=" not in arguments[10] and ";pb" not in arguments[10]
    monkeypatch.setattr(agent.epc_claim, "read_claim", lambda setting: (None, "no EPC"))
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[5] == 32 << 16


def test_an_offer_without_a_record_naming_a_cas_trustedzone_states_no_readings(agent, monkeypatch, tmp_path):
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), None)
    monkeypatch.setattr(agent.epc_claim, "read_claim", lambda setting: (2048, "CPUID leaf 0x12"))
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[5] == 0


def test_without_matching_the_offer_is_todays(agent, monkeypatch, tmp_path):
    node = params_node(agent, monkeypatch, tmp_path, None, RECORD, bench=BENCH)
    _, arguments = node._EtnyPoXNode__dp_request_params()
    assert arguments[:4] == [8, 16, 100, 100]
    assert arguments[5] == 250
    assert ";pb" not in arguments[10]


def test_no_offer_is_posted_while_a_figure_is_below_one_gb(agent, monkeypatch, tmp_path):
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH, storage=(15 * GIB, 15 * GIB))
    assert node._EtnyPoXNode__dp_request_held() is True
    node = params_node(agent, monkeypatch, tmp_path, StubMatching(), RECORD, bench=BENCH)
    node._EtnyPoXNode__record_missing_hold = SimpleNamespace(disposition=lambda advertised, runnable: 'post')
    assert node._EtnyPoXNode__dp_request_held() is False


# ------------------------------------------------------------ the refresh

class RefreshPox:
    def __init__(self):
        self.tips = []

    @property
    def functions(self):
        pox = self

        def refresh(req_id, tip):
            pox.tips.append(tip)
            return SimpleNamespace(build_transaction=lambda fields: {})
        return SimpleNamespace(refreshDPRequest=refresh)

    def caller(self):
        return SimpleNamespace(dpTerms=lambda req_id: (250, 7, True, 2_000_000_000, True))


def refreshed_tip(agent, monkeypatch, stub, computed):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    network = intake.polygon_mainnet(monkeypatch)
    node._EtnyPoXNode__network_config = network
    node._EtnyPoXNode__matching = stub
    pox = RefreshPox()
    node._EtnyPoXNode__etny = pox
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=SimpleNamespace(
        wait_for_transaction_receipt=lambda h, timeout: SimpleNamespace(status=1)))
    node.get_transaction_build = lambda: {}
    node.send_transaction = lambda txn: "0xhash"
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__current_tip", lambda self, gflops: computed)
    monkeypatch.setattr(agent, "time", SimpleNamespace(sleep=lambda s: None, time=lambda: 0))
    node._EtnyPoXNode__refresh_dp_request(7, SimpleNamespace(gflops=250))
    return pox.tips[0]


def period(tip):
    return matching.Period(0, 1, 0, 0, 0, 7, 4, 8, 40, 100, 250, 64, 1, tip, 0)


def test_the_refresh_keeps_the_periods_tip_while_a_claim_stands(agent, monkeypatch):
    assert refreshed_tip(agent, monkeypatch, StubMatching(standing=True, period=period(100)), 300) == 100
    assert refreshed_tip(agent, monkeypatch, StubMatching(standing=False, period=period(100)), 300) == 300
    assert refreshed_tip(agent, monkeypatch, StubMatching(standing=False, period=period(100)), 105) == 100
    assert refreshed_tip(agent, monkeypatch, None, 300) == 300


# ------------------------------------------------- the integration test

def test_no_integration_test_starts_while_a_claim_can_be_approved(agent, monkeypatch):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__last_registry_check_at = agent.time.monotonic()
    node._EtnyPoXNode__integration_images = ["img"]
    node._EtnyPoXNode__replication_only = False
    monkeypatch.setattr(agent.config, "skip_integration_test", False)
    taken = []
    monkeypatch.setattr(agent, "integration_tests_due", lambda *args: ["img"])
    monkeypatch.setattr(agent, "get_task_running_on", lambda: None)
    monkeypatch.setattr(agent, "acquire_task_slot", lambda name: taken.append(name))
    node._EtnyPoXNode__matching = StubMatching(approvable=True)
    node._EtnyPoXNode__maybe_retry_integration_tests()
    assert taken == []


def test_an_approved_order_ends_a_running_integration_test(agent, monkeypatch):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    clock = [1000.0]
    monkeypatch.setattr(agent, "time", SimpleNamespace(time=lambda: clock[0], sleep=lambda s: clock.__setitem__(
        0, clock[0] + s), monotonic=lambda: clock[0]))
    node._EtnyPoXNode__esr = None
    node._EtnyPoXNode__matching = StubMatching()
    node.swift_stream_service = SimpleNamespace(is_object_in_bucket=lambda bucket, name: (False, ""))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__enclave_failure", lambda self: None)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__refresh_standing_dp_request", lambda self: None)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__matching_order_appeared", lambda self: True)
    with pytest.raises(agent.EnclaveFailed):
        node.wait_for_enclave_v2(agent.INTEGRATION_BUCKET, "context_test.etny", 900)
    assert clock[0] - 1000 < agent.MATCHING_TEST_POLL_SECONDS + 2


# ------------------------------------------------------------ fetching

def fetch_node(agent, monkeypatch, stub, outcomes, approved_at, head_times):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__matching = stub
    attempts = []

    def download_many(hashes, attempts=5, delay=3, artefacts=False):
        result = outcomes[min(len(attempts_made), len(outcomes) - 1)]
        attempts_made.append(artefacts)
        return result
    attempts_made = attempts
    node.storage = SimpleNamespace(download_many=download_many)
    node._EtnyPoXNode__etny = SimpleNamespace(caller=lambda: SimpleNamespace(orderApprovedAt=lambda o: approved_at))
    times = iter(head_times)
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=SimpleNamespace(
        get_block=lambda tag: SimpleNamespace(timestamp=next(times))))
    synced = []
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__maybe_sync_ipfs_peers", lambda self: synced.append(1))
    monkeypatch.setattr(agent, "stop_event", SimpleNamespace(wait=lambda s: False))
    return node, attempts, synced


def test_an_order_is_fetched_for_ten_minutes_after_its_approval(agent, monkeypatch):
    node, attempts, synced = fetch_node(agent, monkeypatch, StubMatching(), [False], 10_000,
                                        [10_000 + t for t in (100, 300, 599, 600)])
    assert node._EtnyPoXNode__fetch_order_objects(1, ["a"], ["b"]) is False
    assert len(synced) == 3


def test_a_fetch_that_lands_inside_the_ten_minutes_runs_the_order(agent, monkeypatch):
    node, attempts, synced = fetch_node(agent, monkeypatch, StubMatching(), [False, False, True], 10_000,
                                        [10_000 + t for t in (100, 300, 599)])
    assert node._EtnyPoXNode__fetch_order_objects(1, ["a"], ["b"]) is True
    assert len(synced) == 2


def test_without_matching_a_failed_fetch_closes_at_once(agent, monkeypatch):
    node, attempts, synced = fetch_node(agent, monkeypatch, None, [False], 10_000, [])
    assert node._EtnyPoXNode__fetch_order_objects(1, ["a"], ["b"]) is False
    assert synced == []


# -------------------------------------------------------------- the farm

class Farms:
    def __init__(self, refuse):
        self.refuse = refuse
        self.joined = []

    def caller(self):
        return SimpleNamespace(farmOf=lambda node: "0x" + "00" * 20)

    @property
    def functions(self):
        farms = self

        def join(farm, expires_at, signature):
            def call(fields):
                if farms.refuse:
                    raise ContractLogicError("execution reverted: farm not approved")
            return SimpleNamespace(call=call, name='joinFarm')
        return SimpleNamespace(joinFarm=join)


FARM = "0x" + "44" * 20
INVITE = f"{FARM}:1900000000:0x" + "ab" * 65


def farm_node(agent, monkeypatch, tmp_path, stub, payout, refuse):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    network = intake.polygon_mainnet(monkeypatch)
    node._EtnyPoXNode__network_config = network.__class__(**{**network.__dict__, "farm_invite": INVITE})
    node._EtnyPoXNode__network = "polygon_amoy"
    node._EtnyPoXNode__matching = stub
    node._EtnyPoXNode__address = ADDRESS
    farms = Farms(refuse)
    node._EtnyPoXNode__broker = farms
    node._EtnyPoXNode__broker.address = "0x" + "55" * 20
    node._EtnyPoXNode__etny = SimpleNamespace(caller=lambda: SimpleNamespace(payoutAddressOf=lambda n: payout))
    node._EtnyPoXNode__w3 = SimpleNamespace(to_checksum_address=lambda a: a)
    node.cache_config = SimpleNamespace(farm_refusal_filepath=tmp_path / "farm.json")
    sent = []
    node._EtnyPoXNode__broker_tx = lambda label, fn: sent.append(label)
    return node, sent


def test_a_node_with_a_named_wallet_waits_for_its_approval_before_joining(agent, monkeypatch, tmp_path):
    # MatchingNode.farm_join_ready reads the wallet and its approval
    # (farmJoinOf, payoutAddressOf, farmApprovalOf); the join waits for it.
    wallet = "0x" + "33" * 20
    stub = StubMatching(farm_ready=(False, wallet))
    node, sent = farm_node(agent, monkeypatch, tmp_path, stub, wallet, refuse=False)
    node._EtnyPoXNode__join_farm_if_invited()
    assert sent == [] and not (tmp_path / "farm.json").exists()
    assert ('farm', FARM) in stub.calls
    node, sent = farm_node(agent, monkeypatch, tmp_path, StubMatching(farm_ready=(True, wallet)), wallet,
                           refuse=False)
    node._EtnyPoXNode__join_farm_if_invited()
    assert sent == ["joinFarm"]


def test_a_node_without_a_wallet_joins_on_its_own_key(agent, monkeypatch, tmp_path):
    node, sent = farm_node(agent, monkeypatch, tmp_path, StubMatching(farm_ready=(True, ADDRESS)), ADDRESS,
                           refuse=False)
    node._EtnyPoXNode__join_farm_if_invited()
    assert sent == ["joinFarm"]


def test_the_farm_step_names_no_payout_on_a_matching_network(agent, monkeypatch, tmp_path):
    node, sent = farm_node(agent, monkeypatch, tmp_path, StubMatching(), ADDRESS, refuse=False)
    node._EtnyPoXNode__set_farm_payout()
    assert sent == []
