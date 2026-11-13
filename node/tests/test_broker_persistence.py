"""The broker memory the agent keeps across process starts in
broker_cursor.json (PHASE-E-PLAN.md E31, E33): the HandoverRequested events
whose acceptance is due after the appointee window and the started
reservations without an order since, both acted on after the
'broker_events' cursor passed their logs. broker.acceptances_record and
broker.started_reservations_record give the JSON shapes, their inverses
read them back, and a record of another shape reads as nothing."""
import json

import broker

DPROC = "0x" + "11" * 20
FARM = "0x" + "22" * 20
APPOINTED = "0x" + "33" * 20
EVENT_A = broker.HandoverRequested(7, 4101, DPROC, 52_646_900, 52_647_800, FARM, APPOINTED, 1_800_000_000)
EVENT_B = broker.HandoverRequested(8, 4099, DPROC, 52_646_000, 52_646_900, FARM, broker.ZERO_ADDRESS, 1_799_990_000)


def test_the_pending_acceptances_round_trip_through_json_in_order_id_order():
    pending = {EVENT_A.order_id: EVENT_A, EVENT_B.order_id: EVENT_B}
    record = broker.acceptances_record(pending)
    assert record == [list(EVENT_B), list(EVENT_A)]
    assert broker.acceptances_from_record(json.loads(json.dumps(record))) == pending


def test_the_started_reservations_round_trip_through_json():
    started = {12: 52_647_000, 3: 52_646_500}
    record = broker.started_reservations_record(started)
    assert record == {"3": 52_646_500, "12": 52_647_000}
    assert broker.started_reservations_from_record(json.loads(json.dumps(record))) == started


def test_nothing_and_another_shape_read_as_empty():
    assert broker.acceptances_from_record(None) == {}
    assert broker.acceptances_from_record([]) == {}
    assert broker.acceptances_from_record([[1, 2]]) == {}
    assert broker.acceptances_from_record("7") == {}
    assert broker.started_reservations_from_record(None) == {}
    assert broker.started_reservations_from_record({"x": 1}) == {}
    assert broker.started_reservations_from_record([1, 2]) == {}


def test_a_record_from_a_log_reads_back_as_the_event():
    log = {'args': {'id': 7, 'orderId': 4101, 'dproc': DPROC, 'fromBlock': 52_646_900, 'toBlock': 52_647_800,
                    'farm': FARM, 'appointed': APPOINTED}}
    event = broker.handover_requested(log, 1_800_000_000)
    assert broker.acceptances_from_record(broker.acceptances_record({event.order_id: event})) == {4101: event}
    assert event == EVENT_A
