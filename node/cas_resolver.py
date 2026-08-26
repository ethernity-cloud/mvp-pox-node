#!/usr/bin/env python3
"""CAS resolution from the ValidatorRegistry (Sprint 4, node side).

A node with NO configured CAS address discovers one from chain:

  1. enumerate the ACTIVE validators in the ValidatorRegistry;
  2. gather each validator's endpoints -- ENS names first (resolved to
     multiaddrs where an ENS registry exists), then literal multiaddrs,
     with /onion3 preferred over everything else at each step;
  3. skip transports this node cannot use (onion needs a Tor proxy);
  4. ATTEST whichever endpoint answers BEFORE any task launches: fetch
     /validator/identity from the CAS REST port and check the served quote
     against the CHAIN -- the report_data must bind the validator address,
     the quote MRENCLAVE must equal the validator's registered measurement,
     and the served certificate hash must equal the registered certHash;
  5. collapse the winning multiaddr to host[:port] for SCONE_CAS_ADDR.

A registry entry pointing at a wrong host FAILS attestation and is skipped --
it can slow resolution down, never redirect it. The quote's ECDSA/PCK chain
is NOT verified here (that is the enclave's own duty, Sprint 5, and the
enclave re-attests the CAS itself); what the node establishes is that the
answering endpoint speaks for the on-chain validator identity it claims.

Multiaddrs advertise the ENCLAVE port. The REST/identity port follows the
PAIRING CONVENTION `rest = 8081 + (enclave - 18765)`: co-hosted CAS instances
stack as 18765/8081, 18766/8082, 18767/8083, ... so one advert names both
listeners. Enclave ports outside [18765, 18965) fall back to REST 8081.
"""

import json
import urllib.request

# DCAP quote layout (fixed offsets, version 3 ECDSA quote):
#   [0:48]    quote header
#   [48:432]  report body: ATTRIBUTES at +48 (16B), MRENCLAVE at +64 (32B),
#             report_data at +320 (64B)
_BODY = 48
_MRENCLAVE = (_BODY + 64, _BODY + 96)
_REPORT_DATA = (_BODY + 320, _BODY + 384)

CAS_REST_PORT = 8081
CAS_ENCLAVE_PORT = 18765

VALIDATOR_REGISTRY_ABI = [
    {"name": "validatorCount", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint256"}]},
    {"name": "validatorSet", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "uint256"}], "outputs": [{"type": "address"}]},
    {"name": "isValidator", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}], "outputs": [{"type": "bool"}]},
    {"name": "validators", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}],
     "outputs": [{"name": "mrenclave", "type": "bytes32"},
                 {"name": "certHash", "type": "bytes32"},
                 {"name": "active", "type": "bool"},
                 {"name": "admittedBlock", "type": "uint64"},
                 {"name": "lastVoteBlock", "type": "uint64"}]},
    {"name": "multiaddrsOf", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}], "outputs": [{"type": "string[]"}]},
    {"name": "ensNamesOf", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "address"}], "outputs": [{"type": "string[]"}]},
]


def parse_multiaddr(ma):
    """/dns4|ip4/HOST/tcp/PORT or /onion3/ADDR/tcp/PORT -> (kind, host, port).

    Unknown or partial forms return None -- an unparseable advert is skipped,
    never guessed at.
    """
    parts = [p for p in str(ma).split('/') if p != '']
    if len(parts) != 4 or parts[2] != 'tcp':
        return None
    kind, host, port = parts[0], parts[1], parts[3]
    if kind not in ('dns4', 'dns6', 'dns', 'ip4', 'ip6', 'onion3'):
        return None
    if kind == 'onion3':
        host = host if host.endswith('.onion') else f'{host}.onion'
    try:
        return (kind, host, int(port))
    except ValueError:
        return None


def order_endpoints(multiaddrs):
    """The design's dial order: /onion3 first, everything else after, both in
    their published order."""
    parsed = [p for p in (parse_multiaddr(m) for m in multiaddrs) if p]
    onion = [p for p in parsed if p[0] == 'onion3']
    rest = [p for p in parsed if p[0] != 'onion3']
    return onion + rest


def rest_port_for(enclave_port):
    """The pairing convention (see module docstring)."""
    if CAS_ENCLAVE_PORT <= enclave_port < CAS_ENCLAVE_PORT + 200:
        return CAS_REST_PORT + (enclave_port - CAS_ENCLAVE_PORT)
    return CAS_REST_PORT


def _fetch_identity(host, timeout, rest_port=CAS_REST_PORT):
    url = f'http://{host}:{rest_port}/validator/identity'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _chain(call, attempts=3, delay=2):
    """Retry a chain read: bloxberg's public RPC returns transient 503s, and a
    flaky node must slow resolution down, not knock a valid CAS out of it."""
    import time
    last = None
    for i in range(attempts):
        try:
            return call()
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(delay * (i + 1))
    raise last


def _attest(identity, expected_address, expected_mrenclave, expected_cert):
    """The chain binding: served quote vs registered validator record.

    Returns None when everything matches, else the failure reason.
    """
    addr = str(identity.get('address', '')).lower()
    if addr != expected_address.lower():
        return f"endpoint speaks for {addr}, registry says {expected_address}"
    try:
        quote = bytes.fromhex(identity.get('quote', ''))
    except ValueError:
        return "quote is not hex"
    if len(quote) < _REPORT_DATA[1]:
        return f"quote too short ({len(quote)} bytes)"
    bound = '0x' + quote[_REPORT_DATA[0]:_REPORT_DATA[0] + 20].hex()
    if bound.lower() != expected_address.lower():
        return f"quote report_data binds {bound}, not {expected_address}"
    mr = quote[_MRENCLAVE[0]:_MRENCLAVE[1]].hex()
    if mr.lower() != expected_mrenclave.lower():
        return f"quote MRENCLAVE {mr} != registered {expected_mrenclave}"
    served_cert = str(identity.get('cert_hash', '')).lower()
    if expected_cert.lower() not in ('', '00' * 32) and \
            served_cert != expected_cert.lower():
        return f"served cert_hash {served_cert} != registered {expected_cert}"
    return None


def resolve_cas(w3, registry_address, logger, probe_timeout=10,
                tor_available=False):
    """Pick a CAS for task provisioning. Returns
    {'address', 'host', 'port', 'scone_cas_addr', 'mrenclave', 'cert_hash'}
    for the FIRST endpoint that answers AND attests, or None."""
    reg = w3.eth.contract(address=w3.to_checksum_address(registry_address),
                          abi=VALIDATOR_REGISTRY_ABI)
    try:
        total = _chain(lambda: reg.caller().validatorCount())
    except Exception as e:
        logger.warning(f"CAS resolver: validatorCount failed: {e}")
        return None

    for i in range(total):
        try:
            v = _chain(lambda: reg.caller().validatorSet(i))
            if not _chain(lambda: reg.caller().isValidator(v)):
                continue
            rec = _chain(lambda: reg.caller().validators(v))
            mrenclave, cert_hash = rec[0].hex(), rec[1].hex()
            # ENS names first, per the design. Resolution needs an ENS
            # registry on this chain; where there is none (bloxberg), the
            # names are recorded but cannot be dialed.
            for name in _chain(lambda: reg.caller().ensNamesOf(v)):
                try:
                    resolved = w3.ens.address(name)  # noqa: F841
                    logger.info(f"CAS resolver: ENS {name} resolvable but ENS "
                                f"multiaddr records are not defined yet; skipping")
                except Exception:
                    logger.debug(f"CAS resolver: no ENS resolution for {name}")
            for kind, host, port in order_endpoints(
                    _chain(lambda: reg.caller().multiaddrsOf(v))):
                if kind == 'onion3' and not tor_available:
                    logger.debug(f"CAS resolver: skipping {host} (no Tor)")
                    continue
                rp = rest_port_for(port)
                try:
                    identity = _fetch_identity(host, probe_timeout, rp)
                except Exception as e:
                    logger.info(f"CAS resolver: {host}:{rp} did not "
                                f"answer ({e}); trying next")
                    continue
                reason = _attest(identity, v, mrenclave, cert_hash)
                if reason is not None:
                    logger.warning(f"CAS resolver: {host} FAILED attestation: "
                                   f"{reason}; trying next")
                    continue
                scone_addr = host if port == CAS_ENCLAVE_PORT else f'{host}:{port}'
                logger.info(f"CAS resolver: selected {host} (validator {v}, "
                            f"MRENCLAVE {mrenclave[:16]}…)")
                return {'address': v, 'host': host, 'port': port,
                        'scone_cas_addr': scone_addr,
                        'mrenclave': mrenclave, 'cert_hash': cert_hash}
        except Exception as e:
            logger.warning(f"CAS resolver: validator #{i}: {e}")
    logger.warning("CAS resolver: no registered CAS endpoint answered and "
                   "attested; falling back to the compose default")
    return None
