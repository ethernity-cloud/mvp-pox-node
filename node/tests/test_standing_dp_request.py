"""utils.has_standing_dp_request: the idle tick's five standing-request gates
(the terms tick, the enclave wait's refresh, the supervise hint, the broker's
placed order, the DPRequestCancelled scan) act on the request whose chain id
the agent stores, and request ids count from 0: the first request on a
storage is a standing request, None is the state before one is adopted."""
import pytest

from utils import has_standing_dp_request


@pytest.mark.parametrize("dp_request_id", [0, 1, 39, 2 ** 64])
def test_a_stored_chain_id_is_a_standing_request(dp_request_id):
    assert has_standing_dp_request(dp_request_id) is True


def test_none_is_no_standing_request():
    assert has_standing_dp_request(None) is False
