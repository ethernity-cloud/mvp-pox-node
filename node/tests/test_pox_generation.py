"""pox12 (PHASE-E-PLAN.md E6, B.1): pricingVersion() answering makes the
contract 1.2, a revert or an undecodable answer legacy; chain_handles binds
the reader, the pricing, the broker and its token, None for a zero address
and no token without a broker."""
from types import SimpleNamespace

import pytest
from eth_abi.exceptions import InsufficientDataBytes
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

import pox12

READER = "0x1111111111111111111111111111111111111111"
PRICING = "0x2222222222222222222222222222222222222222"
BROKER = "0x3333333333333333333333333333333333333333"
TOKEN = "0x4444444444444444444444444444444444444444"
ABIS = {"reader": "[reader]", "pricing": "[pricing]", "broker": "[broker]", "erc20": "[erc20]"}


def raising(error):
    def call():
        raise error
    return call


def test_a_returning_call_is_a_12_contract():
    assert pox12.pox_generation(lambda: 1) is True


def test_a_reverting_call_is_legacy():
    assert pox12.pox_generation(raising(ContractLogicError("execution reverted"))) is False


def test_an_undecodable_answer_is_legacy():
    assert pox12.pox_generation(raising(InsufficientDataBytes("0 bytes"))) is False
    assert pox12.pox_generation(raising(BadFunctionCallOutput("no code"))) is False


def test_an_rpc_failure_propagates():
    with pytest.raises(ConnectionError):
        pox12.pox_generation(raising(ConnectionError("refused")))


class FakeContract:
    """A contract whose functions answer the values of `answers` on call()."""

    def __init__(self, address, answers):
        self.address = address
        self.answers = answers
        self.functions = SimpleNamespace(**{
            name: (lambda value: (lambda: SimpleNamespace(call=lambda: value)))(value)
            for name, value in answers.items()})


class FakeW3:
    """Records every contract bound through eth.contract."""

    def __init__(self, token_of_broker=TOKEN):
        self.bound = []
        self.token_of_broker = token_of_broker

    def to_checksum_address(self, address):
        return address

    @property
    def eth(self):
        w3 = self

        class Eth:
            def contract(self, address, abi):
                w3.bound.append((address, abi))
                answers = {"token": w3.token_of_broker} if abi == ABIS["broker"] else {}
                return FakeContract(address, answers)
        return Eth()


def pox(reader=READER, pricing=PRICING, broker=BROKER):
    return FakeContract("0x" + "ee" * 20, {
        "readerAddress": reader, "pricingAddress": pricing, "reservationBrokerAddress": broker})


def test_every_handle_is_bound_under_its_abi():
    w3 = FakeW3()
    handles = pox12.chain_handles(pox(), w3, ABIS)
    assert {name: handle.address for name, handle in handles.items()} == {
        "reader": READER, "pricing": PRICING, "broker": BROKER, "token": TOKEN}
    assert w3.bound == [(READER, "[reader]"), (PRICING, "[pricing]"), (BROKER, "[broker]"), (TOKEN, "[erc20]")]


def test_a_zero_broker_binds_no_broker_and_no_token():
    w3 = FakeW3()
    handles = pox12.chain_handles(pox(broker=pox12.ZERO_ADDRESS), w3, ABIS)
    assert handles["broker"] is None
    assert handles["token"] is None
    assert handles["reader"].address == READER
    assert handles["pricing"].address == PRICING
    assert [abi for _, abi in w3.bound] == ["[reader]", "[pricing]"]


def test_a_zero_reader_binds_no_reader():
    handles = pox12.chain_handles(pox(reader=pox12.ZERO_ADDRESS), FakeW3(), ABIS)
    assert handles["reader"] is None
    assert handles["pricing"].address == PRICING
    assert handles["broker"].address == BROKER
