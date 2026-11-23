"""The ranking rule of PoX 2.0 matching on the node agent's side
(MATCHING-DESIGN.md 4.4, 4.5, 4.7, 7.5, 7.6; pox-smart-contract-litvm
spec/matching.md sections 3 to 8 and 18; D183).

The agent ranks itself on a copy of the roll it holds in memory and never
writes to disk (RollCopy): the roll entries of the slots its open rounds
rank on, every period of their nodes with its end, and the nodes' skips of
the images it has seen requested. The copy is rebuilt at every start from
PoxMatching's bulk getters (rollLengths, rollPeriodsOf, periodAt, skipOf)
and fed from the Beat, Announced, Left and Skipped logs from the block of
that scan on.

candidates() and turn_of() answer what PoxMatchReader's candidates and
turnOf answer on the same inputs (PoxMatchReaderBase.sol _scan, _evaluate,
_listedPeriod, _ended; PoxMatchReader.sol turnOf, _countTurn), and
mandated() is the reader's _select: spec/ranking.json holds the reader's
answers, and node/tests/test_matching_ranking.py compares them. Every value
is an integer; a hash is keccak256 over the packed bytes of spec/matching.md
section 3."""
from collections import namedtuple

from eth_utils import keccak

BANDS = 50
TOP_BAND = 49
# The `until` of a list read; a turn read ends with its node's band
# (PoxMatchReaderBase.LIST).
LIST = 255
DEFAULT_NEED_GFLOPS = 100
METADATA1_FIELDS_WITHIN = 512
UINT16_MAX = 2**16 - 1
UINT32_MAX = 2**32 - 1
UINT128_MAX = 2**128 - 1

# The end reasons of a period (PoxMatchingState.sol END_*).
END_BUSY = 1
END_PAUSED = 2
END_REMOVED = 3
END_EXPIRED = 4
END_MISS = 5
END_REPLACED = 6
END_BANNED = 7

# The fields of IPoxMatchingTypes.ParameterSet, in the order of the struct
# and of parameterSet(index)'s answer (spec/matching.md section 2).
PARAMETER_FIELDS = (
    'effectAt', 'claims', 'fitPercent', 'maxSteps', 'maxRounds', 'fullBlockPercent', 'settleMax', 'pendingMax',
    'correctionMaxEntries', 'stepBlocks', 'leadBlocks', 'banSlots', 'gain', 'missUnits', 'lateUnits', 'bandWidth',
    'minListedSeconds', 'maxRequestAgeSeconds', 'startTimeoutSeconds', 'minPauseSeconds', 'servingGraceSeconds',
    'correctionWindowSeconds', 'correctionAskSeconds', 'fullWindowSeconds', 'fetchTrySeconds', 'heldMarginSeconds',
    'nodeFaultCodes', 'defaultFaultCodes', 'ambiguousCodes', 'runBilledCodes')
Parameters = namedtuple('Parameters', PARAMETER_FIELDS)

# A period of a node (PoxMatchingState.Period): `start` is its `from`.
Period = namedtuple('Period', 'ordinal start to to_block end_reason dp_request_id cpu mem storage bandwidth gflops '
                              'enclave_memory tz_mask tip chain_first')
Skip = namedtuple('Skip', 'at until prev_at prev_until')
RollEntry = namedtuple('RollEntry', 'node score beat_time')

# A request as the ranking reads it (spec/matching.md section 4): `need` the
# GFLOPS it requires, `ceil` its ceiling (0: none), `g` the GFLOPS its escrow
# is sized for, `tz` its template's bit (0: not registered before its add),
# `image` its 31-byte image key, `ranked` whether it has a schedule (4.3).
Request = namedtuple('Request', 'id seed cpu mem storage bandwidth need ceil hours g tz image ranked params')
# A round of a request: its number, the block and time that opened it, its
# bid and its base, in wei per CU-hour.
Round = namedtuple('Round', 'number block time bid base')

# An eligible entry of a list read: the reader's list word's fields, the
# period's tip, the entry's band and its index in the band.
Eligible = namedtuple('Eligible', 'node score fit match listed_ordinal dp_request_id tip band index')
# One read of a round's roll (candidates): the eligible entries found, the
# list hash and the matching count carried, the cursor and `done`.
ListRead = namedtuple('ListRead', 'eligible list_hash match_found next_band next_from done')
# turnOf's answer, without the round's stamp and `settled`.
Turn = namedtuple('Turn', 'eligible fit matches match_above fit_before rest_before fit_in_band rest_in_band done '
                          'mandate_step excused carry next_band next_from')
ZERO_HASH = b'\x00' * 32


def parameters_of(answer):
    """Parameters of a parameterSet(index) answer: a tuple in the struct's
    order, or a mapping by field name."""
    if isinstance(answer, dict):
        return Parameters(*(answer[name] for name in PARAMETER_FIELDS))
    return Parameters(*answer)


def address_bytes(address):
    """The 20 bytes of a hex address."""
    text = str(address)
    return bytes.fromhex(text[2:] if text.startswith(('0x', '0X')) else text)


def address_text(address):
    """A hex address in lower case, the form the copy keys nodes by."""
    return '0x' + address_bytes(address).hex()


def uint(value, width):
    """The `width`-byte big-endian bytes of the integer `value`."""
    return int(value).to_bytes(width, 'big')


def seed_of(seed_stamp, request_id):
    """The request's seed: keccak256(bytes22 seedStamp, uint256 id) (3.5)."""
    return keccak(bytes(seed_stamp) + uint(request_id, 32))


def key_of(seed, node):
    """The node's key in a request's order: uint256(keccak256(bytes32 seed,
    address node)) (4.4)."""
    return int.from_bytes(keccak(bytes(seed) + address_bytes(node)), 'big')


def round_stamp(request_id, seed_stamp, number, block, time, bid, base):
    """The stamp of a round (4.1): keccak256(uint256 id, bytes22 seedStamp,
    uint8 round, uint48 block, uint32 time, uint128 bid, uint128 base)."""
    return keccak(uint(request_id, 32) + bytes(seed_stamp) + uint(number, 1) + uint(block, 6) + uint(time, 4)
                  + uint(min(bid, UINT128_MAX), 16) + uint(min(base, UINT128_MAX), 16))


def list_link(h, node, fit, match):
    """One link of the list hash: keccak256(bytes32 h, address node, uint8
    flags), flags = fit + 2 x match (4.5)."""
    return keccak(bytes(h) + address_bytes(node) + uint(int(bool(fit)) + 2 * int(bool(match)), 1))


def metadata1_field(metadata1, index):
    """Field `index` of the ':'-separated metadata1 read as if it ended at
    its first 512 bytes (PoxMatchingState._fieldOf); b'' when it has fewer
    fields by then."""
    raw = metadata1.encode('utf-8') if isinstance(metadata1, str) else bytes(metadata1)
    fields = raw[:METADATA1_FIELDS_WITHIN].split(b':')
    return fields[index] if index < len(fields) else b''


def template_key(metadata1):
    """keccak256 of the request's template name, field 2 of metadata1 (4.2)."""
    return keccak(metadata1_field(metadata1, 2))


def image_key(metadata1):
    """The request's image key: the first 31 bytes of keccak256 of field 1
    of metadata1 (spec/matching.md section 3)."""
    return keccak(metadata1_field(metadata1, 1))[:31]


def need_gflops(max_gflops):
    """(need, ceil) of a V2 add's maxGflops argument (4.2, D38): the ceiling
    in its low 16 bits, the required GFLOPS in its high 16; a request that
    states none requires its ceiling when that is below 100, else 100."""
    ceil = int(max_gflops) & UINT16_MAX
    need = (int(max_gflops) >> 16) & UINT16_MAX
    if need == 0:
        need = ceil if 0 < ceil < DEFAULT_NEED_GFLOPS else DEFAULT_NEED_GFLOPS
    return need, ceil


def band_of(score, band_width):
    """The band a heartbeat writes a score into: min(49, score / bandWidth)."""
    return min(TOP_BAND, int(score) // int(band_width))


def step_at(block, round_block, params):
    """step(C) of 4.5 on the clock of a round opened in `round_block`,
    without a bound."""
    lead = round_block + params.leadBlocks
    if block <= lead:
        return 0
    return 1 + (block - lead - 1) // params.stepBlocks


def closed_step(block, round_block, params):
    """The last step whose last block is `block` or earlier (4.5)."""
    return max(0, step_at(block + 1, round_block, params) - 1)


def stored_step(step, params):
    """The step the contract stores for a step: at most maxSteps + 1."""
    return min(step, params.maxSteps + 1)


def first_block_of_step(step, round_block, params):
    """The first block of step `step` >= 1 of a round opened in
    `round_block`."""
    return round_block + params.leadBlocks + (step - 1) * params.stepBlocks + 1


def matches(request, price, gflops, bid):
    """The match of 3.7: price at most the bid, and the order's work at the
    price within the request's escrow at the bid."""
    return price <= bid and price * gflops * request.hours // 100 <= bid * request.g * request.hours // 100


def covers(request, period, full=True):
    """Whether `period` covers the request: cpu, memory, storage, bandwidth,
    the required GFLOPS and the ceiling, and with `full` the template and the
    enclave memory need (3.7, D146)."""
    return (period.cpu >= request.cpu and period.mem >= request.mem and period.storage >= request.storage
            and period.bandwidth >= request.bandwidth and period.gflops >= request.need
            and (request.ceil == 0 or period.gflops <= request.ceil)
            and (not full or ((period.tz_mask & request.tz) != 0 and period.enclave_memory >= request.mem)))


def fits(request, period, params):
    """The 20% fit rule of 3.7 on CU, memory and storage."""
    widen = 100 + params.fitPercent
    return (period.gflops * 100 <= request.need * widen and period.mem * 100 <= request.mem * widen + 99
            and period.storage * 100 <= request.storage * widen + 99)


def skipped(skip, t, min_listed):
    """Whether a skip (current or previous) is in force at `t` and was made
    minListedSeconds or more before it (7.15)."""
    if skip is None:
        return False
    return ((skip.until > t and skip.at + min_listed <= t)
            or (skip.prev_until > t and skip.prev_at + min_listed <= t))


def before(key_a, node_a, key_b, node_b):
    """The order inside a band: key ascending, then address."""
    return key_a < key_b or (key_a == key_b and int(address_text(node_a), 16) < int(address_text(node_b), 16))


# ------------------------------------------------------------ word decoders

def decode_period(ordinal, first, second):
    """A Period from periodAt's (or rollPeriodsOf's) two words."""
    first = int(first)
    second = int(second)
    return Period(
        ordinal=ordinal,
        start=first & UINT32_MAX,
        to=(first >> 32) & UINT32_MAX,
        to_block=(first >> 64) & (2**48 - 1),
        dp_request_id=(first >> 112) & (2**48 - 1),
        gflops=(first >> 160) & UINT16_MAX,
        cpu=(first >> 176) & 255,
        mem=(first >> 184) & 255,
        storage=(first >> 192) & 255,
        bandwidth=(first >> 200) & 255,
        tz_mask=(first >> 208) & UINT16_MAX,
        end_reason=(first >> 224) & 255,
        tip=second & UINT128_MAX,
        chain_first=(second >> 128) & (2**24 - 1),
        enclave_memory=(second >> 152) & 255,
    )


def period_ordinal(first):
    """The ordinal of a node's current period: its record's pastCount."""
    return (int(first) >> 232) & (2**24 - 1)


def decode_skip(word):
    """A Skip from skipOf's word; None for a node with no skip of the image."""
    word = int(word)
    if word == 0:
        return None
    return Skip(word & UINT32_MAX, (word >> 32) & UINT32_MAX, (word >> 64) & UINT32_MAX, (word >> 96) & UINT32_MAX)


def decode_roll_entry(word):
    """A RollEntry from a roll word: node << 96 | score << 80 | beatTime << 40."""
    word = int(word)
    return RollEntry('0x' + uint(word >> 96, 20).hex(), (word >> 80) & UINT16_MAX, (word >> 40) & (2**40 - 1))


# ---------------------------------------------------------------- the copy

class RollCopy:
    """The copy of the roll a node ranks on (spec/matching.md section 18),
    held in memory only. Nodes are keyed by their address in lower case.

    `rolls` {slot: [entries of band 0, ..., entries of band 49]};
    `periods` {node: [Period by ordinal]}, every period of the node from its
    first one the copy holds; `skips` {(node, image key): Skip}; `params`
    the parameter sets in the array's order; `templates` {template key:
    (bit, registeredAt)}. `block` is the last block whose logs the copy has
    applied; a node's periods and skips fetched at a block are not changed
    by logs of that block or earlier (`node_block`)."""

    def __init__(self):
        self.rolls = {}
        self.periods = {}
        self.first_ordinal = {}
        self.skips = {}
        self.node_block = {}
        self.params = []
        self.templates = {}
        self.block = None

    # --- the rebuild

    def set_roll(self, slot, bands):
        """The roll of `slot`: `bands` maps a band to its list of RollEntry
        in stored order."""
        roll = [[] for _ in range(BANDS)]
        for band, entries in bands.items():
            roll[int(band)] = list(entries)
        self.rolls[int(slot)] = roll

    def set_periods(self, node, periods, at_block=None):
        """The periods of `node` from the ordinal of the first of `periods`
        on, as read at `at_block`."""
        node = address_text(node)
        ordered = sorted(periods, key=lambda p: p.ordinal)
        self.periods[node] = ordered
        self.first_ordinal[node] = ordered[0].ordinal if ordered else 0
        if at_block is not None:
            self.node_block[node] = max(self.node_block.get(node, 0), at_block)

    def set_skip(self, node, image, skip):
        self.skips[(address_text(node), bytes(image))] = skip

    def knows(self, node):
        return address_text(node) in self.periods

    def forget_slots_before(self, slot):
        """Drops the rolls of the slots before `slot`, which no open round
        ranks on, and the periods and skips of the nodes no roll left holds."""
        for old in [s for s in self.rolls if s < slot]:
            del self.rolls[old]
        held = {e.node for roll in self.rolls.values() for band in roll for e in band}
        for node in [n for n in self.periods if n not in held]:
            del self.periods[node]
            self.first_ordinal.pop(node, None)
            self.node_block.pop(node, None)
        for k in [k for k in self.skips if k[0] not in held]:
            del self.skips[k]

    # --- the logs

    def __fresh(self, node, block):
        return block is None or block > self.node_block.get(node, -1)

    def on_beat(self, node, slot, score, band, index, beat_time):
        """A Beat log: the entry at `index` of `band` in the roll of `slot`,
        when the copy holds that roll; its beat time is the block's."""
        roll = self.rolls.get(int(slot))
        if roll is None:
            return
        entries = roll[int(band)]
        if int(index) == len(entries):
            entries.append(RollEntry(address_text(node), int(score), int(beat_time)))

    def on_announced(self, node, dp_request_id, start, cpu, mem, storage, bandwidth, gflops, tip, tz_mask,
                     enclave_memory, block=None):
        """An Announced log of a node whose periods the copy holds: a new
        period, its ordinal the previous one's plus one. An open period it
        replaces ended by the Left log before it."""
        node = address_text(node)
        if node not in self.periods or not self.__fresh(node, block):
            return
        periods = self.periods[node]
        ordinal = periods[-1].ordinal + 1 if periods else self.first_ordinal.get(node, 0)
        chain_first = ordinal
        if periods and periods[-1].end_reason == END_REPLACED and periods[-1].to == start:
            chain_first = periods[-1].chain_first
        periods.append(Period(ordinal, int(start), 0, 0, 0, int(dp_request_id), int(cpu), int(mem), int(storage),
                              int(bandwidth), int(gflops), int(enclave_memory), int(tz_mask), int(tip), chain_first))

    def on_left(self, node, reason, at, block):
        """A Left log: the node's open period ended for `reason` at time `at`
        in block `block`."""
        node = address_text(node)
        if node not in self.periods or not self.__fresh(node, block):
            return
        periods = self.periods[node]
        if periods and periods[-1].to == 0:
            periods[-1] = periods[-1]._replace(to=int(at), to_block=int(block), end_reason=int(reason))

    def on_skipped(self, node, image, at, until, prev_at, prev_until, block=None):
        """A Skipped log: the node's skip of the image as skipImage left it."""
        node = address_text(node)
        if not self.__fresh(node, block):
            return
        self.skips[(node, bytes(image))] = Skip(int(at), int(until), int(prev_at), int(prev_until))

    # --- reads

    def lengths(self, slot):
        roll = self.rolls.get(int(slot))
        return [len(roll[b]) for b in range(BANDS)] if roll is not None else [0] * BANDS

    def entry(self, slot, band, index):
        roll = self.rolls.get(int(slot))
        if roll is None or not 0 <= int(band) < BANDS or not 0 <= int(index) < len(roll[int(band)]):
            return None
        return roll[int(band)][int(index)]

    def place_of(self, slot, node):
        """(band, index) of `node` in the roll of `slot`, None when it holds
        no entry of it."""
        node = address_text(node)
        roll = self.rolls.get(int(slot))
        if roll is None:
            return None
        for band in range(TOP_BAND, -1, -1):
            for index, e in enumerate(roll[band]):
                if e.node == node:
                    return band, index
        return None

    def period(self, node, ordinal):
        """The period of `node` with `ordinal`, None for one the copy does
        not hold (an ordinal above the current one's included)."""
        periods = self.periods.get(address_text(node), [])
        first = self.first_ordinal.get(address_text(node), 0)
        i = ordinal - first
        return periods[i] if 0 <= i < len(periods) else None

    def skip(self, node, image):
        return self.skips.get((address_text(node), bytes(image)))

    def parameters_for(self, created_at):
        """The parameter set of a request added at `created_at`: the last of
        the array whose effectAt is below it (4.1); None when none is."""
        for params in reversed(self.params):
            if params.effectAt < created_at:
                return params
        return None

    def template_bit(self, key, created_at):
        """1 << the bit of template `key` when it was registered before
        `created_at`, 0 otherwise (4.2)."""
        found = self.templates.get(bytes(key))
        if found is None:
            return 0
        bit, registered_at = found
        return 1 << bit if registered_at < created_at else 0


def listed_period(copy, node, t, min_listed):
    """The newest period of `node` announced minListedSeconds or more before
    `t` (4.4; PoxMatchReaderBase._listedPeriod), None when there is none."""
    for period in reversed(copy.periods.get(address_text(node), [])):
        if period.start == 0:
            return None
        if period.start + min_listed <= t:
            return period
    return None


def evaluate(copy, request, rnd, node, score, band, index):
    """The entry (node, score) of `band` at `index` on the list of round
    `rnd` (4.4; PoxMatchReaderBase._evaluate): an Eligible when the node's
    listed period is open at T, covers the request with its template and
    enclave memory and holds no skip of the request's image in force at T;
    None otherwise."""
    params = request.params
    t = rnd.time
    period = listed_period(copy, node, t, params.minListedSeconds)
    if period is None or (period.to != 0 and period.to <= t) or not covers(request, period, True):
        return None
    if skipped(copy.skip(node, request.image), t, params.minListedSeconds):
        return None
    fit = fits(request, period, params)
    match = matches(request, rnd.base + period.tip, period.gflops, rnd.bid)
    return Eligible(address_text(node), score, fit, match, period.ordinal, period.dp_request_id, period.tip, band,
                    index)


def roll_slot(rnd, slot_seconds):
    """The slot of the roll a round ranks on: the one before its time's."""
    return rnd.time // slot_seconds - 1


def _scan(copy, request, rnd, slot_seconds, band, index, count, h, found, beyond, until, hashing):
    """The roll read of PoxMatchReaderBase._scan from the cursor (band,
    index), at most `count` entries: (eligible, h, found, band, index, done).
    A cursor walks past every exhausted band before it tests its count."""
    slot = roll_slot(rnd, slot_seconds)
    lengths = copy.lengths(slot)
    max_steps = request.params.maxSteps
    eligible = []
    read = 0
    done = False
    while True:
        length = lengths[band]
        if index >= length:
            if band == until or band == 0 or (until == LIST and not beyond and found >= max_steps):
                done = True
                break
            if found >= max_steps:
                hashing = False
            band -= 1
            index = 0
            continue
        if read == count:
            break
        k = min(length - index, count - read)
        for _ in range(k):
            at = index
            index += 1
            read += 1
            e = copy.entry(slot, band, at)
            ev = evaluate(copy, request, rnd, e.node, e.score, band, at)
            if ev is None:
                continue
            if hashing:
                h = list_link(h, ev.node, ev.fit, ev.match)
            if ev.match and found < max_steps:
                found += 1
            eligible.append(ev)
    return eligible, h, found, band, index, done


def candidates(copy, request, rnd, slot_seconds, from_band=TOP_BAND, from_index=0, count=None, h=ZERO_HASH,
               match_found=0, beyond=False):
    """The reader's candidates(doRequestId, round, fromBand, from, count, h,
    matchFound, beyond) on the copy (4.5, 4.6): the eligible entries of
    `count` roll entries from the cursor (all with None), the list hash and
    matching count carried, the next cursor and `done`. An unranked request
    reads nothing and is done."""
    if count is None:
        count = sum(copy.lengths(roll_slot(rnd, slot_seconds)))
    if not request.ranked:
        return ListRead([], bytes(h), match_found, from_band, from_index, True)
    hashing = not (beyond and match_found >= request.params.maxSteps)
    eligible, h, found, band, index, done = _scan(copy, request, rnd, slot_seconds, from_band, from_index, count,
                                                  bytes(h), match_found, beyond, LIST, hashing)
    return ListRead(eligible, h, found, band, index, done)


def mandated(request, eligible):
    """The mandated sequence of a complete list read (4.5;
    PoxMatchReaderBase._select): band by band from the highest, the band's
    entries that fit and match by key then address, then those that match
    and do not fit, up to maxSteps in all."""
    max_steps = request.params.maxSteps
    out = []
    i = 0
    while i < len(eligible) and len(out) < max_steps:
        j = i
        while j < len(eligible) and eligible[j].band == eligible[i].band:
            j += 1
        band = eligible[i:j]
        for want_fit in (True, False):
            chosen = [e for e in band if e.match and e.fit == want_fit]
            chosen.sort(key=lambda e: (key_of(request.seed, e.node), int(e.node, 16)))
            for e in chosen:
                if len(out) == max_steps:
                    break
                out.append(e)
        i = j
    return out


def ended_by(copy, node, ordinal, step, round_block, params):
    """Whether the period in force at `step`, followed from `ordinal` of
    `node` through its replacements in blocks of that step or lower, ended in
    the step or before it other than by replacement (4.8 `ended`;
    PoxMatchReaderBase._ended), with the block it ended in."""
    p = copy.period(node, ordinal)
    while (p is not None and p.to != 0 and p.end_reason == END_REPLACED
           and step_at(p.to_block, round_block, params) <= step):
        ordinal += 1
        p = copy.period(node, ordinal)
    if p is None:
        return False, 0
    ended = p.to != 0 and p.end_reason != END_REPLACED and step_at(p.to_block, round_block, params) <= step
    return ended, p.to_block


def turn_of(copy, request, rnd, slot_seconds, node, band, index, from_band=TOP_BAND, from_index=0, count=None,
            carry=0):
    """The reader's turnOf(doRequestId, round, node, band, index, fromBand,
    from, count, carry) on the copy (4.6): whether the node's entry at
    (band, index) is eligible, fits and matches; the counts matchAbove,
    fitBefore, restBefore, fitInBand and restInBand over the matching
    entries read (from the bands above the node's and its own band);
    `done`; the step the node is mandated for (0: none) and whether its
    period in force at that step ended in it or before it other than by
    replacement; the carry and the cursor."""
    params = request.params
    if count is None:
        count = sum(copy.lengths(roll_slot(rnd, slot_seconds)))
    c = [carry & UINT32_MAX, (carry >> 32) & UINT16_MAX, (carry >> 48) & UINT16_MAX, (carry >> 64) & UINT16_MAX,
         (carry >> 80) & UINT16_MAX]
    own = None
    if request.ranked:
        e = copy.entry(roll_slot(rnd, slot_seconds), band, index)
        if e is not None and e.node == address_text(node):
            own = evaluate(copy, request, rnd, e.node, e.score, band, index)
    eligible = own is not None
    next_band, next_from, done = from_band, from_index, False
    if eligible:
        found, _, _, next_band, next_from, done = _scan(copy, request, rnd, slot_seconds, from_band, from_index,
                                                        count, ZERO_HASH, 0, False, band, False)
        own_key = key_of(request.seed, node)
        for w in found:
            if not w.match:
                continue
            if w.band > band:
                if c[0] < UINT32_MAX:
                    c[0] += 1
                continue
            if w.band != band:
                continue
            f = 1 if w.fit else 2
            if c[f + 2] < UINT16_MAX:
                c[f + 2] += 1
            if w.node != address_text(node) and before(key_of(request.seed, w.node), w.node, own_key, node) \
                    and c[f] < UINT16_MAX:
                c[f] += 1
    fit = bool(own and own.fit)
    match = bool(own and own.match)
    step = 0
    excused = False
    if done and match and c[0] < params.maxSteps:
        step = c[0] + (c[1] if fit else c[3] + c[2]) + 1
        if step > params.maxSteps:
            step = 0
        else:
            excused, _ = ended_by(copy, node, own.listed_ordinal, step, rnd.block, params)
    carry_out = c[0] | (c[1] << 32) | (c[2] << 48) | (c[3] << 64) | (c[4] << 80)
    return Turn(eligible, fit, match, c[0], c[1], c[2], c[3], c[4], (not eligible) or done or c[0] >= params.maxSteps,
                step, excused, carry_out, next_band, next_from)


def allowed_from(request, eligible, sequence, node):
    """The first step from which `node` is allowed (4.5): for a node of the
    list the smallest j whose mandated node stands at or after it in the
    list's order, M + 1 when there is none; M + 1 for a node not on the
    list."""
    order = list_order(request, eligible)
    position = {e.node: i for i, e in enumerate(order)}
    node = address_text(node)
    if node not in position:
        return len(sequence) + 1
    for j, m in enumerate(sequence, start=1):
        if position[m.node] >= position[node]:
            return j
    return len(sequence) + 1


def list_order(request, eligible):
    """The list's order (4.4): band descending, then key ascending, then
    address ascending."""
    return sorted(eligible, key=lambda e: (-e.band, key_of(request.seed, e.node), int(e.node, 16)))


def full_list(copy, request, rnd, slot_seconds):
    """Every eligible entry of the round's roll in roll order (a read from
    the top and on with `beyond`), with the list hash and the matching count
    of the list read without `beyond`."""
    first = candidates(copy, request, rnd, slot_seconds)
    eligible = list(first.eligible)
    if request.ranked:
        rest = candidates(copy, request, rnd, slot_seconds, first.next_band, first.next_from, None, first.list_hash,
                          first.match_found, True)
        eligible.extend(rest.eligible)
    return eligible, first.list_hash, first.match_found
