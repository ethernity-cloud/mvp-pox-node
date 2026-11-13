"""utils.admits_do_request (PHASE-E-PLAN.md E17, D.3): a sub-token V2 price
above the quote plus the tip is taken, below it refused, maxCu below the cu
refused, a reserved request left to the broker, a legacy request judged
fee-inclusive at the fixture's fee percents; the placement refusals that
name the node's own request; the settlement line."""
import pytest

from models import DORequestV2
from utils import (PLACEMENT_REFUSALS_OFFER, PLACEMENT_REFUSALS_TERMS, Refusal, admits_do_request,
                   placement_refusal_disposition, settlement_line)

DOWNER = "0x" + "11" * 20
FEE_PERCENTS = (5, 2)
PUBLISHER_FEES = (2, 1)
QUOTE_PRICE = 3 * 10**17
TIP = 10**16
CU = 2500


def do_v2(max_price=0, reserved_price=0, max_cu=0, status=0):
    return DORequestV2((DOWNER, 2, 4, 8, 10, 24, 1, max_price, reserved_price, max_cu, 60, 10**18,
                        2 * 10**18, 5 * 10**18, status))


def test_the_model_reads_the_fifteen_fields():
    request = do_v2(max_price=7, reserved_price=0, max_cu=3000, status=1)
    assert (request.downer, request.cpu, request.memory, request.storage, request.bandwidth) == (DOWNER, 2, 4, 8, 10)
    assert (request.duration, request.instances, request.max_price, request.reserved_price) == (24, 1, 7, 0)
    assert (request.max_cu, request.relays_per_hour, request.gas_level) == (3000, 60, 10**18)
    assert (request.chain_fee, request.escrow, request.status) == (2 * 10**18, 5 * 10**18, 1)


def test_a_sub_token_v2_price_above_the_bid_is_taken():
    assert admits_do_request(do_v2(max_price=QUOTE_PRICE + TIP), 0, QUOTE_PRICE, TIP, CU, FEE_PERCENTS,
                             (0, 0)) is None
    assert admits_do_request(do_v2(max_price=5 * 10**17), 0, QUOTE_PRICE, TIP, CU, FEE_PERCENTS, (0, 0)) is None


def test_a_v2_price_below_the_bid_is_refused():
    refusal = admits_do_request(do_v2(max_price=QUOTE_PRICE + TIP - 1), 0, QUOTE_PRICE, TIP, CU, FEE_PERCENTS, (0, 0))
    assert isinstance(refusal, Refusal)
    assert refusal.kind == "price"
    assert str(QUOTE_PRICE + TIP) in refusal.text


def test_a_max_cu_below_the_cu_is_refused():
    assert admits_do_request(do_v2(max_price=10**18, max_cu=2000), 0, QUOTE_PRICE, TIP, CU, FEE_PERCENTS,
                             (0, 0)).kind == "cu"
    assert admits_do_request(do_v2(max_price=10**18, max_cu=2500), 0, QUOTE_PRICE, TIP, CU, FEE_PERCENTS,
                             (0, 0)) is None
    assert admits_do_request(do_v2(max_price=10**18, max_cu=0), 0, QUOTE_PRICE, TIP, 64000, FEE_PERCENTS,
                             (0, 0)) is None


def test_a_reserved_request_is_left_to_the_broker():
    refusal = admits_do_request(do_v2(max_price=10**18, reserved_price=10**18), 0, 0, 0, CU, FEE_PERCENTS, (0, 0))
    assert refusal.kind == "reserved"


def test_a_legacy_request_is_judged_fee_inclusive():
    # 110% of the bid at most the uint8 price in token wei: 1 token admits a
    # bid up to 1e18 x 100 / 110.
    bound = 10**20 // 110
    assert admits_do_request(do_v2(), 1, bound, 0, CU, FEE_PERCENTS, PUBLISHER_FEES) is None
    refusal = admits_do_request(do_v2(), 1, bound + 1, 0, CU, FEE_PERCENTS, PUBLISHER_FEES)
    assert refusal.kind == "legacy-price"
    assert "10% fees" in refusal.text
    assert admits_do_request(do_v2(), 2, bound + 1, 0, CU, FEE_PERCENTS, PUBLISHER_FEES) is None
    assert admits_do_request(do_v2(), 1, bound - TIP, TIP, CU, FEE_PERCENTS, PUBLISHER_FEES) is None
    assert admits_do_request(do_v2(), 1, bound - TIP + 1, TIP, CU, FEE_PERCENTS, PUBLISHER_FEES).kind == "legacy-price"


def test_a_legacy_request_without_fees_takes_the_whole_price():
    assert admits_do_request(do_v2(), 1, 10**18, 0, CU, (0, 0), (0, 0)) is None
    assert admits_do_request(do_v2(), 1, 10**18 + 1, 0, CU, (0, 0), (0, 0)).kind == "legacy-price"
    assert admits_do_request(do_v2(), 0, 1, 0, CU, (0, 0), (0, 0)).kind == "legacy-price"


def test_a_legacy_request_ignores_max_cu():
    assert admits_do_request(do_v2(max_cu=0), 1, 10**17, 0, 64000, FEE_PERCENTS, PUBLISHER_FEES) is None


@pytest.mark.parametrize("reason", PLACEMENT_REFUSALS_TERMS)
def test_the_terms_refusals_name_the_own_request(reason):
    assert placement_refusal_disposition(reason) == "terms"
    assert placement_refusal_disposition(reason.upper()) == "terms"


@pytest.mark.parametrize("reason", PLACEMENT_REFUSALS_OFFER)
def test_the_offer_refusals_name_the_own_request(reason):
    assert placement_refusal_disposition(reason) == "offer"
    assert placement_refusal_disposition(f" {reason} ") == "offer"


@pytest.mark.parametrize("reason", ["bid price too low", "node address invalid", "status must be available",
                                    "instances booked", "", None])
def test_the_other_refusals_cache_the_do_request(reason):
    assert placement_refusal_disposition(reason) == "cache"


def test_the_four_own_refusals():
    assert PLACEMENT_REFUSALS_TERMS == ("dp request has no terms", "dp request expired")
    assert PLACEMENT_REFUSALS_OFFER == ("offer does not accept reserved orders", "node above maxCu")


def test_the_settlement_line():
    assert settlement_line(7, (0, 0, 0, 0, 0, 0)) is None
    line = settlement_line(7, (3, 9 * 10**17, 9 * 10**16, 12 * 10**15, 10**15, 0))
    assert line == ("Order 7 settlement: 3 hours billed, work 900000000000000000 wei, fees 90000000000000000 wei, "
                    "chain paid 12000000000000000 wei, refund 1000000000000000 wei, outcome 0")
    assert settlement_line(8, (0, 0, 0, 0, 5, 2)) == ("Order 8 settlement: 0 hours billed, work 0 wei, fees 0 wei, "
                                                      "chain paid 0 wei, refund 5 wei, outcome 2")
