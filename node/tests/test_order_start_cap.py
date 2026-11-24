"""An order whose recorded gasPriceCapWei is 0 is logged and not started
(MATCHING-SYNC-PLAN.md A18; gas_ledger.cap_refuses_start): the run sends no
started row for it; an order with a cap, and one the PoX did not record,
go on to the started row."""
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import gas_ledger

NODE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Started(Exception):
    """The run reached the started row."""


def terms(cap):
    return gas_ledger.OrderTerms(10**17, 250, 10**18, cap, 10**6, 10**18)


def test_a_recorded_order_with_a_cap_of_zero_is_not_started():
    assert gas_ledger.cap_refuses_start(terms(0)) is True
    assert gas_ledger.cap_refuses_start(terms(1)) is False
    assert gas_ledger.cap_refuses_start(None) is False


@pytest.mark.parametrize("order_terms, started", [(terms(0), False), (terms(25 * 10**9), True), (None, True)])
def test_the_run_sends_no_started_row_for_a_cap_of_zero(agent, monkeypatch, tmp_path, caplog, order_terms, started):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node.cache_config = SimpleNamespace(process_orders_cache_filepath=tmp_path / "process_order.json")
    node.process_order_data = {}
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__read_order_terms", lambda self, order_id: order_terms)

    def send_order_started(self, order_id):
        raise Started(order_id)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__send_order_started", send_order_started)
    caplog.set_level(logging.ERROR)
    metadata = ("0x" + "11" * 20, "v3:Qm:tz:compose:challenge:cert", "v3:payload", "v3:input", "")
    if started:
        with pytest.raises(Started):
            node._EtnyPoXNode__run_order(41, metadata)
    else:
        node._EtnyPoXNode__run_order(41, metadata)
        assert any("gasPriceCapWei is 0" in m for m in caplog.messages)
