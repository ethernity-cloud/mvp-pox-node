"""The bench record: the line a node's memory probe of one listed enclave
size signs with that size's probe key (pox-smart-contract-litvm 2f600f6
spec/metadata-keys.md "The bench record", spec/bench-record.json;
MATCHING-DESIGN.md 3.7, D147, D188 to D191, D197; MATCHING-SYNC-PLAN.md B01,
B04, C01):

    etny-pb|v1|<chainId>:<pox>|<node>|<gflops>|<cuv>|<validator>|<n0>|<salt>|<t0>|<t1>|<sig t0>|<sig t1>|heap=<h>;workers=<n>;encmem=<g>;custorage=<s>;epc=<e>

parsed from the integration bucket's probe.<g>.etny and probe.time.<g>.etny,
admitted by the checks the validators' members make (bench_verdict, in
benchChecks' order) and those of a reader that holds both time answers'
bodies (capability.closing_check), persisted per network in bench.json
(BenchStore), and carried in the DP request's metadata4 and metadata3 under
the seventeen pb keys after the trustedzone record's cu keys
(metadata4_keys, metadata3_keys). A request carries a bench record only
beside a trustedzone record."""
import json
import math
import os
import re
import threading

import capability
from constants import BENCHMARK_OPS_PER_ROUND, CU_TTL_SECONDS, CU_VERSION_MIN

BENCH_TAG = 'etny-pb'
BENCH_VERSION = 'v1'
LINE_FIELDS = 14
# The names of the bench line's fields, those of a v2 trustedzone line.
FIELD_NAMES = capability.LINE_FIELD_NAMES[capability.RECORD_VERSION_V2]
# The readings field: exactly these five entries in this order.
READINGS = ('heap', 'workers', 'encmem', 'custorage', 'epc')
HEAP_RE = re.compile(r'[1-9][0-9]*[MG]', re.ASCII)
READING_MAX = 2**53 - 1
EPC_MAX_MIB = 2**31 - 1
# The integration-bucket objects of the probe of size <g> GiB.
PROBE_OBJECT = 'probe.{size}.etny'
PROBE_TIME_OBJECT = 'probe.time.{size}.etny'
# The pb keys, appended after the cu keys of the same string.
METADATA4_KEYS = ('pbgflops', 'pbcuv', 'pbsig', 'pbheap', 'pbworkers', 'pbencmem', 'pbstorage', 'pbepc')
METADATA3_KEYS = ('pbval', 'pbn', 'pbs', 'pbt0', 'pbt1', 'pbb0', 'pbb1', 'pbsig0', 'pbsig1')
BENCH_KEYS = METADATA4_KEYS + METADATA3_KEYS
# The trustedzone record's cu keys; cus only on a v2 record (a request
# without it carries a v1 record).
TRUSTEDZONE_METADATA4_KEYS = ('cugflops', 'cuv', 'cuat', 'cusig', 'cuheap', 'cuworkers')
TRUSTEDZONE_METADATA3_KEYS = ('cuval', 'cun', 'cus', 'cut0', 'cut1', 'cub0', 'cub1', 'cusig0', 'cusig1')
TRUSTEDZONE_OPTIONAL_KEYS = ('cus',)

# The members' bench checks in their order (benchChecks.order) and their
# figures, placeholders to be calibrated on bare metal (OD16).
CHECKS = ('keys', 'line', 'signature', 'node', 'statements', 'order', 'windowBound', 'expiry', 'region', 'share',
          'floor', 'cuv')
SHARE_MILLI = 100
FLOOR_GFLOPS_BY_ISA = {'sse2': 7, 'avx2-fma': 7, 'avx512f': 7}
WINDOW_BOUND_SECONDS = {1: 80, 2: 80, 4: 80, 8: 80, 16: 80, 32: 80, 64: 80}
# The region run's constants (benchChecks.regionWork.constants: the probe's
# probe.json and probe_kernel.js).
TRUSTEDZONE_HEAP_MIB = 256
SECURELOCK_HEAP_MIB = 256
CHUNK_MIB = 1
PAGE_BYTES = 4096
STRIDE_SEGMENTS = 16
EPC_ALLOWANCE_MIB = 16
VERSION_ARRAY_MIB_PER_GIB = 2
EPC_PASSES = 2
EPC_MIN_VISITS = 1048576
EPC_SLICES = 8


def readings_why(field):
    """The test a bench line's readings field fails (bench-record.json
    readings.refused `why`), None when it is in the grammar: 'entries' for
    other than five entries, 'order' for another key at a place, 'heapForm'
    for a heap that is not a digit 1-9, digits and M or G, 'notPlainDecimal'
    for a workers, encmem, custorage or epc value that is not a plain decimal
    of at most 2^53 - 1, 'encmemNotListed' for a size not listed (D150),
    'epcBelowOne' and 'epcAboveMax' for an epc outside 1 to 2^31 - 1."""
    entries = str(field).split(';')
    if len(entries) != len(READINGS):
        return 'entries'
    values = {}
    for name, entry in zip(READINGS, entries):
        key, separator, value = entry.partition('=')
        if key != name:
            return 'order'
        values[name] = value if separator else None
    if not HEAP_RE.fullmatch(values['heap'] or ''):
        return 'heapForm'
    for name in ('workers', 'encmem', 'custorage', 'epc'):
        value = values[name]
        if value is None or not capability.PLAIN_DECIMAL_RE.fullmatch(value) or int(value) > READING_MAX:
            return 'notPlainDecimal'
    if int(values['encmem']) not in capability.ENCLAVE_SIZES_GIB:
        return 'encmemNotListed'
    if int(values['epc']) < 1:
        return 'epcBelowOne'
    if int(values['epc']) > EPC_MAX_MIB:
        return 'epcAboveMax'
    return None


def parse_readings(field):
    """{heap, workers, encmem, custorage, epc} of a readings field in the
    grammar (readings_why), the four figures as integers. Raises ValueError
    naming the test a field outside it fails."""
    why = readings_why(field)
    if why is not None:
        raise ValueError(f'the readings field fails {why}')
    values = dict(entry.split('=', 1) for entry in field.split(';'))
    return {'heap': values['heap'], 'workers': int(values['workers']), 'encmem': int(values['encmem']),
            'custorage': int(values['custorage']), 'epc': int(values['epc'])}


def region_work(encmem, workers, epc):
    """The fixed work of the probe's strided region run over the EPC claim
    `epc` MiB at size `encmem` GiB on `workers` workers
    (benchChecks.regionWork.rule): heapGib = ceil((encmem x 1024 + 256 +
    256) / 1024), allowance = 16 + 2 x heapGib MiB, region = epc - allowance
    MiB, regionPages = region x 2^20 / 4096, visits = max(2 x regionPages,
    1048576), slices = max(1, min(8, floor(visits / regionPages))), rounds =
    ceil(visits / (slices x 16 x workers)), W = slices x rounds x workers x
    8388608; None when the claim leaves no region of CHUNK_MIB beside the
    allowance."""
    heap_gib = math.ceil((int(encmem) * 1024 + SECURELOCK_HEAP_MIB + TRUSTEDZONE_HEAP_MIB) / 1024)
    region_mib = int(epc) - (EPC_ALLOWANCE_MIB + VERSION_ARRAY_MIB_PER_GIB * heap_gib)
    if region_mib < CHUNK_MIB:
        return None
    pages = region_mib * 2**20 // PAGE_BYTES
    visits = max(EPC_PASSES * pages, EPC_MIN_VISITS)
    slices = max(1, min(EPC_SLICES, visits // pages))
    rounds = -(-visits // (slices * STRIDE_SEGMENTS * int(workers)))
    return slices * rounds * int(workers) * BENCHMARK_OPS_PER_ROUND


def entries_of(text, positional):
    """[(key, value)] of a metadata string's ;-separated entries, each key
    up to its first '='; the first field skipped for metadata3's geography
    (`positional`); a bare flag's value None."""
    parts = str(text or '').split(';')[1 if positional else 0:]
    entries = []
    for part in parts:
        key, separator, value = part.partition('=')
        entries.append((key, value if separator else None))
    return entries


def family_keys(metadata3, metadata4, names, optional=()):
    """{key: value} of the keys `names` in a DP request's two strings, taken
    order-free (metadata-keys.md, bench-record.json keyRules); None when a
    key of `names` is missing or any key of `names` or `optional` is written
    twice; a key of `optional` present once is included."""
    entries = entries_of(metadata3, True) + entries_of(metadata4, False)
    found = {}
    for name in tuple(names) + tuple(optional):
        values = [value for key, value in entries if key == name]
        if len(values) > 1 or (not values and name not in optional):
            return None
        if values:
            found[name] = values[0]
    return found


def request_records(metadata3, metadata4):
    """(trustedzone keys, bench keys) a DP request carries by the key rules:
    each the {key: value} of its family (family_keys), None when the request
    carries no such record. A bench record stands only beside a trustedzone
    record's cugflops, which its share check reads; the members judge that
    (bench_verdict)."""
    trustedzone = family_keys(metadata3, metadata4,
                              [k for k in TRUSTEDZONE_METADATA4_KEYS + TRUSTEDZONE_METADATA3_KEYS
                               if k not in TRUSTEDZONE_OPTIONAL_KEYS], TRUSTEDZONE_OPTIONAL_KEYS)
    return trustedzone, family_keys(metadata3, metadata4, BENCH_KEYS)


def rebuilt_line(keys, chain_id, pox, dproc):
    """The bench line a validator rebuilds from the seventeen pb keys, the
    baked chain id and PoX and the request's dproc (metadata-keys.md
    lineFromMetadata)."""
    readings = (f"heap={keys['pbheap']};workers={keys['pbworkers']};encmem={keys['pbencmem']};"
                f"custorage={keys['pbstorage']};epc={keys['pbepc']}")
    return '|'.join([BENCH_TAG, BENCH_VERSION, f'{int(chain_id)}:{pox}', str(dproc).lower(), keys['pbgflops'],
                     keys['pbcuv'], keys['pbval'], keys['pbn'], keys['pbs'], keys['pbt0'], keys['pbt1'],
                     keys['pbsig0'], keys['pbsig1'], readings])


def lowest_floor():
    """The floor the share check's companion applies: the bench line names no
    instruction set, so the lowest of FLOOR_GFLOPS_BY_ISA."""
    return min(FLOOR_GFLOPS_BY_ISA.values())


def bench_verdict(keys, chain_id, pox, dproc, registry, probe_keys, trustedzone_keys, is_seated, read_time, cugflops):
    """None when the seventeen pb keys `keys` carry a verified bench record,
    else the first check of CHECKS that fails (bench-record.json
    benchChecks): keys (all seventeen, None when absent); line (the rebuilt
    line's readings in the grammar); signature (pbsig verifies in the plain
    form under one of `probe_keys`, the probe certificates of size pbencmem,
    and under none of `trustedzone_keys`); node (the rebuild bakes
    `chain_id`, `pox` and `dproc`, so a record of another node or network
    fails its signature); statements (pbsig0 over n0 = pbn and pbsig1 over
    n1 = close_nonce(pbn, pbs, "<pbgflops>|<pbcuv>|<readings>") recover to
    pbval over `chain_id` and `registry`, and `is_seated(pbval)`); order
    (pbt1 >= pbt0, pbb1 >= pbb0); windowBound (pbt1 - pbt0 at most
    WINDOW_BOUND_SECONDS of the size); expiry (pbt1 within CU_TTL_SECONDS of
    `read_time`); region (region_work leaves a region); share (W x 1000 at
    least SHARE_MILLI x `cugflops` x window x 10^9, `cugflops` the request's
    verified trustedzone record's, None for none); floor (W at least the
    lowest floor x window x 10^9); cuv (pbcuv at least CU_VERSION_MIN). No
    check reads pbgflops beyond the closing nonce: the line's GFLOPS is a
    figure of the enclave's clock."""
    if keys is None or any(name not in keys for name in BENCH_KEYS):
        return 'keys'
    line = rebuilt_line(keys, chain_id, pox, dproc)
    fields = line.split('|')
    readings = fields[13]
    if len(fields) != LINE_FIELDS or readings_why(readings) is not None:
        return 'line'
    try:
        t0, t1, b0, b1 = (int(keys[name]) for name in ('pbt0', 'pbt1', 'pbb0', 'pbb1'))
        cuv = int(keys['pbcuv'])
    except ValueError:
        return 'line'
    if not any(capability.verify_p384(key, line, keys['pbsig']) for key in probe_keys):
        return 'signature'
    if any(capability.verify_p384(key, line, keys['pbsig']) for key in trustedzone_keys):
        return 'signature'
    validator = str(keys['pbval']).lower()
    if not (capability.NONCE_RE.match(keys['pbn']) and capability.NONCE_RE.match(keys['pbs'])
            and capability.SIG_RE.match(keys['pbsig0']) and capability.SIG_RE.match(keys['pbsig1'])):
        return 'statements'
    n1 = capability.close_nonce(keys['pbn'], keys['pbs'], f"{keys['pbgflops']}|{keys['pbcuv']}|{readings}")
    for nonce, t, block, sig in ((keys['pbn'], t0, b0, keys['pbsig0']), (n1, t1, b1, keys['pbsig1'])):
        answer = {'node': str(dproc).lower(), 'nonce': nonce, 't': t, 'block': block, 'sig': sig}
        if capability.recover_time_statement_signer(chain_id, registry, answer) != validator:
            return 'statements'
    if not is_seated(validator):
        return 'statements'
    if t1 < t0 or b1 < b0:
        return 'order'
    window = t1 - t0
    values = parse_readings(readings)
    if window > WINDOW_BOUND_SECONDS[values['encmem']]:
        return 'windowBound'
    if abs(int(read_time) - t1) > CU_TTL_SECONDS:
        return 'expiry'
    work = region_work(values['encmem'], values['workers'], values['epc'])
    if work is None:
        return 'region'
    if cugflops is None or work * 1000 < SHARE_MILLI * int(cugflops) * window * 10**9:
        return 'share'
    if work < lowest_floor() * window * 10**9:
        return 'floor'
    if cuv < CU_VERSION_MIN:
        return 'cuv'
    return None


def parse_probe_etny(text, name):
    """probe.<g>.etny parsed (capability.parse_record_object) with {named}:
    {field name: text} by FIELD_NAMES and {readings}: parse_readings of the
    last field. Raises ValueError on any other shape."""
    parsed = capability.parse_record_object(text, name, {(BENCH_TAG, BENCH_VERSION): LINE_FIELDS})
    parsed['named'] = dict(zip(FIELD_NAMES, parsed['fields']))
    parsed['readings'] = parse_readings(parsed['named']['last'])
    return parsed


def record_keys(record):
    """The seventeen pb keys of a bench record, as metadata4 and metadata3
    carry them."""
    sig = record['sig_der_hex'][2:] if record['sig_der_hex'].lower().startswith('0x') else record['sig_der_hex']
    return {
        'pbgflops': str(record['gflops']), 'pbcuv': str(record['cuv']), 'pbsig': sig.lower(),
        'pbheap': record['heap'], 'pbworkers': str(record['workers']), 'pbencmem': str(record['encmem']),
        'pbstorage': str(record['custorage']), 'pbepc': str(record['epc']),
        'pbval': record['validator'], 'pbn': record['nonce'], 'pbs': record['salt'], 'pbt0': str(record['t0']),
        'pbt1': str(record['t1']), 'pbb0': str(record['block0']), 'pbb1': str(record['block1']),
        'pbsig0': record['sig0'], 'pbsig1': record['sig1'],
    }


def bench_record_from(probe_etny, probe_time_etny, size, node_address, now, chain_id, pox, registry, is_seated,
                      probe_keys, trustedzone_keys, cugflops):
    """(record, None) when the probe of size `size` GiB wrote an admitted
    bench record, (None, reason) otherwise. Admitted: probe.<g>.etny parses
    (parse_probe_etny), its encmem is `size`, its node is `node_address` and
    its network `chain_id`:`pox` (capability.network_field), and
    probe.time.<g>.etny holds two answers whose bodies differ and which name
    the line's node, validator, times and signatures; then bench_verdict
    over the record's seventeen keys passes, read at `now` with the
    trustedzone record's `cugflops`. A refusal reads '<check>: <why>' with
    the check's name (CHECKS, equalBodies, statements). The record: the
    line's fields (network, node, gflops, cuv, validator, nonce, salt, t0,
    t1, sig0, sig1), its readings (heap, workers, encmem, custorage, epc),
    line, sig_der_hex, platform_line, block0 and block1 of the answers, size
    and the two raw texts."""
    name, time_name = PROBE_OBJECT.format(size=int(size)), PROBE_TIME_OBJECT.format(size=int(size))
    if probe_etny is None:
        return None, f'no {name}'
    try:
        parsed = parse_probe_etny(probe_etny, name)
    except ValueError as e:
        return None, f'line: {name}: {e}'
    named, readings = parsed['named'], parsed['readings']
    if readings['encmem'] != int(size):
        return None, f"line: {name} states encmem={readings['encmem']}, not {int(size)}"
    if named['node'] != str(node_address).lower():
        return None, f"node: the record names node {named['node']}, not {str(node_address).lower()}"
    if named['network'].lower() != capability.network_field(chain_id, pox):
        return None, f"node: the record names network {named['network']}, not {capability.network_field(chain_id, pox)}"
    if probe_time_etny is None:
        return None, f'statements: no {time_name}'
    try:
        bodies = capability.time_statement_bodies(probe_time_etny, time_name)
        statements = capability.parse_capability_time_etny(probe_time_etny, time_name)
    except ValueError as e:
        return None, f'statements: {e}'
    if bodies[0] == bodies[1]:
        return None, 'equalBodies: the closing answer is the opening one'
    for index, (statement, t, sig) in enumerate(zip(statements, (named['t0'], named['t1']),
                                                    (named['sig0'], named['sig1']))):
        if (statement['validator'].lower() != named['validator'] or statement['node'].lower() != named['node']
                or str(statement['t']) != t or statement['sig'].lower() != sig.lower()):
            return None, f'statements: time statement {index} does not match the record line'
    record = {
        'line': parsed['line'], 'network': named['network'], 'node': named['node'], 'gflops': int(named['gflops']),
        'cuv': int(named['cuv']), 'validator': named['validator'], 'nonce': named['nonce'], 'salt': named['salt'],
        't0': int(named['t0']), 't1': int(named['t1']), 'sig0': named['sig0'], 'sig1': named['sig1'],
        **readings, 'sig_der_hex': parsed['sig_der_hex'], 'platform_line': parsed['platform_line'],
        'block0': statements[0]['block'], 'block1': statements[1]['block'], 'size': int(size),
        'probe_etny': probe_etny, 'probe_time_etny': probe_time_etny,
    }
    failed = bench_verdict(record_keys(record), chain_id, pox, node_address, registry, probe_keys, trustedzone_keys,
                           is_seated, now, cugflops)
    if failed is not None:
        return None, f'{failed}: the bench checks refuse the record'
    return record, None


def bench_key(record):
    """What identifies a bench record on the chain: its pbsig, the DER
    signature hex lower-cased without 0x; None for no record."""
    return None if record is None else record_keys(record)['pbsig']


def metadata4_keys(record):
    """The pb keys a DP request's metadata4 carries after the cu keys, ';'
    first; '' without a record."""
    if record is None:
        return ''
    keys = record_keys(record)
    return ''.join(f';{name}={keys[name]}' for name in METADATA4_KEYS)


def metadata3_keys(record):
    """The pb keys a DP request's metadata3 carries after the cu keys, ';'
    first; '' without a record."""
    if record is None:
        return ''
    keys = record_keys(record)
    return ''.join(f';{name}={keys[name]}' for name in METADATA3_KEYS)


def share_holds(record, cugflops):
    """Whether an admitted bench record passes the share and floor checks
    beside a trustedzone record of `cugflops` (bench_verdict's share and
    floor, from the record's attested window and region work): a request
    carries the bench record only beside a trustedzone record it proves
    something with."""
    if record is None or cugflops is None:
        return False
    work = region_work(record['encmem'], record['workers'], record['epc'])
    window = int(record['t1']) - int(record['t0'])
    return (work is not None and work * 1000 >= SHARE_MILLI * int(cugflops) * window * 10**9
            and work >= lowest_floor() * window * 10**9)


# The separator of an offer's key: the trustedzone record's cusig, then the
# bench record's pbsig when the request carries one.
OFFER_KEY_SEPARATOR = '|'


def offer_key(trustedzone_key, bench):
    """What identifies the records a DP request carries: the trustedzone
    record's key (utils.record_key, None for none), and after
    OFFER_KEY_SEPARATOR the bench record's pbsig when it carries one; None
    without a trustedzone record, beside which alone a bench record is
    carried."""
    if trustedzone_key is None:
        return None
    return trustedzone_key if bench is None else f'{trustedzone_key}{OFFER_KEY_SEPARATOR}{bench}'


def split_offer_key(key):
    """(trustedzone record key, bench record key) of an offer_key, each None
    when absent."""
    if key is None:
        return None, None
    trustedzone, _, bench = str(key).partition(OFFER_KEY_SEPARATOR)
    return trustedzone or None, bench or None


def request_offer_key(metadata3, metadata4):
    """The offer_key of the records a DP request's metadata3 and metadata4
    carry by the key rules (request_records): the cusig lower-cased without
    0x, and the pbsig beside it."""
    trustedzone, bench = request_records(metadata3, metadata4)
    if trustedzone is None:
        return None
    cusig = trustedzone['cusig']
    cusig = (cusig[2:] if cusig.lower().startswith('0x') else cusig).lower()
    return offer_key(cusig, bench['pbsig'].lower() if bench is not None else None)


def record_expired(record, now):
    """Whether a bench record has reached CU_TTL_SECONDS after its t1."""
    return int(now) >= int(record['t1']) + CU_TTL_SECONDS


class BenchStore:
    """node/<network>/bench.json: the network's bench record {record,
    passed_at, image_hash, size} of the probe run that admitted it, the
    record the dict of bench_record_from, or nothing. Every change is
    written as a whole snapshot through a temporary file."""

    def __init__(self, filepath):
        self.filepath = str(filepath)
        self._lock = threading.RLock()
        self.entry = None
        self.load()

    def load(self):
        """Reads the file; an absent or unreadable file, or one that holds no
        record, is an empty store."""
        with self._lock:
            try:
                with open(self.filepath) as f:
                    loaded = json.load(f)
            except (OSError, ValueError):
                loaded = None
            self.entry = loaded if isinstance(loaded, dict) and isinstance(loaded.get('record'), dict) else None
            return self.entry

    def _write(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
        temporary = f'{self.filepath}.tmp'
        with open(temporary, 'w') as f:
            json.dump(self.entry, f)
        os.replace(temporary, self.filepath)

    def put(self, record, image_hash, passed_at):
        with self._lock:
            self.entry = {'record': record, 'passed_at': int(passed_at), 'image_hash': image_hash,
                          'size': int(record['size'])}
            self._write()

    def drop(self):
        with self._lock:
            self.entry = None
            self._write()

    def record(self, now):
        """The stored record while it is younger than CU_TTL_SECONDS at
        `now`, None otherwise."""
        if self.entry is None or record_expired(self.entry['record'], now):
            return None
        return self.entry['record']

    def remeasure_due(self, now):
        """Whether the probe runs again: no record is stored, or the stored
        one is capability.CU_REMEASURE_MARGIN_SECONDS from its
        CU_TTL_SECONDS."""
        return self.entry is None or capability.remeasure_due(self.entry['passed_at'], now)
