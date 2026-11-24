"""The keeper calls' gas limit (MATCHING-SYNC-PLAN.md A13; OD28: estimate
plus 50 %; pox-smart-contract-litvm 52e36e0 ReservationKeepers.sol): the
superviseWith of an unmatched reservation's hint and the supervise of a
handover window past toBlock are built with broker.keeper_gas_limit of
their estimate as the transaction's gas; another broker call is built
without one, its limit the node's build."""
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import broker
from models import RequestStatus

NODE = Path(__file__).resolve().parents[1]
ADDRESS = "0x" + "0a" * 20
ESTIMATE = 412_345


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Call:
    """A bound contract call: its pre-flight answers True, its estimate
    ESTIMATE, and its built transactions are kept."""

    def __init__(self):
        self.built = []

    def call(self, fields):
        return True

    def estimate_gas(self, fields):
        return ESTIMATE

    def build_transaction(self, opts):
        self.built.append(dict(opts))
        return opts


def keeper_node(agent, fn):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__address = ADDRESS
    node._EtnyPoXNode__network_config = SimpleNamespace(rpc_delay=0)
    node._EtnyPoXNode__dprequest = 7
    node._EtnyPoXNode__started_reservations = {}
    node._EtnyPoXNode__broker_params_read = broker.BrokerParams(*([0] * len(broker.BrokerParams._fields)))
    node._EtnyPoXNode__etny = SimpleNamespace(caller=lambda: SimpleNamespace(
        _getDPRequest=lambda request_id: (ADDRESS, 4, 8, 40, 100, 24, 1, RequestStatus.AVAILABLE)))
    node._EtnyPoXNode__broker = SimpleNamespace(
        functions=SimpleNamespace(superviseWith=lambda reservation, dp: fn, supervise=lambda reservation: fn),
        caller=lambda: SimpleNamespace(nextDue=lambda reservation: 0))
    node.get_transaction_build = lambda: {'from': ADDRESS, 'nonce': 3}
    node.send_transaction = lambda txn: "0x" + "ab" * 32
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=SimpleNamespace(
        wait_for_transaction_receipt=lambda h, timeout: SimpleNamespace(status=1)))
    return node


def test_the_keeper_limit_is_the_estimate_plus_half_rounded_up():
    assert broker.KEEPER_GAS_MARGIN_PERCENT == 50
    assert broker.keeper_gas_limit(100) == 150
    assert broker.keeper_gas_limit(101) == 152
    assert broker.keeper_gas_limit(ESTIMATE) == 618_518


def test_the_supervise_hint_carries_the_keeper_limit(agent):
    fn = Call()
    node = keeper_node(agent, fn)
    assert node._EtnyPoXNode__supervise_hint(5, 'unmatched', broker.NEVER) is True
    assert [t['gas'] for t in fn.built] == [broker.keeper_gas_limit(ESTIMATE)]


def test_the_supervise_past_the_window_carries_the_keeper_limit(agent):
    fn = Call()
    node = keeper_node(agent, fn)
    node._EtnyPoXNode__order_handover = {'reservation': 5, 'supervise_after': 0, 'supervised': False}
    node._EtnyPoXNode__supervise_past_window(9)
    assert node._EtnyPoXNode__order_handover['supervised'] is True
    assert [t['gas'] for t in fn.built] == [broker.keeper_gas_limit(ESTIMATE)]


def test_another_broker_call_takes_the_nodes_build(agent):
    fn = Call()
    node = keeper_node(agent, fn)
    node._EtnyPoXNode__broker_tx("commitReserved(1)", fn)
    assert fn.built == [{'from': ADDRESS, 'nonce': 3}]
