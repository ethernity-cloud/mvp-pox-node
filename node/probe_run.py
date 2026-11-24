"""The node's memory probe run (MATCHING-SYNC-PLAN.md B02; MATCHING-DESIGN.md
7.24, D147, D189, D191; OD17 A, OD20 A, OD22 A; pox-smart-contract-litvm
2f600f6 spec/bench-record.json inputs, session and identity): which probe
the agent runs at which enclave size, and the inputs it writes to the
integration bucket's .env; etny-node.py runs each under the task slot as it
runs an integration test, from the image's own compose (its entrypoint, the
EDMM and memory settings the compose registered with the image holds, the
key /private/ecld-probe/key.pem the network's CAS gives the size's session),
reads probe.<g>.etny and probe.time.<g>.etny before the bucket is wiped, and
keeps the record bench_record.bench_record_from admits.

A size's probe is found as a trustedzone is: the newest ECImageRegistryV4
default release that lists a probe measurement of the size
(probeMrenclave(index, size) not zero), and the image the registry's
trustedzone table holds under the size's name, the network's probe_image
setting with {size} replaced (<NETWORK>_PROBE_IMAGE); the image's
certificate is the probe key a record of that size verifies under. No size
is run without both."""
from collections import namedtuple

import capability

SIZE_PLACEHOLDER = '{size}'
# A size's probe: its enclave size in GiB, the release that lists it and its
# measurement there, and the image the registry holds under the size's name
# (its IPFS hash, its certificate and its compose's IPFS hash).
ProbeImage = namedtuple('ProbeImage', 'size release mrenclave name image_hash cert compose_hash')


def probe_image_name(pattern, size):
    """The registry name of the probe of `size` GiB: `pattern` with
    SIZE_PLACEHOLDER replaced; None for a pattern that is empty or names no
    size."""
    pattern = str(pattern or '').strip()
    if SIZE_PLACEHOLDER not in pattern:
        return None
    return pattern.replace(SIZE_PLACEHOLDER, str(int(size)))


def newest_release_listing(count, probe_mrenclave, size):
    """(index, measurement) of the newest of the `count` default releases
    whose probeMrenclave(index, size) is not zero; None when none lists the
    size."""
    for index in range(int(count) - 1, -1, -1):
        measurement = probe_mrenclave(index, int(size))
        value = measurement.hex() if isinstance(measurement, (bytes, bytearray)) else str(measurement)
        if int(value, 16) != 0:
            return index, measurement
    return None


def find_probe(size, pattern, release_count, probe_mrenclave, latest_image):
    """The ProbeImage of `size` GiB, None without a name for the size
    (probe_image_name), without a release listing the size
    (newest_release_listing over `release_count()` and `probe_mrenclave`),
    or without an image the registry holds under the name (`latest_image(name)`:
    (IPFS hash, certificate, compose hash), None for none) or with an empty
    certificate."""
    name = probe_image_name(pattern, size)
    if name is None:
        return None
    listed = newest_release_listing(release_count(), probe_mrenclave, size)
    if listed is None:
        return None
    found = latest_image(name)
    if found is None or not str(found[1] or '').strip():
        return None
    image_hash, cert, compose_hash = found
    return ProbeImage(int(size), listed[0], listed[1], name, image_hash, cert, compose_hash)


def sizes_to_try(memory_gib):
    """The listed enclave sizes the node runs a probe at, largest first: those
    at or below its offer's memory figure (the order of a size holds its
    whole heap)."""
    return [size for size in reversed(capability.ENCLAVE_SIZES_GIB) if size <= int(memory_gib)]


def probe_env(node_address, storage_free_bytes, epc_mib):
    """The three node values of the probe's inputs in the integration
    bucket's .env (bench-record.json inputs): ETNY_NODE_ADDRESS, the wallet
    the DP requests are added with, ETNY_STORAGE_FREE_BYTES, the free bytes
    of the task filesystem, and ETNY_EPC_MIB, the EPC claim
    (epc_claim.read_claim)."""
    return {
        'ETNY_NODE_ADDRESS': str(node_address).lower(),
        'ETNY_STORAGE_FREE_BYTES': int(storage_free_bytes),
        'ETNY_EPC_MIB': int(epc_mib),
    }


def run_largest(sizes, find, run, admit, log):
    """(probe, record) of the largest size of `sizes` whose probe is found
    (`find(size)`, a ProbeImage or None), runs (`run(probe)`: the two
    objects' texts, None when the run produced none) and writes a record
    `admit(probe, objects)` admits ((record, None) or (None, reason));
    (None, None) when no size does. Each size passed over is logged."""
    for size in sizes:
        probe = find(size)
        if probe is None:
            log(f"Probe {size} GiB: no listed release or no registered image of the size; not run")
            continue
        objects = run(probe)
        if objects is None:
            log(f"Probe {size} GiB ({probe.name}, {probe.image_hash}): it wrote no record")
            continue
        record, reason = admit(probe, objects)
        if record is not None:
            return probe, record
        log(f"Probe {size} GiB ({probe.name}, {probe.image_hash}): its record is not admitted: {reason}")
    return None, None
