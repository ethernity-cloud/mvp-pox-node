"""Whether an image tree on IPFS is an enclave image the Ethernity CLOUD SDK builds.

A registered image's IPFS tree is a Docker registry (docker/registry/v2). The image passes when its repository's
`latest` tag resolves, for linux/amd64, to a manifest one of whose layers holds lib/libbinary-fs.so -- the SCONE
binary-fs the enclave's code is compiled into -- and that library carries both the SCONE runtime's environment
variable names (SCONE_) and the enclave's role (securelock or trustedzone). publickey.ethernity.cloud refuses a
securelock on the same library (extract-publickey-hash.sh: "securelock" in its .rodata). Passing is a property of
the tree's shape and strings, not an attestation of the enclave: a tree built to look like one passes.

The layers are read newest first, and only until the library is found, within bounds per verification: the layer
bytes read (scan_max_bytes), the bytes they decompress to (inflate_max_bytes), the archive entries walked
(MEMBERS_MAX), the extended headers (EXTENDED_HEADERS_MAX, each at most EXTENDED_MAX bytes and PAX_RECORDS_MAX
records) and the gzip members of a layer (GZIP_MEMBERS_MAX). Archives are walked here, header by header, an extended
header refused before it is read when it is too large, and no entry is held in memory; gzip input is fed to zlib
FEED bytes at a time. A tree that does not show the library within these bounds is refused. IPFS content does not
change, so a refusal stands for these bounds; an error while reading (a timeout, a stream cut short) is not a refusal,
and neither is a layer this module cannot decompress (CannotVerify).
"""
import json
import math
import re
import time
import zlib

BINARY_FS = "lib/libbinary-fs.so"
SCONE_MARKER = b"SCONE_"
# Registry table -> the repository its image tree holds and the role its binary-fs names.
ROLES = {
    "securelock": ("etny-securelock", b"securelock"),
    "trustedzone": ("etny-trustedzone", b"trustedzone"),
}
# Changes with what a verification checks, so a verdict recorded under another version is reached again.
VERSION = 4
LINK_MAX_BYTES = 256
MANIFEST_MAX_BYTES = 1024 * 1024
MEMBERS_MAX = 100_000
EXTENDED_MAX = 1024 * 1024
EXTENDED_HEADERS_MAX = 1000
PAX_RECORDS_MAX = 256
GZIP_MEMBERS_MAX = 64
CHUNK = 1024 * 1024
FEED = 64 * 1024
BLOCK = 512
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
REGULAR = (b"0", b"\0", b"7")
EXTENDED = (b"x", b"g", b"L", b"K")


class NotAnEnclaveImage(Exception):
    """The tree was read and is not an enclave image the SDK builds, within the verification's bounds."""


class CannotVerify(Exception):
    """The tree holds a layer this module cannot decompress; that says nothing about the image."""


class _ShortRead(Exception):
    """A layer's archive ended inside a header or an entry."""


def _shown(value, limit=80):
    """A value taken from the tree, cut to `limit` characters for messages."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


class _Scan:
    """What a verification may still read and walk, and when it must end."""

    def __init__(self, scan_max_bytes, inflate_max_bytes, deadline):
        self.scan_max_bytes = scan_max_bytes
        self.read_remaining = scan_max_bytes
        self.inflate_max_bytes = inflate_max_bytes
        self.inflate_remaining = inflate_max_bytes
        self.members_remaining = MEMBERS_MAX
        self.extended_remaining = EXTENDED_HEADERS_MAX
        self.deadline = deadline

    def seconds_left(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("the verification ran past its deadline")
        return max(1, math.ceil(left))


def _open(open_path, path, scan):
    return open_path(path, scan.seconds_left())


class _LayerReader:
    """One layer blob as stored, read within the scan's byte budget and deadline."""

    def __init__(self, stream, scan):
        self.stream = stream
        self.scan = scan
        self.read_bytes = 0

    def read(self, size):
        self.scan.seconds_left()
        if self.scan.read_remaining <= 0:
            raise NotAnEnclaveImage(f"no {BINARY_FS} within the first {self.scan.scan_max_bytes} bytes of layer data")
        data = self.stream.read(min(size, self.scan.read_remaining))
        self.read_bytes += len(data)
        self.scan.read_remaining -= len(data)
        return data


class _Inflated:
    """A layer's tar stream in chunks: the blob itself, or the blob gunzipped (every gzip member, at most
    GZIP_MEMBERS_MAX), within the scan's budget of decompressed bytes and its deadline. zlib is fed at most FEED bytes
    per call and returns at most CHUNK, so what it holds back between calls, and between members, stays that small."""

    def __init__(self, reader, scan, gzip, head):
        self.reader = reader
        self.scan = scan
        self.gzip = gzip
        self.carry = head
        self.pending = b""
        self.position = 0
        self.inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if gzip else None
        self.members = 1
        self.ended = False

    def _input(self):
        if self.carry:
            data, self.carry = self.carry, b""
            return data
        if self.position >= len(self.pending):
            self.pending = self.reader.read(CHUNK)
            self.position = 0
        data = self.pending[self.position:self.position + FEED]
        self.position += len(data)
        return data

    def _more(self):
        if self.gzip and self.inflater.unconsumed_tail:
            data = self.inflater.unconsumed_tail
        else:
            data = self._input()
            if not data:
                self.ended = True
                return b""
        if not self.gzip:
            return data
        if self.inflater.eof:
            # Data after the end of a member starts the next one.
            self.members += 1
            if self.members > GZIP_MEMBERS_MAX:
                raise NotAnEnclaveImage(f"a layer holds more than {GZIP_MEMBERS_MAX} gzip members")
            self.inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = self.inflater.decompress(data, CHUNK)
        if self.inflater.eof:
            self.carry = self.inflater.unused_data
        return out

    def next_chunk(self):
        """The next chunk of the tar stream; b"" at its end."""
        out = b""
        while not out and not self.ended:
            self.scan.seconds_left()
            out = self._more()
        self.scan.inflate_remaining -= len(out)
        if self.scan.inflate_remaining < 0:
            raise NotAnEnclaveImage(
                f"its layers decompress to more than {self.scan.inflate_max_bytes} bytes before {BINARY_FS}")
        return out


class _TarStream:
    """read(n) over an _Inflated's chunks: n bytes, fewer only at the stream's end."""

    def __init__(self, inflated):
        self.inflated = inflated
        self.buffer = b""
        self.offset = 0

    def read(self, size):
        parts = []
        while size > 0:
            if self.offset >= len(self.buffer):
                self.buffer = self.inflated.next_chunk()
                self.offset = 0
                if not self.buffer:
                    break
            part = self.buffer[self.offset:self.offset + size]
            self.offset += len(part)
            size -= len(part)
            parts.append(part)
        return b"".join(parts)


def _read_exact(tar, size):
    data = tar.read(size)
    if len(data) < size:
        raise _ShortRead()
    return data


def _skip(tar, size):
    while size > 0:
        size -= len(_read_exact(tar, min(size, CHUNK)))


def _padded(size):
    return -(-size // BLOCK) * BLOCK


def _number(field, what):
    """A tar header's numeric field: octal digits, or base-256 when its first byte is 0x80."""
    if field[:1] and field[0] & 0x80:
        if field[0] != 0x80:
            raise NotAnEnclaveImage(f"a tar header's {what} is negative")
        return int.from_bytes(field[1:], "big")
    text = field.split(b"\0", 1)[0].strip(b" ")
    if not text:
        return 0
    if any(c not in b"01234567" for c in text):
        raise NotAnEnclaveImage(f"a tar header's {what} is not a number")
    return int(text, 8)


def _checksum_ok(header):
    stored = _number(header[148:156], "checksum")
    unsigned = sum(header[:148]) + 8 * 32 + sum(header[156:])
    signed = sum(b - 256 if b > 127 else b for b in header[:148] + header[156:]) + 8 * 32
    return stored in (unsigned, signed)


def _pax_records(data):
    """The path and size records of a pax extended header ("<length> <key>=<value>\\n" each, at most PAX_RECORDS_MAX
    of them); the others are read past."""
    records = {}
    i = 0
    count = 0
    while i < len(data):
        if data[i] == 0:
            break
        count += 1
        if count > PAX_RECORDS_MAX:
            raise NotAnEnclaveImage(f"a pax header holds more than {PAX_RECORDS_MAX} records")
        space = data.find(b" ", i, i + 21)
        length_text = data[i:space] if space > i else b""
        if not length_text.isdigit():
            raise NotAnEnclaveImage("a pax header record has no length")
        length = int(length_text)
        if length <= space - i or i + length > len(data) or data[i + length - 1] != 0x0A:
            raise NotAnEnclaveImage("a pax header record is malformed")
        key, sep, value = data[space + 1:i + length - 1].partition(b"=")
        if not sep:
            raise NotAnEnclaveImage("a pax header record has no '='")
        if key in (b"path", b"size"):
            records[key] = value
        i += length
    return records


def _ustar_name(header):
    name = header[0:100].split(b"\0", 1)[0]
    if header[257:262] == b"ustar":
        prefix = header[345:500].split(b"\0", 1)[0]
        if prefix:
            return prefix + b"/" + name
    return name


class _Entry:
    """The data of one archive entry, `size` bytes, read from the tar stream."""

    def __init__(self, tar, size):
        self.tar = tar
        self.remaining = size

    def read(self, size):
        data = self.tar.read(min(size, self.remaining))
        self.remaining -= len(data)
        return data


def _markers_in(f, markers):
    """The markers found in the stream `f`, read to its end or until all are found, and the bytes read."""
    found = set()
    keep = max(len(m) for m in markers) - 1
    carry = b""
    total = 0
    while len(found) < len(markers):
        chunk = f.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        window = carry + chunk
        found.update(m for m in markers if m in window)
        carry = window[-keep:]
    return found, total


def _find_library(tar, scan, marker, repository, digest):
    """True when the archive holds the binary-fs with both markers; False when it ends without it. Raises
    _ShortRead when the archive ends inside a header or an entry."""
    library = BINARY_FS.encode()
    long_name = None
    pax = {}
    while True:
        header = _read_exact(tar, BLOCK)
        if header == bytes(BLOCK):
            return False
        if not _checksum_ok(header):
            raise NotAnEnclaveImage(f"{repository}: layer {digest} holds a block that is not a tar header")
        typeflag = header[156:157]
        size = _number(header[124:136], "size")
        if typeflag in EXTENDED:
            scan.extended_remaining -= 1
            if scan.extended_remaining < 0:
                raise NotAnEnclaveImage(f"no {BINARY_FS} within the first {EXTENDED_HEADERS_MAX} extended tar headers")
            if size > EXTENDED_MAX:
                raise NotAnEnclaveImage(f"{repository}: layer {digest} holds an extended tar header of {size} bytes")
            data = _read_exact(tar, _padded(size))[:size]
            if typeflag == b"x":
                pax = _pax_records(data)
            elif typeflag == b"L":
                long_name = data.split(b"\0", 1)[0]
            continue
        scan.members_remaining -= 1
        if scan.members_remaining < 0:
            raise NotAnEnclaveImage(f"no {BINARY_FS} within the first {MEMBERS_MAX} archive entries")
        if b"size" in pax:
            if not pax[b"size"].isdigit() or len(pax[b"size"]) > 20:
                raise NotAnEnclaveImage("a pax header's size is not a number of at most 20 digits")
            size = int(pax[b"size"])
        name = long_name or pax.get(b"path") or _ustar_name(header)
        long_name, pax = None, {}
        if name.startswith(b"./"):
            name = name[2:]
        if name != library:
            _skip(tar, _padded(size))
            continue
        if typeflag not in REGULAR:
            raise NotAnEnclaveImage(f"{repository}: {BINARY_FS} is not a regular file")
        if size > scan.inflate_remaining:
            raise NotAnEnclaveImage(f"{repository}: {BINARY_FS} is {size} bytes, over what the verification may read")
        markers = (SCONE_MARKER, marker)
        found, read = _markers_in(_Entry(tar, size), markers)
        missing = set(markers) - found
        if not missing:
            return True
        if read < size:
            raise _ShortRead()
        names = ", ".join(sorted(m.decode() for m in missing))
        raise NotAnEnclaveImage(f"{repository}: {BINARY_FS} does not contain {names}")


def _read_small(open_path, path, max_bytes, scan):
    stream = _open(open_path, path, scan)
    try:
        chunks, total = [], 0
        while True:
            scan.seconds_left()
            chunk = stream.read(min(65536, max_bytes + 1 - total))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise NotAnEnclaveImage(f"{path} is larger than {max_bytes} bytes")
    finally:
        stream.close()


def _blob_path(base, digest):
    m = DIGEST.fullmatch(digest) if isinstance(digest, str) else None
    if not m:
        raise NotAnEnclaveImage(f"{_shown(digest)} is not a sha256 digest")
    hexd = m.group(1)
    return f"{base}/blobs/sha256/{hexd[:2]}/{hexd}/data"


def _manifest(open_path, base, digest, repository, scan):
    path = _blob_path(base, digest)
    try:
        raw = _read_small(open_path, path, MANIFEST_MAX_BYTES, scan)
    except FileNotFoundError:
        raise NotAnEnclaveImage(f"{repository}: manifest {digest} is not in the tree")
    try:
        manifest = json.loads(raw)
    except (ValueError, RecursionError):
        raise NotAnEnclaveImage(f"{repository}: manifest {digest} is not JSON this verification reads")
    if not isinstance(manifest, dict):
        raise NotAnEnclaveImage(f"{repository}: manifest {digest} is not a JSON object")
    return manifest


def _compression(layer, head):
    """'gzip' or 'tar' for a layer this module reads; CannotVerify for one it cannot."""
    media_type = layer.get("mediaType") if isinstance(layer.get("mediaType"), str) else ""
    if "zstd" in media_type or head.startswith(ZSTD_MAGIC):
        raise CannotVerify(f"layer {layer.get('digest')} is zstd-compressed")
    if head.startswith(GZIP_MAGIC) or "gzip" in media_type:
        return "gzip"
    return "tar"


def _layer_holds_enclave(open_path, base, layer, marker, scan, repository):
    """True when this layer holds the binary-fs with both markers; False when it does not hold the library."""
    if not isinstance(layer, dict):
        raise NotAnEnclaveImage(f"{repository}: a layer entry is not an object")
    digest = layer.get("digest")
    path = _blob_path(base, digest)
    # The manifest's size is what the stream must deliver: a stream can end early without an error (Kubo reports a
    # failure inside `cat` after the body), and an archive can end early at an entry boundary.
    declared = layer.get("size")
    if not isinstance(declared, int) or declared < 0:
        raise NotAnEnclaveImage(f"{repository}: layer {digest} declares no size")
    try:
        stream = _open(open_path, path, scan)
    except FileNotFoundError:
        raise NotAnEnclaveImage(f"{repository}: layer {digest} is not in the tree")
    reader = _LayerReader(stream, scan)
    try:
        head = reader.read(len(ZSTD_MAGIC))
        kind = _compression(layer, head)
        tar = _TarStream(_Inflated(reader, scan, kind == "gzip", head))
        try:
            if _find_library(tar, scan, marker, repository, digest):
                return True
        except (_ShortRead, zlib.error) as e:
            # A stream that ends before the declared size was cut short; one that goes on held bytes that are not
            # a (gzipped) tar archive.
            if not stream.read(1) and reader.read_bytes < declared:
                raise IOError(f"layer {digest} ended after {reader.read_bytes} of {declared} bytes") from e
            raise NotAnEnclaveImage(f"{repository}: layer {digest} is not a readable tar archive")
        while reader.read(CHUNK):
            pass
        if reader.read_bytes < declared:
            raise IOError(f"layer {digest} ended after {reader.read_bytes} of {declared} bytes")
        if reader.read_bytes > declared:
            raise NotAnEnclaveImage(f"{repository}: layer {digest} holds more than the {declared} bytes it declares")
        return False
    finally:
        stream.close()


def verify(open_path, root, role, scan_max_bytes, inflate_max_bytes, timeout_seconds):
    """Return when the tree `root` holds an enclave image of `role` ("securelock" or "trustedzone") the SDK builds;
    raise NotAnEnclaveImage when it does not, CannotVerify when it holds a layer this module cannot decompress.
    `open_path(path, seconds)` opens an IPFS path for reading (read(n), close()), ending the read after `seconds`,
    and raises FileNotFoundError when the tree has no such path; any other error it raises, and a TimeoutError once
    timeout_seconds have passed, propagate: the tree could not be read, which is not a refusal."""
    repository, marker = ROLES[role]
    scan = _Scan(scan_max_bytes, inflate_max_bytes, time.monotonic() + timeout_seconds)
    base = f"/ipfs/{root}/docker/registry/v2"
    try:
        link = _read_small(open_path, f"{base}/repositories/{repository}/_manifests/tags/latest/current/link",
                           LINK_MAX_BYTES, scan)
    except FileNotFoundError:
        raise NotAnEnclaveImage(f"the tree holds no {repository} repository with a latest tag")
    manifest = _manifest(open_path, base, link.decode("ascii", "replace").strip(), repository, scan)
    if "manifests" in manifest:
        entries = manifest["manifests"] if isinstance(manifest["manifests"], list) else []
        amd64 = [m for m in entries
                 if isinstance(m, dict) and isinstance(m.get("platform"), dict)
                 and m["platform"].get("os") == "linux" and m["platform"].get("architecture") == "amd64"]
        if not amd64:
            raise NotAnEnclaveImage(f"{repository}: the image index has no linux/amd64 image")
        manifest = _manifest(open_path, base, amd64[0].get("digest"), repository, scan)
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise NotAnEnclaveImage(f"{repository}: the manifest lists no layers")
    for layer in reversed(layers):
        if _layer_holds_enclave(open_path, base, layer, marker, scan, repository):
            return
    raise NotAnEnclaveImage(f"{repository}: no layer holds {BINARY_FS}")
