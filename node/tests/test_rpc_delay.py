"""The delay between an agent's chain calls (config.NetworkConfig rpc_delay,
<NETWORK>_RPC_DELAY in milliseconds): etny-node.py sleeps rpc_delay / 1000
seconds in its loops, before most of their chain calls. node/.env, the
release's defaults, gives polygon_mainnet 200 ms, the delay of every network
but the bloxberg testnet pair (0). A variable the process's environment
carries stands before the file's (config loads node/.env with load_dotenv,
which sets the variables the environment lacks), and the
--<network>_rpc_delay argument before both (config.parse_networks); a
variable or an argument that is no integer is refused.

The loops run on an EtnyPoXNode built without __init__ whose chain records
each read and send and whose sleeps are recorded, not slept; the loop of an
idle node that reaches neither its stop nor its send ends at its SLEEPS_MAX-th
sleep. On polygon_mainnet, a legacy PoX without an Enclave State Registry, a
CAS session registry or a broker, a round of run_esr_replication_loop reads
the newest 200 orders for their results, the newest 200 DO requests and 200
orders for their inputs, and 200 orders, each with its DO request's metadata,
for their session rows, sleeping the delay before each order or request, then
the registered-image scan's head block and logs, and sleeps
ESR_REPLICATION_INTERVAL_SECONDS. On a PoX of 241 DO requests and n orders,
none of them OPEN, PROCESSING or AVAILABLE, a round reads the contract
204 + 4 min(n, 200) times after 200 + 3 min(n, 200) sleeps: 1,004 times after
800 sleeps from 200 orders, 160 s of sleep at 200 ms where 10 ms gives 8 s.
web3 posts three requests for a contract read (eth_chainId, eth_call,
eth_chainId) and one for a balance, the block number or logs: 3,014 requests a
round from 200 orders. The idle tick of a standing offer with no DO request to
judge (process_dp_request) sleeps block_time - 1.3 s, reads the balance,
sleeps the delay and reads the DO request count. A tick with a DO request to
judge establishes the age boundary first, sleeping the delay before each
read: the latest block, then on a handle's first boundary ethStorageAddress()
and the storage's length and newest request, then the storage word of the
boundary before (a request created within do_request_max_age_seconds
leaves it in place). The tick that places a new DO request sleeps the delay
twice more before the placement is sent, a due heartbeat is built after the
delay, and the first pass of a node without doreq_cache.txt reads, after the
boundary, each DO request and its metadata and, for one it may place, the
block number, then sleeps the delay and reads the request again: 10 requests
a sleep. A round's result and input scans post at most 15
requests a second and its session scan 30 if the RPC answered at once; beside
the idle tick the session scan keeps to 1,200 requests in 60 s once a request
takes 29 ms, where 10 ms needs 62 ms and 400 ms no time."""
import argparse
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers.rpc.utils import ExceptionRetryConfiguration

import config
import do_request_age
from models import OrderStatus, RequestStatus
from utils import Cache, ListCache, ResourceRefusals

NODE = Path(__file__).resolve().parents[1]
VARIABLE = "POLYGON_MAINNET_RPC_DELAY"
ARGUMENT = "--polygon_mainnet_rpc_delay"
ADDRESS = "0x" + "11" * 20
# The storage ethStorageAddress() of the recorded PoX names.
STORAGE_CHECKSUM = Web3.to_checksum_address("0x" + "44" * 20)
# <NETWORK>_RPC_DELAY of node/.env, in milliseconds.
DELAYS = {
    "polygon_mainnet": 200,
    "polygon_amoy": 200,
    "polygon_amoy_unsafe": 200,
    "bloxberg_mainnet": 200,
    "bloxberg_testnet": 0,
    "bloxberg_testnet_unsafe": 0,
    "iotex_testnet": 200,
    "ethereum_sepolia": 200,
    "ethereum_sepolia_unsafe": 200,
    "litvm_liteforge": 200,
    "litvm_liteforge_unsafe": 200,
}
# The DO requests of the recorded PoX and, unless a test gives another count,
# its orders: more than the 200 a scan of the replication reads
# (config.do_request_scan_requests, config.esr_result_scan_orders).
ITEMS = 241
SCANNED = 200
# This node's standing DP request on the recorded PoX.
DP_REQUEST = 7
# The recorded chain's head, and the block the registered-image scan of the
# round before stopped at: within one range of logs_chunk_blocks (9000).
HEAD = 97_700_000
SCANNED_TO = HEAD - 300
# The head's timestamp, 2026-11-20T12:40:00Z, and every DO request's
# createdAt, a minute before it.
HEAD_TIMESTAMP = 1795178400
CREATED_AT = HEAD_TIMESTAMP - 60
# The requests polygon_mainnet's RPC of node/.env,
# https://polygon-bor-rpc.publicnode.com, serves an address in 60 s.
LIMIT = 1200
# The sleeps an idle node's loop records before its sleep raises Stuck: the
# tests' loops reach their stop or send within 250 (241 of them in a first
# pass over the DO requests).
SLEEPS_MAX = 1000
# The lines of etny-node.py's entry point that load the networks, after
# conftest.py's stand-in for dependency_manager: the program of a process
# started in node/ with the agent's arguments, which prints the delay of the
# one network they name.
START = """
import sys, types
sys.modules["dependency_manager"] = types.SimpleNamespace(check_dependencies=lambda: None)
import config
network_names = list(config.NETWORKS.keys())
parser = config.parse_arguments(network_names)
args, unknown_args = parser.parse_known_args()
(network,) = config.parse_networks(args, parser, network_names)
print(network.rpc_delay)
"""


class Sent(BaseException):
    """Raised by the recorded send: it ends a loop at its first transaction,
    which the agent's retries (except Exception) do not catch."""


class RoundEnded(BaseException):
    """Raised by the recorded sleep of the replication interval: it ends
    run_esr_replication_loop after its first round."""


class Stuck(BaseException):
    """Raised by an idle node's recorded sleep at its SLEEPS_MAX-th sleep,
    with the last events recorded: it ends a loop that reaches neither its
    stop nor its send, past the tick's except Exception, which logs a
    failure and starts the next tick."""


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def posts():
    """The JSON-RPC methods web3 posts for one read of the PoX
    (_getDORequestsCount, under 'read') and for one read of a balance, of
    the block number, of logs, of a storage word and of the latest block
    (under their methods): on an HTTPProvider
    with the retry configuration and the POA middleware
    EtnyPoXNode.__init__ gives its Web3 (a copy of that construction), whose
    posts are answered here and not sent. The web3 is the one installed
    where the suite runs; dependency_manager pins the agent's at 7.6.1."""
    w3 = Web3(Web3.HTTPProvider(
        "https://rpc.invalid", request_kwargs={"timeout": (3, 30)},
        exception_retry_configuration=ExceptionRetryConfiguration(
            errors=(ConnectionError, requests.ConnectionError, requests.HTTPError, requests.Timeout))))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    pox = w3.eth.contract(address=Web3.to_checksum_address(ADDRESS), abi=config.abi_filepath.read_text())
    answers = {"eth_chainId": hex(137), "eth_call": "0x" + ITEMS.to_bytes(32, "big").hex(),
               "eth_getBalance": hex(10**18), "eth_blockNumber": hex(HEAD), "eth_getLogs": [],
               "eth_getStorageAt": "0x" + CREATED_AT.to_bytes(32, "big").hex(),
               "eth_getBlockByNumber": {"number": hex(HEAD), "timestamp": hex(HEAD_TIMESTAMP), "hash": "0x" + "00" * 32,
                                        "parentHash": "0x" + "00" * 32, "extraData": "0x", "transactions": []}}
    posted = []

    def post(endpoint_uri, data, **kwargs):
        request = json.loads(data)
        posted.append(request["method"])
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": answers[request["method"]]}).encode()

    w3.provider._request_session_manager.make_post_request = post

    def methods(call, answer):
        del posted[:]
        assert call() == answer
        return list(posted)

    return {"read": methods(lambda: pox.caller()._getDORequestsCount(), ITEMS),
            "eth_getBalance": methods(lambda: w3.eth.get_balance(ADDRESS), 10**18),
            "eth_blockNumber": methods(lambda: w3.eth.block_number, HEAD),
            "eth_getLogs": methods(lambda: w3.eth.get_logs({"address": ADDRESS, "fromBlock": SCANNED_TO,
                                                            "toBlock": HEAD}), []),
            "eth_getStorageAt": methods(lambda: int.from_bytes(w3.eth.get_storage_at(STORAGE_CHECKSUM, 3), "big"),
                                        CREATED_AT),
            "eth_getBlockByNumber": methods(lambda: w3.eth.get_block("latest").timestamp, HEAD_TIMESTAMP)}


def load(monkeypatch, network, environment=None, arguments=()):
    """The NetworkConfig of `network` that config.parse_networks gives from
    the command line's `arguments` and an environment holding the variables
    `environment` and, for the variables it lacks, node/.env's: the helper
    loads the file into it with load_dotenv, called without override as
    config.py calls it at import (started runs config.py's own call)."""
    monkeypatch.setattr(os, "environ", dict(environment or {}))
    config.load_dotenv(NODE / ".env")
    family = network.split("_")[0].upper()
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", nargs="+")
    config.add_network_override_arguments(parser, [family])
    parsed = parser.parse_args(["--network", network, *arguments])
    (loaded,) = config.parse_networks(parsed, parser, [family])
    return loaded


def started(variable=None):
    """polygon_mainnet's delay in a process started in node/, where
    config.py's own load of .env runs: the process runs START with the
    agent's arguments -k (required; the text given is no key) and -n, in
    this process's environment with POLYGON_MAINNET_RPC_DELAY at `variable`,
    without the variable for None."""
    environment = {name: value for name, value in os.environ.items() if name != VARIABLE}
    if variable is not None:
        environment[VARIABLE] = variable
    done = subprocess.run([sys.executable, "-c", START, "-k", "none", "-n", "polygon_mainnet"], cwd=NODE,
                          env=environment, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return int(done.stdout)


class Chain:
    """What an agent reads and sends on a legacy PoX: `requests` DO requests,
    BOOKED but those in `available`, unpinned, naming the trustedzone
    ecld-pynithy and created at CREATED_AT; `orders` CLOSED orders (order i
    of DO request i), none of them this node's; this node's AVAILABLE DP
    request; the heartbeat contract; the node's wallet; the head block HEAD
    at HEAD_TIMESTAMP and no logs; the storage ethStorageAddress() names,
    whose doRequestsList length slot (3) holds `requests` and whose every
    other word CREATED_AT. It stands for the PoX contract and for web3's eth
    namespace. Each read appends its name to `events`, with the order or
    request it reads, and `counted` is called at each read of the DO request
    count."""

    def __init__(self, requests=ITEMS, available=(), counted=lambda: None, orders=ITEMS):
        self.events = []
        self.requests = requests
        self.available = set(available)
        self.counted = counted
        self.orders = orders
        self.address = Web3.to_checksum_address(ADDRESS)
        self.functions = SimpleNamespace(
            _getMyDOOrders=lambda: SimpleNamespace(call=lambda transaction: self.read("_getMyDOOrders", [])),
            _placeOrder=self.placement,
            ethStorageAddress=lambda: SimpleNamespace(call=lambda: self.read("ethStorageAddress", STORAGE_CHECKSUM)),
            logCall=lambda version: SimpleNamespace(build_transaction=lambda fields: self.read("logCall built", {})))

    def read(self, name, answer):
        self.events.append(name)
        return answer

    def caller(self):
        return self

    @property
    def block_number(self):
        return self.read("eth_blockNumber", HEAD)

    def get_logs(self, filter_params):
        return self.read("eth_getLogs", [])

    def get_balance(self, address):
        return self.read("eth_getBalance", 10**18)

    def get_transaction_count(self, address):
        return self.read("eth_getTransactionCount", 3)

    def get_block(self, block):
        return self.read("eth_getBlockByNumber", SimpleNamespace(number=HEAD, timestamp=HEAD_TIMESTAMP,
                                                                 baseFeePerGas=100 * 10**9))

    def get_storage_at(self, address, slot):
        word = self.requests if (address, slot) == (STORAGE_CHECKSUM, do_request_age.CLOUD.length_slot) else CREATED_AT
        return self.read("eth_getStorageAt", word.to_bytes(32, "big"))

    def _getOrdersCount(self):
        return self.read("_getOrdersCount", self.orders)

    def _getOrder(self, order):
        return self.read(f"_getOrder({order})", (ADDRESS, ADDRESS, order, order, OrderStatus.CLOSED))

    def _getDORequestsCount(self):
        self.counted()
        return self.read("_getDORequestsCount", self.requests)

    def _getDORequest(self, request):
        status = RequestStatus.AVAILABLE if request in self.available else RequestStatus.BOOKED
        return self.read(f"_getDORequest({request})", (ADDRESS, 1, 1, 1, 1, 60, 3, status))

    def _getDORequestMetadata(self, request):
        return self.read(f"_getDORequestMetadata({request})", (ADDRESS, "v3:image:ecld-pynithy", "", "", ""))

    def _getDPRequest(self, request):
        return self.read(f"_getDPRequest({request})", (ADDRESS, 1, 1, 1, 1, 60, 3, RequestStatus.AVAILABLE))

    def placement(self, do_request, dp_request):
        name = f"_placeOrder({do_request}, {dp_request})"
        return SimpleNamespace(call=lambda transaction: self.read(f"{name} simulated", None),
                               build_transaction=lambda fields: self.read(f"{name} built", {}))

    def getNodesCount(self):
        return self.read("getNodesCount", 1)


def node_on(agent, monkeypatch, network, chain, sleep=None):
    """An EtnyPoXNode of `network` (a NetworkConfig) built without __init__
    on the recorded `chain`, its PoX and its web3, whose sleeps go to
    `sleep`, by default appended to the chain's events in seconds, and are
    not slept: no RPC, no key."""
    monkeypatch.setattr(agent, "time", SimpleNamespace(sleep=sleep or chain.events.append, time=time.time,
                                                       monotonic=time.monotonic))
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__network_config = network
    node._EtnyPoXNode__network = network.name
    node._EtnyPoXNode__etny = chain
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=chain, to_wei=Web3.to_wei, from_wei=Web3.from_wei)
    return node


def replication_round(agent, monkeypatch, directory, network, orders=ITEMS):
    """The events of the first round of run_esr_replication_loop on
    `network`, ended by the sleep of its interval, the last event: a handle
    of a legacy PoX (no broker) of ITEMS DO requests and `orders` orders
    without an Enclave State Registry or a CAS session registry, whose
    registered-image scan continues from SCANNED_TO, its progress in
    `directory`; Kubo holds no pin, the disk 100 GB free (above
    config.esr_min_free_storage_gb), and no scan finds anything to pin."""
    chain = Chain(orders=orders)
    interval = config.esr_replication_interval_seconds

    def sleep(seconds):
        chain.events.append(seconds)
        if seconds == interval:
            raise RoundEnded

    node = node_on(agent, monkeypatch, network, chain, sleep)
    monkeypatch.setattr(agent.HardwareInfoProvider, "get_free_storage", staticmethod(lambda: 100))
    node._EtnyPoXNode__esr = None
    node._EtnyPoXNode__cas_session_registry = None
    node._EtnyPoXNode__broker = None
    node._EtnyPoXNode__image_registry_v2 = Web3().eth.contract(
        address=Web3.to_checksum_address(network.image_registry_contract_address), abi=agent.IMAGE_REGISTRY_V2_ABI)
    node._EtnyPoXNode__image_registry_last_block = SCANNED_TO
    node._EtnyPoXNode__image_pin_thread = None
    node._EtnyPoXNode__peerings_checked = False
    node.esr_progress = Cache(10_000_000, directory / "esr_progress.json")
    node.storage = SimpleNamespace(recursive_pins=set, reprovide_recent=lambda: None,
                                   sync_chain_peers=lambda multiaddrs: None)
    with pytest.raises(RoundEnded):
        node.run_esr_replication_loop()
    return chain.events


def idle_node(agent, monkeypatch, directory, network, chain, stop, heartbeat_at, cached=range(ITEMS)):
    """An EtnyPoXNode of `network` whose process_dp_request runs the idle
    loop of a standing offer (DP request DP_REQUEST) on the recorded `chain`
    until `stop` is set, working in `directory`: no order of its own, the DO
    requests `cached` in its doreq_cache.txt, no approval awaited, the IPFS
    cleanup and peer sync done now, the heartbeat last sent at
    `heartbeat_at`, SKIP_INTEGRATION_TEST set and no SGX driver conflict.
    The task slot is taken in name only, no DO request age boundary is
    established yet, a send is appended to the chain's events as 'sent' and
    raises Sent, and the SLEEPS_MAX-th sleep raises Stuck."""
    slept = []

    def sleep(seconds):
        chain.events.append(seconds)
        slept.append(seconds)
        if len(slept) == SLEEPS_MAX:
            raise Stuck(f"{SLEEPS_MAX} sleeps without the loop's stop or send; the last events: {chain.events[-12:]}")

    node = node_on(agent, monkeypatch, network, chain, sleep)
    monkeypatch.setattr(agent, "stop_event", stop)
    monkeypatch.setattr(agent, "acquire_task_slot", lambda name: None)
    monkeypatch.setattr(agent.config, "skip_integration_test", True)
    monkeypatch.chdir(directory)
    (directory / "heartbeat.etny").write_text(str(int(heartbeat_at)))
    (directory / "doreq_cache.txt").write_text(json.dumps(list(cached)))
    node.cache_config = SimpleNamespace(heart_beat_log_file_path=directory / "heartbeat.etny",
                                        accepted_handover_filepath=directory / "accepted.json")
    node.storage = SimpleNamespace(connect=lambda attempts: True, repo_gc=lambda: None)
    node.orders_cache = Cache(ITEMS, directory / "orders_cache.txt")
    node.doreq_cache = ListCache(ITEMS, directory / "doreq_cache.txt")
    node.resource_refusals = ResourceRefusals(ITEMS, directory / "doreq_resources.json")
    node._EtnyPoXNode__address = ADDRESS
    node._EtnyPoXNode__acct = SimpleNamespace(address=ADDRESS)
    node._EtnyPoXNode__dprequest = DP_REQUEST
    node._EtnyPoXNode__heart_beat = chain
    node._EtnyPoXNode__replication_only = False
    node._EtnyPoXNode__trustedzones = config.trustedzone_requirements(network.trustedzone_images)
    node._EtnyPoXNode__pox_is_20 = False
    node._EtnyPoXNode__reader = None
    node._EtnyPoXNode__broker = None
    node._EtnyPoXNode__started_row_pending = None
    node._EtnyPoXNode__do_requests_build_pending = False
    node._EtnyPoXNode__last_ipfs_cleanup_at = time.time()
    node._EtnyPoXNode__last_ipfs_peer_sync_at = time.time()
    node._EtnyPoXNode__do_request_ages = None
    node._EtnyPoXNode__do_request_boundary = 0
    monkeypatch.setattr(agent, "do_request_age_logged", {})

    def send(transaction):
        chain.events.append("sent")
        raise Sent

    node.send_transaction = send
    node._check_installed_drivers = lambda: False
    return node


def idle_ticks(agent, monkeypatch, directory, network, ticks, cached=range(ITEMS)):
    """The events of process_dp_request on an idle node of `network`
    (idle_node, its heartbeat sent now, the DO requests `cached` in its
    doreq_cache.txt), the loop stopped at the `ticks`-th read of the DO
    request count."""
    stop = threading.Event()
    counts = []

    def counted():
        counts.append(None)
        if len(counts) == ticks:
            stop.set()

    chain = Chain(counted=counted)
    idle_node(agent, monkeypatch, directory, network, chain, stop, heartbeat_at=time.time(),
              cached=cached).process_dp_request()
    return chain.events


def scan(delay, *reads):
    """The events of a scan of the SCANNED newest of ITEMS orders or DO
    requests, the newest first: a sleep of `delay` seconds before each, then
    its `reads`."""
    return [event for item in range(ITEMS - 1, ITEMS - 1 - SCANNED, -1)
            for event in (delay, *(f"{read}({item})" for read in reads))]


def reads(events):
    return [event for event in events if isinstance(event, str)]


def sleeps(events):
    return [event for event in events if not isinstance(event, str)]


def posted(read, posts):
    """The requests web3 posts for a recorded read: those of its method for
    a read of web3's eth namespace, those of a contract read otherwise."""
    return len(posts[read] if read in posts else posts["read"])


def in_60_s(events, posts, answer):
    """The requests a loop posts in 60 s while it repeats `events` and a
    request takes `answer` seconds to answer. The model: a loop posts one
    request at a time, so it repeats `events` in the sum of their sleeps and
    of their requests' answer times; the rates of two loops add; the RPC's
    limit is a sustained rate of LIMIT requests in 60 s, which every request
    posted counts against."""
    count = sum(posted(read, posts) for read in reads(events))
    return 60 * count / (sum(sleeps(events)) + count * answer)


def test_the_releases_file_gives_each_network_its_delay(monkeypatch):
    delays = {name: load(monkeypatch, name).rpc_delay for name in config.available_networks()}
    assert delays == DELAYS


def test_polygon_mainnet_takes_200_ms_on_a_block_time_of_2_s_as_polygon_amoy(monkeypatch):
    mainnet = load(monkeypatch, "polygon_mainnet")
    amoy = load(monkeypatch, "polygon_amoy")
    assert (mainnet.rpc_delay, mainnet.block_time) == (200, 2)
    assert (amoy.rpc_delay, amoy.block_time) == (200, 2)


def test_a_variable_of_the_environment_stands_before_the_files(monkeypatch):
    delays = [load(monkeypatch, "polygon_mainnet", environment).rpc_delay
              for environment in ({}, {VARIABLE: "50"}, {VARIABLE: "0"})]
    assert delays == [200, 50, 0]
    # A variable that is no integer is refused: the file's 200 does not stand
    # in for it.
    for text in ("abc", "", "0.2"):
        with pytest.raises(EnvironmentError, match="Invalid integer value for POLYGON_MAINNET_RPC_DELAY"):
            load(monkeypatch, "polygon_mainnet", {VARIABLE: text})


def test_the_argument_stands_before_the_environment_and_the_file(monkeypatch):
    delays = [load(monkeypatch, "polygon_mainnet", environment, arguments).rpc_delay
              for environment, arguments in (({}, ()), ({}, (ARGUMENT, "350")), ({VARIABLE: "50"}, (ARGUMENT, "350")),
                                             ({VARIABLE: "50"}, (ARGUMENT, "0")))]
    assert delays == [200, 350, 350, 0]
    # An argument that is no integer ends the parse: neither the
    # environment's 50 nor the file's 200 stands in for it.
    with pytest.raises(SystemExit):
        load(monkeypatch, "polygon_mainnet", {VARIABLE: "50"}, (ARGUMENT, "abc"))


def test_a_process_started_in_node_loads_the_files_delay_and_the_environments_before_it():
    assert [started(), started("50")] == [200, 50]


def test_a_replication_round_on_polygon_mainnet_posts_3014_requests_after_800_sleeps_of_the_delay(
        agent, monkeypatch, tmp_path, posts):
    # polygon_mainnet's configuration names no Enclave State Registry, CAS
    # session registry or validator registry: the round reads none of them,
    # and its peer sync reads no validator's IPFS endpoint from the chain.
    for addresses in (config.esr_contract_addresses, config.cas_session_registry_addresses,
                      config.validator_registry_addresses):
        assert "POLYGON_MAINNET" not in addresses
    assert posts == {"read": ["eth_chainId", "eth_call", "eth_chainId"], "eth_getBalance": ["eth_getBalance"],
                     "eth_blockNumber": ["eth_blockNumber"], "eth_getLogs": ["eth_getLogs"],
                     "eth_getStorageAt": ["eth_getStorageAt"], "eth_getBlockByNumber": ["eth_getBlockByNumber"]}
    events = replication_round(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet"))
    results = ["_getOrdersCount"] + scan(0.2, "_getOrder")
    inputs = ["_getDORequestsCount"] + scan(0.2, "_getDORequest") + ["_getOrdersCount"] + scan(0.2, "_getOrder")
    sessions = ["_getOrdersCount"] + scan(0.2, "_getOrder", "_getDORequestMetadata")
    assert events == (results + inputs + sessions + ["eth_blockNumber", "eth_getLogs"]
                      + [config.esr_replication_interval_seconds])
    work = events[:-1]
    assert len([read for read in reads(work) if read not in posts]) == 1004
    assert (sum(posted(read, posts) for read in reads(work)), len(sleeps(work))) == (3014, 800)
    assert sum(sleeps(work)) == pytest.approx(160)
    # The same round at 10 ms, given through the environment.
    work = replication_round(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet", {VARIABLE: "10"}))[:-1]
    assert len(sleeps(work)) == 800
    assert sum(sleeps(work)) == pytest.approx(8)


def test_a_round_sleeps_200_times_for_the_do_requests_and_3_times_for_each_order_up_to_200(
        agent, monkeypatch, tmp_path, posts):
    # Beside the newest 200 of the 241 DO requests, the round reads the newest
    # 200 orders, or all of fewer, in three scans: 204 + 4 min(n, 200)
    # contract reads after 200 + 3 min(n, 200) sleeps of the delay.
    network = load(monkeypatch, "polygon_mainnet")
    for orders in (0, 1, 150, 199, 200, ITEMS):
        work = replication_round(agent, monkeypatch, tmp_path, network, orders)[:-1]
        counted = min(orders, SCANNED)
        assert (len([read for read in reads(work) if read not in posts]), len(sleeps(work))) == (
            204 + 4 * counted, 200 + 3 * counted), orders
    # 150 orders: 650 sleeps, 130 s at 200 ms where 10 ms gives 6.5 s.
    assert sum(sleeps(replication_round(agent, monkeypatch, tmp_path, network, 150)[:-1])) == pytest.approx(130)
    network = load(monkeypatch, "polygon_mainnet", {VARIABLE: "10"})
    assert sum(sleeps(replication_round(agent, monkeypatch, tmp_path, network, 150)[:-1])) == pytest.approx(6.5)


def test_the_idle_tick_reads_the_balance_and_the_count_after_0_7_s_and_the_delay(agent, monkeypatch, tmp_path):
    events = idle_ticks(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet"), ticks=3)
    assert events == (["_getMyDOOrders", 0.2, f"_getDPRequest({DP_REQUEST})", "getNodesCount"]
                      + [0.7, "eth_getBalance", 0.2, "_getDORequestsCount"] * 3)
    assert sum(sleeps(events[-4:])) == pytest.approx(0.9)
    # The same ticks at 10 ms, given through the environment.
    events = idle_ticks(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet", {VARIABLE: "10"}), ticks=3)
    assert events[4:] == [0.7, "eth_getBalance", 0.01, "_getDORequestsCount"] * 3
    assert sum(sleeps(events[-4:])) == pytest.approx(0.71)


def test_the_tick_that_places_a_new_do_request_sleeps_the_delay_before_each_read_up_to_the_send(
        agent, monkeypatch, tmp_path):
    def placing(environment, bound):
        """The events of the first tick, which finds DO request ITEMS new,
        AVAILABLE and unpinned, through the send of its placement; the
        storage of the DO requests' ages is bound before the tick when
        `bound`."""
        chain = Chain(requests=ITEMS + 1, available={ITEMS})
        node = idle_node(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet", environment), chain,
                         threading.Event(), heartbeat_at=time.time())
        if bound:
            node._EtnyPoXNode__do_request_ages = (STORAGE_CHECKSUM, do_request_age.CLOUD)
        with pytest.raises(Sent):
            node.process_dp_request()
        return chain.events[4:]

    placement = f"_placeOrder({ITEMS}, {DP_REQUEST})"
    request = [f"_getDORequest({ITEMS})", f"_getDORequestMetadata({ITEMS})", "eth_blockNumber",
               0.2, f"_getDORequest({ITEMS})",
               0.2, f"{placement} simulated", "eth_getTransactionCount", "eth_getBlockByNumber",
               f"{placement} built", "sent"]
    # A handle's first boundary: the latest block, ethStorageAddress(), the
    # storage's length and newest request, then request 0, the boundary
    # before, created within the limit.
    events = placing({}, bound=False)
    assert events == [0.7, "eth_getBalance", 0.2, "_getDORequestsCount",
                      0.2, "eth_getBlockByNumber", 0.2, "ethStorageAddress", 0.2, "eth_getStorageAt",
                      0.2, "eth_getStorageAt", 0.2, "eth_getStorageAt"] + request
    # A bound handle: the latest block and the boundary before.
    bound = placing({}, bound=True)
    assert bound == [0.7, "eth_getBalance", 0.2, "_getDORequestsCount",
                     0.2, "eth_getBlockByNumber", 0.2, "eth_getStorageAt"] + request

    # After the tick's 0.7 s: the delay before the count, before each read
    # of the boundary, before the request's status and before the
    # placement's simulation; all but the first follow the count read that
    # sees the request.
    def delays(events):
        seen = events.index("_getDORequestsCount")
        return sum(sleeps(events[1:])), sum(sleeps(events[seen:]))

    assert delays(events) == pytest.approx((1.6, 1.4))
    assert delays(bound) == pytest.approx((1.0, 0.8))
    # The same ticks at 10 ms, given through the environment.
    assert delays(placing({VARIABLE: "10"}, bound=False)) == pytest.approx((0.08, 0.07))
    assert delays(placing({VARIABLE: "10"}, bound=True)) == pytest.approx((0.05, 0.04))


def test_a_due_heartbeat_is_built_after_the_ticks_sleep_and_the_delay(agent, monkeypatch, tmp_path):
    def beating(environment):
        """The events of the first tick, whose heartbeat is due, through
        the send of the heartbeat."""
        chain = Chain()
        node = idle_node(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet", environment), chain,
                         threading.Event(), heartbeat_at=0)
        with pytest.raises(Sent):
            node.process_dp_request()
        return chain.events[4:]

    assert beating({}) == [0.7, 0.2, "eth_getTransactionCount", "eth_getBlockByNumber", "logCall built", "sent"]
    assert beating({VARIABLE: "10"})[:2] == [0.7, 0.01]


def test_the_first_pass_of_a_node_without_doreq_cache_sleeps_the_delay_once_a_do_request(
        agent, monkeypatch, tmp_path, posts):
    def first_pass(environment, delay):
        """The events of the first tick of a node whose doreq_cache.txt holds
        no DO request, after its read of the count; the next tick, whose
        count read stops the loop, finds every request cached."""
        events = idle_ticks(agent, monkeypatch, tmp_path, load(monkeypatch, "polygon_mainnet", environment), ticks=2,
                            cached=())
        tick = [0.7, "eth_getBalance", delay, "_getDORequestsCount"]
        assert events[4:8] == events[-4:] == tick
        return events[8:-4]

    # The age boundary, each read after the delay: the latest block,
    # ethStorageAddress(), the storage's length and newest request, and
    # request 0, created within the limit. Then each of the 241 BOOKED,
    # unpinned DO requests: the request and its metadata, the block number of
    # the node's dispersion turn, the delay, and the request again, which
    # caches it as matched.
    work = first_pass({}, 0.2)
    boundary = [0.2, "eth_getBlockByNumber", 0.2, "ethStorageAddress", 0.2, "eth_getStorageAt",
                0.2, "eth_getStorageAt", 0.2, "eth_getStorageAt"]
    assert work == boundary + [event for request in range(ITEMS - 1, -1, -1)
                               for event in (f"_getDORequest({request})", f"_getDORequestMetadata({request})",
                                             "eth_blockNumber", 0.2, f"_getDORequest({request})")]
    assert sum(posted(read, posts) for read in reads(boundary)) == 7
    assert sum(posted(read, posts) for read in reads(work)) == 10 * ITEMS + 7
    # 49.2 s of sleep where 10 ms gives 2.46 s: 46.74 s more.
    assert sum(sleeps(work)) == pytest.approx(49.2)
    assert sum(sleeps(first_pass({VARIABLE: "10"}, 0.01))) == pytest.approx(2.46)


def test_beside_the_idle_tick_a_round_keeps_to_1200_requests_in_60_s_from_29_ms_a_request(
        agent, monkeypatch, tmp_path, posts):
    def loops(environment):
        """The events of one DO request of a round's input scan, of one order
        of its session scan and of one idle tick."""
        network = load(monkeypatch, "polygon_mainnet", environment)
        events = replication_round(agent, monkeypatch, tmp_path, network)
        request = events.index("_getDORequest(240)")
        order = events.index("_getDORequestMetadata(240)")
        tick = idle_ticks(agent, monkeypatch, tmp_path, network, ticks=1)[-4:]
        return events[request - 1:request + 1], events[order - 2:order + 1], tick

    def both(scanned, tick, answer):
        """The requests in 60 s of a scan, `scanned` after `scanned`, beside
        the idle tick's, when a request takes `answer` seconds."""
        return in_60_s(scanned, posts, answer) + in_60_s(tick, posts, answer)

    request, order, tick = loops({})
    assert request == [0.2, "_getDORequest(240)"]
    assert order == [0.2, "_getOrder(240)", "_getDORequestMetadata(240)"]
    assert tick == [0.7, "eth_getBalance", 0.2, "_getDORequestsCount"]
    # If the RPC answered at once: 15 requests a second in the result and
    # input scans, 30 in the session scan, 4 in 0.9 s in the idle tick. The
    # result and input scans keep to the limit beside the idle tick whatever
    # the RPC's answer time; the session scan once a request takes 29 ms.
    assert in_60_s(request, posts, 0) == pytest.approx(60 * 15)
    assert in_60_s(order, posts, 0) == pytest.approx(60 * 30)
    assert in_60_s(tick, posts, 0) == pytest.approx(60 * 4 / 0.9)
    assert both(request, tick, 0) <= LIMIT
    assert both(order, tick, 0.029) <= LIMIT < both(order, tick, 0.028)
    # The same loops at 10 ms, given through the environment: the RPC's
    # answers alone pace the scans, and the session scan keeps to the limit
    # beside the idle tick once a request takes 62 ms.
    request, order, tick = loops({VARIABLE: "10"})
    assert in_60_s(request, posts, 0) == pytest.approx(60 * 300)
    assert in_60_s(order, posts, 0) == pytest.approx(60 * 600)
    assert both(order, tick, 0.062) <= LIMIT < both(order, tick, 0.061)
    # At 400 ms the session scan keeps to the limit whatever the RPC's answer
    # time.
    request, order, tick = loops({VARIABLE: "400"})
    assert both(order, tick, 0) <= LIMIT
