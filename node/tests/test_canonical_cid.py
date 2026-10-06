"""utils.canonical_cid against CIDs built here: the text Kubo prints for a CID passes, and every other spelling of
the same bytes, or bytes go-cid refuses, does not."""
import base64
import hashlib

from utils import canonical_cid

BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DIGEST = hashlib.sha256(b"an image tree").digest()


def base58(raw):
    number = int.from_bytes(raw, "big")
    text = ""
    while number:
        number, digit = divmod(number, 58)
        text = BASE58[digit] + text
    return text


def uvarint(n):
    out = bytearray()
    while n >= 0x80:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def cidv1(raw):
    return "b" + base64.b32encode(raw).decode().rstrip("=").lower()


def multihash():
    return uvarint(0x12) + uvarint(32) + DIGEST


def test_the_cidv0_kubo_prints_passes_and_other_46_character_texts_do_not():
    cid = base58(b"\x12\x20" + DIGEST)
    assert len(cid) == 46 and cid.startswith("Qm")
    assert canonical_cid(cid)
    assert not canonical_cid(cid[:-1] + "0")
    assert not canonical_cid("Qm" + "1" * 44)
    assert not canonical_cid(cid + " ")
    assert not canonical_cid(None)


def test_the_cidv1_kubo_prints_passes_and_a_spelling_with_padding_bits_does_not():
    raw = uvarint(1) + uvarint(0x70) + multihash()
    cid = cidv1(raw)
    assert canonical_cid(cid)
    assert not canonical_cid(cid.upper())
    # 36 bytes are 57.6 base32 characters: the last character carries 3 padding bits.
    alias = cid[:-1] + "abcdefghijklmnopqrstuvwxyz234567"["abcdefghijklmnopqrstuvwxyz234567".index(cid[-1]) | 1]
    assert alias != cid and base64.b32decode((alias[1:] + "=" * (-len(alias[1:]) % 8)).upper()) == raw
    assert not canonical_cid(alias)


def test_a_varint_go_cid_refuses_is_refused():
    # Version 1 written as 0x81 0x00: not minimally encoded.
    assert not canonical_cid(cidv1(b"\x81\x00" + uvarint(0x70) + multihash()))
    # A codec written with a trailing zero continuation.
    assert not canonical_cid(cidv1(uvarint(1) + b"\xf0\x00" + multihash()))
    # A codec of 2**63 takes 10 bytes; go-varint reads at most 9.
    assert not canonical_cid(cidv1(uvarint(1) + uvarint(2 ** 63) + multihash()))
    # The largest value 9 bytes hold is read.
    assert len(uvarint(2 ** 63 - 1)) == 9
    assert canonical_cid(cidv1(uvarint(1) + uvarint(2 ** 63 - 1) + multihash()))


def test_a_cidv1_whose_digest_length_disagrees_with_its_bytes_is_refused():
    assert not canonical_cid(cidv1(uvarint(1) + uvarint(0x70) + uvarint(0x12) + uvarint(32) + DIGEST[:31]))
    assert not canonical_cid(cidv1(uvarint(1) + uvarint(0x70) + multihash() + b"\x00"))
    assert not canonical_cid(cidv1(uvarint(0) + uvarint(0x70) + multihash()))
