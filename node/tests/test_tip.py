"""utils.tip_per_cu_hour_wei (PHASE-E-PLAN.md E10, D.1): the configured tip
plus the node's own gas (the profile's three figures) at avgGasPriceWei,
converted at tokenPerPol, over cu thousandths for TIP_HOURS, rounded up and
capped at MAX_TIP."""
from fractions import Fraction
import math

from constants import MAX_TIP, TIP_HOURS
from utils import ONE_CU, tip_per_cu_hour_wei

AMOY = {"dpRequest": 1158938, "placement": 681380, "startedRow": 110101}
GAS = sum(AMOY.values())
AVG_GAS_PRICE_WEI = 30 * 10**9
TOKEN_PER_POL = 2 * 10**18


def expected(configured, gas, avg, token_per_pol, cu, hours=TIP_HOURS):
    return configured + math.ceil(Fraction(gas * avg * token_per_pol, 10**18) / Fraction(cu * hours, ONE_CU))


def test_the_fixture_cu_over_one_hour():
    tip = tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500)
    assert tip == expected(0, GAS, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500)
    assert tip == 46_810_056_000_000_000
    assert TIP_HOURS == 1


def test_one_cu_without_a_record():
    without = tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 0)
    assert without == tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, ONE_CU)
    assert without == expected(0, GAS, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, ONE_CU)
    assert without == 117_025_140_000_000_000
    assert without == tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500) * 2500 // ONE_CU


def test_the_configured_tip_is_added():
    assert tip_per_cu_hour_wei(10**15, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500) == \
        10**15 + tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500)


def test_the_max_tip_cap():
    assert tip_per_cu_hour_wei(MAX_TIP, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500) == MAX_TIP
    assert tip_per_cu_hour_wei(0, {"dpRequest": 2**200}, 10**18, 10**18, 1) == MAX_TIP
    assert MAX_TIP == 2**128 - 1


def test_tip_zero_with_an_empty_profile_and_no_configured_tip():
    assert tip_per_cu_hour_wei(0, {}, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500) == 0
    assert tip_per_cu_hour_wei(0, {"dpRequest": 0, "placement": 0, "startedRow": 0}, AVG_GAS_PRICE_WEI,
                               TOKEN_PER_POL, 2500) == 0
    assert tip_per_cu_hour_wei(0, AMOY, 0, TOKEN_PER_POL, 2500) == 0


def test_the_own_gas_is_rounded_up():
    assert tip_per_cu_hour_wei(0, {"dpRequest": 1}, 1, 1, ONE_CU) == 1
    assert tip_per_cu_hour_wei(0, {"dpRequest": 1}, 1, 10**18, ONE_CU) == 1
    assert tip_per_cu_hour_wei(0, {"dpRequest": 3}, 1, 10**18, 2000) == 2
    assert tip_per_cu_hour_wei(0, {"dpRequest": 4}, 1, 10**18, 2000) == 2


def test_longer_hours_spread_the_gas():
    one = tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500, hours=1)
    two = tip_per_cu_hour_wei(0, AMOY, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500, hours=2)
    assert two == expected(0, GAS, AVG_GAS_PRICE_WEI, TOKEN_PER_POL, 2500, hours=2)
    assert two * 2 == one
