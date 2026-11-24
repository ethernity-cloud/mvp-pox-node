"""The node agent's side of PoX 2.0 matching on a network whose PoX is 2.0
and whose pricing names a matching contract (MATCHING-DESIGN.md section 7;
D183): the copy of the roll rebuilt at start and fed from the logs, the
heartbeat once per slot at the node's offset, the announce and the pause,
the claim with the round's stamp and the stated steps, the corrections of
full blocks, the local list of images with its skip, the proof of three
nodes and the rewards wallet. ChainAdapter binds PoxMatching, its reader and
the PoX through web3; MatchingNode decides with matching.py and
matching_rules.py and sends through the agent's transaction callback."""
import time
from collections import namedtuple
from types import SimpleNamespace

import matching
import matching_rules as rules

ZERO_IMAGE = b'\x00' * 31
# The logs the copy of the roll follows, and those of the node's own
# records it reads for its corrections and its ban (7.18, 7.12).
COPY_EVENTS = ('Beat', 'Announced', 'Left', 'Skipped')
OWN_EVENTS = ('Charged', 'OrderFailed', 'Corrected', 'Banned')
JUDGED_EVENTS = ('ListJudged',)
# The pricing's matching pointer (IPoxPricing.matching), which the agent's
# pricing ABI does not carry, and the price stamp of a request as a PoX whose
# VERSION() is "1.2" answers it: two values. The PoX whose VERSION() is "2.0"
# answers a third, the request's minimum EPC, under the union ABI's doStamp
# (pox20.EPC_VERSION); a decode of that answer under two outputs drops the
# third value without an error, so the decode follows the version.
POINTER_ABI = [{"type": "function", "name": "matching", "stateMutability": "view", "inputs": [],
                "outputs": [{"name": "", "type": "address"}]}]
STAMP_ABI_TWO_VALUES = [{"type": "function", "name": "doStamp", "stateMutability": "view",
                         "inputs": [{"name": "id", "type": "uint256"}],
                         "outputs": [{"name": "basePriceWei", "type": "uint128"},
                                     {"name": "escrowGflops", "type": "uint16"}]}]
# The claim word a reader answers (PoxMatchingState.Claim): its offer, block,
# time, round, state and stated steps, and the tip it was admitted at.
Claim = namedtuple('Claim', 'dp_request_id block time round state steps tip')
# The node's offer as the agent's tick passes it: its DP request id, whether
# it is AVAILABLE and whether it has expired, its tip in the PoX, and
# whether an order on it is placed and not started.
Offer = namedtuple('Offer', 'dp_request_id available expired tip placed_unstarted')
# The matching settings of one network (config.NetworkConfig); `lag_blocks`
# is how far below the head the copy reads the logs and the requests.
Settings = namedtuple('Settings', 'claim_ahead_blocks claim_spread_nodes claim_stand_limit claim_hold_back '
                                  'claim_marked_images tip_keep_percent minimum_score_hold beat_gas_allowance '
                                  'page_entries lag_blocks')


def decode_claim(word):
    word = int(word)
    packed = (word >> 128) & matching.UINT32_MAX
    return Claim(word & (2**48 - 1), (word >> 48) & (2**48 - 1), (word >> 96) & matching.UINT32_MAX, packed & 15,
                 (packed >> 8) & 15, packed >> 12, word >> 160)


def decode_fill(word):
    """The fill record (PoxMatchingState.Fill): (node, claim block, round,
    step, score); node None for an unfilled request."""
    word = int(word)
    node = word & (2**160 - 1)
    return (('0x' + matching.uint(node, 20).hex()) if node else None, (word >> 160) & (2**48 - 1),
            (word >> 208) & 255, (word >> 216) & 255, (word >> 224) & 0xFFFF)


def decode_charge(word):
    """A charge record (PoxMatchingState.Charge): (kind, step, passed, units,
    at, undone)."""
    word = int(word)
    return (word & 255, (word >> 8) & 255, (word >> 16) & 255, (word >> 24) & 0xFFFF,
            (word >> 40) & matching.UINT32_MAX, (word >> 72) & 255)


def page_ranges(length, page):
    return [(start, min(page, length - start)) for start in range(0, length, page)]


def mined(receipt):
    return receipt is not None and int(receipt.get('status', 0)) == 1


class ChainAdapter:
    """PoxMatching (`matching_contract`), PoxMatchReader (`reader`) and the
    PoX (`pox`, under the union ABI) as the driver reads them, a call
    answered at `block` when one is named; `epc_forms` whether the PoX's
    VERSION() names the EPC forms (pox20.takes_epc_forms). The node's
    transactions go through `transact(fn, label, gas, full_streak)`, the
    agent's sender, which answers the receipt."""

    def __init__(self, w3, matching_contract, reader, pox, address, transact, logs_chunk_blocks, epc_forms):
        self.w3 = w3
        self.matching = matching_contract
        self.reader = reader
        self.pox = pox
        self.epc_forms = bool(epc_forms)
        self.stamps = pox if self.epc_forms else w3.eth.contract(address=pox.address, abi=STAMP_ABI_TWO_VALUES)
        self.address = address
        self.transact = transact
        self.logs_chunk_blocks = int(logs_chunk_blocks)
        self.headers = {}

    @staticmethod
    def _at(block):
        return {} if block is None else {'block_identifier': int(block)}

    def head_number(self):
        return int(self.w3.eth.block_number)

    def block(self, number):
        number = int(number)
        if number not in self.headers:
            b = self.w3.eth.get_block(number)
            self.headers[number] = {'number': int(b['number']), 'timestamp': int(b['timestamp']),
                                    'gasUsed': int(b['gasUsed']), 'gasLimit': int(b['gasLimit'])}
            if len(self.headers) > 4096:
                self.headers.pop(next(iter(self.headers)))
        return self.headers[number]

    def blocks_in_time(self, t0, t1, head):
        """The headers of the blocks with t0 <= timestamp < t1 up to `head`:
        a binary search for the first block at or after t0, then each block
        until t1."""
        lo, hi = 0, int(head)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.block(mid)['timestamp'] < t0:
                lo = mid + 1
            else:
                hi = mid
        out = []
        for n in range(lo, int(head) + 1):
            h = self.block(n)
            if h['timestamp'] >= t1:
                break
            if h['timestamp'] >= t0:
                out.append(h)
        return out

    def slot_seconds(self):
        return int(self.matching.functions.slotSeconds().call())

    def parameter_set_count(self):
        return int(self.matching.functions.parameterSetCount().call())

    def parameter_sets(self):
        return [matching.parameters_of(self.matching.functions.parameterSet(i).call())
                for i in range(self.parameter_set_count())]

    def template(self, key):
        """(bit, registeredAt) of template `key`, None for one not registered."""
        word, _ = self.matching.functions.templateOf(bytes(key)).call()
        word = int(word)
        if (word >> 48) & 255 == 0:
            return None
        return word & 255, (word >> 8) & matching.UINT32_MAX

    def roll_lengths(self, slot, block):
        return [int(n) for n in self.matching.functions.rollLengths(int(slot)).call(**self._at(block))]

    def roll_periods(self, slot, band, start, count, image, block):
        return [int(w) for w in self.matching.functions.rollPeriodsOf(int(slot), int(band), int(start), int(count),
                                                                      bytes(image)).call(**self._at(block))]

    def period_at(self, node, ordinal, block):
        first, second = self.matching.functions.periodAt(self.w3.to_checksum_address(node), int(ordinal)).call(
            **self._at(block))
        return int(first), int(second)

    def current_period(self, node, block=None):
        words = self.matching.functions.periodsOf([self.w3.to_checksum_address(node)]).call(**self._at(block))
        return int(words[0]), int(words[1])

    def skip_of(self, node, image, block):
        return int(self.matching.functions.skipOf(self.w3.to_checksum_address(node), bytes(image)).call(
            **self._at(block)))

    def standing(self):
        score, age, until = self.matching.functions.nodeStanding(self.address).call()
        return int(score), int(age), int(until)

    def beat_counts(self):
        return bool(self.matching.functions.beatCounts(self.address).call())

    def record(self):
        """recordOf's (beats, claims, lates, misses, failures, pending,
        lastFailureSlot)."""
        return tuple(int(v) for v in self.matching.functions.recordOf(self.address).call())

    def minimum_on(self):
        return bool(self.matching.functions.minimumOn().call())

    def request_info(self, request_id, block):
        names = ('claims', 'ranked', 'imageMarked', 'settled', 'isFinal', 'round', 'roundStamp', 'seed',
                 'roundBlock', 'roundTime', 'createdAt', 'rollSlot', 'bidWei', 'baseWei', 'stepNow', 'closedStep',
                 'lastOpenStep', 'filledStep', 'claimCount', 'claimedInRound', 'parameterSet')
        return dict(zip(names, self.reader.functions.requestInfo(int(request_id)).call(**self._at(block))))

    def round_at(self, request_id, number, block):
        names = ('roundStamp', 'roundBlock', 'roundTime', 'bidWei', 'baseWei', 'lastOpenStep', 'closed', 'judged')
        return dict(zip(names, self.reader.functions.roundAt(int(request_id), int(number)).call(**self._at(block))))

    def request_image(self, request_id):
        """(image key, flags) PoxMatching recorded at the request's add."""
        word = int(self.matching.functions.requestImageOf(int(request_id)).call())
        return matching.uint(word & (2**248 - 1), 31), word >> 248

    def stamp(self, request_id):
        """(basePriceWei, escrowGflops, epcUnits) of the PoX's doStamp:
        three values where the PoX takes the EPC forms, two elsewhere, whose
        requests state no minimum EPC (0)."""
        answer = self.stamps.functions.doStamp(int(request_id)).call()
        if self.epc_forms:
            base, g, epc = answer
            return int(base), int(g), int(epc)
        base, g = answer
        return int(base), int(g), 0

    def claimers(self, request_id):
        nodes, claims, approvable = self.reader.functions.claimersOf(int(request_id), 0, 2**16).call()
        return [(matching.address_text(n), int(c), bool(a)) for n, c, a in zip(nodes, claims, approvable)]

    def claims_of(self, request_id, nodes):
        claims, approvable = self.reader.functions.claimsOf(
            int(request_id), [self.w3.to_checksum_address(n) for n in nodes]).call()
        return [(int(c), bool(a)) for c, a in zip(claims, approvable)]

    def fill(self, request_id):
        return int(self.matching.functions.fillOf(int(request_id)).call())

    def charge(self, request_id, number):
        return int(self.matching.functions.chargeOf(int(request_id), int(number), self.address).call())

    def ambiguous(self, order_id):
        return int(self.matching.functions.ambiguousOf(int(order_id)).call())

    def early33(self, order_id, block):
        """PoxMatching's early33: whether a code 33 in the order's stored
        result is a timeout before its time on its request's marked
        template."""
        return bool(self.matching.functions.early33(int(order_id)).call(**self._at(block)))

    def result_at(self, order_id, block):
        """The time of the order's result in the PoX, 0 while it has none."""
        at, _ = self.pox.functions.orderResultAt(int(order_id)).call(**self._at(block))
        return int(at)

    def payout_of(self):
        return self.pox.functions.payoutAddressOf(self.address).call()

    def order_times(self, order_id):
        """(approvedAt, placedAt, terminatedAt) of an order in the PoX."""
        return (int(self.pox.functions.orderApprovedAt(int(order_id)).call()),
                int(self.pox.functions.orderPlacedAt(int(order_id)).call()),
                int(self.pox.functions.orderTerminatedAt(int(order_id)).call()))

    def events(self, names, start, stop, own=False):
        """The logs of PoxMatching's events `names` over [start, stop], read
        in ranges of logs_chunk_blocks, in block and log order; with `own`
        those whose indexed node is this node."""
        out = []
        for name in names:
            event = getattr(self.matching.events, name)()
            for a in range(int(start), int(stop) + 1, self.logs_chunk_blocks):
                kwargs = {'from_block': a, 'to_block': min(int(stop), a + self.logs_chunk_blocks - 1)}
                if own:
                    kwargs['argument_filters'] = {'node': self.address}
                for log in event.get_logs(**kwargs):
                    out.append(SimpleNamespace(event=name, args=dict(log['args']), blockNumber=int(log['blockNumber']),
                                               logIndex=int(log['logIndex'])))
        out.sort(key=lambda log: (log.blockNumber, log.logIndex))
        return out

    def receipt_events(self, receipt, names):
        out = []
        for name in names:
            for log in getattr(self.matching.events, name)().process_receipt(receipt):
                out.append(SimpleNamespace(event=name, args=dict(log['args']), blockNumber=int(log['blockNumber']),
                                           logIndex=int(log['logIndex'])))
        out.sort(key=lambda log: log.logIndex)
        return out

    def function(self, name, *args):
        return getattr(self.matching.functions, name)(*args)

    def pox_function(self, name, *args):
        return getattr(self.pox.functions, name)(*args)

    def estimate(self, fn):
        return int(fn.estimate_gas({'from': self.address}))

    def simulate(self, fn):
        fn.call({'from': self.address})


class MatchingNode:
    """The node's matching state on one network, in memory only but for the
    local list of images (rules.ImageFailureList): the copy of the roll, the
    slot of the last counted heartbeat and its attempts, the node's roll
    entries (from its Beat logs), the steps it stated per request and
    round, its claims and unanswered mandates, the corrections it asks for
    and the orders it proves wide. `terms_of(request_id)` and
    `order_end_of(order_id, placed_at, terminated_at)` are the agent's: a
    request's terms (request_of) and the time from which expireOrder was
    admitted for an order."""

    def __init__(self, chain, address, settings, logger, failure_list_path, terms_of=None, order_end_of=None):
        self.chain = chain
        self.address = matching.address_text(address)
        self.settings = settings
        self.logger = logger
        self.terms_of = terms_of
        self.order_end_of = order_end_of
        self.copy = matching.RollCopy()
        self.slot_seconds = None
        self.offset = None
        self.synced = False
        self.oldest_time = 0
        self.tz_mask = 0
        self.images = set()
        self.terms = {}
        self.stated = {}
        self.claimed = set()
        self.open_claims = {}
        self.mandates = {}
        self.beat_slot = None
        self.attempts = {}
        self.unbeaten = set()
        self.own_entries = {}
        self.corrections = rules.CorrectionBook()
        self.late_records = set()
        self.wide_pending = {}
        # The node's ends in code 33 on a paid class, {order id: (image key
        # hex, time noted)}, until PoxMatching's early33 judges them.
        self.early_pending = {}
        self.failures = rules.ImageFailureList(failure_list_path)
        self.ban_logged = None
        self.hold_logged = None
        self.payout_logged = None
        self.score = None

    def params(self):
        """The parameter set in force for a request added now: the last one."""
        return self.copy.params[-1] if self.copy.params else None

    def now(self):
        return self.chain.block(self.chain.head_number())['timestamp']

    # ------------------------------------------------------------ the copy

    def start(self, trustedzones=()):
        """Reads the deployment's slot length and parameter sets, the template
        bits of the node's trustedzones (its announce's tzMask) and rebuilds
        the copy by one paged scan (spec/matching.md section 18)."""
        self.slot_seconds = self.chain.slot_seconds()
        self.offset = rules.beat_offset(self.address, self.slot_seconds)
        self.set_trustedzones(trustedzones)
        self.rebuild()

    def set_trustedzones(self, names):
        """tzMask (7.3): the bits of the registered templates among the
        trustedzones the node runs."""
        mask = 0
        for name in names:
            found = self.chain.template(matching.keccak(str(name).encode()))
            if found is not None:
                mask |= 1 << found[0]
        self.tz_mask = mask

    def rebuild(self):
        """The copy at block head - lag_blocks: the rolls of the slots from
        the one before the oldest open request's to the current one, each
        entry's node with its periods back to the newest one an open round
        can find, and the skips of the images seen requested."""
        head = self.chain.head_number() - int(self.settings.lag_blocks)
        t = self.chain.block(head)['timestamp']
        self.copy = matching.RollCopy()
        self.copy.params = self.chain.parameter_sets()
        max_age = self.params().maxRequestAgeSeconds if self.params() else 86400
        first = (t - max_age) // self.slot_seconds - 1
        self.oldest_time = t - max_age
        for slot in range(first, t // self.slot_seconds + 1):
            self._scan_slot(slot, head)
        self.copy.block = head
        self.synced = True
        self.logger.info(f"Matching: the copy of the roll is rebuilt at block {head} (slots {first} to "
                         f"{t // self.slot_seconds}, {len(self.copy.periods)} nodes)")

    def _scan_slot(self, slot, block):
        lengths = self.chain.roll_lengths(slot, block)
        bands = {}
        page = max(1, int(self.settings.page_entries))
        for band in range(matching.BANDS):
            if not lengths[band]:
                continue
            entries = []
            for start, count in page_ranges(lengths[band], page):
                words = self.chain.roll_periods(slot, band, start, count, ZERO_IMAGE, block)
                for i in range(0, len(words), 4):
                    e = matching.decode_roll_entry(words[i])
                    entries.append(e)
                    if not self.copy.knows(e.node):
                        self._load_periods(e.node, words[i + 1], words[i + 2], block)
            bands[band] = entries
        self.copy.set_roll(slot, bands)

    def _load_periods(self, node, first, second, block):
        """The periods of `node` from its current one (words `first`,
        `second`) back to the newest one whose start plus minListedSeconds is
        at or before the oldest open request's time, and its skips of the
        images seen requested, read at `block`."""
        min_listed = self.params().minListedSeconds if self.params() else 60
        if int(first) & matching.UINT32_MAX == 0:
            self.copy.set_periods(node, [], block)
        else:
            periods = [matching.decode_period(matching.period_ordinal(first), first, second)]
            while periods[0].ordinal > 0 and periods[0].start + min_listed > self.oldest_time:
                ordinal = periods[0].ordinal - 1
                w1, w2 = self.chain.period_at(node, ordinal, block)
                periods.insert(0, matching.decode_period(ordinal, w1, w2))
            self.copy.set_periods(node, periods, block)
        for image in self.images:
            self.copy.set_skip(node, image, matching.decode_skip(self.chain.skip_of(node, image, block)))

    def _know_image(self, image):
        if image in self.images:
            return
        for node in list(self.copy.periods):
            self.copy.set_skip(node, image, matching.decode_skip(self.chain.skip_of(node, image, self.copy.block)))
        self.images.add(image)

    def follow(self):
        """Applies the Beat, Announced, Left and Skipped logs from the
        copy's block to head - lag_blocks, then the node's own
        Charged, OrderFailed, Corrected and Banned logs and the ListJudged
        logs of the requests it holds a late charge on; a node a Beat names
        that the copy does not hold is read at the last block applied. The
        rolls no open round ranks on are dropped, and the parameter sets
        read again when one was added."""
        if not self.synced:
            return
        head = self.chain.head_number() - int(self.settings.lag_blocks)
        if head <= self.copy.block:
            return
        start = self.copy.block + 1
        unknown = []
        for log in self.chain.events(COPY_EVENTS, start, head):
            a = log.args
            if log.event == 'Beat':
                slot = int(a['slot'])
                if slot not in self.copy.rolls and slot > max(self.copy.rolls, default=slot - 1):
                    self.copy.set_roll(slot, {})
                node = matching.address_text(a['node'])
                self.copy.on_beat(node, slot, a['score'], a['band'], a['index'],
                                  self.chain.block(log.blockNumber)['timestamp'])
                if node == self.address:
                    self.own_entries[slot] = (int(a['band']), int(a['index']))
                if not self.copy.knows(node):
                    unknown.append(node)
            elif log.event == 'Announced':
                self.copy.on_announced(a['node'], a['dpRequestId'], a['from'], a['cpu'], a['memGB'], a['storageGB'],
                                       a['bandwidth'], a['gflops'], a['tipWei'], a['tzMask'], a['enclaveMemoryGB'],
                                       a['epcUnits'], log.blockNumber)
            elif log.event == 'Left':
                self.copy.on_left(a['node'], a['reason'], a['at'], log.blockNumber)
            elif log.event == 'Skipped':
                self.copy.on_skipped(a['node'], bytes(a['imageKey']), a['at'], a['until'], a['prevAt'],
                                     a['prevUntil'], log.blockNumber)
        for node in dict.fromkeys(unknown):
            first, second = self.chain.current_period(node, head)
            self._load_periods(node, first, second, head)
        for log in self.chain.events(OWN_EVENTS, start, head, own=True):
            self._own_log(log)
        if self.late_records:
            for log in self.chain.events(JUDGED_EVENTS, start, head):
                self._judged(log)
        self.copy.block = head
        if self.chain.parameter_set_count() != len(self.copy.params):
            self.copy.params = self.chain.parameter_sets()
        max_age = self.params().maxRequestAgeSeconds if self.params() else 86400
        self.oldest_time = self.chain.block(head)['timestamp'] - max_age
        self.copy.forget_slots_before(self.oldest_time // self.slot_seconds - 1)

    # ------------------------------------------------------- the heartbeat

    def heartbeat_tick(self, offer, holding, refresh):
        """The slot's heartbeat (7.2): once the latest block's time has
        passed the node's offset, unless nodeStanding shows it counted or the
        node is banned; the leg beatCounts shows; the refresh first when the
        offer has expired (`refresh()`); the announce as the heartbeat when
        the node has no open period and does not hold; nothing between a
        placement and its start row. A send that fails is tried at the next
        tick, at most BEAT_ATTEMPTS_PER_SLOT times in the slot. Returns what
        it did."""
        t = self.now()
        slot = rules.slot_of(t, self.slot_seconds)
        self._note_unbeaten(slot)
        if self.beat_slot == slot or not rules.beat_due(t, self.slot_seconds, self.offset):
            return 'idle'
        score, age, banned_until = self.chain.standing()
        self.score = score
        if rules.banned(t, banned_until):
            self._log_ban(banned_until)
            return 'banned'
        if self.attempts.get(slot, 0) >= rules.BEAT_ATTEMPTS_PER_SLOT:
            return 'idle'
        period = self.own_period()
        action = rules.beat_action(self.chain.beat_counts(), age, False, offer is not None and offer.expired,
                                   period is not None and period.to == 0, holding,
                                   offer is not None and offer.placed_unstarted)
        if action == 'done':
            self.beat_slot = slot
            return action
        if action == 'refresh':
            refresh()
            return action
        if action == 'wait' or (action == 'announce' and (offer is None or not offer.available)):
            return 'wait'
        self.attempts[slot] = self.attempts.get(slot, 0) + 1
        try:
            if action == 'announce':
                receipt = self.announce(offer, "the slot's heartbeat")
            else:
                fn = self.chain.function('beat')
                gas = rules.beat_gas_limit(self.chain.estimate(fn), self.chain.record()[5],
                                           self.settings.beat_gas_allowance)
                streak = self.full_streak() if rules.in_last_quarter(t, self.slot_seconds) else 0
                receipt = self.chain.transact(fn, 'beat', gas, streak)
                if mined(receipt):
                    self._receipt(receipt)
        except Exception as e:
            self.logger.warning(f"Matching: the heartbeat of slot {slot} was not sent ({e}); attempt "
                                f"{self.attempts[slot]}/{rules.BEAT_ATTEMPTS_PER_SLOT}")
            return 'failed'
        if not mined(receipt):
            self.logger.warning(f"Matching: the heartbeat of slot {slot} reverted; tried again next tick")
            return 'failed'
        self.beat_slot = slot
        self.logger.info(f"Matching: heartbeat of slot {slot} ({action}), score {score}")
        return action

    def _note_unbeaten(self, slot):
        """A slot before `slot` whose heartbeat the node tried and never had
        mined is a correction of kind 5 once its window shows full (7.18)."""
        for s in [s for s in self.attempts if s < slot]:
            if self.beat_slot != s:
                self.unbeaten.add(s)
            del self.attempts[s]

    def _log_ban(self, banned_until):
        if self.ban_logged != banned_until:
            self.ban_logged = banned_until
            self.logger.warning(f"Matching: this node is banned until {banned_until}; it sends no heartbeat, "
                                f"announce or claim until then and places no order on a request pinned to it")

    def is_banned(self):
        """Whether the node is banned (7.12); the ban's end is logged once."""
        _, _, until = self.chain.standing()
        if rules.banned(self.now(), until):
            self._log_ban(until)
            return True
        return False

    def own_period(self):
        """The node's current period as PoxMatching holds it, None for none."""
        first, second = self.chain.current_period(self.address)
        if int(first) & matching.UINT32_MAX == 0:
            return None
        return matching.decode_period(matching.period_ordinal(first), first, second)

    # ------------------------------------------------- announce and pause

    def announce(self, offer, why):
        receipt = self.chain.transact(self.chain.function('announce', int(offer.dp_request_id), int(self.tz_mask)),
                                      'announce', None, 0)
        if mined(receipt):
            self._receipt(receipt)
            self.logger.info(f"Matching: announced DP request {offer.dp_request_id} ({why})")
        return receipt

    def hold_tick(self, offer, reasons):
        """Pauses while a hold reason stands (7.8, 7.19) and announces when
        none does, the offer is AVAILABLE and unexpired and the node has no
        open period, or one at another tip than the offer's (7.3); after a
        pause the announce waits minPauseSeconds. Nothing while banned.
        Returns 'paused', 'announced' or 'idle'."""
        if self.is_banned():
            return 'idle'
        period = self.own_period()
        open_period = period is not None and period.to == 0
        if reasons:
            if self.hold_logged != tuple(reasons):
                self.hold_logged = tuple(reasons)
                self.logger.info(f"Matching: the offer is held: {'; '.join(reasons)}")
            if open_period and mined(self.chain.transact(self.chain.function('pause'), 'pause', None, 0)):
                return 'paused'
            return 'idle'
        self.hold_logged = None
        if offer is None:
            return 'idle'
        paused_at = period.to if period is not None and period.end_reason == matching.END_PAUSED else None
        tip_changed = open_period and int(period.tip) != int(offer.tip)
        min_pause = self.params().minPauseSeconds if self.params() else 3600
        if rules.announce_due(offer.available, offer.expired, open_period, False, paused_at, min_pause, self.now(),
                              tip_changed):
            if mined(self.announce(offer, 'its tip changed' if tip_changed else 'no open period')):
                return 'announced'
        return 'idle'

    def hold_reasons(self, slot_elsewhere, handover_accepted, fee, ceiling, fee_percent):
        """The hold reasons of 7.8 and 7.19 as the node's state shows them."""
        below = False
        if self.settings.minimum_score_hold:
            score, _, _ = self.chain.standing()
            below = rules.below_default_minimum(self.chain.minimum_on(), score, True)
        return rules.hold_reasons(slot_elsewhere, handover_accepted, rules.fee_near_ceiling(fee, ceiling, fee_percent),
                                  below)

    def standing_claim_or_mandate(self):
        """Whether a claim of the node can still be approved or it holds a
        mandate it has not answered (7.3, 7.7): the refresh keeps the
        period's tip and no integration test starts."""
        now = self.now()
        for request_id in [r for r, until in self.mandates.items() if until < now]:
            del self.mandates[request_id]
        return bool(self.mandates) or self.claim_approvable()

    def claim_approvable(self):
        """Whether a claim the node sent can still be approved (the reader's
        claimsOf)."""
        now = self.now()
        for request_id, until in list(self.open_claims.items()):
            if until >= now:
                ((_, approvable),) = self.chain.claims_of(request_id, [self.address])
                if approvable:
                    return True
            del self.open_claims[request_id]
        return False

    # ------------------------------------------------------------ requests

    def request_of(self, request_id, info, terms):
        """matching.Request of DO request `request_id` from its requestInfo
        `info` and its `terms` (cpu, memory, storage, bandwidth, duration,
        max_gflops, metadata1), read once: the need from maxGflops, G and the
        minimum EPC from the PoX's stamp, the template bit, the image key
        PoxMatching recorded; its parameter set the one requestInfo's
        parameterSet names. None when no set is in force for it
        (parameterSet 256) or the copy does not hold the set named."""
        cached = self.terms.get(request_id)
        if cached is None:
            need, ceil = matching.need_gflops(terms['max_gflops'])
            _, g, epc = self.chain.stamp(request_id)
            image, _ = self.chain.request_image(request_id)
            key = matching.template_key(terms['metadata1'])
            if key not in self.copy.templates:
                found = self.chain.template(key)
                if found is not None:
                    self.copy.templates[key] = found
            cached = (need, ceil, g, epc, image, key)
            self.terms[request_id] = cached
        need, ceil, g, epc, image, key = cached
        created_at = int(info['createdAt'])
        index = int(info['parameterSet'])
        if index >= len(self.copy.params):
            return None
        params = self.copy.params[index]
        return matching.Request(int(request_id), bytes(info['seed']), int(terms['cpu']), int(terms['memory']),
                                int(terms['storage']), int(terms['bandwidth']), need, ceil,
                                int(terms['duration']) // 3600, g, self.copy.template_bit(key, created_at), image,
                                bool(info['ranked']), params, epc)

    def rounds_of(self, request_id, info):
        rounds = []
        for r in range(int(info['round'])):
            v = self.chain.round_at(request_id, r, self.copy.block)
            rounds.append(matching.Round(r, int(v['roundBlock']), int(v['roundTime']),
                                         min(int(v['bidWei']), matching.UINT128_MAX), int(v['baseWei'])))
        rounds.append(matching.Round(int(info['round']), int(info['roundBlock']), int(info['roundTime']),
                                     min(int(info['bidWei']), matching.UINT128_MAX), int(info['baseWei'])))
        return rounds

    def turn_in(self, request, rnd):
        """The node's turn in round `rnd` on the copy, None when the round's
        roll does not hold it."""
        place = self.copy.place_of(matching.roll_slot(rnd, self.slot_seconds), self.address)
        if place is None:
            return None
        return matching.turn_of(self.copy, request, rnd, self.slot_seconds, self.address, place[0], place[1])

    def steps_of(self, request, rounds):
        """The step the node states for every round of the request (3.9,
        D77), each computed once on the copy and kept."""
        out = []
        for rnd in rounds:
            key = (request.id, rnd.number)
            if key not in self.stated:
                self.stated[key] = rules.stated_step(self.turn_in(request, rnd))
            out.append(self.stated[key])
        return out

    def serve(self, request_id, terms, offer):
        """One tick of DO request `request_id` (7.6, 7.15): 'placement' when
        its parameter set does not serve it by claims (the agent places as
        today), 'done' when the node has nothing more to do on it, 'claimed'
        when its claim was mined, 'wait' otherwise. It acts only on a settled
        requestInfo read at the block the copy has read the logs to, with
        every round's roll in the copy."""
        if not self.synced:
            return 'wait'
        info = self.chain.request_info(request_id, self.copy.block)
        if not info['claims']:
            return 'placement'
        if info['isFinal'] or int(info['filledStep']) != 0:
            self._forget(request_id)
            return 'done'
        if request_id in self.claimed or (info['imageMarked'] and not self.settings.claim_marked_images):
            return 'done'
        if not info['settled'] or offer is None:
            return 'wait'
        ((word, _),) = self.chain.claims_of(request_id, [self.address])
        if decode_claim(word).block != 0:
            self.claimed.add(request_id)
            return 'done'
        if self.is_banned():
            return 'done'
        period = self.own_period()
        if (period is None or period.to != 0 or period.dp_request_id != int(offer.dp_request_id)
                or int(period.tip) != int(offer.tip)):
            return 'wait'
        request = self.request_of(request_id, info, terms)
        if request is None:
            return 'wait'
        rounds = self.rounds_of(request_id, info)
        if any(matching.roll_slot(r, self.slot_seconds) not in self.copy.rolls for r in rounds):
            return 'wait'
        current = rounds[-1]
        if not matching.covers(request, period, with_template=False):
            return 'wait'
        if terms.get('with_terms', True) and not matching.matches(request, current.base + period.tip, period.gflops,
                                                                  current.bid):
            return 'wait'
        self._know_image(request.image)
        steps = self.steps_of(request, rounds)
        params = request.params
        step_now = int(info['stepNow'])
        read = matching.candidates(self.copy, request, current, self.slot_seconds)
        sequence = matching.mandated(request, read.eligible) if request.ranked else []
        if steps[-1]:
            self.mandates[request_id] = int(info['createdAt']) + params.maxRequestAgeSeconds
            if self.settings.claim_hold_back and self._earlier_mandate_stands(request_id, sequence):
                return 'wait'
            if not rules.mandated_send_ready(self.copy.block, steps[-1], current.block, params,
                                             self.settings.claim_ahead_blocks):
                return 'wait'
        elif not self._unmandated_ready(request, current, sequence, step_now, request_id):
            return 'wait'
        if rules.adds_pending(steps, step_now + 1) and not self._pending_room(params):
            return 'wait'
        return self._claim(request_id, offer, info, steps, params)

    def _forget(self, request_id):
        self.mandates.pop(request_id, None)
        self.terms.pop(request_id, None)

    def _earlier_mandate_stands(self, request_id, sequence):
        """Whether an approvable claim of a node mandated before this one
        stands (7.6, the hold-back setting)."""
        earlier = []
        for m in sequence:
            if m.node == self.address:
                break
            earlier.append(m.node)
        return bool(earlier) and any(approvable for _, approvable in self.chain.claims_of(request_id, earlier))

    def _unmandated_ready(self, request, rnd, sequence, step_now, request_id):
        """The rule of 7.6 for a node that holds no mandate in the round: its
        `a` from its turn over its band, or the roll's matching entries less
        M outside the list; the approvable claims of the mandated nodes and
        of the nodes ahead of it in the order, or of every other claimer
        outside the list."""
        eligible, _, _ = matching.full_list(self.copy, request, rnd, self.slot_seconds)
        order = matching.list_order(request, eligible)
        mine = next((i for i, e in enumerate(order) if e.node == self.address), None)
        in_list = mine is not None and request.ranked
        turn = self.turn_in(request, rnd) if in_list else None
        mandates = len(sequence)
        a = rules.nodes_ahead(turn, request.params.maxSteps, mandates, in_list, sum(1 for e in eligible if e.match))
        ahead = {m.node for m in sequence} | ({e.node for e in order[:mine]} if in_list else set())
        standing = sum(1 for node, _, approvable in self.chain.claimers(request_id)
                       if approvable and node != self.address and (not in_list or node in ahead))
        return rules.unmandated_claim_ready(step_now, mandates, a, self.settings.claim_spread_nodes,
                                            self.settings.claim_stand_limit, standing)

    def _pending_room(self, params):
        """No claim that would add a pending entry goes into a full list
        without a heartbeat first (7.2): a heartbeat the contract admits
        settles some, after which the record is read again."""
        if self.chain.record()[5] < params.pendingMax:
            return True
        if self.chain.beat_counts():
            fn = self.chain.function('beat')
            receipt = self.chain.transact(
                fn, 'beat', rules.beat_gas_limit(self.chain.estimate(fn), 1, self.settings.beat_gas_allowance), 0)
            if mined(receipt):
                self._receipt(receipt)
        return self.chain.record()[5] < params.pendingMax

    def _claim(self, request_id, offer, info, steps, params):
        fn = self.chain.function('claim', int(request_id), int(offer.dp_request_id), bytes(info['roundStamp']),
                                 rules.encode_steps(steps))
        try:
            receipt = self.chain.transact(fn, 'claim', None, self.full_streak())
        except Exception as e:
            self.logger.warning(f"Matching: the claim on DO request {request_id} was not sent ({e}); next tick")
            return 'wait'
        if not mined(receipt):
            # A raise that opened a round, or a replaced block, makes the
            # statement and the stamp stale: both are read again.
            self.stated = {k: v for k, v in self.stated.items() if k[0] != request_id}
            self.logger.info(f"Matching: the claim on DO request {request_id} reverted; read again next tick")
            return 'wait'
        self.claimed.add(request_id)
        self.open_claims[request_id] = int(info['createdAt']) + params.maxRequestAgeSeconds
        self.mandates.pop(request_id, None)
        self.logger.info(f"Matching: claimed DO request {request_id} in round {info['round']}, stating {steps}")
        return 'claimed'

    def full_streak(self):
        """How many of the last 8 blocks, the newest last, used at least the
        parameter set's fullBlockPercent of their gas limit without a break
        (7.18): the sender doubles its price per block of it."""
        percent = self.params().fullBlockPercent if self.params() else 0
        if not percent:
            return 0
        head = self.chain.head_number()
        return rules.full_streak([self.chain.block(n) for n in range(max(0, head - 7), head + 1)], percent)

    # ---------------------------------------------------- receipts, logs

    def _receipt(self, receipt):
        """The node's Beat and Charged logs of its own heartbeat or announce:
        its roll entry, and the late claims the heartbeat settled."""
        for log in self.chain.receipt_events(receipt, ('Beat', 'Charged')):
            if log.event == 'Beat':
                self.own_entries[int(log.args['slot'])] = (int(log.args['band']), int(log.args['index']))
            else:
                self._own_log(log)

    def _own_log(self, log):
        a = log.args
        params = self.params()
        if params is None:
            return
        until = self.chain.block(log.blockNumber)['timestamp'] + params.correctionWindowSeconds
        if log.event == 'Charged' and int(a['kind']) == rules.CHARGE_LATE:
            self.late_records.add((int(a['doRequestId']), int(a['round'])))
            if int(a['taken']) > 0:
                self._late_corrections(int(a['doRequestId']), int(a['round']), int(a['mandateStep']),
                                       int(a['passed']), until, params)
        elif log.event == 'OrderFailed' and int(a['units']) > 0:
            kind = rules.CORRECTION_OF_FAILURE.get(int(a['kind']))
            if kind is not None:
                self._failure_correction(kind, int(a['orderId']), until, params)
        elif log.event == 'Corrected':
            self.corrections.corrected(int(a['entry']))
            self.logger.info(f"Matching: correction {int(a['entry']):#x} confirmed, {a['unitsBack']} units back")
        elif log.event == 'Banned':
            self.logger.warning(f"Matching: this node was banned for order {a['orderId']} until {a['bannedUntil']}")

    def _late_corrections(self, request_id, number, stated, passed, until, params):
        """An entry of kind 1 for each step a late charge counted, at most
        maxSteps and 7 above the stated one, whose blocks were all full
        (3.12)."""
        round_block = int(self.chain.round_at(request_id, number, None)['roundBlock'])
        head = self.chain.head_number()
        for q in range(stated, stated + passed):
            if q > params.maxSteps or q - stated > 7:
                break
            first = matching.first_block_of_step(q, round_block, params)
            blocks = [self.chain.block(n) for n in range(first, first + params.stepBlocks) if n <= head]
            if rules.window_full(blocks, params.fullBlockPercent):
                self.corrections.add(rules.correction_entry(rules.CORRECTION_CLAIM, request_id, number, q), until)

    def _failure_correction(self, kind, order_id, until, params):
        """An entry of kind 2 (the last fullWindowSeconds before the 15
        minutes) or 3 (before the time expireOrder was admitted from) for a
        failed order whose window was full. Kind 4 takes the blocks of the
        wait the trustedzone states in its result, which no result states
        yet."""
        approved_at, placed_at, terminated_at = self.chain.order_times(order_id)
        if kind == rules.CORRECTION_START:
            end = approved_at + params.startTimeoutSeconds
        elif kind == rules.CORRECTION_RESULT and self.order_end_of is not None:
            end = self.order_end_of(order_id, placed_at, terminated_at)
        else:
            return
        blocks = self.chain.blocks_in_time(end - params.fullWindowSeconds, end, self.chain.head_number())
        if rules.window_full(blocks, params.fullBlockPercent):
            self.corrections.add(rules.correction_entry(kind, order_id=order_id), until)

    def _judged(self, log):
        """A ListJudged log of a request on which the node holds a late
        charge: when the fill record's step now gives a lower count, an
        entry of kind 6 (7.18, 4.9)."""
        request_id, number = int(log.args['doRequestId']), int(log.args['round'])
        if (request_id, number) not in self.late_records:
            return
        kind, stated, _, units, at, _ = decode_charge(self.chain.charge(request_id, number))
        if kind != rules.CHARGE_LATE:
            return
        params = self.params()
        fill_node, fill_block, fill_round, fill_step, _ = decode_fill(self.chain.fill(request_id))
        round_block = int(self.chain.round_at(request_id, number, None)['roundBlock'])
        ((word, _),) = self.chain.claims_of(request_id, [self.address])
        claim = decode_claim(word)
        if claim.round == number:
            end = matching.step_at(claim.block, round_block, params)
        else:
            end = matching.step_at(int(self.chain.round_at(request_id, number + 1, None)['roundBlock']), round_block,
                                   params)
        cut = rules.fill_cut(fill_node, fill_step, fill_round, fill_block, self.address, stated, number, round_block,
                             params)
        if rules.recount_due(units, rules.late_units(rules.late_passed(stated, end, cut), params)):
            self.corrections.add(rules.correction_entry(rules.CORRECTION_RECOUNT, request_id, number, 0),
                                 at + params.correctionWindowSeconds)

    # ---------------------------------------------------------- corrections

    def correction_tick(self):
        """The node's correction list, at most once an hour (7.18): first the
        kind 5 entries of its unmined heartbeats whose last fullWindowSeconds
        were full, then every entry due, at most correctionMaxEntries."""
        params = self.params()
        if params is None:
            return 'idle'
        head = self.chain.head_number()
        for slot in sorted(self.unbeaten):
            end = (slot + 1) * self.slot_seconds
            if rules.window_full(self.chain.blocks_in_time(end - params.fullWindowSeconds, end, head),
                                 params.fullBlockPercent):
                self.corrections.add(rules.correction_entry(rules.CORRECTION_BEAT, slot=slot),
                                     end + params.correctionWindowSeconds)
        self.unbeaten.clear()
        now = self.chain.block(head)['timestamp']
        due = self.corrections.due(now, params.correctionAskSeconds, params.correctionMaxEntries)
        if not due:
            return 'idle'
        try:
            receipt = self.chain.transact(self.chain.function('askCorrection', due), 'askCorrection', None, 0)
        except Exception as e:
            self.logger.warning(f"Matching: the correction list was not sent ({e}); tried again in an hour")
            self.corrections.last_ask = now
            return 'failed'
        self.corrections.asked(now, due)
        self.logger.info(f"Matching: correction list of {len(due)} entries {'sent' if mined(receipt) else 'reverted'}")
        return 'sent' if mined(receipt) else 'failed'

    # ------------------------------------------------- the image list

    def note_order_end(self, order_id, request_id, image, code, paid_class):
        """An end of one of the node's orders (7.15, 7.21): counted in the
        local list when the image likely caused it, a 33 on a paid class once
        PoxMatching's early33 judges it a timeout before its time
        (_judge_early_ends); a 20, 33, 59, 62 or 63 is queued for its proof
        of three nodes."""
        now = self.now()
        image = bytes(image).hex()
        if int(code) == 33 and paid_class:
            self.early_pending[int(order_id)] = (image, now)
        else:
            self.failures.note(image, order_id, code, paid_class, False, now)
        if int(code) in (20, 33, 59, 62, 63):
            self.wide_pending[int(order_id)] = (int(request_id), now + rules.LOCAL_LIST_WINDOW_SECONDS)

    def _judge_early_ends(self, now):
        """Counts in the local list, at the time it was noted, each pending
        33 whose result PoxMatching's early33 judges a timeout before its
        time (7.15, D153), both reads answered at the block the copy has
        read the logs to. One whose result the PoX does not answer at that
        block is read again at the next tick; one noted 7 days or more
        before `now` is dropped."""
        block = self.copy.block
        for order_id, (image, at) in list(self.early_pending.items()):
            if at + rules.LOCAL_LIST_WINDOW_SECONDS <= now:
                del self.early_pending[order_id]
                continue
            if block is None or self.chain.result_at(order_id, block) == 0:
                continue
            if self.chain.early33(order_id, block):
                self.failures.note(image, order_id, 33, True, True, at)
            del self.early_pending[order_id]

    def skip_tick(self):
        """skipImage for each image of the local list with 3 counted ends in
        7 days (7.15, D161), simulated before it is sent, after the pending
        33s are judged."""
        now = self.now()
        self._judge_early_ends(now)
        sent = 0
        for image in list(self.failures.ends):
            due = self.failures.skip_due(image, now)
            if due is None:
                continue
            until, ids = due
            fn = self.chain.function('skipImage', bytes.fromhex(image), until, ids)
            try:
                self.chain.simulate(fn)
            except Exception as e:
                self.logger.warning(f"Matching: the skip of image {image} is refused ({e})")
                continue
            if mined(self.chain.transact(fn, 'skipImage', None, 0)):
                self.failures.skip_sent(image, ids)
                self.logger.info(f"Matching: image {image} skipped until {until} for orders {ids}")
                sent += 1
        return sent

    def wide_tick(self):
        """proveWide for a queued order with no proof yet (7.21): simulated
        first and sent when the simulation passes, once its end is recorded;
        an order is dropped after 7 days or once proven."""
        now = self.now()
        for order_id, (request_id, until) in list(self.wide_pending.items()):
            if now > until or (self.chain.ambiguous(order_id) >> 16) & 255:
                del self.wide_pending[order_id]
                continue
            hints = self._hints(request_id)
            if hints is None:
                continue
            fn = self.chain.function('proveWide', int(order_id), hints)
            try:
                self.chain.simulate(fn)
            except Exception:
                continue
            if mined(self.chain.transact(fn, 'proveWide', None, 0)):
                del self.wide_pending[order_id]
                self.logger.info(f"Matching: order {order_id} proven wide")

    def _hints(self, request_id):
        """Three matching nodes of the list of the round the request was
        approved in, on the copy."""
        _, _, fill_round, _, _ = decode_fill(self.chain.fill(request_id))
        terms = self.terms_of(request_id) if self.terms_of is not None else None
        if terms is None:
            return None
        info = self.chain.request_info(request_id, self.copy.block)
        request = self.request_of(request_id, info, terms)
        if request is None:
            return None
        rounds = self.rounds_of(request_id, info)
        if fill_round >= len(rounds) or matching.roll_slot(rounds[fill_round], self.slot_seconds) not in self.copy.rolls:
            return None
        eligible, _, _ = matching.full_list(self.copy, request, rounds[fill_round], self.slot_seconds)
        return rules.wide_hints(request, eligible)

    # --------------------------------------------------- the rewards wallet

    def payout_tick(self, setting):
        """Names the rewards wallet of the setting once (7.13, D34): sent when
        the PoX answers the node's own address for payoutAddressOf; logged
        once when another wallet is named."""
        if not setting:
            return 'none'
        action = rules.payout_action(setting, self.address, self.chain.payout_of())
        if action == 'set':
            fn = self.chain.pox_function('setPayoutAddress', setting)
            self.chain.simulate(fn)
            if mined(self.chain.transact(fn, 'setPayoutAddress', None, 0)):
                self.logger.info(f"Matching: the rewards wallet {setting} is named")
                return 'set'
            return 'failed'
        if action == 'differs' and self.payout_logged != setting:
            self.payout_logged = setting
            self.logger.warning(f"Matching: another rewards wallet than {setting} is named; a change is the named "
                                f"wallet's own transaction, changePayoutAddress")
        return action
