"""The capability record of a trustedzone's integration test (PHASE-E-PLAN.md
WS-C, E1-E3, E8, E15, E40; tests/spec/capability-record.json): the two
integration-bucket objects parsed, the record admitted, persisted per network
in capability.json, expired, and the DP request's metadata3 and metadata4
built from it (tests/spec/metadata-keys.md)."""
import json
import os
import re
import threading

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from eth_keys import keys
from eth_keys.exceptions import BadSignature
from eth_utils import keccak

from constants import BENCHMARK_MAX_SECONDS, CU_MAX_THOUSANDTHS, CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS
from utils import DP_STARTED_ROW_CAPABILITY

RECORD_TAG = 'etny-cu'
RECORD_VERSION = 'v1'
# The | fields of the record line (capability-record.json lineLayout).
LINE_FIELDS = 13
CAPABILITY_OBJECT = 'capability.etny'
CAPABILITY_TIME_OBJECT = 'capability.time.etny'
# The keys of a GET /validator/time answer, one JSON object per line of
# capability.time.etny.
TIME_ANSWER_KEYS = ('validator', 'node', 'nonce', 't', 'block', 'sig')
# The tag of the time statement's preimage: tag || chainId uint256 BE ||
# registry (20) || node (20) || nonce (32) || t uint64 BE || block uint64 BE,
# keccak256-digested and signed without an EIP-191 prefix.
TIME_TAG = b'ecas-time-1'
# The platform requirement whose images' records are admitted.
ADMITTED_REQUIREMENT = 'dcap'

ADDRESS_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
NONCE_RE = re.compile(r'^0x[0-9a-fA-F]{64}$')
SIG_RE = re.compile(r'^0x[0-9a-fA-F]{130}$')
DER_HEX_RE = re.compile(r'^[0-9a-f]+$')

# The reason codes of PoX.cancelDPRequestByValidators (ethernity-cas
# capability.rs Reason), and whether the agent re-tests before it re-adds:
# the record's own faults (1..6) call for a new record, the strike rule (7)
# for a new request alone.
CANCEL_REASONS = {
    1: 'RecordSignatureInvalid',
    2: 'RecordNodeMismatch',
    3: 'TimestampNotSeated',
    4: 'RunTooLong',
    5: 'RecordExpired',
    6: 'CuAboveMax',
    7: 'DeliveredBelowAttested',
}
RETEST_REASONS = frozenset(range(1, 7))


def cancel_reason_name(code):
    """The name of a DPRequestCancelled reason, 'Unknown(<code>)' past the table."""
    return CANCEL_REASONS.get(int(code), f'Unknown({int(code)})')


def cancel_disposition(code):
    """'retest' when the reason faults the record (1..6: the test runs again
    before the next request), 'readd' otherwise (7, and any code outside the
    table: the request alone is replaced)."""
    return 'retest' if int(code) in RETEST_REASONS else 'readd'


def parse_capability_etny(text):
    """capability.etny parsed: {line, fields, sig_der_hex, platform_line}.
    The object is three LF-terminated lines: the record line of LINE_FIELDS
    | fields tagged etny-cu|v1, its DER signature as lower-case hex, the
    platform line. Raises ValueError on any other shape."""
    if not isinstance(text, str):
        raise ValueError('capability.etny is not text')
    lines = text.split('\n')
    if len(lines) < 3:
        raise ValueError('capability.etny does not have three lines')
    line, sig_der_hex, platform_line = lines[0], lines[1].strip(), lines[2]
    fields = line.split('|')
    if len(fields) != LINE_FIELDS:
        raise ValueError(f'the record line has {len(fields)} fields, not {LINE_FIELDS}')
    if fields[0] != RECORD_TAG or fields[1] != RECORD_VERSION:
        raise ValueError(f'the record line is not tagged {RECORD_TAG}|{RECORD_VERSION}')
    if not sig_der_hex or not DER_HEX_RE.match(sig_der_hex) or len(sig_der_hex) % 2:
        raise ValueError('the record signature is not lower-case hex')
    return {'line': line, 'fields': fields, 'sig_der_hex': sig_der_hex, 'platform_line': platform_line}


def _time_answer(raw, index):
    try:
        answer = json.loads(raw)
    except ValueError:
        raise ValueError(f'time statement {index} is not JSON')
    if not isinstance(answer, dict) or set(answer) != set(TIME_ANSWER_KEYS):
        raise ValueError(f'time statement {index} does not have the keys {", ".join(TIME_ANSWER_KEYS)}')
    for key in ('validator', 'node'):
        if not isinstance(answer[key], str) or not ADDRESS_RE.match(answer[key]):
            raise ValueError(f'time statement {index}: {key} is not an address')
    if not isinstance(answer['nonce'], str) or not NONCE_RE.match(answer['nonce']):
        raise ValueError(f'time statement {index}: nonce is not 32 bytes of 0x hex')
    for key in ('t', 'block'):
        if isinstance(answer[key], bool) or not isinstance(answer[key], int) or answer[key] < 0:
            raise ValueError(f'time statement {index}: {key} is not a non-negative integer')
    if not isinstance(answer['sig'], str) or not SIG_RE.match(answer['sig']):
        raise ValueError(f'time statement {index}: sig is not a 65-byte serialization')
    return answer


def parse_capability_time_etny(text):
    """capability.time.etny parsed: [t0, t1], the two GET /validator/time
    answers (the six keys of TIME_ANSWER_KEYS each), one JSON object per
    LF-terminated line, t0 first. Raises ValueError on any other shape."""
    if not isinstance(text, str):
        raise ValueError('capability.time.etny is not text')
    lines = [line for line in text.split('\n') if line.strip()]
    if len(lines) != 2:
        raise ValueError(f'capability.time.etny has {len(lines)} statements, not 2')
    return [_time_answer(lines[0], 0), _time_answer(lines[1], 1)]


def time_statement_digest(chain_id, registry, node, nonce, t, block):
    """keccak256 of the 131-byte time statement preimage."""
    preimage = (TIME_TAG + int(chain_id).to_bytes(32, 'big')
                + bytes.fromhex(registry[2:]) + bytes.fromhex(node[2:]) + bytes.fromhex(nonce[2:])
                + int(t).to_bytes(8, 'big') + int(block).to_bytes(8, 'big'))
    return keccak(preimage)


def recover_time_statement_signer(chain_id, registry, answer):
    """The lower-case address `answer['sig']` recovers to over the statement
    rebuilt with `chain_id` and `registry`, or None when it recovers to none
    (a v other than 27 or 28, r or s out of range)."""
    sig = bytes.fromhex(answer['sig'][2:])
    v = sig[64]
    if v not in (27, 28):
        return None
    digest = time_statement_digest(chain_id, registry, answer['node'], answer['nonce'], answer['t'], answer['block'])
    try:
        signature = keys.Signature(vrs=(v - 27, int.from_bytes(sig[:32], 'big'), int.from_bytes(sig[32:64], 'big')))
        return signature.recover_public_key_from_msg_hash(digest).to_address().lower()
    except (BadSignature, ValueError):
        return None


def record_expired(t1, now):
    """Whether a record whose benchmark ended at `t1` has reached CU_TTL_SECONDS."""
    return int(now) >= int(t1) + CU_TTL_SECONDS


def remeasure_due(passed_at, now):
    """Whether a test passed at `passed_at` is CU_REMEASURE_MARGIN_SECONDS
    from its record's CU_TTL_SECONDS, when it runs again."""
    return int(passed_at) + CU_TTL_SECONDS - CU_REMEASURE_MARGIN_SECONDS <= int(now)


def _int_field(fields, index, name):
    value = fields[index]
    if not value.isdigit():
        raise ValueError(f'{name} is not a decimal integer')
    return int(value)


def _heap_and_workers(field):
    heap, _, workers = field.partition(';')
    if not heap.startswith('heap=') or not workers.startswith('workers='):
        raise ValueError('the last field is not heap=<heap>;workers=<n>')
    workers = workers[len('workers='):]
    if not workers.isdigit():
        raise ValueError('workers is not a decimal integer')
    return heap[len('heap='):], int(workers)


def network_field(chain_id, pox):
    """The record line's network field as the trustedzone writes it,
    `<chainId>:<pox>`, with the address lower-cased for the compare."""
    return f'{int(chain_id)}:{str(pox).lower()}'


def capability_record_from(capability_etny, capability_time_etny, node_address, requirement, has_validator_set,
                           now, chain_id, pox, registry, is_seated):
    """(record, None) when the integration test's objects make an admitted
    record, (None, reason) otherwise. Admitted: capability.etny parses, the
    line's node is `node_address` lower-cased, its network is `chain_id`
    and `pox` (network_field, the address compared case-insensitively), t1
    is at least t0 and at most BENCHMARK_MAX_SECONDS after it, its validator
    and both signature fields are non-empty, capability.time.etny exists
    with two statements whose node, nonce, validator and t match the line's,
    each signature recovers over `chain_id` and `registry` to the validator,
    `is_seated(validator)` holds, t1 is within CU_TTL_SECONDS of `now`, cu
    is at most CU_MAX_THOUSANDTHS, `requirement` is dcap and
    `has_validator_set` holds.

    The record: the line's fields (network, node, cu, cuv, validator, nonce,
    t0, t1, sig0, sig1, heap, workers), line, sig_der_hex, platform_line,
    block0 and block1 from the statements, and the two raw texts."""
    if requirement != ADMITTED_REQUIREMENT:
        return None, f'the image requires {requirement}, not {ADMITTED_REQUIREMENT}'
    if not has_validator_set:
        return None, 'the network has no validator set'
    if capability_etny is None:
        return None, f'no {CAPABILITY_OBJECT}'
    try:
        parsed = parse_capability_etny(capability_etny)
        fields = parsed['fields']
        network, node, validator, nonce, sig0, sig1 = fields[2], fields[3], fields[6], fields[7], fields[10], fields[11]
        cu = _int_field(fields, 4, 'cu')
        cuv = _int_field(fields, 5, 'cuv')
        t0 = _int_field(fields, 8, 't0')
        t1 = _int_field(fields, 9, 't1')
        heap, workers = _heap_and_workers(fields[12])
    except ValueError as e:
        return None, f'{CAPABILITY_OBJECT}: {e}'
    if node != str(node_address).lower():
        return None, f'the record names node {node}, not {str(node_address).lower()}'
    if network.lower() != network_field(chain_id, pox):
        return None, f'the record names network {network}, not {network_field(chain_id, pox)}'
    if t1 < t0 or t1 - t0 > BENCHMARK_MAX_SECONDS:
        return None, f'run too long: t0 {t0}, t1 {t1} (at most BENCHMARK_MAX_SECONDS {BENCHMARK_MAX_SECONDS})'
    if not validator:
        return None, 'the record carries no validator (host time)'
    if not sig0 or not sig1:
        return None, 'the record carries an empty time statement signature'
    if capability_time_etny is None:
        return None, f'no {CAPABILITY_TIME_OBJECT}'
    try:
        statements = parse_capability_time_etny(capability_time_etny)
    except ValueError as e:
        return None, f'{CAPABILITY_TIME_OBJECT}: {e}'
    for index, (statement, t, sig) in enumerate(zip(statements, (t0, t1), (sig0, sig1))):
        if (statement['validator'].lower() != validator or statement['node'].lower() != node
                or statement['nonce'].lower() != nonce.lower() or statement['t'] != t
                or statement['sig'].lower() != sig.lower()):
            return None, f'time statement {index} does not match the record line'
        recovered = recover_time_statement_signer(chain_id, registry, statement)
        if recovered != validator:
            return None, f'time statement {index} recovers to {recovered}, not to {validator}'
    if not is_seated(validator):
        return None, f'validator {validator} is not seated'
    if abs(int(now) - t1) > CU_TTL_SECONDS:
        return None, f't1 {t1} is outside CU_TTL_SECONDS of now'
    if cu > CU_MAX_THOUSANDTHS:
        return None, f'cu {cu} is above CU_MAX_THOUSANDTHS {CU_MAX_THOUSANDTHS}'
    record = {
        'line': parsed['line'],
        'network': network,
        'node': node,
        'cu': cu,
        'cuv': cuv,
        'validator': validator,
        'nonce': nonce,
        't0': t0,
        't1': t1,
        'sig0': sig0,
        'sig1': sig1,
        'heap': heap,
        'workers': workers,
        'sig_der_hex': parsed['sig_der_hex'],
        'platform_line': parsed['platform_line'],
        'block0': statements[0]['block'],
        'block1': statements[1]['block'],
        'capability_etny': capability_etny,
        'capability_time_etny': capability_time_etny,
    }
    return record, None


class RecordStore:
    """node/<network>/capability.json: {image name: {image_hash, passed_at,
    record, probe_first_pending}} for the passed integration tests of the
    network, `record` the dict of capability_record_from or None. Every
    change is written as a whole snapshot through a temporary file."""

    def __init__(self, filepath):
        self.filepath = str(filepath)
        self._lock = threading.RLock()
        self.entries = {}
        self.load()

    def load(self):
        """Reads the file; an absent or unreadable file is an empty store."""
        with self._lock:
            try:
                with open(self.filepath) as f:
                    loaded = json.load(f)
            except (OSError, ValueError):
                loaded = {}
            self.entries = loaded if isinstance(loaded, dict) else {}
            return self.entries

    def _write(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
        temporary = f'{self.filepath}.tmp'
        with open(temporary, 'w') as f:
            json.dump(self.entries, f)
        os.replace(temporary, self.filepath)

    def get(self, image):
        return self.entries.get(image)

    def put(self, image, image_hash, passed_at, record):
        """Records a passed test; a record arms the probe-first flag of its
        first order."""
        with self._lock:
            self.entries[image] = {
                'image_hash': image_hash,
                'passed_at': int(passed_at),
                'record': record,
                'probe_first_pending': record is not None,
            }
            self._write()

    def drop(self, image):
        """Forgets the test and its record."""
        with self._lock:
            if image in self.entries:
                del self.entries[image]
                self._write()

    def drop_record(self, image):
        """Keeps the passed test, drops its record."""
        with self._lock:
            entry = self.entries.get(image)
            if entry and entry.get('record') is not None:
                entry['record'] = None
                entry['probe_first_pending'] = False
                self._write()

    def probe_first_pending(self, image):
        entry = self.entries.get(image)
        return bool(entry and entry.get('probe_first_pending'))

    def consume_probe_first(self, image):
        """Clears the flag; whether it was set."""
        with self._lock:
            entry = self.entries.get(image)
            if not entry or not entry.get('probe_first_pending'):
                return False
            entry['probe_first_pending'] = False
            self._write()
            return True

    def records(self, now):
        """{image: record} of the entries holding a record not expired at `now`."""
        return {image: entry['record'] for image, entry in self.entries.items()
                if entry.get('record') is not None and not record_expired(entry['record']['t1'], now)}

    def advertised(self, runnable, now):
        """(image, record) of the record a DP request carries: among the
        unexpired records of the images in `runnable`, the one with the
        newest t1 (the image name the tie-breaker); None without one."""
        candidates = [(record['t1'], image, record) for image, record in self.records(now).items() if image in runnable]
        if not candidates:
            return None
        _, image, record = max(candidates, key=lambda item: (item[0], item[1]))
        return image, record

    def remeasure_due(self, now):
        """The images whose test is due again (remeasure_due)."""
        return {image for image, entry in self.entries.items() if remeasure_due(entry['passed_at'], now)}

    def expired(self, now):
        """The images whose record has reached CU_TTL_SECONDS."""
        return {image for image, entry in self.entries.items()
                if entry.get('record') is not None and record_expired(entry['record']['t1'], now)}


def metadata4(trustedzones_runnable, record_image, peer_id, record):
    """A DP request's metadata4: trustedzones=<names>;ipfs=<peer id>;startrow=v1
    and, with `record`, the six cu keys (cu, cuv, cuat = t1, cusig = the DER
    hex without 0x, cuheap, cuworkers). `record_image` is listed first (the
    validators verify cusig under the first name's certificates) and the
    other runnable names sorted after it; without a record the names are
    sorted."""
    names = sorted(trustedzones_runnable)
    if record is not None and record_image in names:
        names.remove(record_image)
        names.insert(0, record_image)
    value = f"trustedzones={','.join(names)};ipfs={peer_id};{DP_STARTED_ROW_CAPABILITY}"
    if record is None:
        return value
    sig = record['sig_der_hex'][2:] if record['sig_der_hex'].lower().startswith('0x') else record['sig_der_hex']
    return (f"{value};cu={record['cu']};cuv={record['cuv']};cuat={record['t1']};cusig={sig.lower()}"
            f";cuheap={record['heap']};cuworkers={record['workers']}")


def metadata3(geo, record):
    """A DP request's metadata3: the geography (possibly empty) first and,
    with `record`, the eight keys cuval, cun, cut0, cut1, cub0, cub1, cusig0,
    cusig1."""
    geo = geo or ''
    if record is None:
        return geo
    return (f"{geo};cuval={record['validator']};cun={record['nonce']};cut0={record['t0']};cut1={record['t1']}"
            f";cub0={record['block0']};cub1={record['block1']};cusig0={record['sig0']};cusig1={record['sig1']}")


def p384_public_key(cert_text):
    """The P-384 public key of a certificate as the image registry holds it:
    a PEM certificate or PEM public key, or the DER of either as hex with or
    without 0x. None for anything else, a key on another curve included."""
    text = str(cert_text or '').strip()
    if not text:
        return None
    loaders = []
    if '-----BEGIN' in text:
        data = text.encode('utf-8')
        if 'CERTIFICATE' in text:
            loaders.append(lambda d: x509.load_pem_x509_certificate(d, default_backend()).public_key())
        else:
            loaders.append(lambda d: serialization.load_pem_public_key(d, default_backend()))
    else:
        try:
            data = bytes.fromhex(text[2:] if text.lower().startswith('0x') else text)
        except ValueError:
            return None
        loaders.append(lambda d: x509.load_der_x509_certificate(d, default_backend()).public_key())
        loaders.append(lambda d: serialization.load_der_public_key(d, default_backend()))
    for load in loaders:
        try:
            key = load(data)
        except Exception:
            continue
        if isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP384R1):
            return key
    return None


def verify_p384(cert_text, message, der_hex):
    """Whether `der_hex` (an ECDSA P-384 signature, DER, hex with or without
    0x) verifies over SHA-256 of `message` (text or bytes) under the key of
    `cert_text` (p384_public_key): the trustedzones' signData / sign_data
    scheme the etny-sw row and the fence are signed with."""
    key = p384_public_key(cert_text)
    if key is None:
        return False
    try:
        signature = bytes.fromhex(der_hex[2:] if str(der_hex).lower().startswith('0x') else der_hex)
    except (ValueError, TypeError):
        return False
    data = message.encode('utf-8') if isinstance(message, str) else bytes(message)
    digest = hashes.Hash(hashes.SHA256(), default_backend())
    digest.update(data)
    try:
        key.verify(signature, digest.finalize(), ec.ECDSA(Prehashed(hashes.SHA256())))
    except Exception:
        return False
    return True
