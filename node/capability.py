"""The capability record of a trustedzone's integration test (PHASE-E-PLAN.md
WS-C, E1-E3, E8, E15, E40; tests/spec/capability-record.json at
pox-smart-contract-litvm 2f600f6): the two integration-bucket objects parsed,
the record admitted in its v1 or v2 form by the network kind (the closing
nonce, the closing checks and the rate bound of a v2 record), persisted per
network in capability.json, expired, and the DP request's metadata3 and
metadata4 built from it (tests/spec/metadata-keys.md); the validators' cancel
reasons 1 to 13, the RecordMissing hold a reason-9 cancel sets, and the
enclave memory, EPC units and GFLOPS an offer states with the figures a
reason-10, 12 or 13 cancel refused."""
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

import offer_shape
from constants import (BENCHMARK_FULL_ROUNDS, BENCHMARK_MAX_SECONDS, BENCHMARK_OPS_PER_ROUND, CU_MAX_GFLOPS,
                       CU_REMEASURE_MARGIN_SECONDS, CU_TTL_SECONDS, CU_VERSION_MIN, RATE_ALLOWANCE_SECONDS,
                       RATE_TOLERANCE_PERCENT)
from utils import DP_STARTED_ROW_CAPABILITY, UNSAFE_TRUSTEDZONE_SUFFIX, read_cursor, record_key, write_cursor

RECORD_TAG = 'etny-cu'
# The two forms of the trustedzone record line (pox-smart-contract-litvm
# 2f600f6 spec/capability-record.json): v1 both time statements over the run
# nonce n0, thirteen | fields (legacyV1.lineLayout); v2 the closing statement
# over n1 (close_nonce), fourteen fields, the salt in field 8 (lineLayout).
RECORD_VERSION_V1 = 'v1'
RECORD_VERSION_V2 = 'v2'
LINE_FIELDS = {RECORD_VERSION_V1: 13, RECORD_VERSION_V2: 14}
# The names of the record line's fields of each form, in order.
LINE_FIELD_NAMES = {
    RECORD_VERSION_V1: ('tag', 'version', 'network', 'node', 'gflops', 'cuv', 'validator', 'nonce', 't0', 't1',
                        'sig0', 'sig1', 'last'),
    RECORD_VERSION_V2: ('tag', 'version', 'network', 'node', 'gflops', 'cuv', 'validator', 'nonce', 'salt', 't0',
                        't1', 'sig0', 'sig1', 'last'),
}
# The tag of the closing nonce's preimage: n1 = keccak256("ecld-close-1" ||
# n0 || salt || keccak256(utf8(outcome))) (capability-record.json closeNonce).
CLOSE_NONCE_TAG = b'ecld-close-1'
# A plain decimal: "0" or a digit 1-9 followed by digits, ASCII only.
PLAIN_DECIMAL_RE = re.compile(r'0|[1-9][0-9]*', re.ASCII)
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
# capability.rs Reason; pox-smart-contract-litvm 2f600f6 spec/metadata-keys.md
# reasons 1 to 13), and whether the agent re-tests before it re-adds: the
# faults of the record (1..6 and 8), its absence (9) and an offer stating
# more GFLOPS than its record (13, OD7) call for a new record, the strike
# rule (7) for a new request alone, and an offer stating more enclave memory
# (10, D147), more storage (11) or more EPC (12, D191) than the bench record
# in its DP request shows for a request with the figure the record states
# (figure_after_cancel).
CANCEL_REASONS = {
    1: 'RecordSignatureInvalid',
    2: 'RecordNodeMismatch',
    3: 'TimestampNotSeated',
    4: 'RunTooLong',
    5: 'RecordExpired',
    6: 'GflopsAboveMax',
    7: 'DeliveredBelowAttested',
    8: 'CuVersionBelowMinimum',
    9: 'RecordMissing',
    10: 'EnclaveMemoryAboveRecord',
    11: 'StorageAboveRecord',
    12: 'EpcAboveRecord',
    13: 'GflopsAboveRecord',
}
RETEST_REASONS = frozenset({1, 2, 3, 4, 5, 6, 8, 9, 13})
REASON_ENCLAVE_MEMORY_ABOVE_RECORD = 10
REASON_STORAGE_ABOVE_RECORD = 11
REASON_EPC_ABOVE_RECORD = 12
REASON_GFLOPS_ABOVE_RECORD = 13
# The EPC unit of an offer and a request, MiB (D191): an offer states at
# most floor(epc / EPC_UNIT_MIB) units of a bench record's epc= MiB, in bits
# 24 to 39 of the V2 add's gflops argument.
EPC_UNIT_MIB = 64
EPC_UNITS_MAX = 65535
# The enclave sizes an offer states, in GiB (D150): the probe of each size is
# its own measurement.
ENCLAVE_SIZES_GIB = (1, 2, 4, 8, 16, 32, 64)
# The capability record's key of the enclave size its probe passed at, the
# memory the bench allocated and touched (encmem=, SLH 24.7), and of the
# storage reading the bench signs beside it (custorage=, GiB); both travel in
# the DP request's metadata4 beside the record's cu keys.
RECORD_ENCLAVE_MEMORY_KEY = 'encmem'
RECORD_STORAGE_KEY = 'custorage'
# The broker_cursor.json keys of the enclave memory, the storage, the EPC
# units and the GFLOPS each DP request of the node stated, and of the
# figures refused under each record (OfferFigureBook).
OFFER_ENCLAVE_MEMORY = 'offer_enclave_memory'
ENCLAVE_MEMORY_REFUSED = 'enclave_memory_refused'
OFFER_STORAGE = 'offer_storage'
STORAGE_REFUSED = 'storage_refused'
OFFER_EPC = 'offer_epc'
EPC_REFUSED = 'epc_refused'
OFFER_GFLOPS = 'offer_gflops'
GFLOPS_REFUSED = 'gflops_refused'
# The reason of an offer that carries no capability record and is not passed
# over (tests/spec/metadata-keys.md): its trustedzones= list names none, names
# a trustedzone that can carry one (without the -unsafe suffix), or the
# network admits no -unsafe trustedzone.
REASON_RECORD_MISSING = 9
# The broker_cursor.json key of the RecordMissing hold (RecordMissingHold).
RECORD_MISSING_HOLD = 'record_missing_hold'
# How often the agent, while the RecordMissing hold keeps its DP request back,
# runs its due integration tests and looks for an advertised record.
RECORD_MISSING_POLL_SECONDS = 60


def cancel_reason_name(code):
    """The name of a DPRequestCancelled reason, 'Unknown(<code>)' past the table."""
    return CANCEL_REASONS.get(int(code), f'Unknown({int(code)})')


def cancel_disposition(code):
    """'retest' when the reason faults the record or its absence (1..6, 8, 9
    and 13: the test runs again before the next request), 'memory' for an
    offer that stated more enclave memory than its bench record (10),
    'storage' for one that stated more storage (11) and 'epc' for one that
    stated more EPC (12): the next request states the record's figure
    (figure_after_cancel); 'readd' otherwise (7, and any code outside the
    table: the request alone is replaced)."""
    if int(code) in RETEST_REASONS:
        return 'retest'
    if int(code) == REASON_ENCLAVE_MEMORY_ABOVE_RECORD:
        return 'memory'
    if int(code) == REASON_STORAGE_ABOVE_RECORD:
        return 'storage'
    if int(code) == REASON_EPC_ABOVE_RECORD:
        return 'epc'
    return 'readd'


def record_enclave_memory(record):
    """The enclave size in GiB a capability record states (its encmem=), a
    listed size; None for a record that states none or another value."""
    if not record:
        return None
    try:
        size = int(record.get(RECORD_ENCLAVE_MEMORY_KEY))
    except (TypeError, ValueError):
        return None
    return size if size in ENCLAVE_SIZES_GIB else None


def record_storage(record):
    """The storage in GiB a capability record's bench read (its custorage=),
    None for a record that carries none or another value."""
    if not record:
        return None
    try:
        storage = int(record.get(RECORD_STORAGE_KEY))
    except (TypeError, ValueError):
        return None
    return storage if storage >= 0 else None


def record_epc_units(record):
    """The EPC units a bench record proves, floor(epc / EPC_UNIT_MIB) of its
    epc= MiB, at most EPC_UNITS_MAX; None for a record that carries no
    epc."""
    if not record:
        return None
    try:
        epc = int(record.get('epc'))
    except (TypeError, ValueError):
        return None
    return min(EPC_UNITS_MAX, epc // EPC_UNIT_MIB) if epc >= 0 else None


def offer_epc_units(proven_units, setting_mib, refused):
    """The EPC units an offer states in bits 24 to 39 (D191, OD21 A): the
    units its bench record proves (`proven_units`, record_epc_units; None:
    no record, 0 units), at most floor(`setting_mib` / EPC_UNIT_MIB) when the
    operator's setting is above 0, and below `refused`, the units a
    reason-12 cancel refused under that record (None for none)."""
    if proven_units is None:
        return 0
    units = int(proven_units)
    if int(setting_mib) > 0:
        units = min(units, int(setting_mib) // EPC_UNIT_MIB)
    if refused is not None:
        units = min(units, int(refused) - 1)
    return max(0, min(EPC_UNITS_MAX, units))


def offer_gflops(record_gflops, refused):
    """The GFLOPS an offer states in bits 0 to 15: its trustedzone record's
    (`record_gflops`, 0 without a record), below `refused`, the GFLOPS a
    reason-13 cancel refused under that record (None for none)."""
    gflops = int(record_gflops)
    if refused is not None:
        gflops = min(gflops, int(refused) - 1)
    return max(0, gflops)


def figure_after_cancel(stated, record_figure):
    """What follows a cancel for reason 10 (enclave memory), 11 (storage) or
    12 (EPC units) of an offer that stated `stated` under a record whose
    bench reading is `record_figure` (None: none): (figure, rerun). A
    reading below the refused figure gives it, the bench standing; a record
    with no reading, or one not below the refused figure, gives none and the
    bench runs again (rerun), so that the refused figure is never stated
    again on that record."""
    if record_figure is not None and record_figure < int(stated):
        return record_figure, False
    return None, True


def offer_enclave_memory(setting, record_size, refused, memory):
    """The enclave memory an offer states, in GiB (7.24, D146, D150): the
    largest of the sizes the node is benchmarked at, the listed sizes at or
    below its offer's memory figure `memory` (offer_shape.bench_sizes), whose
    signed bench record passed: at most the size the record states
    (`record_size`, its encmem=), at most the operator's `setting` when it is
    above 0, and below `refused`, the size a reason-10 cancel refused under
    that record, when there is one (None for none); 0 for a record that
    states none."""
    if record_size is None:
        return 0
    bound = int(record_size)
    if int(setting) > 0:
        bound = min(bound, int(setting))
    if refused is not None:
        bound = min(bound, int(refused) - 1)
    sizes = [size for size in offer_shape.bench_sizes(memory, ENCLAVE_SIZES_GIB) if size <= bound]
    return sizes[-1] if sizes else 0


class OfferFigureBook:
    """One figure of the node's offers that a bench record bounds, kept in
    the network's broker_cursor.json beside the other cursors
    (utils.read_cursor, utils.write_cursor), so a restart keeps it: the
    figure each DP request of the node stated (under `stated_key`, {request
    id: GiB}) and the figure a cancel refused under each capability record
    (under `refused_key`, {record key: GiB}). A request the book does not
    hold stated 0: no agent before it stated the figure. enclave_memory_book
    and storage_book are the two the agent keeps."""

    def __init__(self, filepath, stated_key, refused_key):
        self.filepath = str(filepath)
        self.stated_key = stated_key
        self.refused_key = refused_key

    def __read(self, name):
        value = read_cursor(self.filepath, name)
        return dict(value) if isinstance(value, dict) else {}

    def stated(self, request_id):
        return int(self.__read(self.stated_key).get(str(int(request_id)), 0))

    def record_stated(self, request_id, figure):
        stated = self.__read(self.stated_key)
        stated[str(int(request_id))] = int(figure)
        write_cursor(self.filepath, self.stated_key, stated)

    def refused(self, record_key):
        """The figure refused under the record `record_key`, None for none."""
        if record_key is None:
            return None
        value = self.__read(self.refused_key).get(str(record_key))
        return int(value) if value is not None else None

    def refuse(self, record_key, figure):
        """Records that `figure` was refused under the record `record_key`; a
        smaller refusal under the same record stands before a larger one."""
        if record_key is None:
            return
        refused = self.__read(self.refused_key)
        before = refused.get(str(record_key))
        refused[str(record_key)] = int(figure) if before is None else min(int(before), int(figure))
        write_cursor(self.filepath, self.refused_key, refused)


def enclave_memory_book(filepath):
    """The enclave memory each DP request stated and the sizes reason 10
    refused (OFFER_ENCLAVE_MEMORY, ENCLAVE_MEMORY_REFUSED)."""
    return OfferFigureBook(filepath, OFFER_ENCLAVE_MEMORY, ENCLAVE_MEMORY_REFUSED)


def storage_book(filepath):
    """The storage each DP request stated and the figures reason 11 refused
    (OFFER_STORAGE, STORAGE_REFUSED)."""
    return OfferFigureBook(filepath, OFFER_STORAGE, STORAGE_REFUSED)


def epc_book(filepath):
    """The EPC units each DP request stated and the units reason 12 refused
    (OFFER_EPC, EPC_REFUSED)."""
    return OfferFigureBook(filepath, OFFER_EPC, EPC_REFUSED)


def gflops_book(filepath):
    """The GFLOPS each DP request stated and the GFLOPS reason 13 refused
    (OFFER_GFLOPS, GFLOPS_REFUSED)."""
    return OfferFigureBook(filepath, OFFER_GFLOPS, GFLOPS_REFUSED)


def cancel_reaction(code, request_key):
    """What a validators' cancel of one of the node's DP requests asks of the
    agent, from its reason `code` and `request_key`, the cusig the cancelled
    request carried (utils.record_key form, None for a request without a
    record): (hold, retest). hold: RecordMissing (9) sets the network's
    RecordMissingHold; no other reason sets it. retest: a reason that
    faults the record (cancel_disposition 'retest') on a request that
    carried one drops that record (RecordStore.image_of `request_key`) and
    its integration test runs again before the next request; a request
    without a record has none to drop. Reasons 10, 11 and 12 are neither:
    their reaction is figure_after_cancel's."""
    return int(code) == REASON_RECORD_MISSING, request_key is not None and cancel_disposition(code) == 'retest'


def unsafe_only(trustedzones):
    """Whether a DP request's trustedzones= list (`trustedzones`, the names)
    names at least one trustedzone and every one ends in
    UNSAFE_TRUSTEDZONE_SUFFIX: the offer without a record the validators pass
    over on a network that admits -unsafe trustedzones (ethernity-cas
    CapabilityKeys::unsafe_only), since such a trustedzone contacts no CAS
    and its node never carries a record."""
    names = list(trustedzones)
    return bool(names) and all(name.endswith(UNSAFE_TRUSTEDZONE_SUFFIX) for name in names)


class RecordMissingHold:
    """The hold a RecordMissing (9) cancel sets on a network: until an
    integration test has produced a capability record, the agent posts no DP
    request without one there unless the request names only -unsafe
    trustedzones (unsafe_only), which the validators pass over. Kept under
    RECORD_MISSING_HOLD in the network's broker_cursor.json beside its scan
    cursors (utils.read_cursor, utils.write_cursor), so a restart keeps it; a
    network whose validators never cancelled with reason 9 has no hold."""

    def __init__(self, filepath):
        self.filepath = str(filepath)
        self.held = bool(read_cursor(self.filepath, RECORD_MISSING_HOLD))

    def set(self, held):
        """Sets (True) or clears (False) the hold and writes it."""
        self.held = bool(held)
        write_cursor(self.filepath, RECORD_MISSING_HOLD, self.held)

    def disposition(self, advertised, runnable):
        """What the agent does with its next DP request, `advertised` the
        (image, record) the request would carry (RecordStore.advertised, None
        without one) and `runnable` the trustedzone names its trustedzones=
        list carries: 'clear' (clear the hold and post the request carrying
        the record) while the hold stands and a record is advertised; 'hold'
        (post nothing) while it stands, no record is advertised and the list
        names no trustedzone or one that can carry a record (unsafe_only
        false); 'post' otherwise: without the hold, or for a request without
        a record that names only -unsafe trustedzones."""
        if not self.held:
            return 'post'
        if advertised is not None:
            return 'clear'
        return 'post' if unsafe_only(runnable) else 'hold'

    def retest_due(self, requirement, record):
        """Whether a passed integration test runs again for a capability
        record: the hold stands, the image's platform requirement is
        ADMITTED_REQUIREMENT (the images whose records are admitted) and its
        pass holds no record."""
        return self.held and requirement == ADMITTED_REQUIREMENT and record is None


def parse_record_object(text, name, forms):
    """A record object parsed: {line, fields, version, sig_der_hex,
    platform_line}. The object `name` is three LF-terminated lines: the
    record line, its DER signature as lower-case hex, the platform line.
    `forms` is {(tag, version): field count} of the line forms admitted.
    Raises ValueError on any other shape."""
    if not isinstance(text, str):
        raise ValueError(f'{name} is not text')
    lines = text.split('\n')
    if len(lines) < 3:
        raise ValueError(f'{name} does not have three lines')
    line, sig_der_hex, platform_line = lines[0], lines[1].strip(), lines[2]
    fields = line.split('|')
    count = forms.get(tuple(fields[:2]))
    if count is None:
        tags = ', '.join(f'{tag}|{version}' for tag, version in forms)
        raise ValueError(f'the record line is not tagged {tags}')
    if len(fields) != count:
        raise ValueError(f'the record line has {len(fields)} fields, not {count}')
    if not sig_der_hex or not DER_HEX_RE.match(sig_der_hex) or len(sig_der_hex) % 2:
        raise ValueError('the record signature is not lower-case hex')
    return {'line': line, 'fields': fields, 'version': fields[1], 'sig_der_hex': sig_der_hex,
            'platform_line': platform_line}


def parse_capability_etny(text):
    """capability.etny parsed (parse_record_object): the record line tagged
    etny-cu|v1 with LINE_FIELDS['v1'] fields or etny-cu|v2 with
    LINE_FIELDS['v2'], plus {named}: {field name: text} by LINE_FIELD_NAMES.
    Raises ValueError on any other shape."""
    parsed = parse_record_object(text, CAPABILITY_OBJECT,
                                 {(RECORD_TAG, version): count for version, count in LINE_FIELDS.items()})
    parsed['named'] = dict(zip(LINE_FIELD_NAMES[parsed['version']], parsed['fields']))
    return parsed


def close_nonce(n0, salt, outcome):
    """n1 = keccak256("ecld-close-1" || n0 || salt || keccak256(utf8(outcome)))
    as 0x-prefixed lower-case hex (capability-record.json closeNonce): the
    nonce of the closing time statement of a v2 record or a bench line,
    `n0` and `salt` 0x-prefixed 32-byte hex, `outcome` the record's
    <gflops>|<cuv>|<last field>."""
    preimage = (CLOSE_NONCE_TAG + bytes.fromhex(str(n0)[2:]) + bytes.fromhex(str(salt)[2:])
                + keccak(str(outcome).encode('utf-8')))
    return '0x' + keccak(preimage).hex()


def record_work(workers):
    """The fixed work of the trustedzone record's benchmark: BENCHMARK_FULL_ROUNDS
    x `workers` x BENCHMARK_OPS_PER_ROUND (capability-record.json rateBound
    recordWork)."""
    return BENCHMARK_FULL_ROUNDS * int(workers) * BENCHMARK_OPS_PER_ROUND


def above_rate_bound(gflops, window, work):
    """Whether `gflops` measured over an attested window of `window` seconds
    is above the rate bound of work `work` (capability-record.json
    rateBound): gflops x 100 x (window - A) x 10^9 > work x (100 + T), A
    RATE_ALLOWANCE_SECONDS and T RATE_TOLERANCE_PERCENT; a window at or below
    A bounds nothing."""
    span = int(window) - RATE_ALLOWANCE_SECONDS
    if span <= 0:
        return False
    return int(gflops) * 100 * span * 10**9 > int(work) * (100 + RATE_TOLERANCE_PERCENT)


def time_statement_bodies(text, name):
    """The two lines of a time object (capability.time.etny,
    probe.time.<g>.etny) as written: [opening, closing], each line with a
    trailing CR removed. Raises ValueError unless it holds exactly two
    non-empty lines."""
    if not isinstance(text, str):
        raise ValueError(f'{name} is not text')
    lines = [line.rstrip('\r') for line in text.split('\n') if line.strip()]
    if len(lines) != 2:
        raise ValueError(f'{name} has {len(lines)} statements, not 2')
    return lines


def closing_check(statements, bodies, chain_id, registry, node, validator, n0, salt, outcome, t0, t1, sig0, sig1):
    """The closing checks of a timed record in the close nonce's form, as a
    reader that holds both answers' bodies makes them
    (capability-record.json closingChecks): None when they pass, else
    '<check>: <why>' naming the first that fails, in this order:
    equalBodies, the closing answer's body (`bodies[1]`) equal to the
    opening one's; the two answers (`statements`) naming the record's node,
    validator, times and signatures; closeNonce, the opening statement over
    `n0` and the closing one over n1 = close_nonce(n0, salt, outcome), each
    recovering to `validator` over `chain_id` and `registry`; times, t1 at
    least t0; blocks, the closing answer's block at least the opening
    one's."""
    if bodies[0] == bodies[1]:
        return 'equalBodies: the closing answer is the opening one'
    n1 = close_nonce(n0, salt, outcome)
    for index, (statement, nonce, t, sig) in enumerate(zip(statements, (n0, n1), (t0, t1), (sig0, sig1))):
        if (statement['validator'].lower() != validator or statement['node'].lower() != node
                or statement['t'] != t or statement['sig'].lower() != sig.lower()):
            return f'statements: time statement {index} does not match the record line'
        if statement['nonce'].lower() != nonce.lower():
            return (f'closeNonce: time statement {index} is over {statement["nonce"].lower()}, not over '
                    f'{"n0" if index == 0 else "n1"} {nonce.lower()}')
        recovered = recover_time_statement_signer(chain_id, registry, statement)
        if recovered != validator:
            return f'closeNonce: time statement {index} recovers to {recovered}, not to {validator}'
    if t1 < t0:
        return f'times: t1 {t1} is below t0 {t0}'
    if statements[1]['block'] < statements[0]['block']:
        return f'blocks: block1 {statements[1]["block"]} is below block0 {statements[0]["block"]}'
    return None


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


def parse_capability_time_etny(text, name=CAPABILITY_TIME_OBJECT):
    """A time object (capability.time.etny, probe.time.<g>.etny) parsed:
    [t0, t1], the two GET /validator/time answers (the six keys of
    TIME_ANSWER_KEYS each), one JSON object per LF-terminated line, t0
    first. Raises ValueError on any other shape."""
    lines = time_statement_bodies(text, name)
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


def _int_field(named, name):
    value = named[name]
    if not value.isdigit() or not value.isascii():
        raise ValueError(f'{name} is not a decimal integer')
    return int(value)


def heap_and_workers(field):
    """(heap, workers) of a trustedzone record line's last field, which is
    exactly the two entries heap=<SCONE_HEAP>;workers=<n>, the heap not
    empty and the worker count a plain decimal (capability-record.json
    lineLayout; bench-record.json readings.trustedzoneField). Raises
    ValueError on any other field, a bench line's five readings
    included."""
    entries = field.split(';')
    if (len(entries) != 2 or not entries[0].startswith('heap=') or not entries[1].startswith('workers=')
            or entries[0] == 'heap='):
        raise ValueError('the last field is not heap=<heap>;workers=<n>')
    workers = entries[1][len('workers='):]
    if not PLAIN_DECIMAL_RE.fullmatch(workers):
        raise ValueError('workers is not a plain decimal')
    return entries[0][len('heap='):], int(workers)


def network_field(chain_id, pox):
    """The record line's network field as the trustedzone writes it,
    `<chainId>:<pox>`, with the address lower-cased for the compare."""
    return f'{int(chain_id)}:{str(pox).lower()}'


def capability_record_from(capability_etny, capability_time_etny, node_address, requirement, has_validator_set,
                           now, chain_id, pox, registry, is_seated, matching_network=False):
    """(record, None) when the integration test's objects make an admitted
    record, (None, reason) otherwise. `matching_network` is whether the
    network's pricing names a PoxMatching (matching() is not zero), where
    a v1 record is refused (capability-record.json admission). Admitted:
    capability.etny parses (parse_capability_etny), the line's node is
    `node_address` lower-cased, its network is `chain_id` and `pox`
    (network_field, the address compared case-insensitively), its
    validator and both signature fields are non-empty, capability.time.etny
    exists with two statements naming the line's node, validator, times and
    signatures, each recovering over `chain_id` and `registry` to the
    validator, `is_seated(validator)` holds, t1 is within CU_TTL_SECONDS of
    `now`, gflops is at most CU_MAX_GFLOPS, cuv is at least CU_VERSION_MIN,
    `requirement` is dcap and `has_validator_set` holds; and by its form:

    - v1: both statements over the run nonce, t1 at least t0 and at most
      BENCHMARK_MAX_SECONDS after it, as at pox-smart-contract-litvm
      52e36e0;
    - v2: the closing checks (closing_check, the outcome
      <gflops>|<cuv>|<last field>), t1 - t0 at most BENCHMARK_MAX_SECONDS
      and gflops within the rate bound of the window (above_rate_bound over
      record_work(workers)); a refusal by a closing check or the rate bound
      reads '<check>: <why>' (closingChecks names).

    The record: the line's fields (version, network, node, gflops, cuv,
    validator, nonce, salt (None on v1), t0, t1, sig0, sig1, heap, workers),
    line, sig_der_hex, platform_line, block0 and block1 from the
    statements, and the two raw texts. gflops (field 4) is the node's
    attested GFLOPS; one CU is 100 GFLOPS."""
    if requirement != ADMITTED_REQUIREMENT:
        return None, f'the image requires {requirement}, not {ADMITTED_REQUIREMENT}'
    if not has_validator_set:
        return None, 'the network has no validator set'
    if capability_etny is None:
        return None, f'no {CAPABILITY_OBJECT}'
    try:
        parsed = parse_capability_etny(capability_etny)
        named = parsed['named']
        version = parsed['version']
        network, node, validator, nonce = named['network'], named['node'], named['validator'], named['nonce']
        sig0, sig1, salt = named['sig0'], named['sig1'], named.get('salt')
        gflops = _int_field(named, 'gflops')
        cuv = _int_field(named, 'cuv')
        t0 = _int_field(named, 't0')
        t1 = _int_field(named, 't1')
        heap, workers = heap_and_workers(named['last'])
        if salt is not None and not NONCE_RE.match(salt):
            raise ValueError('the salt is not 32 bytes of 0x hex')
    except ValueError as e:
        return None, f'{CAPABILITY_OBJECT}: {e}'
    if version == RECORD_VERSION_V1 and matching_network:
        return None, ('admission: a v1 record is refused on a network whose pricing names a PoxMatching (its line is '
                      'rebuilt as v2 and does not verify)')
    if node != str(node_address).lower():
        return None, f'the record names node {node}, not {str(node_address).lower()}'
    if network.lower() != network_field(chain_id, pox):
        return None, f'the record names network {network}, not {network_field(chain_id, pox)}'
    if version == RECORD_VERSION_V1 and (t1 < t0 or t1 - t0 > BENCHMARK_MAX_SECONDS):
        return None, f'run too long: t0 {t0}, t1 {t1} (at most BENCHMARK_MAX_SECONDS {BENCHMARK_MAX_SECONDS})'
    if not validator:
        return None, 'the record carries no validator (host time)'
    if not sig0 or not sig1:
        return None, 'the record carries an empty time statement signature'
    if capability_time_etny is None:
        return None, f'no {CAPABILITY_TIME_OBJECT}'
    try:
        bodies = time_statement_bodies(capability_time_etny, CAPABILITY_TIME_OBJECT)
        statements = parse_capability_time_etny(capability_time_etny)
    except ValueError as e:
        return None, f'{CAPABILITY_TIME_OBJECT}: {e}'
    if version == RECORD_VERSION_V2:
        outcome = f"{named['gflops']}|{named['cuv']}|{named['last']}"
        refused = closing_check(statements, bodies, chain_id, registry, node, validator, nonce, salt, outcome,
                                t0, t1, sig0, sig1)
        if refused is not None:
            return None, refused
        if t1 - t0 > BENCHMARK_MAX_SECONDS:
            return None, f'run too long: t0 {t0}, t1 {t1} (at most BENCHMARK_MAX_SECONDS {BENCHMARK_MAX_SECONDS})'
        if above_rate_bound(gflops, t1 - t0, record_work(workers)):
            return None, (f'rateBound: gflops {gflops} over a window of {t1 - t0} s is above the rate bound of '
                          f'{workers} workers')
    else:
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
    if gflops > CU_MAX_GFLOPS:
        return None, f'gflops {gflops} is above CU_MAX_GFLOPS {CU_MAX_GFLOPS}'
    if cuv < CU_VERSION_MIN:
        return None, f'cuv {cuv} is below CU_VERSION_MIN {CU_VERSION_MIN}'
    record = {
        'line': parsed['line'],
        'version': version,
        'network': network,
        'node': node,
        'gflops': gflops,
        'cuv': cuv,
        'validator': validator,
        'nonce': nonce,
        'salt': salt,
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


def record_current(record):
    """Whether a stored record is one the agent advertises: it carries gflops
    and its cuv is at least CU_VERSION_MIN."""
    return 'gflops' in record and int(record.get('cuv') or 0) >= CU_VERSION_MIN


def record_version(record):
    """The form of a stored record, v1 for one stored without a version (a
    record stored before v2 existed is a v1 record)."""
    return (record or {}).get('version') or RECORD_VERSION_V1


def record_admitted_on(record, matching_network):
    """Whether a stored record is admitted on a network: a v1 record is not
    where the pricing names a PoxMatching (capability-record.json
    admission), every other record is."""
    return not (matching_network and record_version(record) == RECORD_VERSION_V1)


class RecordStore:
    """node/<network>/capability.json: {image name: {image_hash, passed_at,
    record, probe_first_pending}} for the passed integration tests of the
    network, `record` the dict of capability_record_from or None; an entry
    whose record load() dropped also carries retest_pending true until the
    next put. Every change is written as a whole snapshot through a
    temporary file."""

    def __init__(self, filepath):
        self.filepath = str(filepath)
        self._lock = threading.RLock()
        self.entries = {}
        self.load()

    def load(self):
        """Reads the file; an absent or unreadable file is an empty store. A
        stored record that is not record_current is dropped, its passed test
        kept with retest_pending set, and the file written."""
        with self._lock:
            try:
                with open(self.filepath) as f:
                    loaded = json.load(f)
            except (OSError, ValueError):
                loaded = {}
            self.entries = loaded if isinstance(loaded, dict) else {}
            dropped = [image for image, entry in self.entries.items()
                       if entry.get('record') is not None and not record_current(entry['record'])]
            for image in dropped:
                self.entries[image].update(record=None, probe_first_pending=False, retest_pending=True)
            if dropped:
                self._write()
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

    def retest_pending(self, image):
        """Whether load() dropped `image`'s record and no put has followed:
        its integration test runs again, the pass standing."""
        entry = self.entries.get(image)
        return bool(entry and entry.get('retest_pending'))

    def consume_probe_first(self, image):
        """Clears the flag; whether it was set."""
        with self._lock:
            entry = self.entries.get(image)
            if not entry or not entry.get('probe_first_pending'):
                return False
            entry['probe_first_pending'] = False
            self._write()
            return True

    def image_of(self, key):
        """The image whose stored record `key` identifies (utils.record_key,
        the cusig a DP request carries), None when no stored record has it."""
        if key is None:
            return None
        for image, entry in self.entries.items():
            if entry.get('record') is not None and record_key(entry['record']) == key:
                return image
        return None

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
    and, with `record`, the six cu keys (cugflops = the record's gflops, cuv,
    cuat = t1, cusig = the DER hex without 0x, cuheap, cuworkers), then the
    bench's two readings when the record carries them, encmem= (the enclave
    size it allocated) and custorage= (the storage it read). `record_image`
    is listed first (the validators verify cusig under the first name's
    certificates) and the other runnable names sorted after it; without a
    record the names are sorted."""
    names = sorted(trustedzones_runnable)
    if record is not None and record_image in names:
        names.remove(record_image)
        names.insert(0, record_image)
    value = f"trustedzones={','.join(names)};ipfs={peer_id};{DP_STARTED_ROW_CAPABILITY}"
    if record is None:
        return value
    sig = record['sig_der_hex'][2:] if record['sig_der_hex'].lower().startswith('0x') else record['sig_der_hex']
    value = (f"{value};cugflops={record['gflops']};cuv={record['cuv']};cuat={record['t1']};cusig={sig.lower()}"
             f";cuheap={record['heap']};cuworkers={record['workers']}")
    if record_enclave_memory(record) is not None:
        value += f";{RECORD_ENCLAVE_MEMORY_KEY}={record_enclave_memory(record)}"
    if record_storage(record) is not None:
        value += f";{RECORD_STORAGE_KEY}={record_storage(record)}"
    return value


def metadata3(geo, record):
    """A DP request's metadata3: the geography (possibly empty) first and,
    with `record`, the keys cuval, cun, cus (a v2 record's salt; none for a
    v1 record), cut0, cut1, cub0, cub1, cusig0, cusig1 (metadata-keys.md
    metadata3)."""
    geo = geo or ''
    if record is None:
        return geo
    salt = f";cus={record['salt']}" if record_version(record) == RECORD_VERSION_V2 else ''
    return (f"{geo};cuval={record['validator']};cun={record['nonce']}{salt};cut0={record['t0']};cut1={record['t1']}"
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


# The bytes a trustedzone identity key signs for its own statements, an
# etny-cu|v2 record line and an etny-sw row (pox-smart-contract-litvm 2f600f6
# spec/reserved-prefixes.json statementForm, SF19, OD10 B): 0xFF ||
# "ecld-stmt-1" || 0x00 || the statement's UTF-8 bytes. 0xFF occurs in no
# UTF-8 encoding, so no hand-over signature is over these bytes.
STATEMENT_PREFIX = b'\xffecld-stmt-1\x00'


def statement_bytes(statement):
    """The signed bytes of `statement` in the statement form."""
    return STATEMENT_PREFIX + str(statement).encode('utf-8')


def verify_statement(cert_text, statement, der_hex, matching_network):
    """Whether `der_hex` signs the trustedzone statement `statement` (an
    etny-sw row's text) under `cert_text` (verify_p384) in a form the
    network admits (reserved-prefixes.json statementForm.cutRule): on a
    network whose pricing names a PoxMatching the statement form alone
    (statement_bytes), on any other the plain form (the UTF-8 text) and the
    statement form."""
    if verify_p384(cert_text, statement_bytes(statement), der_hex):
        return True
    return not matching_network and verify_p384(cert_text, statement, der_hex)


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
