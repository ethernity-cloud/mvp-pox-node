"""utils.do_request_verdict and refusal_cached (PHASE-E-PLAN.md D.3, E17,
WS-G): the idle loop's pre-filter. Without a quote (a legacy contract, or a
1.2 one without a reader) the legacy uint8 compare runs, so a V2 request
priced below one token, which reads 0 there, is skipped as every agent before
3.8.0 skips it; with the pass's quote a V2 request is judged on its recorded
terms (taken above the bid, refused below it, maxCu below the cu refused, a
reserved request left to the broker) and a legacy request fee-inclusive. The
legacy, reserved and cu refusals are cached against the request, a price
refusal is not."""
from models import DORequestV2
from utils import CACHED_REFUSALS, Refusal, do_request_verdict, refusal_cached

DOWNER = "0x" + "11" * 20
FEE_PERCENTS = (5, 2)
QUOTE_PRICE = 3 * 10**17
TIP = 10**16
CU = 2500
NODE_PRICE = 3


def do_v2(max_price=0, reserved_price=0, max_cu=0):
    return DORequestV2((DOWNER, 2, 4, 8, 10, 24, 1, max_price, reserved_price, max_cu, 60, 10**18,
                        2 * 10**18, 5 * 10**18, 0))


def test_without_a_quote_a_sub_token_v2_request_reads_zero_and_is_skipped():
    refusal = do_request_verdict(0, NODE_PRICE)
    assert isinstance(refusal, Refusal)
    assert refusal.kind == "legacy"
    assert refusal.text == "price 0 below this node's 3"
    assert refusal_cached(refusal)


def test_without_a_quote_the_legacy_compare_takes_a_price_at_least_the_nodes():
    assert do_request_verdict(NODE_PRICE, NODE_PRICE) is None
    assert do_request_verdict(NODE_PRICE + 1, NODE_PRICE) is None
    assert do_request_verdict(NODE_PRICE - 1, NODE_PRICE).kind == "legacy"


def test_with_a_quote_a_sub_token_v2_request_above_the_bid_is_taken():
    assert do_request_verdict(0, NODE_PRICE, QUOTE_PRICE, do_v2(max_price=QUOTE_PRICE + TIP), TIP, CU,
                              FEE_PERCENTS, (0, 0)) is None


def test_with_a_quote_a_v2_request_below_the_bid_is_refused_and_not_cached():
    refusal = do_request_verdict(0, NODE_PRICE, QUOTE_PRICE, do_v2(max_price=QUOTE_PRICE + TIP - 1), TIP, CU,
                                 FEE_PERCENTS, (0, 0))
    assert refusal.kind == "price"
    assert not refusal_cached(refusal)


def test_a_max_cu_below_the_cu_is_refused_and_cached():
    refusal = do_request_verdict(0, NODE_PRICE, QUOTE_PRICE, do_v2(max_price=10**18, max_cu=2000), TIP, CU,
                                 FEE_PERCENTS, (0, 0))
    assert refusal.kind == "cu"
    assert refusal_cached(refusal)


def test_a_reserved_request_is_left_to_the_broker_and_cached():
    refusal = do_request_verdict(0, NODE_PRICE, QUOTE_PRICE, do_v2(max_price=10**18, reserved_price=10**18), TIP,
                                 CU, FEE_PERCENTS, (0, 0))
    assert refusal.kind == "reserved"
    assert refusal_cached(refusal)


def test_with_a_quote_a_legacy_request_is_judged_fee_inclusive():
    bound = 10**20 // 110
    assert do_request_verdict(1, NODE_PRICE, bound, do_v2(), 0, CU, FEE_PERCENTS, (2, 1)) is None
    refusal = do_request_verdict(1, NODE_PRICE, bound + 1, do_v2(), 0, CU, FEE_PERCENTS, (2, 1))
    assert refusal.kind == "legacy-price"
    assert not refusal_cached(refusal)


def test_the_cached_kinds():
    assert CACHED_REFUSALS == ("legacy", "reserved", "cu")
