"""The age of a DO request, read from the storage of the PoX.

Every DO request stores createdAt, uint32 of the timestamp of the block that
added it, and no getter of the PoX returns it: the agent reads it with
eth_getStorageAt from the contract that holds doRequestsList. Where the PoX
answers ethStorageAddress(), that is the storage contract it names, laid out
as CLOUD (EthernityCloudStorage, behind every PoX of node/.env but bloxberg
mainnet's and bloxberg_testnet_pox10's); where it does not, the PoX itself,
laid out as V05 (bloxberg mainnet's PoX 1.0 and bloxberg_testnet_pox10's,
the testnet copy of its code: each proxy holds its storage). A layout is
taken once its length slot and its newest request's createdAt are plausible
(bind).
Requests are appended in block order, so createdAt does not decrease with the
id: the requests created at or after a cutoff are the ids from a boundary on
(first_within, boundary)."""
from collections import namedtuple
from datetime import datetime, timezone

from eth_utils import keccak

from pox20 import FUNCTION_ABSENT

# Where a layout keeps doRequestsList: the slot of its length; the slots of
# one Models.DORequest; the slot within it that holds createdAt and the bit
# createdAt starts at.
Layout = namedtuple('Layout', 'name length_slot struct_slots created_at_slot created_at_shift')

# EthernityCloudStorage (pox-smart-contract-litvm EthernityCloudStorage.sol,
# Models.sol): owner and newOwner (Owned) at slots 0 and 1, allowedCallers
# (SharedEternalStorage) at 2, doRequestsList at 3. A DORequest takes 10
# slots: downer, the five resource fields, instances and bookedInstances fill
# 31 bytes of its slot 0, createdAt the low 32 bits of its slot 1.
CLOUD = Layout('cloud', 3, 10, 1, 0)
# EthernityStorage of the bloxberg mainnet PoX and of its testnet copy
# (pox-smart-contract contracts/v05 EthernityStorage.sol, Models.sol):
# doRequestsList at slot 19.
# A DORequest takes 9 slots: downer, the six resource fields, instances and
# bookedInstances fill 28 bytes of its slot 0, createdAt its top 32 bits.
V05 = Layout('v05', 19, 9, 0, 224)

# The earliest plausible createdAt, 2020-01-01T00:00:00Z: before every PoX
# deployment (bloxberg mainnet's request 0 is of 2021-11-06).
EARLIEST_CREATED_AT = 1577836800
# How far a plausible createdAt may lie after the latest block's timestamp:
# the block and the storage are read in separate requests, which a
# load-balanced RPC answers from nodes a few blocks apart.
CREATED_AT_AHEAD_SECONDS = 60
# How far the length slot may lie from the DO request count read before it:
# requests added between the two reads, or held by one node of a
# load-balanced RPC and not yet by another.
LENGTH_SLACK = 16

UINT32 = 0xFFFFFFFF


class AgeUnknown(Exception):
    """No layout is plausible, or a createdAt read is not; str() is the
    reason."""


class NotHeld(Exception):
    """A createdAt read 0: the RPC's node does not hold the request."""


def created_at_slot(layout, request_id):
    """The slot of request `request_id`'s createdAt under `layout`: the
    array's data starts at keccak256 of its length slot."""
    start = int.from_bytes(keccak(layout.length_slot.to_bytes(32, 'big')), 'big')
    return (start + int(request_id) * layout.struct_slots + layout.created_at_slot) % 2**256


def created_at(layout, word):
    """createdAt in `word`, the integer of the storage word at
    created_at_slot."""
    return (int(word) >> layout.created_at_shift) & UINT32


def candidates(storage_address, pox_address):
    """[(address, layout)] in the order bind tries them: the storage
    `storage_address()` (the PoX's ethStorageAddress().call) names under
    CLOUD when the call answers, then the PoX under V05. A failure other
    than a missing function propagates."""
    try:
        storage = storage_address()
    except FUNCTION_ABSENT:
        return [(pox_address, V05)]
    return [(storage, CLOUD), (pox_address, V05)]


def created_at_refusal(created, head_timestamp):
    """Why `created` is no plausible createdAt at the latest block's
    `head_timestamp`, None when it is one."""
    if created < EARLIEST_CREATED_AT:
        return f"createdAt {created} is before {EARLIEST_CREATED_AT} (2020-01-01)"
    if created > head_timestamp + CREATED_AT_AHEAD_SECONDS:
        return (f"createdAt {created} is more than {CREATED_AT_AHEAD_SECONDS} s after the latest block's "
                f"timestamp {head_timestamp}")
    return None


def length_refusal(count, length):
    """Why a length slot holding `length` is not doRequestsList's at a DO
    request count `count` read before it, None when it is taken as that."""
    if length < 1 or abs(length - count) > LENGTH_SLACK:
        return f"the length slot holds {length} where the PoX counts {count} DO requests"
    return None


def bind(tried, read_word, count, head_timestamp):
    """(address, layout) of the first of `tried` (candidates) whose length
    slot length_refusal takes at `count` DO requests and whose newest
    request both hold, min(count, length) - 1, reads a createdAt plausible
    at the latest block's `head_timestamp`; read_word(address, slot) is the
    storage word as an int. Raises AgeUnknown naming each refusal when none
    is taken."""
    refusals = []
    for address, layout in tried:
        length = read_word(address, layout.length_slot)
        refusal = length_refusal(count, length)
        if refusal is None:
            newest = min(count, length) - 1
            refusal = created_at_refusal(created_at(layout, read_word(address, created_at_slot(layout, newest))),
                                         head_timestamp)
            if refusal is not None:
                refusal = f"DO request {newest}: {refusal}"
        if refusal is None:
            return address, layout
        refusals.append(f"{layout.name} at {address}: {refusal}")
    raise AgeUnknown('; '.join(refusals))


def checked_created_at(request_id, created, head_timestamp):
    """`created`, request `request_id`'s createdAt, when it is plausible at
    the latest block's `head_timestamp`. Raises NotHeld for 0 and AgeUnknown
    for any other implausible value."""
    if created == 0:
        raise NotHeld(f"DO request {request_id} reads createdAt 0: the RPC's node does not hold it")
    refusal = created_at_refusal(created, head_timestamp)
    if refusal is not None:
        raise AgeUnknown(f"DO request {request_id}: {refusal}")
    return created


def first_within(read_created_at, low, high, cutoff):
    """The lowest id in [low, high) whose read_created_at(id) is at or after
    `cutoff`, `high` when none is; createdAt does not decrease with the id.
    `low` is read first, so a boundary that has not moved takes one read;
    otherwise a binary search over the ids above it takes at most
    ceil(log2(high - low)) more."""
    if low >= high:
        return high
    if read_created_at(low) >= cutoff:
        return low
    low += 1
    while low < high:
        middle = (low + high) // 2
        if read_created_at(middle) >= cutoff:
            high = middle
        else:
            low = middle + 1
    return low


def boundary(read_word, address, layout, low, count, head_timestamp, max_age):
    """The lowest id in [low, count) of a request created at or after
    `head_timestamp` - `max_age`, `count` when none is (first_within), each
    createdAt read with read_word(address, slot) under `layout` and checked
    (checked_created_at)."""
    def read(request_id):
        word = read_word(address, created_at_slot(layout, request_id))
        return checked_created_at(request_id, created_at(layout, word), head_timestamp)

    return first_within(read, low, count, head_timestamp - max_age)


def utc(timestamp):
    """`timestamp` as an ISO 8601 UTC time."""
    return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
