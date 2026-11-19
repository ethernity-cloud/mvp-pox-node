"""utils.scone_queues: the enclave thread count the enclave composes get as
ETNY_SCONE_QUEUES, twice the node's CPUs within the images' floor of four
queues and, on a network whose PoX is 1.2, their 64 TCS; on a legacy PoX,
whose trustedzones are signed without --tcs, within four and eight."""
import pytest

from utils import SCONE_QUEUES_LEGACY_MAX, SCONE_QUEUES_MAX, SCONE_QUEUES_MIN, scone_queues


def test_the_bounds_are_the_images():
    assert (SCONE_QUEUES_MIN, SCONE_QUEUES_MAX) == (4, 64)


def test_the_legacy_maximum_is_eight():
    assert SCONE_QUEUES_LEGACY_MAX == 8


@pytest.mark.parametrize("cpus, queues", [
    (1, 4), (2, 4), (3, 6), (4, 8), (8, 16), (16, 32), (32, 64), (48, 64), (64, 64), (128, 64),
])
def test_twice_the_cpus_within_the_bounds(cpus, queues):
    assert scone_queues(cpus) == queues


@pytest.mark.parametrize("cpus, queues", [(1, 4), (2, 4), (3, 6), (4, 8), (8, 8), (16, 8), (64, 8)])
def test_on_a_legacy_pox_the_count_stops_at_eight(cpus, queues):
    assert scone_queues(cpus, legacy=True) == queues


@pytest.mark.parametrize("cpus", [1, 4, 8, 16, 64, 128])
def test_a_network_not_named_legacy_takes_the_64_tcs_bound(cpus):
    assert scone_queues(cpus, legacy=False) == scone_queues(cpus)


def test_a_count_read_as_text_is_taken():
    assert scone_queues("6") == 12
