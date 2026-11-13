"""The agent's fixed values of PHASE-E-PLAN.md section 10, shared by
etny-node.py and the modules beside it. The order grace (utils.ORDER_GRACE_SECONDS)
and the relay budget default (config.esr_relay_gas_budget_wei_default) keep
their places."""
from fractions import Fraction

# The trustedzone's integration test: the wait for its result object, which
# spans the attestation, the two time statements and the benchmark.
INTEGRATION_TEST_SECONDS = 900
# The longest benchmark run (t1 - t0) a capability record may carry: the
# validators cancel a request above it (ethernity-cas capability.rs
# BENCHMARK_MAX_SECONDS, reason RunTooLong).
BENCHMARK_MAX_SECONDS = 300
# A failed integration test is tried again after a backoff doubling from the
# minimum to the maximum.
INTEGRATION_TEST_RETRY_MIN_SECONDS = 600
INTEGRATION_TEST_RETRY_MAX_SECONDS = 6 * 3600
# How often the idle loop reads the image registry for a newer trustedzone,
# which makes the passed test of the older one stale.
INTEGRATION_TEST_REGISTRY_CHECK_SECONDS = 300

# A capability record stands for CU_TTL_SECONDS from its t1 (the validators'
# bound); the node re-measures CU_REMEASURE_MARGIN_SECONDS before that.
CU_TTL_SECONDS = 604800
CU_REMEASURE_MARGIN_SECONDS = 86400
# The largest cu (thousandths) a record may claim: the validators cancel a
# request above it.
CU_MAX_THOUSANDTHS = 64000

# A 1.2 DP request is refreshed when its expiry is this close.
DP_REFRESH_MARGIN_SECONDS = 3600
# How often the enclave wait reads the standing DP request's terms for the
# refresh (the idle tick reads them every pass).
DP_TERMS_POLL_SECONDS = 60
# How many sends, a block apart, a cancel of an AVAILABLE request is given.
CANCEL_ATTEMPTS = 5
# The order length the node's own gas is priced into its tip over.
TIP_HOURS = 1
# The contract's bound on a tip (uint128).
MAX_TIP = 2**128 - 1
# The weight of a status-1 receipt in the gas profile's moving average.
GAS_PROFILE_ALPHA = Fraction(1, 4)

# From this many relayable items of one kind at a tick they go as one batch.
RELAY_BATCH_MIN = 2
# The most commits relayed for one order (the trustedzone's ledger cap).
ESR_LEDGER_CAP = 256
# How long past the order's deadline a result held by the gas price is still
# sent.
RESULT_SEND_GRACE_SECONDS = 3600
# How many blocks a read is kept below the head, for the RPC's lagging
# replicas.
RPC_REPLICA_LAG_BLOCKS = 8
# How long after the switch the trustedzone fetches a staged replay input.
REPLAY_FETCH_SECONDS = 120
# How long, and over how many ticks, the historical state objects of a fence
# are staged.
FENCE_STAGE_SECONDS = 120
FENCE_STAGE_TICKS = 24
# The reserved commitment's window, in multiples of the broker's commitMinSeconds.
COMMIT_WINDOW_FACTOR = 2
# How many broker transactions an idle tick sends.
BROKER_TX_PER_TICK = 1
# How long the --handover command waits for the running agent's answer.
HANDOVER_REQUEST_TIMEOUT_SECONDS = 120
