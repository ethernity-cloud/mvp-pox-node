"""utils.transaction_known: a pool's answer that it holds the transaction
already, in OpenEthereum's and geth's words (bloxberg testnet, 2026-10-26: a
placement's receipt wait timed out, the retry sent the same transaction, the
pool answered so, and the node took its own order for another node's)."""
from utils import transaction_known


def test_openethereums_answer_is_recognised_in_the_dict_web3_raises_it_as():
    assert transaction_known(ValueError({'code': -32010, 'message': 'Transaction with the same hash was already imported.'}))


def test_geths_answer_is_recognised():
    assert transaction_known(ValueError('already known'))


def test_other_refusals_are_not():
    assert not transaction_known(ValueError({'code': -32010, 'message': 'Transaction nonce is too low.'}))
    assert not transaction_known(ValueError({'code': -32010, 'message': 'Insufficient funds. The account you tried to '
                                                                        'send transaction from does not have enough funds.'}))
