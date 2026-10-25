"""utils: the row a node adds to its DP request when it starts running an
order, 'v1:<order>:<block>' under the key etny-st, and the capability a DP
request's metadata4 carries for it. A client reads the row to know within
blocks that its order is being worked on."""
from utils import (DP_STARTED_ROW_CAPABILITY, ORDER_STARTED_KEY, order_started_row,
                   order_started_row_order)


def test_the_row_names_the_order_and_the_block():
    assert ORDER_STARTED_KEY == "etny-st"
    assert order_started_row(2053, 30783036) == "v1:2053:30783036"
    assert order_started_row_order("v1:2053:30783036") == 2053


def test_a_value_of_another_shape_names_no_order():
    for value in ("", None, "v1:2053", "v2:2053:1", "v1:order:1", "v1:2053:block", "2053:1:v1"):
        assert order_started_row_order(value) is None


def test_the_capability_is_one_metadata4_entry():
    metadata4 = f"trustedzones=etny-pynithy-testnet;ipfs=12D3KooWKJZN;{DP_STARTED_ROW_CAPABILITY}"
    assert DP_STARTED_ROW_CAPABILITY in metadata4.split(";")
