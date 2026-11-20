"""The intake loop's age rule (process_dp_request): the agent judges, reads
and places only the DO requests created within the network's
do_request_max_age_seconds (DO_REQUEST_MAX_AGE_SECONDS, 86400 in node/.env)
of the latest block's timestamp, the ids from the age boundary on, which it
establishes from createdAt in the storage of the PoX: a request below the
boundary is never read nor placed, pinned to this node or not, and a pass
whose boundary is not established (an RPC failure, a storage that is not
doRequestsList) places nothing, the next pass trying again.

The node is an EtnyPoXNode built without __init__ on a recorded legacy PoX
(polygon_mainnet's configuration from node/.env): every read and send is
appended to the chain's events and every sleep is recorded, not slept. The
storage is modelled from the contracts' declarations, independently of
do_request_age: doRequestsList's length at slot 3 and createdAt the low 32
bits of slot keccak256(3) + 10 id + 1 in the storage ethStorageAddress()
names (cloud), or the length at slot 19 and createdAt the top 32 bits of
slot keccak256(19) + 9 id in the PoX itself, which has no
ethStorageAddress() (v05)."""
import argparse
import calendar
import importlib.util
import json
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import ContractLogicError

import config
from models import OrderStatus, RequestStatus
from utils import Cache, ListCache, ResourceRefusals

NODE = Path(__file__).resolve().parents[1]
NODE_ADDRESS = Web3.to_checksum_address("0x" + "11" * 20)
CLIENT = Web3.to_checksum_address("0x" + "22" * 20)
POX = Web3.to_checksum_address("0x" + "33" * 20)
STORAGE = Web3.to_checksum_address("0x" + "44" * 20)
DP_REQUEST = 7
HEAD = 97_650_000
DAY = 86400
HOUR = 3600
# The latest block's timestamp: 2026-11-20T12:40:00Z.
NOW = 1795178400
# (length slot, slots a request, createdAt's slot in a request, createdAt's
# first bit) of each storage the chain models.
LAYOUTS = {"cloud": (3, 10, 1, 0), "v05": (19, 9, 0, 224), "other": (5, 4, 0, 0)}
# The sleeps a node's loop records before its sleep raises Stuck.
SLEEPS_MAX = 2000


def utc(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


class Sent(BaseException):
    """Raised by the recorded send: the loop's first transaction ends it."""


class Stuck(BaseException):
    """Raised at a node's SLEEPS_MAX-th sleep: its loop reached neither its
    stop nor a send."""


class Chain:
    """A legacy PoX of DO requests created at `created` (createdAt by id),
    AVAILABLE when in `available` (every one by default) and BOOKED
    otherwise, each naming the trustedzone ecld-pynithy, pinned to this
    node when in `pinned` and to none otherwise; laid out as `layout`
    ('cloud' in STORAGE, 'v05' or 'other' in the PoX); this node's AVAILABLE
    DP request; `nodes` nodes registered with the heartbeat contract; the
    latest block HEAD, its timestamp the next of `heads` at each read of the
    DO request count (the last one stays). The reads named in `failing`
    raise an RPC error while `failing_reads` is above 0, each failure taking
    one off. Each read appends its name to `events`; `counted` is called at
    each count read."""

    def __init__(self, created, layout="cloud", available=None, pinned=(), heads=(NOW,), nodes=1, failing=(),
                 failing_reads=0, counted=lambda: None):
        self.events = []
        self.created = list(created)
        self.available = set(range(len(self.created)) if available is None else available)
        self.layout = layout
        self.pinned = set(pinned)
        self.heads = list(heads)
        self.head_timestamp = self.heads[0]
        self.nodes = nodes
        self.failing = set(failing)
        self.failing_reads = failing_reads
        self.counted = counted
        self.counts = 0
        self.address = POX
        self.holder = STORAGE if layout == "cloud" else POX
        length_slot, size, field, shift = LAYOUTS[layout]
        start = int.from_bytes(keccak(length_slot.to_bytes(32, "big")), "big")
        self.words = {length_slot: (len(self.created), "length")}
        for request_id, timestamp in enumerate(self.created):
            # The v05 word holds the downer and the resource fields below createdAt.
            filler = int(CLIENT, 16) | (0x0101003C01 << 160) if layout == "v05" else 0
            self.words[start + request_id * size + field] = ((timestamp << shift) | filler, f"createdAt({request_id})")
        self.functions = SimpleNamespace(
            _getMyDOOrders=lambda: SimpleNamespace(call=lambda transaction: self.read("_getMyDOOrders", [])),
            _placeOrder=self.placement,
            ethStorageAddress=lambda: SimpleNamespace(call=self.storage_address),
            logCall=lambda version: SimpleNamespace(build_transaction=lambda fields: self.read("logCall built", {})))

    def read(self, name, answer):
        self.events.append(name)
        if name.split("(")[0] in self.failing and self.failing_reads > 0:
            self.failing_reads -= 1
            raise ConnectionError(f"{name}: 429 Client Error: Too Many Requests")
        return answer

    def caller(self):
        return self

    def storage_address(self):
        if self.layout == "cloud":
            return self.read("ethStorageAddress", STORAGE)
        self.read("ethStorageAddress", None)
        raise ContractLogicError("execution reverted")

    def get_storage_at(self, address, slot):
        word, name = self.words.get(slot, (0, f"slot {slot}")) if address == self.holder else (0, f"{address} slot")
        return self.read(f"eth_getStorageAt({name})", word.to_bytes(32, "big"))

    @property
    def block_number(self):
        return self.read("eth_blockNumber", HEAD)

    def get_block(self, block):
        return self.read("eth_getBlockByNumber", SimpleNamespace(number=HEAD, timestamp=self.head_timestamp,
                                                                 baseFeePerGas=100 * 10**9))

    def get_balance(self, address):
        return self.read("eth_getBalance", 10**18)

    def get_transaction_count(self, address):
        return self.read("eth_getTransactionCount", 3)

    def _getOrder(self, order):
        return self.read(f"_getOrder({order})", (CLIENT, NODE_ADDRESS, order, DP_REQUEST, OrderStatus.CLOSED))

    def _getDORequestsCount(self):
        self.head_timestamp = self.heads[min(self.counts, len(self.heads) - 1)]
        self.counts += 1
        self.counted()
        return self.read("_getDORequestsCount", len(self.created))

    def _getDORequest(self, request):
        status = RequestStatus.AVAILABLE if request in self.available else RequestStatus.BOOKED
        return self.read(f"_getDORequest({request})", (CLIENT, 1, 1, 1, 1, 60, 3, status))

    def _getDORequestMetadata(self, request):
        pinned_to = NODE_ADDRESS if request in self.pinned else ""
        return self.read(f"_getDORequestMetadata({request})", (CLIENT, "v3:image:ecld-pynithy", "", "", pinned_to))

    def _getDPRequest(self, request):
        return self.read(f"_getDPRequest({request})", (NODE_ADDRESS, 1, 1, 1, 1, 60, 3, RequestStatus.AVAILABLE))

    def placement(self, do_request, dp_request):
        name = f"_placeOrder({do_request}, {dp_request})"
        return SimpleNamespace(call=lambda transaction: self.read(f"{name} simulated", None),
                               build_transaction=lambda fields: self.read(f"{name} built", {}))

    def getNodesCount(self):
        return self.read("getNodesCount", self.nodes)

    def requests_read(self):
        """The DO requests whose record or metadata the loop read."""
        return sorted({int(event.split("(")[1].rstrip(")")) for event in self.events
                       if event.startswith(("_getDORequest(", "_getDORequestMetadata("))})


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def polygon_mainnet(monkeypatch, environment=None):
    """polygon_mainnet's NetworkConfig from node/.env and `environment`."""
    monkeypatch.setattr(os, "environ", dict(environment or {}))
    config.load_dotenv(NODE / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", nargs="+")
    config.add_network_override_arguments(parser, ["POLYGON"])
    (network,) = config.parse_networks(parser.parse_args(["--network", "polygon_mainnet"]), parser, ["POLYGON"])
    return network


def run(agent, monkeypatch, directory, chain, passes=None, clock=time.time, network=None):
    """process_dp_request of a node serving `chain` with its standing DP
    request DP_REQUEST, working in `directory` with an empty doreq_cache.txt,
    its clock `clock` (the heartbeat and the IPFS upkeep done at its time),
    the integration tests skipped and no SGX driver conflict; the loop is
    stopped at the `passes`-th read of the DO request count (the passes
    before it run whole), or ends at the send of a placement (Sent). Returns
    the node."""
    stop = threading.Event()
    if passes is not None:
        def counted():
            if chain.counts == passes:
                stop.set()
        chain.counted = counted
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == SLEEPS_MAX:
            raise Stuck(f"the last events: {chain.events[-12:]}")

    monkeypatch.setattr(agent, "time", SimpleNamespace(sleep=sleep, time=clock, monotonic=time.monotonic))
    monkeypatch.setattr(agent, "stop_event", stop)
    monkeypatch.setattr(agent, "acquire_task_slot", lambda name: None)
    monkeypatch.setattr(agent, "release_task_slot", lambda name: None)
    monkeypatch.setattr(agent, "do_request_age_logged", {}, raising=False)
    monkeypatch.setattr(agent.config, "skip_integration_test", True)
    monkeypatch.chdir(directory)
    network = network or polygon_mainnet(monkeypatch)
    (directory / "heartbeat.etny").write_text(str(int(clock())))
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("do-request-age")
    node._EtnyPoXNode__network_config = network
    node._EtnyPoXNode__network = network.name
    node._EtnyPoXNode__etny = chain
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=chain, to_wei=Web3.to_wei, from_wei=Web3.from_wei)
    node._EtnyPoXNode__heart_beat = chain
    node.cache_config = SimpleNamespace(heart_beat_log_file_path=directory / "heartbeat.etny",
                                        accepted_handover_filepath=directory / "accepted.json")
    node.storage = SimpleNamespace(connect=lambda attempts: True, repo_gc=lambda: None)
    node.orders_cache = Cache(1000, directory / "orders_cache.txt")
    node.doreq_cache = ListCache(1000, directory / "doreq_cache.txt")
    node.resource_refusals = ResourceRefusals(1000, directory / "doreq_resources.json")
    node.merged_orders_cache = SimpleNamespace(add=lambda **record: None, rem=lambda **record: None)
    node._EtnyPoXNode__address = NODE_ADDRESS
    node._EtnyPoXNode__acct = SimpleNamespace(address=NODE_ADDRESS)
    node._EtnyPoXNode__dprequest = DP_REQUEST
    node._EtnyPoXNode__orders = {}
    node._EtnyPoXNode__is_first_cycle = defaultdict(lambda: True)
    node._EtnyPoXNode__replication_only = False
    node._EtnyPoXNode__trustedzones = config.trustedzone_requirements(network.trustedzone_images)
    node._EtnyPoXNode__pox_is_12 = False
    node._EtnyPoXNode__reader = None
    node._EtnyPoXNode__broker = None
    node._EtnyPoXNode__started_row_pending = None
    node._EtnyPoXNode__do_requests_build_pending = False
    node._EtnyPoXNode__last_ipfs_cleanup_at = clock()
    node._EtnyPoXNode__last_ipfs_peer_sync_at = clock()
    node._EtnyPoXNode__do_request_ages = None
    node._EtnyPoXNode__do_request_boundary = 0

    def send(transaction):
        chain.events.append("sent")
        raise Sent

    node.send_transaction = send
    node._check_installed_drivers = lambda: False
    node.slept = slept
    if passes is None:
        with pytest.raises(Sent):
            node.process_dp_request()
    else:
        node.process_dp_request()
    return node


def placed(chain):
    """The DO requests whose placement was sent."""
    return [int(event.split("(")[1].split(",")[0]) for event, after in zip(chain.events, chain.events[1:])
            if event.startswith("_placeOrder(") and event.endswith(" built") and after == "sent"]


def boundary_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("DO requests below")]


def test_a_fresh_request_is_placed_and_the_old_ones_are_never_read(agent, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - 3 * DAY, NOW - 2 * DAY, NOW - DAY - 1, NOW - HOUR])
    node = run(agent, monkeypatch, tmp_path, chain)
    assert placed(chain) == [3]
    assert chain.requests_read() == [3]
    assert node._EtnyPoXNode__do_request_boundary == 3
    assert boundary_lines(caplog) == [
        f"DO requests below 3 (of 4) were created more than 86400 s before block {HEAD} (2026-11-20T12:40:00Z); "
        f"none of them is judged or placed"]
    assert f"DO request ages: createdAt is read from {STORAGE} (layout cloud)" in caplog.messages
    # The pass reads the latest block, the storage's address, its length and
    # newest request, then searches from 0: the probe of request 0 and the
    # binary search over 1 to 3.
    seen = chain.events.index("_getDORequestsCount")
    assert chain.events[seen + 1:seen + 9] == [
        "eth_getBlockByNumber", "ethStorageAddress", "eth_getStorageAt(length)", "eth_getStorageAt(createdAt(3))",
        "eth_getStorageAt(createdAt(0))", "eth_getStorageAt(createdAt(2))", "eth_getStorageAt(createdAt(3))",
        "_getDORequest(3)"]


def test_a_request_exactly_at_the_limit_is_placed(agent, monkeypatch, tmp_path):
    chain = Chain([NOW - DAY - 1, NOW - DAY])
    node = run(agent, monkeypatch, tmp_path, chain)
    assert placed(chain) == [1]
    assert chain.requests_read() == [1]
    assert node._EtnyPoXNode__do_request_boundary == 1


def test_an_old_pinned_request_is_never_read_nor_placed(agent, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - 3 * DAY, NOW - 2 * DAY], pinned={0, 1})
    node = run(agent, monkeypatch, tmp_path, chain, passes=4)
    assert chain.requests_read() == []
    assert placed(chain) == [] and "sent" not in chain.events
    assert node._EtnyPoXNode__do_request_boundary == 2
    assert len(boundary_lines(caplog)) == 1
    # Once the boundary is the count, a pass with no request from it on
    # reads no storage: only the first pass did.
    first, second = [i for i, event in enumerate(chain.events) if event == "_getDORequestsCount"][:2]
    assert any(event.startswith("eth_getStorageAt") for event in chain.events[first:second])
    assert not any(event.startswith("eth_getStorageAt") for event in chain.events[second:])


def test_a_fresh_pinned_request_is_placed(agent, monkeypatch, tmp_path):
    chain = Chain([NOW - 3 * DAY, NOW - 600], pinned={0, 1})
    node = run(agent, monkeypatch, tmp_path, chain)
    assert placed(chain) == [1]
    assert chain.requests_read() == [1]
    assert node._EtnyPoXNode__do_request_boundary == 1


def polygon_mainnet_requests():
    """createdAt of Polygon mainnet's 242 DO requests as its storage
    0x6aAafE99... holds them: 98, 99 and 100 created on 2024-12-20, 241 at
    2026-11-20T03:00:50Z, the others between and before them."""
    t98, t99, t100 = utc("2024-12-20T09:02:13Z"), utc("2024-12-20T09:04:05Z"), utc("2024-12-20T09:52:15Z")
    return ([t98 - (98 - i) * 600 for i in range(98)] + [t98, t99, t100]
            + [t100 + (i - 100) * 50000 for i in range(101, 241)] + [utc("2026-11-20T03:00:50Z")])


def test_polygon_mainnets_available_requests_98_to_100_are_never_read(agent, monkeypatch, tmp_path, caplog):
    # 98, 99 and 100 AVAILABLE and unpinned, every other request BOOKED.
    caplog.set_level(logging.INFO)
    chain = Chain(polygon_mainnet_requests(), available={98, 99, 100})
    node = run(agent, monkeypatch, tmp_path, chain, passes=3)
    assert node._EtnyPoXNode__do_request_boundary == 241
    assert chain.requests_read() == [241]
    assert "sent" not in chain.events
    # The search from 0: the probe of request 0 and at most 8 reads over 1
    # to 241, after the binding's reads of the length and of request 241.
    reads = [event for event in chain.events if event.startswith("eth_getStorageAt(createdAt(")]
    assert len(reads) <= 1 + 1 + 8


def test_polygon_mainnets_request_241_is_placed(agent, monkeypatch, tmp_path):
    chain = Chain(polygon_mainnet_requests(), available={98, 99, 100, 241})
    node = run(agent, monkeypatch, tmp_path, chain)
    assert placed(chain) == [241]
    assert chain.requests_read() == [241]
    assert node._EtnyPoXNode__do_request_boundary == 241


def test_v05_reads_the_pox_itself(agent, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - 900 * DAY, NOW - 30 * DAY, NOW - 7200], layout="v05")
    node = run(agent, monkeypatch, tmp_path, chain)
    assert node._EtnyPoXNode__do_request_ages[0] == POX
    assert f"DO request ages: createdAt is read from {POX} (layout v05)" in caplog.messages
    assert placed(chain) == [2]
    assert chain.requests_read() == [2]


def test_the_age_is_judged_by_the_chains_time_not_the_nodes_clock(agent, monkeypatch, tmp_path):
    created = [NOW - 3 * DAY, NOW - HOUR]
    # The node's clock 30 days ahead of the chain: the request an hour old by
    # the latest block is placed.
    chain = Chain(created)
    (tmp_path / "ahead").mkdir()
    run(agent, monkeypatch, tmp_path / "ahead", chain, clock=lambda: NOW + 30 * DAY)
    assert placed(chain) == [1]
    # The chain 2 days ahead of the node's clock: the request is 49 hours
    # old by the latest block, an hour by the clock, and is not read.
    chain = Chain(created, heads=(NOW + 2 * DAY,))
    (tmp_path / "behind").mkdir()
    node = run(agent, monkeypatch, tmp_path / "behind", chain, passes=3, clock=lambda: NOW)
    assert chain.requests_read() == []
    assert node._EtnyPoXNode__do_request_boundary == 2


@pytest.mark.parametrize("failing", ["eth_getBlockByNumber", "ethStorageAddress", "eth_getStorageAt"])
def test_an_rpc_failure_places_nothing_in_that_pass(agent, monkeypatch, tmp_path, caplog, failing):
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - 2 * DAY, NOW - HOUR], failing={failing}, failing_reads=1)
    run(agent, monkeypatch, tmp_path, chain)
    # The first pass ends at the failure; the second establishes the
    # boundary and places request 1.
    counts = [i for i, event in enumerate(chain.events) if event == "_getDORequestsCount"]
    assert len(counts) == 2
    assert not any(event.startswith(("_getDORequest(", "_placeOrder")) for event in chain.events[:counts[1]])
    assert placed(chain) == [1]
    assert chain.requests_read() == [1]
    warnings = [r.getMessage() for r in caplog.records
                if r.levelno == logging.WARNING and "DO request ages" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].startswith(f"DO request ages were not read ({failing}")
    assert warnings[0].endswith("429 Client Error: Too Many Requests); no order is placed this pass")


def test_a_storage_that_is_not_do_requests_list_places_nothing_and_logs_one_error(agent, monkeypatch, tmp_path,
                                                                                  caplog):
    # The storage the PoX names keeps its length elsewhere: neither layout
    # holds, every pass refuses, and the error is logged once in the hour.
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - HOUR, NOW - 60], layout="other")
    chain.functions.ethStorageAddress = lambda: SimpleNamespace(call=lambda: chain.read("ethStorageAddress", POX))
    run(agent, monkeypatch, tmp_path, chain, passes=5)
    assert chain.requests_read() == []
    assert "sent" not in chain.events
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        f"DO request ages are not established (cloud at {POX}: the length slot holds 0 where the PoX counts 2 DO "
        f"requests; v05 at {POX}: the length slot holds 0 where the PoX counts 2 DO requests); "
        f"no order is placed until they are"]
    # Every pass tried the binding again, the fifth too, which the count read
    # stops after its boundary.
    assert chain.events.count("ethStorageAddress") == 5


def test_an_implausible_newest_created_at_is_refused(agent, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - HOUR, NOW + DAY])
    run(agent, monkeypatch, tmp_path, chain, passes=3)
    assert chain.requests_read() == []
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert f"DO request 1: createdAt {NOW + DAY} is more than 60 s after the latest block's timestamp {NOW}" in errors[0]


def test_a_request_the_rpcs_node_does_not_hold_ends_the_pass_and_the_next_binds_again(agent, monkeypatch, tmp_path,
                                                                                    caplog):
    # The count names request 2, which the storage read does not hold yet.
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - 2 * DAY, NOW - HOUR])
    count = chain._getDORequestsCount
    answers = iter([3])

    def count_ahead():
        answer = count()
        return next(answers, answer)

    chain._getDORequestsCount = count_ahead
    run(agent, monkeypatch, tmp_path, chain)
    counts = [i for i, event in enumerate(chain.events) if event == "_getDORequestsCount"]
    assert not any(event.startswith(("_getDORequest(", "_placeOrder")) for event in chain.events[:counts[1]])
    assert placed(chain) == [1]
    assert chain.events.count("ethStorageAddress") == 2
    assert any("reads createdAt 0: the RPC's node does not hold it" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)


def test_the_boundary_moves_forward_as_the_chain_time_advances(agent, monkeypatch, tmp_path, caplog):
    # 2,500 registered nodes take turns over 100 blocks: none of the requests
    # is this node's to place at HEAD, and each is judged again every pass.
    # The latest block is 11 and then 15 hours later.
    caplog.set_level(logging.INFO)
    chain = Chain([NOW - 30 * HOUR, NOW - 20 * HOUR, NOW - 10 * HOUR], nodes=2500,
                  heads=(NOW, NOW + 11 * HOUR, NOW + 15 * HOUR))
    node = run(agent, monkeypatch, tmp_path, chain, passes=4)
    assert boundary_lines(caplog) == [
        f"DO requests below {b} (of 3) were created more than 86400 s before block {HEAD} ({stamp}); "
        f"none of them is judged or placed"
        for b, stamp in ((1, "2026-11-20T12:40:00Z"), (2, "2026-11-20T23:40:00Z"), (3, "2026-11-21T03:40:00Z"))]
    assert node._EtnyPoXNode__do_request_boundary == 3
    assert chain.requests_read() == [1, 2]
    assert "sent" not in chain.events
    # Each search starts at the boundary before: the second reads request 1
    # (old now) and 2, the third request 2 (old now), and the fourth pass,
    # with no request from 3 on, reads no storage.
    counts = [i for i, event in enumerate(chain.events) if event == "_getDORequestsCount"]
    storage = [[event for event in chain.events[start:end] if event.startswith("eth_getStorageAt")]
               for start, end in zip(counts, counts[1:] + [len(chain.events)])]
    assert storage[1:] == [["eth_getStorageAt(createdAt(1))", "eth_getStorageAt(createdAt(2))"],
                           ["eth_getStorageAt(createdAt(2))"], []]


def test_a_networks_own_limit_applies(agent, monkeypatch, tmp_path):
    network = polygon_mainnet(monkeypatch, {"POLYGON_MAINNET_DO_REQUEST_MAX_AGE_SECONDS": str(2 * HOUR)})
    assert network.do_request_max_age_seconds == 2 * HOUR
    chain = Chain([NOW - 3 * HOUR, NOW - HOUR])
    run(agent, monkeypatch, tmp_path, chain, network=network)
    assert placed(chain) == [1]
    assert chain.requests_read() == [1]
