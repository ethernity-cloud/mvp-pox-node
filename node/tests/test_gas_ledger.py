"""gas_ledger.OrderGasLedger judges one counter as the trustedzones do
(nodenithy chain_fee.js chainCost): on a recorded 1.2 order the rows at
gasPerRow x cap, the commits at their estimate x min(live, cap), the result
reserved at gasPerResult x cap, in token wei at tokenPerPol against
chainFeeWei; on an order without terms the network budget over rows and
commits at the live price, no reserve. The vector is result-v5.json's ESR
example (three commits at 120000 gas), three rows and the Amoy per-relay gas
(gasPerRow 343834, gasPerCommit 267445, gasPerResult 971708)."""
from gas_ledger import (COMMIT_GAS_FALLBACK, WEI_PER_TOKEN, OrderGasLedger, OrderTerms, PricingParams, capped_price,
                        order_terms, pricing_params)

GWEI = 10**9
CAP = 100 * GWEI
LIVE_UNDER_CAP = 80 * GWEI
LIVE_OVER_CAP = 130 * GWEI
PARAMS = PricingParams(343834, 267445, 971708, 30 * GWEI)
COMMIT_GAS = [COMMIT_GAS_FALLBACK] * 3
ROWS = 3


def chain_cost(commit_gas, live, rows, terms, params):
    """chain_fee.js chainCost: the token-wei cost of the counted set."""
    price = min(live, terms.gas_price_cap_wei)
    gas_token_wei = sum(gas * price for gas in commit_gas)
    gas_token_wei += rows * params.gas_per_row * terms.gas_price_cap_wei
    gas_token_wei += params.gas_per_result * terms.gas_price_cap_wei
    return gas_token_wei * terms.token_per_pol // WEI_PER_TOKEN


def terms_with(chain_fee_wei, token_per_pol=WEI_PER_TOKEN):
    return OrderTerms(10**15, 1000, chain_fee_wei, CAP, 0, token_per_pol)


def charged(ledger, live=LIVE_UNDER_CAP):
    for _ in range(ROWS):
        ledger.charge(ledger.rows_cost(1, 400000, live))
    names = [f"esr.commit.k.{i}.json" for i in range(3)]
    ledger.charge_commits(names, [ledger.commit_cost(gas, live) for gas in COMMIT_GAS])
    return ledger


def test_the_counter_equals_chain_fee_js_chain_cost_on_the_shared_vector():
    terms = terms_with(3 * 10**17)
    ledger = charged(OrderGasLedger(1, terms, PARAMS, 10**17))
    # 3 x 120000 x 80 gwei + 3 x 343834 x 100 gwei + 971708 x 100 gwei
    assert ledger.total() == chain_cost(COMMIT_GAS, LIVE_UNDER_CAP, ROWS, terms, PARAMS) == 229_121_000 * GWEI


def test_the_fixture_ledger_fits_under_the_fee_and_is_refused_over_it():
    cost = chain_cost(COMMIT_GAS, CAP, ROWS, terms_with(0), PARAMS)
    fits = OrderGasLedger(1, terms_with(cost), PARAMS, 10**17)
    for _ in range(ROWS):
        assert fits.admits(fits.rows_cost(1, 400000, CAP))
        fits.charge(fits.rows_cost(1, 400000, CAP))
    last = fits.commit_cost(COMMIT_GAS[-1], CAP)
    fits.charge_commits(["a", "b"], [fits.commit_cost(g, CAP) for g in COMMIT_GAS[:2]])
    assert fits.admits(last)
    refused = OrderGasLedger(1, terms_with(cost - 1), PARAMS, 10**17)
    for _ in range(ROWS):
        refused.charge(refused.rows_cost(1, 400000, CAP))
    refused.charge_commits(["a", "b"], [refused.commit_cost(g, CAP) for g in COMMIT_GAS[:2]])
    assert not refused.admits(last)


def test_a_commit_is_priced_at_the_live_price_capped():
    ledger = OrderGasLedger(1, terms_with(10**18), PARAMS, 10**17)
    assert ledger.commit_cost(120000, LIVE_UNDER_CAP) == 120000 * LIVE_UNDER_CAP
    assert ledger.commit_cost(120000, LIVE_OVER_CAP) == 120000 * CAP
    assert capped_price(LIVE_OVER_CAP, 0) == 0


def test_a_row_is_priced_at_gas_per_row_and_the_cap_whatever_its_estimate():
    ledger = OrderGasLedger(1, terms_with(10**18), PARAMS, 10**17)
    assert ledger.rows_cost(1, 400000, LIVE_UNDER_CAP) == PARAMS.gas_per_row * CAP
    assert ledger.rows_cost(2, 650000, LIVE_OVER_CAP) == 2 * PARAMS.gas_per_row * CAP


def test_nothing_is_counted_before_a_relay_and_the_reserve_once():
    ledger = OrderGasLedger(1, terms_with(10**18), PARAMS, 10**17)
    reserve = PARAMS.gas_per_result * CAP
    assert ledger.total() == reserve
    ledger.charge(ledger.rows_cost(1, 400000, CAP))
    ledger.charge(ledger.rows_cost(1, 400000, CAP))
    assert ledger.total() == 2 * PARAMS.gas_per_row * CAP + reserve


def test_the_receipt_replaces_the_estimate():
    ledger = OrderGasLedger(1, terms_with(10**18), PARAMS, 10**17)
    ledger.charge_commits(["a", "b"], [120000 * CAP, 120000 * CAP])
    ledger.settle(["a", "b"], 2 * 148213 * 62 * GWEI)
    assert ledger.spent == 2 * 148213 * 62 * GWEI
    assert ledger.estimates == {}
    assert ledger.relayed == ["a", "b"]


def test_a_raise_admits_a_refused_relay():
    ledger = charged(OrderGasLedger(1, terms_with(231 * 10**15), PARAMS, 10**17))
    more = ledger.commit_cost(120000, LIVE_UNDER_CAP)
    assert not ledger.admits(more)
    assert ledger.terms_stale(1_800_000_000, 15)
    ledger.set_terms(terms_with(240 * 10**15), 1_800_000_000)
    assert ledger.admits(more)
    assert not ledger.terms_stale(1_800_000_014, 15) and ledger.terms_stale(1_800_000_015, 15)


def test_the_rate_converts_the_counter_to_token_wei():
    terms = terms_with(10**18, token_per_pol=2 * WEI_PER_TOKEN)
    ledger = OrderGasLedger(1, terms, PARAMS, 10**17)
    assert ledger.total() == 2 * PARAMS.gas_per_result * CAP
    assert ledger.budget_wei() == 10**18


def test_terms_all_zero_are_the_network_budget_over_the_same_counter():
    assert order_terms((0, 0, 0, 0, 0, 0)) is None
    ledger = OrderGasLedger(1, None, PARAMS, 10**17)
    assert not ledger.recorded
    assert ledger.reserve() == 0
    assert ledger.rows_cost(1, 400000, LIVE_OVER_CAP) == 400000 * LIVE_OVER_CAP
    assert ledger.commit_cost(120000, LIVE_OVER_CAP) == 120000 * LIVE_OVER_CAP
    assert ledger.budget_wei() == 10**17
    ledger.charge(10**17 - 1)
    assert ledger.admits(1)
    assert not ledger.admits(2)


def test_terms_without_params_are_judged_as_legacy():
    assert not OrderGasLedger(1, terms_with(10**18), None, 10**17).recorded


def test_terms_and_params_parse_from_the_contract_shapes():
    terms = order_terms({'pricePerCuHourWei': 1, 'cu': 2, 'chainFeeWei': 3, 'gasPriceCapWei': 4,
                         'gasAllocated': 5, 'tokenPerPol': 6})
    assert terms == OrderTerms(1, 2, 3, 4, 5, 6)
    assert order_terms([1, 2, 3, 4, 5, 6]) == terms
    struct = [0] * 22
    struct[13], struct[14], struct[15], struct[17] = 343834, 267445, 971708, 30 * GWEI
    assert pricing_params(struct) == PARAMS
    assert pricing_params({'gasPerRow': 343834, 'gasPerCommit': 267445, 'gasPerResult': 971708,
                           'priorityFeeWei': 30 * GWEI}) == PARAMS


def test_the_budget_line_names_the_fee_and_the_cap():
    line = OrderGasLedger(77, terms_with(3 * 10**17), PARAMS, 10**17).budget_line()
    assert line.startswith("order 77: chain fee 300000000000000000 wei at cap 100000000000 wei/gas")
    assert OrderGasLedger(77, None, None, 10**17).budget_line() == "order 77: legacy relay budget 100000000000000000 wei of gas"
