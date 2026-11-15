"""utils.scone_queues: the enclave thread count the agent exports as
ETNY_SCONE_QUEUES, twice the node's CPUs within the images' floor of four
queues and their 64 TCS."""
import pytest

from utils import SCONE_QUEUES_MAX, SCONE_QUEUES_MIN, scone_queues


def test_the_bounds_are_the_images():
    assert (SCONE_QUEUES_MIN, SCONE_QUEUES_MAX) == (4, 64)


@pytest.mark.parametrize("cpus, queues", [
    (1, 4), (2, 4), (3, 6), (4, 8), (8, 16), (16, 32), (32, 64), (48, 64), (64, 64), (128, 64),
])
def test_twice_the_cpus_within_the_bounds(cpus, queues):
    assert scone_queues(cpus) == queues


def test_a_count_read_as_text_is_taken():
    assert scone_queues("6") == 12
