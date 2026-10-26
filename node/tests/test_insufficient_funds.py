"""utils.insufficient_funds: an RPC's refusal of a transaction for the
sender's balance, in OpenEthereum's and geth's words (bloxberg testnet,
2026-10-25: a node whose wallet ran dry mid-session retried its result
transaction twenty times in 100 s and abandoned the order)."""
from utils import insufficient_funds


def test_openethereums_refusal_is_recognised_in_the_dict_web3_raises_it_as():
    error = ValueError({'code': -32010, 'message': 'Insufficient funds. The account you tried to send transaction '
                                                   'from does not have enough funds. Required 4000000000000 and got: '
                                                   '2524792000000.'})
    assert insufficient_funds(error)


def test_geths_refusal_is_recognised():
    assert insufficient_funds(ValueError('insufficient funds for gas * price + value'))


def test_other_refusals_are_not():
    assert not insufficient_funds(ValueError({'code': -32010, 'message': 'Transaction nonce is too low.'}))
    assert not insufficient_funds(ValueError('execution reverted: idx invalid'))
