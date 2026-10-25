"""utils.revert_reason: the reason a contract revert carried, from web3's
ContractLogicError, and None for a revert without data, which an RPC replica
behind the chain answers for a call about a request it has not seen yet
(bloxberg testnet, 2026-10-25: two nodes took such an answer, nine seconds
after creating their DP requests, for a refusal of the next DO request and
left its client waiting 1800 s)."""
from web3.exceptions import ContractLogicError

from utils import revert_reason


def test_a_revert_without_data_has_no_reason():
    assert revert_reason(ContractLogicError('execution reverted: ', 'Reverted 0x')) is None
    assert revert_reason(ContractLogicError('execution reverted: ', '0x')) is None
    assert revert_reason(ContractLogicError('execution reverted: ')) is None
    assert revert_reason(ContractLogicError('execution reverted')) is None


def test_a_reason_string_is_the_reason():
    error = ContractLogicError('execution reverted: DO request already matched', '0x08c379a0' + '00' * 36)
    assert revert_reason(error) == 'DO request already matched'


def test_data_without_a_reason_string_is_returned_as_hex():
    assert revert_reason(ContractLogicError('execution reverted: ', 'Reverted 0x1234abcd')) == '0x1234abcd'
    assert revert_reason(ContractLogicError('execution reverted: ', '0x1234abcd')) == '0x1234abcd'


def test_another_message_is_the_reason_as_it_stands():
    assert revert_reason(ContractLogicError('node address invalid')) == 'node address invalid'
