"""broker (PHASE-E-PLAN.md E27, F.1): the commitment window's extension rule
and the broker's structs read by position or by name, params() from an
answer of 30 fields or of 31."""
import json
from pathlib import Path

import pytest
from eth_utils.abi import collapse_if_tuple
from web3 import Web3

import broker
import config
from constants import COMMIT_WINDOW_FACTOR

COMMIT_MIN = 7 * 86400
NOW = 1_800_000_000


def commitment(until, recommit_after=0, suspended_until=0, handover_allowed_after=0):
    return broker.Commitment(until, 0, recommit_after, 0, handover_allowed_after, suspended_until)


def test_no_extension_while_the_window_reaches_commit_min_ahead():
    assert broker.commitment_extension(commitment(NOW + COMMIT_MIN), NOW, COMMIT_MIN) is None
    assert broker.commitment_extension(commitment(NOW + COMMIT_MIN + 1), NOW, COMMIT_MIN) is None


def test_the_window_is_extended_to_now_plus_two_commit_min_below_it():
    assert broker.commitment_extension(commitment(NOW + COMMIT_MIN - 1), NOW, COMMIT_MIN) == NOW + 2 * COMMIT_MIN
    assert COMMIT_WINDOW_FACTOR == 2
    # A node that never committed (until 0) commits at start.
    assert broker.commitment_extension(commitment(0), NOW, COMMIT_MIN) == NOW + 2 * COMMIT_MIN


def test_none_before_recommit_after():
    assert broker.commitment_extension(commitment(0, recommit_after=NOW + 1), NOW, COMMIT_MIN) is None
    assert broker.commitment_extension(commitment(0, recommit_after=NOW), NOW, COMMIT_MIN) == NOW + 2 * COMMIT_MIN


def test_the_extension_is_never_a_shortening():
    until = broker.commitment_extension(commitment(NOW + 3600), NOW, COMMIT_MIN)
    assert until >= NOW + 3600 and until >= NOW + COMMIT_MIN


def test_commitment_of_reads_the_struct_by_position_and_by_name():
    raw = (NOW + 10, 1, 2, 3, 4, 5)
    assert broker.commitment_of(raw) == broker.Commitment(NOW + 10, 1, 2, 3, 4, 5)
    named = dict(zip(broker.COMMITMENT_FIELDS, raw))
    assert broker.commitment_of(named) == broker.commitment_of(raw)


def params_tuple(**overrides):
    values = [0] * 30
    values[7] = 600       # handoverSeconds
    values[9] = 14400     # handoverNoticeSeconds
    values[10] = 900      # handoverWindowBlocks
    values[11] = 300      # handoverWindowMinBlocks
    values[12] = 1800     # handoverPrepSeconds
    values[13] = 2000     # blockMillis
    values[14] = 3600     # farmClaimSeconds
    values[15] = 600      # appointeeClaimSeconds
    values[16] = 86400    # handoverCooldownSeconds
    values[18] = 10**18   # handoverGasDepositWei
    values[20] = 900      # openOrderTimeoutSeconds
    values[23] = COMMIT_MIN
    values[24] = 86400    # recommitDelaySeconds
    values[28] = 300      # brokerGraceSeconds
    for index, value in overrides.items():
        values[index] = value
    return tuple(values)


def test_params_of_reads_the_fields_the_agent_uses_by_position():
    params = broker.params_of(params_tuple())
    assert params.handover_seconds == 600
    assert params.handover_notice_seconds == 14400
    assert params.handover_window_blocks == 900
    assert params.handover_window_min_blocks == 300
    assert params.handover_prep_seconds == 1800
    assert params.block_millis == 2000
    assert params.farm_claim_seconds == 3600
    assert params.appointee_claim_seconds == 600
    assert params.handover_cooldown_seconds == 86400
    assert params.handover_gas_deposit_wei == 10**18
    assert params.open_order_timeout_seconds == 900
    assert params.commit_min_seconds == COMMIT_MIN
    assert params.recommit_delay_seconds == 86400
    assert params.broker_grace_seconds == 300


def test_params_of_reads_a_mapping_by_name():
    named = {name: value for name, _ in broker.PARAMS_FIELDS for value in [7]}
    assert all(value == 7 for value in broker.params_of(named))


@pytest.mark.parametrize("fields", [30, 31])
def test_the_fourteen_parameters_are_read_from_either_length(fields):
    # MATCHING-SYNC-PLAN.md D02, OD18: the deployed brokers answer 30
    # fields, one of pox-smart-contract-litvm 52e36e0 a 31st, farmMinScore,
    # last. The answer is decoded under node/broker.abi's params(), the 30.
    abi = json.loads(Path(config.broker_abi_filepath).read_text())
    entry = next(e for e in abi if e.get("type") == "function" and e.get("name") == "params")
    components = entry["outputs"][0]["components"]
    assert len(components) == 30 and components[-1]["name"] == "brokerMissesToReassign"
    types = [c["type"] for c in components] + ["uint16"]
    values = list(params_tuple()) + [45000]
    values[29] = 3
    answer = Web3().codec.encode([f"({','.join(types[:fields])})"], [tuple(values[:fields])])
    decoded = Web3().codec.decode([collapse_if_tuple(o) for o in entry["outputs"]], answer)[0]
    assert list(decoded) == values[:30]
    assert broker.params_of(decoded) == broker.params_of(params_tuple())
    assert broker.params_of(tuple(values[:fields])) == broker.params_of(params_tuple())


def test_handover_of_and_the_role_it_gives_an_order():
    none = broker.handover_of((False, 0, False, 0, 0, 0))
    assert broker.window_role(none, 4101) is None
    assert broker.window_role(broker.handover_of((True, 4101, False, 0, 0, 0)), 4101) is None
    window = broker.handover_of((True, 4101, True, 4102, 52_646_000, 52_646_900))
    assert window == broker.HandoverOf(True, 4101, True, 4102, 52_646_000, 52_646_900)
    assert broker.window_role(window, 4101) == broker.ROLE_PREDECESSOR
    assert broker.window_role(window, 4102) == broker.ROLE_SUCCESSOR
    # Order 0 is an id: found and fromBlock decide.
    assert broker.window_role(broker.handover_of((True, 0, False, 0, 10, 20)), 0) == broker.ROLE_PREDECESSOR


def test_one_broker_transaction_per_tick():
    assert broker.transactions_admitted(0) is True
    assert broker.transactions_admitted(1) is False
