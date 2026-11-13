"""utils.gas_with_headroom: a DP request __send_dp_request sends takes
DP_REQUEST_GAS_HEADROOM_PERCENT over its estimate, made at the head block
while the request is mined in a later one (Amoy, 2026-11-13: estimates of
1,212,000 against needs of 1,224,205 to 1,230,278 behind results, 1,226,341
against 1,230,378 after a cancel)."""
from utils import DP_REQUEST_GAS_HEADROOM_PERCENT, gas_with_headroom


def test_the_headroom_covers_the_gas_the_mined_call_needs():
    assert gas_with_headroom(1_212_000) >= 1_230_278
    assert gas_with_headroom(1_226_341) >= 1_230_378


def test_the_limit_is_the_estimate_with_the_headroom_rounded_up():
    assert DP_REQUEST_GAS_HEADROOM_PERCENT == 25
    assert gas_with_headroom(100) == 125
    assert gas_with_headroom(101) == 127
    assert gas_with_headroom(80, percent=10) == 88
    assert gas_with_headroom(81, percent=10) == 90
