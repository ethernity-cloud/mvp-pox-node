"""utils.block_ranges: the inclusive block ranges the agent's scans request
one eth_getLogs at a time, each of at most `chunk` blocks. The LiteForge RPC
(rpc.testnet.liteforge.zeeve.net) counts toBlock - fromBlock + 1 against its
cap of 3000: a range of 3000 blocks is answered, one of 3001 refused."""
import pytest

from utils import block_ranges


def test_the_ranges_cover_the_window_in_chunks_of_at_most_chunk_blocks():
    assert list(block_ranges(100, 6999, 3000)) == [(100, 3099), (3100, 6099), (6100, 6999)]


def test_a_window_of_exactly_one_chunk_is_one_range():
    assert list(block_ranges(70574448, 70577447, 3000)) == [(70574448, 70577447)]
    assert list(block_ranges(5, 5, 3000)) == [(5, 5)]


def test_the_ranges_join_without_a_gap_or_an_overlap():
    ranges = list(block_ranges(0, 20000, 9000))
    assert ranges == [(0, 8999), (9000, 17999), (18000, 20000)]
    assert all(stop - start + 1 <= 9000 for start, stop in ranges)


def test_a_from_block_below_zero_starts_at_zero():
    assert list(block_ranges(-50, 10, 3000)) == [(0, 10)]


def test_an_empty_window_yields_no_range():
    assert list(block_ranges(10, 9, 3000)) == []


def test_a_chunk_below_one_block_is_refused():
    with pytest.raises(ValueError):
        list(block_ranges(0, 10, 0))
