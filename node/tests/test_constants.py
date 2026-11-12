"""constants: the values of PHASE-E-PLAN.md section 10."""
from fractions import Fraction

import constants
from utils import ORDER_GRACE_SECONDS


def test_the_phase_e_values():
    assert constants.INTEGRATION_TEST_SECONDS == 900
    assert constants.INTEGRATION_TEST_RETRY_MIN_SECONDS == 600
    assert constants.INTEGRATION_TEST_RETRY_MAX_SECONDS == 6 * 3600
    assert constants.INTEGRATION_TEST_REGISTRY_CHECK_SECONDS == 300
    assert constants.CU_TTL_SECONDS == 604800
    assert constants.CU_REMEASURE_MARGIN_SECONDS == 86400
    assert constants.CU_MAX_THOUSANDTHS == 64000
    assert constants.DP_REFRESH_MARGIN_SECONDS == 3600
    assert constants.CANCEL_ATTEMPTS == 5
    assert constants.TIP_HOURS == 1
    assert constants.MAX_TIP == 2**128 - 1
    assert constants.GAS_PROFILE_ALPHA == Fraction(1, 4)
    assert constants.RELAY_BATCH_MIN == 2
    assert constants.ESR_LEDGER_CAP == 256
    assert constants.RESULT_SEND_GRACE_SECONDS == 3600
    assert constants.RPC_REPLICA_LAG_BLOCKS == 8
    assert constants.REPLAY_FETCH_SECONDS == 120
    assert constants.FENCE_STAGE_SECONDS == 120
    assert constants.FENCE_STAGE_TICKS == 24
    assert constants.COMMIT_WINDOW_FACTOR == 2
    assert constants.BROKER_TX_PER_TICK == 1
    assert constants.HANDOVER_REQUEST_TIMEOUT_SECONDS == 120


def test_the_existing_values_keep_their_places():
    assert ORDER_GRACE_SECONDS == 120
    import config
    assert config.esr_relay_gas_budget_wei_default == 10**17


def test_the_re_measure_margin_is_inside_the_ttl():
    assert 0 < constants.CU_REMEASURE_MARGIN_SECONDS < constants.CU_TTL_SECONDS
    assert constants.INTEGRATION_TEST_SECONDS > 300
