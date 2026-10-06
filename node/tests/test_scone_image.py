"""scone_image.verify against registry trees built in memory: the shapes the SDK publishes, and the ways a tree can
fail to be one (refused), fail to be read (an error, tried again later), or be one this module cannot read."""
import gzip
import hashlib
import io
import json
import os
import tarfile
import time

import pytest

import scone_image
from scone_image import CannotVerify, NotAnEnclaveImage, verify

ROOT = "QmTestTree"
BASE = f"/ipfs/{ROOT}/docker/registry/v2"
GZ = "application/vnd.oci.image.layer.v1.tar+gzip"
SECURELOCK_LIB = b"\x7fELF" + b"\0" * 4096 + b"SCONE_HEAP\0SCONE_CONFIG_ID\0/etny-securelock/app\0" + b"\0" * 4096
TRUSTEDZONE_LIB = b"\x7fELF" + b"\0" * 4096 + b"SCONE_HEAP\0/etny-trustedzone/app\0" + b"\0" * 4096
GIB = 1024 ** 3


def tar_bytes(files, fmt=tarfile.PAX_FORMAT, pax=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if pax and name in pax:
                info.pax_headers = pax[name]
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def tar_gz(files):
    return gzip.compress(tar_bytes(files))


def run(tree, role="securelock", scan=GIB, inflate=2 * GIB, timeout=60):
    verify(tree.open, ROOT, role, scan, inflate, timeout)


class Tree:
    """A docker/registry/v2 tree under /ipfs/ROOT, opened the way the agent opens IPFS paths."""

    def __init__(self):
        self.files = {}
        self.cut = {}
        self.timeouts = []

    def blob(self, data):
        d = hashlib.sha256(data).hexdigest()
        self.files[f"{BASE}/blobs/sha256/{d[:2]}/{d}/data"] = data
        return f"sha256:{d}"

    def image(self, repository, layers, index=True, platform=("linux", "amd64"), sizes=None, media_types=None):
        descriptors = [{"mediaType": GZ if media_types is None else media_types[i], "digest": self.blob(layer),
                        "size": len(layer) if sizes is None else sizes[i]}
                       for i, layer in enumerate(layers)]
        top = self.blob(json.dumps({"schemaVersion": 2, "layers": descriptors}).encode())
        if index:
            attestation = self.blob(b"{}")
            top = self.blob(json.dumps({"schemaVersion": 2, "manifests": [
                {"digest": top, "platform": {"os": platform[0], "architecture": platform[1]}},
                {"digest": attestation, "platform": {"os": "unknown", "architecture": "unknown"}},
            ]}).encode())
        self.files[f"{BASE}/repositories/{repository}/_manifests/tags/latest/current/link"] = top.encode()
        return [d["digest"] for d in descriptors]

    def path_of(self, digest):
        hexd = digest.split(":")[1]
        return f"{BASE}/blobs/sha256/{hexd[:2]}/{hexd}/data"

    def open(self, path, seconds):
        self.timeouts.append(seconds)
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        return io.BytesIO(data[:self.cut[path]] if path in self.cut else data)


def sdk_securelock(tree):
    return tree.image("etny-securelock", [
        tar_gz({"bin/sh": b"#!" + b"x" * 2048}),
        tar_gz({"lib/libbinary-fs.so": SECURELOCK_LIB}),
        tar_gz({"etc/hostname": b"enclave"}),
    ])


def test_a_securelock_the_sdk_builds_passes_through_its_image_index():
    tree = Tree()
    sdk_securelock(tree)
    run(tree)


def test_each_read_is_given_the_time_the_verification_has_left():
    tree = Tree()
    sdk_securelock(tree)
    run(tree, timeout=30)
    assert tree.timeouts and all(1 <= s <= 30 for s in tree.timeouts)


def test_a_trustedzone_passes_under_its_own_role_and_not_as_a_securelock():
    tree = Tree()
    tree.image("etny-trustedzone", [tar_gz({"./lib/libbinary-fs.so": TRUSTEDZONE_LIB})], index=False)
    run(tree, role="trustedzone")
    with pytest.raises(NotAnEnclaveImage, match="no etny-securelock repository"):
        run(tree)


def test_an_uncompressed_layer_and_a_multi_member_gzip_layer_are_read():
    tree = Tree()
    tree.image("etny-securelock", [tar_bytes({"lib/libbinary-fs.so": SECURELOCK_LIB})],
               media_types=["application/vnd.oci.image.layer.v1.tar"])
    run(tree)
    raw = tar_bytes({"etc/a": b"a" * 3000, "lib/libbinary-fs.so": SECURELOCK_LIB})
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(raw[:4096]) + gzip.compress(raw[4096:])])
    run(tree)


def test_a_zstd_layer_cannot_be_verified_and_is_not_refused():
    tree = Tree()
    tree.image("etny-securelock", [b"\x28\xb5\x2f\xfd" + os.urandom(64)],
               media_types=["application/vnd.oci.image.layer.v1.tar+zstd"])
    with pytest.raises(CannotVerify):
        run(tree)


@pytest.mark.parametrize("lib, missing", [
    (b"\x7fELF SCONE_HEAP only", "securelock"),
    (b"\x7fELF /etny-securelock/ without the runtime", "SCONE_"),
])
def test_a_library_without_both_markers_is_refused(lib, missing):
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({"lib/libbinary-fs.so": lib})])
    with pytest.raises(NotAnEnclaveImage, match=f"does not contain {missing}"):
        run(tree)


def test_a_tree_without_the_library_is_refused():
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({"bin/sh": b"x"}), tar_gz({"lib/other.so": b"SCONE_ securelock"})])
    with pytest.raises(NotAnEnclaveImage, match="no layer holds lib/libbinary-fs.so"):
        run(tree)


def test_an_index_without_a_linux_amd64_image_is_refused():
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({"lib/libbinary-fs.so": SECURELOCK_LIB})], platform=("linux", "arm64"))
    with pytest.raises(NotAnEnclaveImage, match="no linux/amd64 image"):
        run(tree)


def test_a_marker_split_across_two_reads_is_found(monkeypatch):
    monkeypatch.setattr(scone_image, "CHUNK", 7)
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({"lib/libbinary-fs.so": b"abcSCONE_xyzsecurelock"})])
    run(tree)


def test_the_scan_stops_at_its_budget_of_layer_bytes():
    tree = Tree()
    tree.image("etny-securelock", [
        tar_gz({"lib/libbinary-fs.so": SECURELOCK_LIB}),
        tar_gz({"padding": os.urandom(200_000)}),
    ])
    with pytest.raises(NotAnEnclaveImage, match="within the first 50000 bytes"):
        run(tree, scan=50_000)


def test_a_layer_that_decompresses_past_the_budget_is_refused_without_inflating_it():
    tree = Tree()
    bomb = tar_gz({"lib/libbinary-fs.so": b"\0" * (64 * 1024 * 1024)})
    tree.image("etny-securelock", [bomb])
    started = time.monotonic()
    with pytest.raises(NotAnEnclaveImage, match="over what the verification may read|decompress to more than"):
        run(tree, inflate=8 * 1024 * 1024)
    assert time.monotonic() - started < 5
    assert len(bomb) < 200_000


def test_entries_skipped_on_the_way_to_the_library_count_against_the_decompression_budget():
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({"padding": b"\0" * (64 * 1024 * 1024),
                                           "lib/libbinary-fs.so": SECURELOCK_LIB})])
    with pytest.raises(NotAnEnclaveImage, match="decompress to more than 8388608 bytes"):
        run(tree, inflate=8 * 1024 * 1024)


def test_a_layer_of_many_entries_is_refused_at_the_entry_bound(monkeypatch):
    monkeypatch.setattr(scone_image, "MEMBERS_MAX", 1000)
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({f"f{i}": b"" for i in range(1500)})])
    with pytest.raises(NotAnEnclaveImage, match="within the first 1000 archive entries"):
        run(tree)


def test_a_layer_that_is_not_a_tar_archive_is_refused():
    tree = Tree()
    tree.image("etny-securelock", [tar_gz({"lib/libbinary-fs.so": SECURELOCK_LIB}), os.urandom(100_000)])
    with pytest.raises(NotAnEnclaveImage, match="not a readable tar archive|not a tar header"):
        run(tree)


def test_long_names_in_pax_and_gnu_archives_are_followed_to_the_library():
    long_name = "usr/share/" + "d" * 150 + "/file"
    for fmt in (tarfile.PAX_FORMAT, tarfile.GNU_FORMAT):
        tree = Tree()
        tree.image("etny-securelock", [gzip.compress(tar_bytes(
            {long_name: b"x" * 700, "lib/libbinary-fs.so": SECURELOCK_LIB}, fmt=fmt,
            pax={"lib/libbinary-fs.so": {"comment": "built by the SDK"}} if fmt == tarfile.PAX_FORMAT else None))])
        run(tree)


def test_an_extended_header_over_the_bound_is_refused_before_it_is_read():
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(tar_bytes(
        {"lib/libbinary-fs.so": SECURELOCK_LIB}, pax={"lib/libbinary-fs.so": {"comment": "a" * 2_000_000}}))])
    with pytest.raises(NotAnEnclaveImage, match="extended tar header of"):
        run(tree)


def test_a_pax_header_of_digits_is_read_in_linear_time():
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(tar_bytes(
        {"lib/libbinary-fs.so": SECURELOCK_LIB}, pax={"lib/libbinary-fs.so": {"comment": "7" * 900_000}}))])
    started = time.monotonic()
    run(tree)
    assert time.monotonic() - started < 2


def test_a_layer_of_many_empty_gzip_members_is_refused_at_once():
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(b"") * 100_000 + tar_gz({"lib/libbinary-fs.so": SECURELOCK_LIB})])
    started = time.monotonic()
    with pytest.raises(NotAnEnclaveImage, match="more than 64 gzip members"):
        run(tree)
    assert time.monotonic() - started < 2


def test_a_layer_of_64_gzip_members_is_read_and_one_of_65_is_refused():
    raw = tar_bytes({"lib/libbinary-fs.so": SECURELOCK_LIB})
    tree = Tree()
    tree.image("etny-securelock", [b"".join(gzip.compress(raw[i:i + 1]) for i in range(63)) + gzip.compress(raw[63:])])
    run(tree)
    tree = Tree()
    tree.image("etny-securelock", [b"".join(gzip.compress(raw[i:i + 1]) for i in range(64)) + gzip.compress(raw[64:])])
    with pytest.raises(NotAnEnclaveImage, match="more than 64 gzip members"):
        run(tree)


def test_extended_headers_and_pax_records_are_counted(monkeypatch):
    monkeypatch.setattr(scone_image, "EXTENDED_HEADERS_MAX", 3)
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(tar_bytes(
        {f"f{i}": b"" for i in range(5)} | {"lib/libbinary-fs.so": SECURELOCK_LIB},
        pax={f"f{i}": {"comment": "x"} for i in range(5)}))])
    with pytest.raises(NotAnEnclaveImage, match="first 3 extended tar headers"):
        run(tree)
    monkeypatch.setattr(scone_image, "EXTENDED_HEADERS_MAX", 1000)
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(tar_bytes(
        {"lib/libbinary-fs.so": SECURELOCK_LIB}, pax={"lib/libbinary-fs.so": {f"k{i}": "v" for i in range(300)}}))])
    with pytest.raises(NotAnEnclaveImage, match="more than 256 records"):
        run(tree)


def test_a_pax_size_of_more_than_20_digits_is_refused():
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(tar_bytes(
        {"lib/libbinary-fs.so": SECURELOCK_LIB}, pax={"lib/libbinary-fs.so": {"size": "9" * 5000}}))])
    with pytest.raises(NotAnEnclaveImage, match="at most 20 digits"):
        run(tree)


def test_a_manifest_nested_past_the_recursion_limit_is_refused():
    tree = Tree()
    nested = tree.blob(b"[" * 100_000)
    tree.files[f"{BASE}/repositories/etny-securelock/_manifests/tags/latest/current/link"] = nested.encode()
    with pytest.raises(NotAnEnclaveImage, match="not JSON"):
        run(tree)


def test_a_header_with_a_size_that_is_not_a_number_is_refused():
    header = bytearray(tar_bytes({"lib/libbinary-fs.so": SECURELOCK_LIB}, fmt=tarfile.USTAR_FORMAT)[:512])
    header[124:136] = b"9z9z9z9z9z9\0"
    header[148:156] = b"        "
    header[148:156] = b"%06o\0 " % sum(header)
    tree = Tree()
    tree.image("etny-securelock", [gzip.compress(bytes(header) + bytes(1024))])
    with pytest.raises(NotAnEnclaveImage, match="size is not a number"):
        run(tree)


def test_a_layer_longer_than_it_declares_is_refused():
    tree = Tree()
    good = tar_gz({"lib/libbinary-fs.so": SECURELOCK_LIB})
    top = tar_gz({"etc/hostname": b"enclave"})
    tree.image("etny-securelock", [good, top], sizes=[len(good), len(top) - 8])
    with pytest.raises(NotAnEnclaveImage, match="holds more than"):
        run(tree)


def test_a_stream_cut_short_in_a_layer_above_the_library_is_an_error_not_a_refusal():
    tree = Tree()
    digests = sdk_securelock(tree)
    top = tree.path_of(digests[-1])
    tree.cut[top] = len(tree.files[top]) // 2
    with pytest.raises(OSError):
        run(tree)


def test_a_stream_cut_short_inside_the_library_is_an_error_not_a_refusal():
    tree = Tree()
    lib = b"\x7fELF" + os.urandom(300_000) + b"SCONE_ securelock"
    (digest,) = tree.image("etny-securelock", [tar_gz({"lib/libbinary-fs.so": lib})])
    path = tree.path_of(digest)
    tree.cut[path] = len(tree.files[path]) // 2
    with pytest.raises(OSError):
        run(tree)


def test_a_verification_past_its_deadline_is_an_error_not_a_refusal():
    tree = Tree()
    sdk_securelock(tree)
    with pytest.raises(TimeoutError):
        run(tree, timeout=-1)


def test_a_link_that_names_no_digest_is_refused_with_the_value_cut_short():
    tree = Tree()
    tree.files[f"{BASE}/repositories/etny-securelock/_manifests/tags/latest/current/link"] = b"x" * 200
    with pytest.raises(NotAnEnclaveImage, match="not a sha256 digest") as refused:
        run(tree)
    assert len(str(refused.value)) < 150
