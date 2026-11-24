"""Timed records built for the tests from the fixtures' test keys
(tests/spec/capability-record.json validator.privateKey, the secp256k1 key a
seated validator signs time statements with; bench-record.json probeKey,
the P-384 key that stands for a probe's): the two GET /validator/time
answers of a record signed over the statement the reader rebuilds, the
trustedzone record line carrying them, and the bench line signed under the
probe key."""
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_keys import keys

import capability

SPEC = Path(__file__).parent / "spec"
RECORD = json.loads((SPEC / "capability-record.json").read_text())
BENCH = json.loads((SPEC / "bench-record.json").read_text())
CHAIN_ID = RECORD["network"]["chainId"]
POX = RECORD["network"]["pox"]
REGISTRY = RECORD["network"]["validatorRegistry"]
NODE = RECORD["node"]["address"]
VALIDATOR = RECORD["validator"]["address"]
PLATFORM = RECORD["capabilityEtny"]["platformLine"]


def answer(nonce, t, block, node=NODE, chain_id=CHAIN_ID, registry=REGISTRY):
    """The validator's GET /validator/time answer over the statement of
    `nonce`, `t` and `block`, signed with the fixture's validator key."""
    digest = capability.time_statement_digest(chain_id, registry, node, nonce, t, block)
    signature = keys.PrivateKey(bytes.fromhex(RECORD["validator"]["privateKey"][2:])).sign_msg_hash(digest)
    serialized = (signature.r.to_bytes(32, "big") + signature.s.to_bytes(32, "big") + bytes([signature.v + 27]))
    return {"validator": VALIDATOR, "node": node.lower(), "nonce": nonce, "t": int(t), "block": int(block),
            "sig": "0x" + serialized.hex()}


def time_object(opening, closing):
    """A time object of the two answers, one compact JSON object per LF line."""
    return "".join(json.dumps(a, separators=(",", ":")) + "\n" for a in (opening, closing))


def trustedzone_objects(gflops=250, cuv=4, t0=None, t1=None, block0=None, block1=None, heap="256M", workers=4,
                        salt=None, version="v2", node=NODE, network=None, last=None):
    """(capability.etny, capability.time.etny) of a trustedzone record of
    the given figures, the fixture's by default; version "v2" closes over
    n1, "v1" over the run nonce. The line's own signature is the
    fixture's."""
    m = RECORD["measurement"]
    t0 = m["t0"] if t0 is None else t0
    t1 = m["t1"] if t1 is None else t1
    block0 = m["block0"] if block0 is None else block0
    block1 = m["block1"] if block1 is None else block1
    salt = m["salt"] if salt is None else salt
    network = RECORD["network"]["networkField"] if network is None else network
    last = f"heap={heap};workers={workers}" if last is None else last
    n0 = m["runNonce"]
    closing = capability.close_nonce(n0, salt, f"{gflops}|{cuv}|{last}") if version == "v2" else n0
    opening_answer, closing_answer = answer(n0, t0, block0, node), answer(closing, t1, block1, node)
    fields = ["etny-cu", version, network, node.lower(), str(gflops), str(cuv), VALIDATOR, n0]
    if version == "v2":
        fields.append(salt)
    fields += [str(t0), str(t1), opening_answer["sig"], closing_answer["sig"], last]
    line = "|".join(fields)
    return f"{line}\n{RECORD['signatureDerHex']}\n{PLATFORM}\n", time_object(opening_answer, closing_answer)


def probe_key():
    return serialization.load_pem_private_key(BENCH["probeKey"]["privateKeyPem"].encode(), password=None)


def sign_plain(private_key, text):
    """DER hex of an ECDSA P-384 signature over SHA-256 of the UTF-8 text."""
    return private_key.sign(text.encode("utf-8"), ec.ECDSA(hashes.SHA256())).hex()


def bench_objects(gflops=None, cuv=None, t0=None, t1=None, block0=None, block1=None, heap=None, workers=None,
                  encmem=None, custorage=None, epc=None, salt=None, node=NODE, network=None, key=None,
                  readings=None, opening_nonce=None, closing_nonce=None):
    """(probe.<g>.etny, probe.time.<g>.etny) of a bench record of the given
    figures, bench-record.json's by default, the line signed under `key`
    (the fixture's probe key by default) and the closing statement over
    close_nonce(n0, salt, <gflops>|<cuv>|<readings>) unless
    `closing_nonce` names another nonce."""
    m = BENCH["measurement"]
    pick = lambda value, name: m[name] if value is None else value
    gflops, cuv, t0, t1 = pick(gflops, "gflops"), pick(cuv, "cuv"), pick(t0, "t0"), pick(t1, "t1")
    block0, block1, salt = pick(block0, "block0"), pick(block1, "block1"), pick(salt, "salt")
    if readings is None:
        readings = (f"heap={pick(heap, 'heap')};workers={pick(workers, 'workers')};encmem={pick(encmem, 'encmem')};"
                    f"custorage={pick(custorage, 'custorage')};epc={pick(epc, 'epc')}")
    network = RECORD["network"]["networkField"] if network is None else network
    n0 = m["n0"]
    opening = answer(n0 if opening_nonce is None else opening_nonce, t0, block0, node)
    closing_value = capability.close_nonce(n0, salt, f"{gflops}|{cuv}|{readings}")
    closing = answer(closing_value if closing_nonce is None else closing_nonce, t1, block1, node)
    line = "|".join(["etny-pb", "v1", network, node.lower(), str(gflops), str(cuv), VALIDATOR, n0, salt, str(t0),
                     str(t1), opening["sig"], closing["sig"], readings])
    signature = sign_plain(key or probe_key(), line)
    return f"{line}\n{signature}\n{PLATFORM}\n", time_object(opening, closing)
