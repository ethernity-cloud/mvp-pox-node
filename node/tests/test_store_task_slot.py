"""The task slot around the store's users (etny-node.py process_dp_request,
acquire_task_slot, release_task_slot; store_access.sweep, prepare). The
start and the end of an enclave environment remove every user of the store,
whoever made it, so each runs under the task slot, which one order or one
integration test holds at a time for all networks: an order found PROCESSING
at a start of the agent is run under the slot as a placed one is, and every
place of etny-node.py that reaches the store's users is under it."""
import ast
import importlib.util
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

NODE = Path(__file__).resolve().parents[1]
NETWORK = "bloxberg_testnet"
OTHER_NETWORK = "polygon_amoy"
ORDER = 74


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resumed:
    """An EtnyPoXNode of NETWORK built without __init__ whose DP request
    holds order ORDER, PROCESSING, placed now on a DO request of one hour.
    The order's run is a stand-in: `ran` holds, for each run, the order and
    the network that holds the task slot then; the run raises `fails` when
    that is set."""

    def __init__(self, agent, monkeypatch):
        self.ran = []
        self.fails = None
        order = agent.Order(("0xd0", "0xd1", 3, 5, agent.OrderStatus.PROCESSING))
        chain = SimpleNamespace(_getDORequest=lambda do_req: ("0xd0", 1, 1, 1, 1, 1, 1, agent.RequestStatus.BOOKED))
        monkeypatch.setattr(agent, "order_placed_at", lambda orders_dir, order_id: time.time())
        node = object.__new__(agent.EtnyPoXNode)
        node.logger = logging.getLogger("slot")
        node._getOrder = lambda: [ORDER, order]
        node.dpreq_cache = SimpleNamespace(add=lambda request: None)
        node._EtnyPoXNode__network = NETWORK
        node._EtnyPoXNode__network_config = SimpleNamespace(block_time=2, rpc_delay=0)
        node._EtnyPoXNode__dprequest = 5
        node._EtnyPoXNode__etny = SimpleNamespace(caller=lambda: chain)

        def run(order_id):
            self.ran.append((order_id, agent.get_task_running_on()))
            if self.fails is not None:
                raise self.fails

        node.process_order = run
        self.resume = node.process_dp_request


@pytest.fixture
def resumed(agent, monkeypatch):
    assert agent.get_task_running_on() is None and not agent.stop_event.is_set()
    yield Resumed(agent, monkeypatch)
    agent.stop_event.clear()
    agent.reset_task_running_on()


def test_an_order_resumed_at_a_start_runs_under_the_task_slot(agent, resumed):
    resumed.resume()
    assert resumed.ran == [(ORDER, NETWORK)]
    assert agent.get_task_running_on() is None


def test_a_resumed_order_whose_run_raises_frees_the_task_slot(agent, resumed):
    resumed.fails = RuntimeError("the order's run failed")
    with pytest.raises(RuntimeError) as failure:
        resumed.resume()
    assert failure.value is resumed.fails
    assert resumed.ran == [(ORDER, NETWORK)]
    assert agent.get_task_running_on() is None


def test_a_resumed_order_waits_for_the_task_slot_another_network_holds(agent, resumed):
    agent.acquire_task_slot(OTHER_NETWORK)
    thread = threading.Thread(target=resumed.resume, daemon=True)
    thread.start()
    thread.join(0.3)
    assert thread.is_alive() and resumed.ran == []
    assert agent.get_task_running_on() == OTHER_NETWORK
    assert agent.release_task_slot(OTHER_NETWORK)
    thread.join(10)
    assert not thread.is_alive()
    assert resumed.ran == [(ORDER, NETWORK)]
    assert agent.get_task_running_on() is None


def test_an_agent_asked_to_stop_while_it_waits_for_the_slot_does_not_run_the_order(agent, resumed):
    agent.acquire_task_slot(OTHER_NETWORK)
    thread = threading.Thread(target=resumed.resume, daemon=True)
    thread.start()
    thread.join(0.3)
    assert thread.is_alive()
    agent.stop_event.set()
    assert agent.release_task_slot(OTHER_NETWORK)
    thread.join(10)
    assert not thread.is_alive()
    assert resumed.ran == []
    assert agent.get_task_running_on() is None


# What reaches the store's users in etny-node.py: the sweep, the start's
# preparation, and the construction of the store service, whose first in a
# process sweeps.
STORE_USERS = ("store_access.sweep", "store_access.prepare", "SwiftStreamService")
SLOT_CALLS = ("acquire_task_slot", "release_task_slot")
LEAVES = (ast.Break, ast.Continue, ast.Return, ast.Raise)


def text_of(node):
    """`name` of a name and `name.attribute` of an attribute of a name (the
    source's spelling: self.__run_order); None for any other node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def slot_statements(function):
    """[((line, column), call, leaves)] of the statements of `function` that
    call acquire_task_slot or release_task_slot, in the source's order;
    `leaves` says that the block the statement is in ends in a break, a
    continue, a return or a raise."""
    found = []
    for node in ast.walk(function):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for statement in block:
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) \
                        and text_of(statement.value.func) in SLOT_CALLS:
                    found.append(((statement.lineno, statement.col_offset), text_of(statement.value.func),
                                  isinstance(block[-1], LEAVES)))
    return sorted(found)


def under_the_slot(function, node):
    """Whether `node` of `function` comes after a statement that takes the
    task slot with, between the two, no statement that frees the slot and
    goes on: every release in between is in a block that is left."""
    before = [statement for statement in slot_statements(function) if statement[0] < (node.lineno, node.col_offset)]
    taken = [index for index, statement in enumerate(before) if statement[1] == "acquire_task_slot"]
    return bool(taken) and all(leaves for _, _, leaves in before[taken[-1] + 1:])


def slot_users(methods):
    """(held, takers) over `methods`, {name: FunctionDef} of EtnyPoXNode.
    `held` are the methods that name one of STORE_USERS, or a method of
    `held`, at a place that is not under_the_slot: the slot is their
    caller's to hold. `takers` are the methods that name one under a slot
    they took themselves."""
    named = {}
    for name, method in methods.items():
        for node in ast.walk(method):
            if text_of(node) is not None:
                named.setdefault(text_of(node), []).append((name, node))
    held, takers, pending = set(), set(), list(STORE_USERS)
    while pending:
        for name, node in named.get(pending.pop(), []):
            if under_the_slot(methods[name], node):
                takers.add(name)
            elif name not in held:
                held.add(name)
                pending.append(f"self.{name}")
    return held, takers


def test_every_place_that_reaches_the_stores_users_is_under_the_task_slot():
    tree = ast.parse((NODE / "etny-node.py").read_text(encoding="utf-8"))
    node_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "EtnyPoXNode")
    methods = {node.name: node for node in node_class.body if isinstance(node, ast.FunctionDef)}
    held, takers = slot_users(methods)
    # The methods the slot's holder runs: the end and the start of an
    # enclave environment, the order and the integration test around them.
    assert held == {"__enclave_environment_down", "__prepare_store_credentials", "__start_enclave_environment",
                    "__order_result_sent", "__run_order", "process_order", "__clean_up_integration_test",
                    "__run_integration_test", "__run_integration_test_if_due", "__retest_requirement"}
    # The methods that take the slot for them: the construction of a handle
    # (the store's start, the integration tests), an order placed, resumed,
    # approved after its window or placed by the broker, and a test's retry.
    assert takers == {"__init__", "process_dp_request", "__take_up_approved_order", "__approve_and_run",
                      "__maybe_retry_integration_tests"}
    assert sorted(name for name in held if slot_statements(methods[name])) == []
    # Nothing outside the class's methods names one of them.
    inside = {id(node) for method in methods.values() for node in ast.walk(method)}
    outside = [node for node in ast.walk(tree) if id(node) not in inside]
    assert [text_of(node) for node in outside if text_of(node) in STORE_USERS] == []
    assert [node.attr for node in outside if isinstance(node, ast.Attribute) and node.attr in held] == []
    # The order's four runs: placed by the broker, resumed, placed by this
    # node, approved after its window.
    runs = [(name, under_the_slot(methods[name], node)) for name, method in methods.items()
            for node in ast.walk(method) if text_of(node) == "self.process_order"]
    assert runs == [("__approve_and_run", True), ("process_dp_request", True), ("process_dp_request", True),
                    ("__take_up_approved_order", True)]
