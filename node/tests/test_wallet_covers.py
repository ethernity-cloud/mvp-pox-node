"""utils.wallet_covers: whether a node's wallet pays WALLET_GAS_TRANSACTIONS
transactions at the gas limit and price it sends them with (bloxberg testnet,
2026-10-25: ten nodes ran their wallets down to a few thousand gwei, could not
create a DP request, and their clients waited 1800 s for a placement)."""
from utils import WALLET_GAS_TRANSACTIONS, wallet_covers


def test_two_transactions_at_the_limit_and_price_are_the_bar():
    assert WALLET_GAS_TRANSACTIONS == 2
    gas_limit, price = 3_000_000, 2_000_000
    assert wallet_covers(2 * gas_limit * price, gas_limit, price)
    assert not wallet_covers(2 * gas_limit * price - 1, gas_limit, price)


def test_the_number_of_transactions_can_be_given():
    assert wallet_covers(6_000_000_000_000, 3_000_000, 2_000_000, transactions=1)
    assert not wallet_covers(6_000_000_000_000, 3_000_000, 2_000_000, transactions=3)


def test_string_amounts_as_web3_returns_them_are_accepted():
    assert wallet_covers("500000000000000", 3_000_000, 2_000_000)
