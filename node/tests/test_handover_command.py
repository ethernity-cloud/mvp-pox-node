"""broker (PHASE-E-PLAN.md E28, E29, F.4): the handover window's arithmetic
with fixed broker parameters (notice 14400 s, window 900 blocks, min 300,
blockMillis 2000, handoverSeconds 600, handoverTimeOfDay 10800), the
announcement's refusals, the --handover marker file the running agent
answers, the command's arguments and what SIGHUP does."""
import pytest

import broker
from constants import HANDOVER_REQUEST_TIMEOUT_SECONDS
from test_broker_commit import params_tuple

PARAMS = broker.params_of(params_tuple())
TIME_OF_DAY = 10800
# Midnight UTC of a day, and the head at that moment.
MIDNIGHT = 1_800_000_000 - 1_800_000_000 % 86400
HEAD = 1000


def window(now=MIDNIGHT, head_number=HEAD, head_timestamp=None, order_end_slot=None, reservation_end=0,
           placed_at=MIDNIGHT - 3600, duration_hours=48):
    if head_timestamp is None:
        head_timestamp = now
    if order_end_slot is None:
        order_end_slot = MIDNIGHT + 86400 + TIME_OF_DAY + 86400
    return broker.handover_window(now, head_number, head_timestamp, PARAMS, TIME_OF_DAY, order_end_slot,
                                  reservation_end, placed_at, duration_hours)


def test_the_window_is_centred_on_the_next_slot_at_least_the_notice_plus_half_a_window_ahead():
    # At midnight the 03:00 slot is 10800 s away, under the notice plus half
    # a window (14400 + 900): the slot is tomorrow's 03:00.
    result, reason = window()
    assert reason is None
    assert result.slot == MIDNIGHT + 86400 + TIME_OF_DAY
    centre = HEAD + (result.slot - MIDNIGHT) * 1000 // PARAMS.block_millis
    assert result.from_block == centre - PARAMS.handover_window_blocks // 2
    assert result.to_block == result.from_block + PARAMS.handover_window_blocks
    span = result.to_block - result.from_block
    assert PARAMS.handover_window_min_blocks <= span <= PARAMS.handover_window_blocks
    # The window's open is at least the notice ahead on the projection.
    assert broker.project_time(result.from_block, MIDNIGHT, HEAD, PARAMS.block_millis) >= MIDNIGHT + PARAMS.handover_notice_seconds


def test_a_slot_far_enough_ahead_today_is_taken():
    now = MIDNIGHT - 20000
    result, reason = window(now=now, placed_at=now - 3600)
    assert reason is None
    assert result.slot == MIDNIGHT + TIME_OF_DAY


def test_none_when_the_slot_is_not_below_the_order_end_slot():
    result, reason = window(order_end_slot=MIDNIGHT + 86400 + TIME_OF_DAY)
    assert result is None
    assert reason.startswith('no earlier slot')
    # The term's last order (end slot 0) is bounded by the reservation's end.
    result, reason = window(order_end_slot=0, reservation_end=MIDNIGHT + 86400 + TIME_OF_DAY)
    assert result is None
    result, reason = window(order_end_slot=0, reservation_end=MIDNIGHT + 86400 + TIME_OF_DAY + 1)
    assert reason is None


def test_none_when_the_window_plus_handover_seconds_passes_the_order_end():
    # to_time = MIDNIGHT + 98100; the order ends at placed_at + 28 h = MIDNIGHT + 97200.
    result, reason = window(duration_hours=28)
    assert result is None
    assert reason == 'window past order end'
    result, reason = window(duration_hours=29)
    assert reason is None


def test_the_projection_mirrors_the_slot_library():
    assert broker.project_time(1100, 1000, 1000, 2000) == 1200
    assert broker.project_time(900, 1000, 1000, 2000) == 800
    assert broker.project_time(0, 1000, 1000, 2000) == 0
    assert broker.project_block(1200, 1000, 1000, 2000) == 1100
    assert broker.project_block(999, 1000, 1000, 2000) == 1000
    assert broker.next_slot(MIDNIGHT, TIME_OF_DAY) == MIDNIGHT + TIME_OF_DAY
    assert broker.next_slot(MIDNIGHT + TIME_OF_DAY, TIME_OF_DAY) == MIDNIGHT + TIME_OF_DAY
    assert broker.next_slot(MIDNIGHT + TIME_OF_DAY + 1, TIME_OF_DAY) == MIDNIGHT + 86400 + TIME_OF_DAY


def commitment(handover_allowed_after=0):
    return broker.Commitment(MIDNIGHT + 10**6, 0, 0, 0, handover_allowed_after, 0)


def test_the_announcement_refusals():
    recorded = broker.handover_of((True, 4101, False, 0, 10, 20))
    none = broker.handover_of((False, 0, False, 0, 0, 0))
    assert broker.handover_refusal(False, none, commitment(), MIDNIGHT, 10**19, 10**18) == 'the order is not reserved'
    assert broker.handover_refusal(True, recorded, commitment(), MIDNIGHT, 10**19, 10**18) == 'window recorded'
    assert broker.handover_refusal(True, none, commitment(MIDNIGHT + 1), MIDNIGHT, 10**19, 10**18).startswith('handover cooldown')
    assert broker.handover_refusal(True, none, commitment(), MIDNIGHT, 10**18 - 1, 10**18).startswith('token balance')
    assert broker.handover_refusal(True, none, commitment(MIDNIGHT), MIDNIGHT, 10**18, 10**18) is None
    assert broker.handover_refusal(True, None, commitment(), MIDNIGHT, 10**18, 10**18) is None


def test_the_marker_file_carries_the_request_and_then_the_answer(tmp_path):
    path = broker.handover_request_path(tmp_path)
    assert broker.read_handover_request(path) is None
    broker.write_handover_request(path, '', MIDNIGHT)
    assert broker.read_handover_request(path) == {'appoint': '', 'requested_at': MIDNIGHT}
    assert broker.read_handover_answer(path) is None
    broker.write_handover_answer(path, {'announced': True, 'tx': '0xabc', 'fromBlock': 10, 'toBlock': 20})
    assert broker.read_handover_answer(path) == {'announced': True, 'tx': '0xabc', 'fromBlock': 10, 'toBlock': 20}
    # An answered request is not acted on again.
    assert broker.read_handover_request(path) is None
    broker.write_handover_request(path, '0x' + 'ab' * 20, MIDNIGHT + 5)
    assert broker.read_handover_request(path) == {'appoint': '0x' + 'ab' * 20, 'requested_at': MIDNIGHT + 5}
    assert broker.read_handover_answer(path) is None


def test_the_command_waits_for_the_answer_and_gives_up_after_the_timeout(tmp_path):
    path = broker.handover_request_path(tmp_path)
    clock = {'t': 0.0}
    writes = []

    def sleep(seconds):
        clock['t'] += seconds
        writes.append(clock['t'])
        if clock['t'] >= 3:
            broker.write_handover_answer(path, {'refused': 'the order is not reserved'})

    answer = broker.await_handover_answer(path, sleep=sleep, now=lambda: clock['t'])
    assert answer == {'refused': 'the order is not reserved'}
    assert writes[-1] == 3.0
    clock['t'] = 0.0
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        clock['t'] += seconds

    assert broker.await_handover_answer(broker.handover_request_path(tmp_path / 'none'), sleep=advance,
                                        now=lambda: clock['t']) is None
    assert sum(sleeps) >= HANDOVER_REQUEST_TIMEOUT_SECONDS
    assert set(sleeps) == {broker.HANDOVER_ANSWER_POLL_SECONDS}


def test_the_command_arguments():
    arguments = broker.handover_command_arguments(['--handover', '--network', 'polygon_amoy'])
    assert (arguments.network, arguments.appoint) == ('polygon_amoy', '')
    arguments = broker.handover_command_arguments(['--handover', '-n', 'polygon_amoy', '--appoint', '0x' + '12' * 20])
    assert arguments.appoint == '0x' + '12' * 20
    with pytest.raises(SystemExit):
        broker.handover_command_arguments(['--handover'])
    with pytest.raises(SystemExit):
        broker.handover_command_arguments(['--handover', '-n', 'polygon_amoy', '--appoint', 'node-7'])


def test_the_appointee_is_the_zero_address_for_none():
    assert broker.appointee('') == broker.ZERO_ADDRESS
    assert broker.appointee(None) == broker.ZERO_ADDRESS
    assert broker.appointee('0x' + 'Ab' * 20) == '0x' + 'Ab' * 20
    assert broker.appointee('0x12') is None


def test_sighup_drains_a_reserved_order_and_exits_otherwise():
    assert broker.signal_action(True, False) == 'drain'
    assert broker.signal_action(True, True) == 'exit'
    assert broker.signal_action(False, False) == 'exit'
