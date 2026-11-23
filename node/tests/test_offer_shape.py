"""offer_shape (MATCHING-DESIGN.md 7.4; the owner's decisions of
2026-11-23): the offer's memory, min(total - OFFER_MEMORY_RESERVE_GIB,
MemAvailable), and storage, min(total - OFFER_STORAGE_RESERVE_GIB, free), at
usage below and above each reserve; the settings and their defaults of 1
and 20 GiB; both figures at most the bench record's readings (encmem=,
custorage=), which metadata4 carries in the DP request; the bench sizes at
or below the memory figure; the offer's enclave memory the largest of them
whose record passed."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import capability
import offer_shape

GIB = 2**30
NODE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("total, available, reserve, figure", [
    (64 * GIB, 63.5 * GIB, 1, 63),   # in use 0.5 GiB, below the reserve: total - reserve
    (64 * GIB, 64 * GIB, 1, 63),     # nothing in use
    (64 * GIB, 40 * GIB, 1, 40),     # in use 24 GiB, above the reserve: what is available
    (64 * GIB, 40.7 * GIB, 1, 40),   # whole GiB, floored
    (64 * GIB, 63.5 * GIB, 4, 60),   # a reserve of 4 GiB
    (GIB // 2, GIB // 2, 1, 0),      # a host below its reserve
])
def test_the_memory_figure(total, available, reserve, figure):
    assert offer_shape.memory_gib(int(total), int(available), reserve) == figure


@pytest.mark.parametrize("total, free, reserve, figure", [
    (500 * GIB, 495 * GIB, 20, 480),  # 5 GiB used, below the reserve: total - reserve
    (500 * GIB, 300 * GIB, 20, 300),  # 200 GiB used, above it: what is free
    (500 * GIB, 495 * GIB, 50, 450),  # a reserve of 50 GiB
    (15 * GIB, 15 * GIB, 20, 0),      # a disk smaller than its reserve
])
def test_the_storage_figure(total, free, reserve, figure):
    assert offer_shape.storage_gib(total, free, reserve) == figure


def test_the_shape_is_at_most_255_and_at_most_the_bench_records_readings():
    shape = offer_shape.offer_shape(512 * GIB, 500 * GIB, 1, 4000 * GIB, 3000 * GIB, 20)
    assert shape == offer_shape.Shape(255, 255)
    assert offer_shape.bounded(offer_shape.Shape(60, 200), 32, 120, None) == offer_shape.Shape(32, 120)
    assert offer_shape.bounded(offer_shape.Shape(16, 100), 32, 120, None) == offer_shape.Shape(16, 100)
    assert offer_shape.bounded(offer_shape.Shape(60, 200), None, None, None) == offer_shape.Shape(60, 200)
    # A storage figure reason 11 refused is never stated again.
    assert offer_shape.bounded(offer_shape.Shape(60, 200), None, 120, 120) == offer_shape.Shape(60, 119)
    assert offer_shape.bounded(offer_shape.Shape(60, 100), None, 120, 120) == offer_shape.Shape(60, 100)


def reserves(environment):
    """config's two reserves in a process started in node/ with
    `environment` over this one's, without the two variables otherwise."""
    env = {k: v for k, v in os.environ.items() if k not in ("OFFER_MEMORY_RESERVE_GIB", "OFFER_STORAGE_RESERVE_GIB")}
    env.update(environment)
    program = ("import sys, types; sys.modules['dependency_manager'] = types.SimpleNamespace("
               "check_dependencies=lambda: None); import config; "
               "print(config.offer_memory_reserve_gib, config.offer_storage_reserve_gib)")
    done = subprocess.run([sys.executable, "-c", program], cwd=NODE, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return tuple(int(v) for v in done.stdout.split())


def test_the_reserves_default_to_1_and_20_gib_and_the_settings_change_them():
    assert reserves({}) == (1, 20)
    assert reserves({"OFFER_MEMORY_RESERVE_GIB": "4", "OFFER_STORAGE_RESERVE_GIB": "50"}) == (4, 50)


def test_the_bench_sizes_are_the_listed_sizes_at_or_below_the_memory_figure():
    sizes = capability.ENCLAVE_SIZES_GIB
    assert offer_shape.bench_sizes(63, sizes) == [1, 2, 4, 8, 16, 32]
    assert offer_shape.bench_sizes(64, sizes) == [1, 2, 4, 8, 16, 32, 64]
    assert offer_shape.bench_sizes(3, sizes) == [1, 2]
    assert offer_shape.bench_sizes(0, sizes) == []


@pytest.mark.parametrize("record_size, memory, size", [
    (32, 63, 32),    # the bench passed at 32 GiB, below the memory figure
    (64, 63, 32),    # a record above the memory figure: the largest bench size at or below it
    (8, 7, 4),
    (None, 63, 0),   # a record without a bench reading
])
def test_the_offer_states_the_largest_bench_size_its_record_passed(record_size, memory, size):
    assert capability.offer_enclave_memory(0, record_size, None, memory) == size


RECORD = {"gflops": 250, "cuv": 4, "t1": 1761724830, "sig_der_hex": "3065AB", "heap": "2G", "workers": 4}


def test_the_dp_request_carries_the_bench_records_readings():
    base = "trustedzones=tz;ipfs=peer;startrow=v1;cugflops=250;cuv=4;cuat=1761724830;cusig=3065ab;cuheap=2G;cuworkers=4"
    assert capability.metadata4({"tz"}, "tz", "peer", RECORD) == base
    assert capability.metadata4({"tz"}, "tz", "peer", {**RECORD, "encmem": "32", "custorage": "400"}) == \
        base + ";encmem=32;custorage=400"
    assert capability.record_storage({**RECORD, "custorage": "400"}) == 400
    assert capability.record_storage(RECORD) is None
    assert capability.record_storage({**RECORD, "custorage": "-1"}) is None
