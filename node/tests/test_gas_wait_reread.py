"""The gas wait sees a ChainFeeIncreased raise of the cap (PHASE-E-PLAN.md
E19, E21): the agent re-reads orderTerms while the wait holds whenever the
ledger's terms are HANDOVER_POLL_SECONDS old (OrderGasLedger.terms_stale,
the trustedzone's re-read cadence), and gas_wait_active over the raised cap
ends the wait the moment the block fits under it."""
from gas_ledger import OrderGasLedger, OrderTerms, PricingParams, WEI_PER_TOKEN, gas_wait_active

GWEI = 10**9
PARAMS = PricingParams(343834, 267445, 971708, 30 * GWEI)
NOW = 1_800_000_000
POLL = 15


def terms(cap):
    return OrderTerms(10**15, 1000, 3 * 10**17, cap, 0, WEI_PER_TOKEN)


def wait(ledger, base_fee):
    return gas_wait_active(base_fee, ledger.params.priority_fee_wei, ledger.cap_wei, NOW, None)


def test_a_raised_cap_ends_the_wait():
    ledger = OrderGasLedger(3, terms(100 * GWEI), PARAMS, 10**17)
    assert wait(ledger, 90 * GWEI)
    ledger.set_terms(terms(130 * GWEI), NOW)
    assert not wait(ledger, 90 * GWEI)


def test_a_raise_below_the_block_keeps_the_wait():
    ledger = OrderGasLedger(3, terms(100 * GWEI), PARAMS, 10**17)
    ledger.set_terms(terms(110 * GWEI), NOW)
    assert wait(ledger, 90 * GWEI)


def test_the_terms_are_stale_from_the_open_and_again_a_poll_after_a_read():
    ledger = OrderGasLedger(3, terms(100 * GWEI), PARAMS, 10**17)
    assert ledger.terms_read_at == 0
    assert ledger.terms_stale(NOW, POLL)
    ledger.set_terms(terms(100 * GWEI), NOW)
    assert not ledger.terms_stale(NOW, POLL)
    assert not ledger.terms_stale(NOW + POLL - 1, POLL)
    assert ledger.terms_stale(NOW + POLL, POLL)


def test_a_reloaded_ledger_reads_its_terms_at_the_first_wait(tmp_path):
    path = str(tmp_path / "relay.json")
    OrderGasLedger(3, terms(100 * GWEI), PARAMS, 10**17, path=path).charge(1)
    reloaded = OrderGasLedger(3, terms(100 * GWEI), PARAMS, 10**17, path=path)
    assert reloaded.spent == 1
    assert reloaded.terms_stale(NOW, POLL)
