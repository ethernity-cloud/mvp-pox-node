"""utils.placement_block_of: the block of this node's placement of an order
among its _placeOrderEV logs, for an order resumed without a placement
record (bloxberg testnet rig, 2026-10-26: two agents that had taken their
placements for failed resumed the orders after a restart with a fresh full
window, twice)."""
from utils import placement_block_of


def log(order, block):
    return {'args': {'_from': '0x6D679f7f60BF87723985207Cc2ab1910d3af8f9B', '_orderNumber': order}, 'blockNumber': block}


def test_the_block_of_the_orders_placement_is_found_among_the_nodes_placements():
    logs = [log(2670, 30790700), log(2682, 30790831), log(2690, 30790990)]
    assert placement_block_of(logs, 2682) == 30790831
    assert placement_block_of(logs, "2690") == 30790990


def test_an_order_placed_before_the_logs_window_is_not_there():
    assert placement_block_of([log(2690, 30790990)], 2682) is None
    assert placement_block_of([], 2682) is None
