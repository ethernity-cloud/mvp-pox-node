"""broker (PHASE-E-PLAN.md E31-E33, F.2, F.5): the dispositions of the order
orderOfDPRequest names for the node's offer (a reserved OPEN order approved
at once, a spot OPEN order kept for the client's approval, a CANCELED one
the offer waiting for the next placement), the supervise decisions, the
spot placement held while an acceptance stands and the bounded cu."""
import broker
from models import OrderStatus, RequestStatus

NOW = 1_800_000_000


def disposition(found=True, status=OrderStatus.OPEN, reserved=True, dp_status=RequestStatus.AVAILABLE):
    return broker.offer_disposition(found, status, reserved, dp_status)


def test_a_reserved_open_order_is_approved_at_once():
    assert disposition() == 'approve'


def test_a_spot_open_order_is_kept_for_the_clients_approval():
    assert disposition(reserved=False) == 'none'


def test_a_processing_order_is_run():
    assert disposition(status=OrderStatus.PROCESSING) == 'run'
    assert disposition(status=OrderStatus.PROCESSING, reserved=False) == 'run'


def test_a_cancelled_order_on_an_available_request_is_the_offer_waiting_again():
    assert disposition(status=OrderStatus.CANCELLED) == 'offer'
    assert disposition(status=OrderStatus.CANCELLED, dp_status=RequestStatus.BOOKED) == 'none'
    assert disposition(status=OrderStatus.CANCELLED, dp_status=RequestStatus.CANCELED) == 'none'


def test_a_closed_order_and_no_order():
    assert disposition(status=OrderStatus.CLOSED) == 'closed'
    assert disposition(found=False) == 'none'


def test_an_unmatched_reservation_takes_the_hint_at_once():
    assert broker.supervise_decision('unmatched', broker.NEVER, 300, NOW, False, True) == 'send'
    assert broker.supervise_decision('unmatched', broker.NEVER, 300, NOW, False, None) == 'send'


def test_a_started_reservation_without_an_order_waits_out_the_grace():
    assert broker.supervise_decision('started', NOW - 300, 300, NOW, False, True) == 'wait'
    assert broker.supervise_decision('started', NOW - 301, 300, NOW, False, True) == 'send'
    assert broker.supervise_decision('started', NOW - 301, 300, NOW, True, True) == 'drop'


def test_the_successors_supervise_past_to_block_and_the_grace():
    assert broker.supervise_decision('window', NOW - 100, 300, NOW, False, True) == 'wait'
    assert broker.supervise_decision('window', NOW - 400, 300, NOW, False, True) == 'send'


def test_a_pre_flight_that_would_act_nothing_drops_the_send():
    assert broker.supervise_decision('unmatched', broker.NEVER, 300, NOW, False, False) == 'drop'
    assert broker.supervise_decision('started', NOW - 400, 300, NOW, False, False) == 'drop'


def test_the_spot_placement_is_held_while_an_acceptance_stands_and_released_on_the_placement():
    accepted = {'orderId': 4101, 'predecessor_cu': 2000}
    open_window = broker.handover_of((True, 4101, False, 0, 100, 1000))
    placed = broker.handover_of((True, 4101, True, 4102, 100, 1000))
    gone = broker.handover_of((False, 0, False, 0, 0, 0))
    assert broker.acceptance_standing(accepted, open_window) is True
    assert broker.acceptance_standing(accepted, placed) is False
    assert broker.acceptance_standing(accepted, gone) is False
    assert broker.acceptance_standing(accepted, None) is False
    assert broker.acceptance_standing(None, open_window) is False
    assert broker.acceptance_standing({}, open_window) is False


def test_the_offer_is_at_most_the_predecessors_cu_while_an_acceptance_stands():
    assert broker.bounded_cu(3000, {'orderId': 4101, 'predecessor_cu': 2000}) == 2000
    assert broker.bounded_cu(1500, {'orderId': 4101, 'predecessor_cu': 2000}) == 1500
    assert broker.bounded_cu(3000, {'orderId': 4101, 'predecessor_cu': 0}) == 3000
    assert broker.bounded_cu(3000, None) == 3000
