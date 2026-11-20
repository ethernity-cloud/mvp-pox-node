"""do_request_age: where a DO request's createdAt lies in the storage of the
PoX under the two layouts (CLOUD, EthernityCloudStorage; V05, the bloxberg
mainnet PoX), which contract and layout are taken (candidates, bind), the
binary search for the first request created at or after a cutoff
(first_within, boundary) and DO_REQUEST_MAX_AGE_SECONDS, the setting that
gives the cutoff (config.NetworkConfig.do_request_max_age_seconds). The
storage words below were read from the chains with eth_getStorageAt."""
import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values
from eth_utils import keccak
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

import config
import do_request_age
from do_request_age import CLOUD, V05, AgeUnknown, NotHeld

NODE = Path(__file__).resolve().parents[1]
POX = "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7"
STORAGE = "0x6aAafE99746Bd9f9258DbC1A8C82f3C15563636C"
DAY = 86400
# The latest block's timestamp of the searches below: 2026-11-20T12:40:00Z.
NOW = 1795178400

# Words read from the chains: (layout, request id, slot, word, createdAt).
# Bloxberg mainnet's PoX 0x549A6E06... (V05): request 34541, created
# 2026-11-19T17:02:10Z by 0xe332a471..., and request 0, created
# 2021-11-06T16:14:55Z by 0x4f92201f...; the word holds the downer in its low
# 160 bits and the resource fields above it. Polygon mainnet's storage
# 0x6aAafE99... (CLOUD): request 98, created 2024-12-20T09:02:13Z. Bloxberg
# testnet's 2.0 storage 0xEE3aAa1A... (CLOUD): request 174, created
# 2026-11-20T10:38:25Z.
WORDS = [
    (V05, 34541, 0x66de8ffda797e3de9c05e8fc57b3bf0ec28a930d40b0d285d93c06501cfb5ee5,
     0x6aff2b9201010001010a0101e332a47153c9674998c3787cfa2038b29b00173d, 1795107730),
    (V05, 0, 0x66de8ffda797e3de9c05e8fc57b3bf0ec28a930d40b0d285d93c06501cf6a090,
     0x6186a9ff0101003c012801014f92201f6baa3764426ed14bdfdc70e6930ffe24, 1636215295),
    (CLOUD, 98, 0xc2575a0e9e593c00f959f8c92f12db2869c3395a3b0502d05e2516446f71fc30,
     0x67653295, 1734685333),
    (CLOUD, 174, 0xc2575a0e9e593c00f959f8c92f12db2869c3395a3b0502d05e2516446f71ff28,
     0x6b002321, 1795171105),
]


def array_start(slot):
    return int.from_bytes(keccak(slot.to_bytes(32, "big")), "big")


@pytest.mark.parametrize("layout, request_id, slot, word, created", WORDS)
def test_a_word_read_from_the_chain_decodes_to_the_requests_created_at(layout, request_id, slot, word, created):
    assert do_request_age.created_at_slot(layout, request_id) == slot
    assert do_request_age.created_at(layout, word) == created


def test_the_layouts_place_created_at_as_the_contracts_declare_it():
    # CLOUD: doRequestsList at slot 3, 10 slots a request, createdAt the low
    # 32 bits of the request's slot 1.
    assert do_request_age.created_at_slot(CLOUD, 0) == array_start(3) + 1
    assert do_request_age.created_at_slot(CLOUD, 241) == array_start(3) + 2410 + 1
    assert do_request_age.created_at(CLOUD, (0xABC << 32) | 1795143650) == 1795143650
    # V05: doRequestsList at slot 19, 9 slots a request, createdAt the top 32
    # bits of the request's slot 0.
    assert do_request_age.created_at_slot(V05, 0) == array_start(19)
    assert do_request_age.created_at_slot(V05, 11046) == array_start(19) + 11046 * 9
    assert do_request_age.created_at(V05, (1795107730 << 224) | (2**224 - 1)) == 1795107730


def test_a_pox_answering_eth_storage_address_names_the_storage_first():
    assert do_request_age.candidates(lambda: STORAGE, POX) == [(STORAGE, CLOUD), (POX, V05)]


@pytest.mark.parametrize("absent", [ContractLogicError("execution reverted"), BadFunctionCallOutput("empty")])
def test_a_pox_without_eth_storage_address_is_read_itself_as_v05(absent):
    def call():
        raise absent

    assert do_request_age.candidates(call, POX) == [(POX, V05)]


def test_an_rpc_failure_of_eth_storage_address_propagates():
    def call():
        raise ConnectionError("Remote end closed connection without response")

    with pytest.raises(ConnectionError):
        do_request_age.candidates(call, POX)


def storage_of(address, layout, created, length=None):
    """read_word over the storage of `address` holding requests created at
    `created` under `layout`, its length slot holding `length` (by default
    the number of requests); every other word reads 0. The reads are
    appended to `reads`."""
    words = {(address, layout.length_slot): len(created) if length is None else length}
    for request_id, timestamp in enumerate(created):
        words[(address, do_request_age.created_at_slot(layout, request_id))] = timestamp << layout.created_at_shift
    reads = []

    def read_word(at, slot):
        reads.append((at, slot))
        return words.get((at, slot), 0)

    read_word.reads = reads
    return read_word


def test_bind_takes_the_storage_whose_length_and_newest_request_hold():
    read_word = storage_of(STORAGE, CLOUD, [NOW - 2 * DAY, NOW - 60])
    assert do_request_age.bind([(STORAGE, CLOUD), (POX, V05)], read_word, 2, NOW) == (STORAGE, CLOUD)
    assert read_word.reads == [(STORAGE, 3), (STORAGE, do_request_age.created_at_slot(CLOUD, 1))]


def test_bind_takes_the_pox_as_v05_where_the_storage_is_not_doRequestsList():
    # The storage's length slot holds 0: the v05 PoX is read next.
    read_word = storage_of(POX, V05, [NOW - 2 * DAY, NOW - 60])
    assert do_request_age.bind([(STORAGE, CLOUD), (POX, V05)], read_word, 2, NOW) == (POX, V05)


def test_bind_allows_the_length_slot_requests_added_or_not_yet_held():
    created = [NOW - 3 * DAY + i for i in range(40)]
    for count, length in ((40, 40), (24, 40), (40, 24)):
        read_word = storage_of(STORAGE, CLOUD, created, length)
        assert do_request_age.bind([(STORAGE, CLOUD)], read_word, count, NOW) == (STORAGE, CLOUD)
        # The newest request both hold is the one read.
        assert read_word.reads[-1] == (STORAGE, do_request_age.created_at_slot(CLOUD, min(count, length) - 1))


@pytest.mark.parametrize("length, newest, reason", [
    (0, NOW, "the length slot holds 0 where the PoX counts 30 DO requests"),
    (13, NOW, "the length slot holds 13 where the PoX counts 30 DO requests"),
    (47, NOW, "the length slot holds 47 where the PoX counts 30 DO requests"),
    (30, 0, "DO request 29: createdAt 0 is before 1577836800 (2020-01-01)"),
    (30, 1577836799, "DO request 29: createdAt 1577836799 is before 1577836800 (2020-01-01)"),
    (30, NOW + 61, f"DO request 29: createdAt {NOW + 61} is more than 60 s after the latest block's timestamp {NOW}"),
])
def test_bind_refuses_an_implausible_length_or_newest_created_at(length, newest, reason):
    created = [NOW - DAY] * 29 + [newest]
    read_word = storage_of(STORAGE, CLOUD, created, length)
    with pytest.raises(AgeUnknown) as refused:
        do_request_age.bind([(STORAGE, CLOUD)], read_word, 30, NOW)
    assert str(refused.value) == f"cloud at {STORAGE}: {reason}"


def test_bind_accepts_the_bounds_of_plausible():
    for newest in (1577836800, NOW + 60):
        read_word = storage_of(STORAGE, CLOUD, [newest])
        assert do_request_age.bind([(STORAGE, CLOUD)], read_word, 1, NOW) == (STORAGE, CLOUD)


def test_bind_names_every_candidates_refusal():
    read_word = storage_of(STORAGE, CLOUD, [], length=5)
    with pytest.raises(AgeUnknown) as refused:
        do_request_age.bind([(STORAGE, CLOUD), (POX, V05)], read_word, 30, NOW)
    assert str(refused.value) == (f"cloud at {STORAGE}: the length slot holds 5 where the PoX counts 30 DO requests; "
                                  f"v05 at {POX}: the length slot holds 0 where the PoX counts 30 DO requests")


def search(created, low, cutoff):
    """(first_within over `created` from `low` to its end at `cutoff`, the
    ids read)."""
    read = []

    def read_created_at(request_id):
        read.append(request_id)
        return created[request_id]

    return do_request_age.first_within(read_created_at, low, len(created), cutoff), read


def test_the_boundary_lies_at_the_first_request_at_or_after_the_cutoff():
    created = [1000 + 10 * i for i in range(100)]
    for cutoff, expected in ((1000, 0), (1001, 1), (1010, 1), (1500, 50), (1505, 51), (1990, 99)):
        assert search(created, 0, cutoff)[0] == expected, cutoff


def test_every_request_old_puts_the_boundary_at_the_count():
    created = [1000 + 10 * i for i in range(100)]
    boundary, read = search(created, 0, 5000)
    assert boundary == 100
    assert len(read) <= 1 + math.ceil(math.log2(100))


def test_every_request_recent_takes_one_read():
    created = [1000 + 10 * i for i in range(100)]
    assert search(created, 0, 1000) == (0, [0])
    assert search(created, 0, 0) == (0, [0])


def test_equal_timestamps_take_the_first_of_them():
    # Requests added in one block share its timestamp.
    created = [100, 100, 200, 200, 200, 200, 300, 300]
    assert search(created, 0, 200)[0] == 2
    assert search(created, 0, 199)[0] == 2
    assert search(created, 0, 201)[0] == 6
    assert search(created, 0, 100)[0] == 0
    assert search(created, 0, 301)[0] == 8
    assert search([500] * 9, 0, 500) == (0, [0])
    assert search([500] * 9, 0, 501)[0] == 9


def test_no_request_from_low_reads_nothing():
    assert search([1, 2, 3], 3, 0) == (3, [])
    assert search([], 0, 0) == (0, [])


def test_the_boundary_moves_forward_as_the_chain_time_advances():
    # A request an hour apart; the latest block an hour later each search,
    # which starts at the boundary before.
    created = [NOW - 30 * 3600 + 3600 * i for i in range(30)]
    boundary, read = search(created, 0, NOW - DAY)
    assert boundary == 6
    for hours in range(1, 25):
        moved, read = search(created, boundary, NOW + 3600 * hours - DAY)
        assert moved == boundary + 1
        # The boundary before is read first; it is old now, and the search
        # reads the ids above it, never one below it.
        assert read[0] == boundary and min(read) == boundary
        boundary = moved
    assert boundary == 30
    # A boundary that has not moved takes one read.
    assert search(created + [NOW + 31 * 3600], 30, NOW + 30 * 3600 - DAY) == (30, [30])


def test_a_search_over_34542_requests_reads_at_most_17():
    created = list(range(1_600_000_000, 1_600_000_000 + 34542 * 100, 100))
    for cutoff in (created[0] - 1, created[11046], created[34541], created[34541] + 1):
        boundary, read = search(created, 0, cutoff)
        assert len(read) <= 17
        assert boundary == next((i for i, c in enumerate(created) if c >= cutoff), len(created))


def test_boundary_reads_through_the_layout_and_checks_each_created_at():
    created = [NOW - 3 * DAY, NOW - 2 * DAY, NOW - DAY + 1, NOW - 60]
    read_word = storage_of(POX, V05, created)
    assert do_request_age.boundary(read_word, POX, V05, 0, 4, NOW, DAY) == 2
    assert do_request_age.boundary(read_word, POX, V05, 0, 4, NOW + 2, DAY) == 3
    assert do_request_age.boundary(read_word, POX, V05, 0, 4, NOW + 2 * DAY, DAY) == 4


def test_a_created_at_of_0_is_not_held_and_any_other_implausible_one_unknown():
    with pytest.raises(NotHeld, match="DO request 7 reads createdAt 0"):
        do_request_age.checked_created_at(7, 0, NOW)
    with pytest.raises(AgeUnknown, match="DO request 7: createdAt 12 is before"):
        do_request_age.checked_created_at(7, 12, NOW)
    with pytest.raises(AgeUnknown, match="more than 60 s after"):
        do_request_age.checked_created_at(7, NOW + 61, NOW)
    assert do_request_age.checked_created_at(7, NOW + 60, NOW) == NOW + 60
    # A request the storage's node does not hold yet ends the search.
    read_word = storage_of(STORAGE, CLOUD, [NOW - 60])
    with pytest.raises(NotHeld):
        do_request_age.boundary(read_word, STORAGE, CLOUD, 1, 2, NOW, DAY)


def test_the_release_file_sets_86400_for_every_network(monkeypatch):
    assert dotenv_values(NODE / ".env")["DO_REQUEST_MAX_AGE_SECONDS"] == "86400"
    assert config.do_request_max_age_seconds_default == 86400
    monkeypatch.setattr(os, "environ", {})
    config.load_dotenv(NODE / ".env")
    for name in config.available_networks():
        family = name.split("_")[0].upper()
        parser = argparse.ArgumentParser()
        parser.add_argument("--network", nargs="+")
        config.add_network_override_arguments(parser, [family])
        (network,) = config.parse_networks(parser.parse_args(["--network", name]), parser, [family])
        assert network.do_request_max_age_seconds == 86400, name


START = """
import sys, types
sys.modules["dependency_manager"] = types.SimpleNamespace(check_dependencies=lambda: None)
import config
network_names = list(config.NETWORKS.keys())
parser = config.parse_arguments(network_names)
args, unknown_args = parser.parse_known_args()
print(" ".join(str(n.do_request_max_age_seconds) for n in config.parse_networks(args, parser, network_names)))
"""


def started(variables, *arguments):
    """The do_request_max_age_seconds of polygon_mainnet and polygon_amoy in
    a process started in node/, where config.py loads .env, with
    `variables` added to this process's environment and `arguments` after
    the agent's -k and -n."""
    environment = {name: value for name, value in os.environ.items() if "DO_REQUEST_MAX_AGE_SECONDS" not in name}
    environment.update(variables)
    done = subprocess.run([sys.executable, "-c", START, "-k", "none", "-n", "polygon_mainnet", "polygon_amoy",
                           *arguments], cwd=NODE, env=environment, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return [int(value) for value in done.stdout.split()]


def test_the_variables_and_the_argument_set_a_networks_limit():
    assert started({}) == [86400, 86400]
    assert started({"DO_REQUEST_MAX_AGE_SECONDS": "3600"}) == [3600, 3600]
    assert started({"POLYGON_MAINNET_DO_REQUEST_MAX_AGE_SECONDS": "7200"}) == [7200, 86400]
    assert started({"POLYGON_MAINNET_DO_REQUEST_MAX_AGE_SECONDS": "7200"},
                   "--polygon_mainnet_do_request_max_age_seconds", "600") == [600, 86400]


def test_a_limit_below_1_second_is_refused():
    environment = {name: value for name, value in os.environ.items() if "DO_REQUEST_MAX_AGE_SECONDS" not in name}
    environment["POLYGON_MAINNET_DO_REQUEST_MAX_AGE_SECONDS"] = "0"
    done = subprocess.run([sys.executable, "-c", START, "-k", "none", "-n", "polygon_mainnet"], cwd=NODE,
                          env=environment, capture_output=True, text=True)
    assert done.returncode != 0
    assert "POLYGON_MAINNET_DO_REQUEST_MAX_AGE_SECONDS must be at least 1 second, not 0" in done.stderr
