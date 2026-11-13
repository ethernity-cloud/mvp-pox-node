"""gas_ledger under a gasPriceCapWei of 0 (PHASE-D-PLAN.md divergence 22: a
1.2 order on a chain without a base fee): every recorded cost is 0, as
chain_fee.js chainCost prices it (cappedGasPrice gives the cap, 0, and the
rows and the result are priced at the cap) and as the PoX accrues the rows
at min(tx.gasprice, cap); the counter is never over any chainFeeWei, so no
relay is refused there, and no gas wait holds."""
from gas_ledger import OrderGasLedger, OrderTerms, PricingParams, WEI_PER_TOKEN, capped_price, gas_wait_active

GWEI = 10**9
LIVE = 130 * GWEI
PARAMS = PricingParams(343834, 267445, 971708, 30 * GWEI)
TERMS = OrderTerms(10**15, 1000, 0, 0, 0, WEI_PER_TOKEN)


def chain_cost(commit_gas, live, rows, terms, params):
    """chain_fee.js chainCost in token wei."""
    price = min(live, terms.gas_price_cap_wei)
    gas_token_wei = sum(gas * price for gas in commit_gas)
    gas_token_wei += rows * params.gas_per_row * terms.gas_price_cap_wei
    gas_token_wei += params.gas_per_result * terms.gas_price_cap_wei
    return gas_token_wei * terms.token_per_pol // WEI_PER_TOKEN


def test_the_capped_price_is_zero_under_a_zero_cap():
    assert capped_price(LIVE, 0) == 0
    assert capped_price(0, 0) == 0


def test_commits_rows_and_the_reserve_cost_nothing_and_match_chain_cost():
    ledger = OrderGasLedger(5, TERMS, PARAMS, 10**17)
    assert ledger.recorded
    assert ledger.commit_cost(120000, LIVE) == 0
    assert ledger.rows_cost(3, 400000, LIVE) == 0
    assert ledger.reserve() == 0
    assert ledger.total() == chain_cost([120000] * 3, LIVE, 3, TERMS, PARAMS) == 0


def test_no_relay_is_refused_under_a_zero_cap_whatever_the_chain_fee():
    ledger = OrderGasLedger(5, TERMS, PARAMS, 10**17)
    for _ in range(3):
        ledger.charge(ledger.rows_cost(1, 400000, LIVE))
    ledger.charge_commits(["esr.commit.k.0.json", "esr.commit.k.1.json"],
                          [ledger.commit_cost(120000, LIVE)] * 2)
    assert ledger.spent == 0
    assert ledger.admits(ledger.commit_cost(120000, LIVE))
    assert ledger.budget_wei() == 0


def test_the_receipt_settles_a_zero_cap_commit_at_its_real_cost():
    ledger = OrderGasLedger(5, TERMS, PARAMS, 10**17)
    ledger.charge_commits(["esr.commit.k.0.json"], [0])
    ledger.settle(["esr.commit.k.0.json"], 95000 * LIVE)
    assert ledger.spent == 95000 * LIVE


def test_no_gas_wait_holds_under_a_zero_cap():
    assert gas_wait_active(200 * GWEI, PARAMS.priority_fee_wei, 0, 1_800_000_000, None) is False
