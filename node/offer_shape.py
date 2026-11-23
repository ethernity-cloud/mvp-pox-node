"""The memory and storage an offer states on a network with PoX 2.0 matching
(MATCHING-DESIGN.md 7.4; the owner's decisions of 2026-11-23): the host's
total less the larger of a reserve and what is in use, at most what the
bench's signed record carried in the DP request shows.

- memory: total - max(reserve, in use) = min(total - reserve, MemAvailable),
  the kernel's estimate of what can be allocated without swapping;
- storage: total - max(reserve, used) = min(total - reserve, free) on the
  filesystem the tasks are staged on.

The bench (the attested probe of build step 27) allocates and touches its
heap and reads the storage, and signs both in its record (encmem=,
custorage=); the offer states neither figure above the record's
(bounded). The memory figure bounds the enclave sizes the node is
benchmarked at: the listed sizes at or below it (bench_sizes)."""
from collections import namedtuple

GIB = 2**30
# The PoX bounds an offer's memory and storage to 1..255 GB.
OFFER_RESOURCE_MIN = 1
OFFER_RESOURCE_MAX = 255

# What the offer states, in whole GiB.
Shape = namedtuple('Shape', 'memory_gib storage_gib')


def memory_gib(total_bytes, available_bytes, reserve_gib):
    """min(total - reserve, MemAvailable) in whole GiB, at least 0."""
    return max(0, min(int(total_bytes) - int(reserve_gib) * GIB, int(available_bytes)) // GIB)


def storage_gib(total_bytes, free_bytes, reserve_gib):
    """min(total - reserve, free) in whole GiB, at least 0."""
    return max(0, min(int(total_bytes) - int(reserve_gib) * GIB, int(free_bytes)) // GIB)


def offer_shape(total_memory, available_memory, memory_reserve_gib, total_storage, free_storage,
                storage_reserve_gib):
    """The Shape an offer states, each figure at most 255."""
    return Shape(min(OFFER_RESOURCE_MAX, memory_gib(total_memory, available_memory, memory_reserve_gib)),
                 min(OFFER_RESOURCE_MAX, storage_gib(total_storage, free_storage, storage_reserve_gib)))


def bounded(shape, record_memory, record_storage, refused_storage):
    """`shape` at most the memory and the storage the bench's record shows
    (None: the record carries no such reading), its storage below the figure
    a reason-11 cancel refused under that record (None: none)."""
    memory = shape.memory_gib if record_memory is None else min(shape.memory_gib, int(record_memory))
    storage = shape.storage_gib if record_storage is None else min(shape.storage_gib, int(record_storage))
    if refused_storage is not None:
        storage = min(storage, max(0, int(refused_storage) - 1))
    return Shape(memory, storage)


def bench_sizes(memory, sizes):
    """The listed enclave sizes (GiB) at or below the offer's memory figure:
    the sizes the node is benchmarked at."""
    return [size for size in sizes if size <= int(memory)]
