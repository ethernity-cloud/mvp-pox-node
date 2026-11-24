"""The node agent's decisions of PoX 2.0 matching (MATCHING-DESIGN.md
section 7; 3.2, 3.3, 3.9 to 3.12, 3.16, 3.17, 3.18, 4.8, 4.9). Every function
is pure over the values the agent reads from the chain and its settings;
matching_node.py reads, decides with them and sends."""
import json
import os
from collections import namedtuple

from eth_utils import keccak

import matching

# The retries of one slot's heartbeat: a send that fails or is not mined is
# repeated at each tick until the slot ends, at most this many times (7.2).
BEAT_ATTEMPTS_PER_SLOT = 20
# The runner's default minimum score, 0.50 (3.10 rule 5): a node below it is
# approved by no default runner once minimumOn() holds (7.19).
DEFAULT_MIN_SCORE = 5000
# The kinds of a correction entry (3.12; spec/matching.md section 16 item 27).
CORRECTION_CLAIM = 1
CORRECTION_START = 2
CORRECTION_RESULT = 3
CORRECTION_ROWS = 4
CORRECTION_BEAT = 5
CORRECTION_RECOUNT = 6
# The kinds of a failure record (PoxMatchingState.sol FAIL_*) and the
# correction kind each of an OrderFailed log asks for: never started 2,
# expired with no result 3, a node fault of code 37 or 39 4.
FAIL_LIVENESS = 1
FAIL_NEVER_STARTED = 2
FAIL_BANNED = 3
FAIL_EXPIRED = 4
FAIL_NODE_FAULT = 5
FAIL_NOT_RUN = 6
FAIL_STRIKE = 7
CORRECTION_OF_FAILURE = {FAIL_NEVER_STARTED: CORRECTION_START, FAIL_EXPIRED: CORRECTION_RESULT,
                         FAIL_NODE_FAULT: CORRECTION_ROWS}
# The kinds of a charge record (PoxMatchingState.sol CHARGE_*).
CHARGE_LATE = 1
CHARGE_MISS = 2
CHARGE_FALSE = 3
# The task codes a node's local list of images counts (7.15, D153): 59 and
# 63 in every class; 20, 62 and a 33 before its time on the paid classes
# (a custom securelock or native Python) only.
LOCAL_LIST_EVERY_CLASS = frozenset({59, 63})
LOCAL_LIST_PAID_CLASS = frozenset({20, 62})
LOCAL_LIST_COUNT = 3
LOCAL_LIST_WINDOW_SECONDS = 7 * 86400
SKIP_SECONDS = 7 * 86400

Hint = namedtuple('Hint', 'band roll_index listed_ordinal step_ordinal')


# ---------------------------------------------------------------- slots

def slot_of(t, slot_seconds):
    return int(t) // int(slot_seconds)


def beat_offset(address, slot_seconds):
    """The node's offset inside a slot (7.2): uint256(keccak256(its 20
    address bytes)) mod (slotSeconds / 2), spreading the nodes over the first
    half of the slot."""
    return int.from_bytes(keccak(matching.address_bytes(address)), 'big') % (int(slot_seconds) // 2)


def beat_due(t, slot_seconds, offset):
    """Whether the heartbeat of the slot of the latest block's time `t` is
    due: `t` at or above slot x slotSeconds + offset."""
    return int(t) >= slot_of(t, slot_seconds) * int(slot_seconds) + int(offset)


def in_last_quarter(t, slot_seconds):
    """Whether `t` lies in the last quarter of its slot (7.18)."""
    return int(t) - slot_of(t, slot_seconds) * int(slot_seconds) >= 3 * int(slot_seconds) // 4


def beat_action(counts, beat_age_slots, banned, offer_expired, period_open, holding, placed_unstarted):
    """What the heartbeat of a due slot does (7.2, 7.12, D15): 'banned' while
    the node is banned (nothing is sent); 'done' once the slot's heartbeat is
    counted (nodeStanding's age 0); 'beat' when beatCounts holds; 'refresh'
    when the offer has expired (the refresh comes first); 'announce' when
    the node has no open period and does not hold, the announce being the
    slot's heartbeat; 'wait' between an order's placement and its etny-sw
    row, when neither leg holds, and otherwise."""
    if banned:
        return 'banned'
    if int(beat_age_slots) == 0:
        return 'done'
    if counts:
        return 'beat'
    if offer_expired:
        return 'refresh'
    if placed_unstarted:
        return 'wait'
    if not period_open and not holding:
        return 'announce'
    return 'wait'


def beat_gas_limit(estimate, pending, allowance):
    """The gas limit of a heartbeat (7.2, D176): its estimate, plus the
    allowance for the worst-case settlement of pending late claims while
    the node has any (690,000 on bloxberg, 1,200,000 on Polygon)."""
    return int(estimate) + (int(allowance) if int(pending) > 0 else 0)


def banned(now, banned_until):
    return int(now) < int(banned_until)


# ------------------------------------------------------------ the offer

def offer_shape(setting, total, reserve):
    """One resource of the offer (7.4): the operator's setting when it is
    above 0, else the host's total less the reserve; at least 1 and at most
    255, the widths the PoX takes."""
    value = int(setting) if int(setting) > 0 else int(total) - int(reserve)
    return max(1, min(255, value))


def refresh_tip(computed, period_tip, standing, keep_percent):
    """The tip the hourly refresh sends (7.3): the open period's tip while a
    claim of the node can still be approved or it holds a mandate it has
    not answered (`standing`), and while the computed tip is within
    `keep_percent` of it; the computed tip otherwise. None for no open
    period: the computed tip."""
    if period_tip is None:
        return int(computed)
    if standing:
        return int(period_tip)
    if abs(int(computed) - int(period_tip)) * 100 <= int(keep_percent) * int(period_tip):
        return int(period_tip)
    return int(computed)


def gflops_argument(gflops, enclave_memory):
    """The V2 offer add's gflops argument (D167): GFLOPS in bits 0 to 15, the
    enclave memory in GiB in bits 16 to 23."""
    return (int(gflops) & matching.UINT16_MAX) | ((int(enclave_memory) & 255) << 16)


def announce_due(available, expired, period_open, holding, paused_at, min_pause, now, tip_changed):
    """Whether the node announces now (7.3, 7.10): its offer AVAILABLE and
    unexpired, it does not hold, and it has no open period (after an add, a
    period ended EXPIRED, PAUSED or MISS, a rejected reserved order) or its
    period's tip is not the offer's (a refresh changed it); a pause admits
    a new announce minPauseSeconds after it."""
    if not available or expired or holding:
        return False
    if paused_at is not None and int(now) < int(paused_at) + int(min_pause):
        return False
    return (not period_open) or bool(tip_changed)


# ------------------------------------------------------------- holding

def fee_near_ceiling(fee, ceiling, percent):
    """Whether the fee the node would offer has reached `percent` of its fee
    ceiling (7.8); never without a ceiling."""
    return int(ceiling) > 0 and int(fee) * 100 >= int(ceiling) * int(percent)


def below_default_minimum(minimum_on, score, hold_on):
    """Whether the node holds while default runners approve none of its
    claims (7.19): the default minimum on and its score below 5,000, unless
    the operator switched the hold off."""
    return bool(hold_on) and bool(minimum_on) and int(score) < DEFAULT_MIN_SCORE


def hold_reasons(slot_elsewhere, handover_accepted, near_ceiling, below_minimum):
    """The reasons the node holds its offer, paused (7.8, 7.19); empty when
    it announces."""
    reasons = []
    if slot_elsewhere:
        reasons.append('the task slot is taken on another network')
    if handover_accepted:
        reasons.append("its acceptance of a hand-over stands")
    if near_ceiling:
        reasons.append('the fee it would offer is near its fee ceiling')
    if below_minimum:
        reasons.append("its score is below the runner's default minimum")
    return reasons


# -------------------------------------------------------------- claims

def encode_steps(steps):
    """A claim's `steps` (3.9): the step stated for round r in bits 4r to
    4r + 3, `steps` the list of rounds 0 to the current one."""
    word = 0
    for r, s in enumerate(steps):
        if not 0 <= int(s) <= 15:
            raise ValueError(f"a stated step of round {r} is {s}")
        word |= int(s) << (4 * r)
    return word


def stated_step(turn):
    """The step a claim states for a round from the node's turn in it
    (3.9, D77): its mandate step, 0 when it holds none or the period in
    force at it ended in it or before it other than by replacement."""
    if turn is None or not turn.eligible or not turn.matches or turn.excused:
        return 0
    return turn.mandate_step


def mandated_send_ready(read_block, step, round_block, params, claim_ahead_blocks):
    """Whether a node mandated for `step` sends its claim at a read of block
    `read_block` (7.6): once it has read the block claimAheadBlocks + 1
    blocks before the first block of its step, or any later block."""
    first = matching.first_block_of_step(step, round_block, params)
    return int(read_block) >= first - (int(claim_ahead_blocks) + 1)


def nodes_ahead(turn, max_steps, mandates, in_list, matching_entries):
    """`a` of 7.6, the matching nodes that hold no mandate in the round and
    stand before the node: for a node of the list, from the counts of its
    turn over its whole band; for a node not in the list, every matching
    entry of the roll less the M mandates."""
    if not in_list:
        return max(0, int(matching_entries) - int(mandates))
    above = turn.match_above
    ahead = above + turn.fit_before + turn.rest_before
    if above < max_steps:
        left = max_steps - above
        in_band = min(turn.fit_before, left) + min(turn.rest_before, max(0, left - turn.fit_in_band))
    else:
        in_band = 0
    return ahead - min(max_steps, above) - in_band


def unmandated_claim_ready(step_now, mandates, ahead, spread_nodes, stand_limit, standing_ahead):
    """The rule of 7.6 (D43) for a node that matches, has no claim and holds
    no mandate in the current round: the mandates are exhausted (stepNow >
    M); its place has come (stepNow >= M + 1 + a / claimSpreadNodes, off at
    0); fewer than claimStandLimit approvable claims of nodes ahead of it
    stand (off at 0)."""
    if int(step_now) <= int(mandates):
        return False
    if int(spread_nodes) > 0 and int(step_now) < int(mandates) + 1 + int(ahead) // int(spread_nodes):
        return False
    if int(stand_limit) > 0 and int(standing_ahead) >= int(stand_limit):
        return False
    return True


def adds_pending(stated, step_of_block):
    """Whether a claim stating `stated` (one step per round, the last the
    current round's) mined at step `step_of_block` of the current round
    enters the node's pending list (4.9): it is mined after the step it
    states for the current round, or states a step in an earlier round."""
    current = int(stated[-1]) if stated else 0
    return (current != 0 and int(step_of_block) > current) or any(int(s) != 0 for s in stated[:-1])


def late_passed(stated, end_step, fill_cut):
    """The steps a late claim stated at `stated` lets pass (4.9): E - s with
    E = min(the end step, the fill's cut) when the cut applies (`fill_cut`,
    step(fill.claimBlock) + 1, None otherwise)."""
    end = int(end_step) if fill_cut is None else min(int(end_step), int(fill_cut))
    return end - int(stated) if int(stated) and end > int(stated) else 0


def late_units(passed, params):
    return min(params.missUnits, params.lateUnits * int(passed))


def fill_cut(fill_node, fill_step, fill_round, fill_claim_block, node, stated, rnd, round_block, params):
    """The fill's cut of a late count (4.9): step(fill.claimBlock) + 1 when
    the request was filled in round `rnd` by another node whose step there
    is above 0 and below `stated`; None otherwise."""
    if fill_node is None or int(fill_round) != int(rnd):
        return None
    if matching.address_text(fill_node) == matching.address_text(node):
        return None
    if not 0 < int(fill_step) < int(stated):
        return None
    return matching.step_at(int(fill_claim_block), round_block, params) + 1


def recount_due(recorded_units, units_on_fill):
    """Whether a kind 6 entry asks the recount of a late charge (7.18): the
    charge counted again on the fill record's step is lower."""
    return int(units_on_fill) < int(recorded_units)


# ------------------------------------------------------ the full blocks

def block_full(gas_used, gas_limit, percent):
    return int(percent) != 0 and int(gas_used) * 100 >= int(gas_limit) * int(percent)


def window_full(blocks, percent):
    """The members' test of a window (3.12; spec/matching.md section 12):
    full when it holds no block, or when every block used at least
    fullBlockPercent of its gas limit (never with fullBlockPercent 0)."""
    if not blocks:
        return True
    return int(percent) != 0 and all(block_full(b['gasUsed'], b['gasLimit'], percent) for b in blocks)


def time_window(blocks, end, window_seconds):
    """The blocks of a window of kinds 2, 3 and 5 that ends at `end`:
    end - fullWindowSeconds <= timestamp < end."""
    return [b for b in blocks if int(end) - int(window_seconds) <= int(b['timestamp']) < int(end)]


def block_window(blocks, from_block, to_block):
    """The blocks of a window of kind 4: numbered `from_block` to `to_block`."""
    return [b for b in blocks if int(from_block) <= int(b['number']) <= int(to_block)]


def full_streak(blocks, percent):
    """How many of the last blocks read, newest last, used at least
    `percent` of their gas limit without a break (7.18)."""
    streak = 0
    for b in reversed(blocks):
        if not block_full(b['gasUsed'], b['gasLimit'], percent):
            break
        streak += 1
    return streak


def full_block_price(price, streak, ceiling):
    """The gas price of a transaction with a deadline while blocks are full
    (7.18): from the first full block read the price doubles at each block
    that stays full, up to the fee ceiling; the price itself without a full
    block or without a ceiling above it."""
    if int(streak) <= 0 or int(ceiling) <= int(price):
        return int(price)
    return min(int(ceiling), int(price) * 2 ** int(streak))


def failure_charged(word):
    """Whether a failure record (failureOf's word, PoxMatchingState.Failure:
    `charged` in bits 112 to 119) holds a charge added to its slot's sum,
    the admission of a correction of kinds 2 to 4 whatever the cap let it
    take (4.9; spec/matching.md section 16 item 13)."""
    return (int(word) >> 112) & 255 != 0


def charged_in_taken(word):
    """The units a node's charges of one slot took from its score
    (chargedInOf's word, PoxMatchingState.ChargedIn: sum in bits 0 to 31,
    taken in bits 32 to 47, failures in bits 48 to 79)."""
    return (int(word) >> 32) & 0xFFFF


def beat_correction_admitted(slot, slot_seconds, banned_until):
    """Whether a kind 5 entry for heartbeat slot `slot` can be admitted: the
    slot ended after the end of the node's ban (`banned_until`), as
    PoxMatching tests at the ask and at the vote (spec/matching.md section
    16 item 26: a ban in force through the slot, or recorded since it, set
    the score to 0)."""
    return int(banned_until) < (int(slot) + 1) * int(slot_seconds)


def correction_entry(kind, request_id=0, rnd=0, step=0, order_id=0, slot=0):
    """A correction entry word (spec/matching.md section 16 item 27): kind <<
    248; kinds 1 and 6 | uint48(doRequestId) << 16 | uint8(round) << 8 |
    uint8(step); kinds 2 to 4 | uint48(orderId); kind 5 | uint32(slot)."""
    word = int(kind) << 248
    if kind in (CORRECTION_CLAIM, CORRECTION_RECOUNT):
        return word | (int(request_id) & (2**48 - 1)) << 16 | (int(rnd) & 255) << 8 | (int(step) & 255)
    if kind in (CORRECTION_START, CORRECTION_RESULT, CORRECTION_ROWS):
        return word | (int(order_id) & (2**48 - 1))
    if kind == CORRECTION_BEAT:
        return word | (int(slot) & matching.UINT32_MAX)
    raise ValueError(f"no correction kind {kind}")


class CorrectionBook:
    """The entries the node asks to have corrected (7.18; 3.12, D80, D87):
    each with the time from which the contract admits it no more (its
    record's time plus correctionWindowSeconds). due() gives at most
    correctionMaxEntries of them once correctionAskSeconds have passed since
    the last list, at most once an hour; an entry stays until the window
    closes, a Corrected log names it or PoxMatching refuses it (a
    ChargeSkipped log naming it, at the ask or at the vote), so a list that
    failed, an entry found late and one the validators did not vote on go in
    a later list."""

    def __init__(self):
        self.entries = {}
        self.last_ask = None

    def add(self, entry, until):
        self.entries[int(entry)] = max(int(until), self.entries.get(int(entry), 0))

    def corrected(self, entry):
        self.entries.pop(int(entry), None)

    def drop(self, entry):
        """Removes an entry PoxMatching refused; True when the book held it."""
        return self.entries.pop(int(entry), None) is not None

    def beat_entries(self):
        """The book's kind 5 entries."""
        return [e for e in self.entries if e >> 248 == CORRECTION_BEAT]

    def due(self, now, ask_seconds, max_entries):
        for entry in [e for e, until in self.entries.items() if until < int(now)]:
            del self.entries[entry]
        if not self.entries:
            return []
        if self.last_ask is not None and int(now) < self.last_ask + int(ask_seconds):
            return []
        return sorted(self.entries, key=lambda e: self.entries[e])[:int(max_entries)]

    def asked(self, now, entries):
        """Records a list sent at `now`; a kind 6 entry, recounted at the ask,
        leaves the book."""
        self.last_ask = int(now)
        for entry in entries:
            if int(entry) >> 248 == CORRECTION_RECOUNT:
                self.entries.pop(int(entry), None)


# ------------------------------------------------ the rewards wallet

def payout_action(setting, node, payout_of):
    """What the agent does with the rewards wallet setting (7.13, 3.18,
    D34): 'set' when the setting names a wallet and the PoX answers the
    node's own address for payoutAddressOf (never named, or handed back);
    'none' without a setting or when the wallet named is the setting;
    'differs' when another wallet is named, whose change is that wallet's
    own transaction."""
    if not setting:
        return 'none'
    setting = matching.address_text(setting)
    current = matching.address_text(payout_of)
    if current == matching.address_text(node):
        return 'set'
    return 'none' if current == setting else 'differs'


def payout_setting_refused(setting, pox):
    """Whether the rewards wallet setting is one the PoX refuses ("payout
    invalid"): the zero address or the PoX's own address."""
    return int(matching.address_text(setting), 16) == 0 or matching.address_text(setting) == matching.address_text(pox)


def farm_wallet(join, payout_of):
    """The node's wallet as onFarmJoin and approveFarm read it: the payout
    stored at its farm join while one is stored (`join`, farmJoinOf's
    (payout, broker, joined)), its stored payout (payoutAddressOf, the node
    itself while none is) otherwise."""
    payout, _, joined = join
    return payout if joined else payout_of


def farm_join_ready(wallet, node, approved_farm, farm):
    """Whether the node joins a farm (7.13, D89): a node whose wallet
    (farm_wallet) is zero or its own address joins on its own key; any
    other only once that wallet's approveFarm names the farm
    (farmApprovalOf)."""
    wallet = matching.address_text(wallet)
    if int(wallet, 16) == 0 or wallet == matching.address_text(node):
        return True
    return approved_farm is not None and matching.address_text(approved_farm) == matching.address_text(farm)


def join_left_behind(join, broker):
    """Whether the stored farm join (farmJoinOf's (payout, broker, joined))
    was reported by another reservation broker than the one the PoX names
    (`broker`, reservationBrokerAddress): the PoX's _leftBehind, after which
    only the routes of left_behind_route undo the join."""
    _, reported_by, joined = join
    return bool(joined) and matching.address_text(reported_by) != matching.address_text(broker)


def left_behind_route(join, node, setting):
    """How the node's payout is recovered from a join left behind by a
    broker replacement (OD31 A; the PoX's setPayoutAddress and
    changePayoutAddress at pox-smart-contract-litvm 52e36e0): ('set',
    wallet) when the join stored zero or the node itself, the node's own
    setPayoutAddress naming the setting, else the node; ('wallet', stored)
    otherwise, the stored wallet's changePayoutAddress, which only that
    wallet sends, or the next join reported by the broker the PoX names,
    which carries the stored wallet over."""
    stored = matching.address_text(join[0])
    if int(stored, 16) == 0 or stored == matching.address_text(node):
        return 'set', matching.address_text(setting) if setting else matching.address_text(node)
    return 'wallet', stored


# ------------------------------------------- the local list of images

class ImageFailureList:
    """The node's local list of images (7.15; D120, D153, D161): the ends of
    its own orders that the image likely caused, per image: codes 59 and 63
    in every class, and 20, 62 and a 33 before its time on the paid classes.
    After 3 of them within 7 days, whichever data owners sent them, the
    agent sends skipImage(image, now + 7 days, the 3 order ids). Kept in a
    JSON file of the network's directory, so a restart keeps the ends of
    the last 7 days."""

    def __init__(self, filepath):
        self.filepath = str(filepath)
        try:
            with open(self.filepath) as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            loaded = {}
        self.ends = loaded.get('ends', {}) if isinstance(loaded, dict) else {}
        self.skipped = loaded.get('skipped', {}) if isinstance(loaded, dict) else {}

    def __write(self):
        temporary = f"{self.filepath}.tmp"
        with open(temporary, 'w') as f:
            json.dump({'ends': self.ends, 'skipped': self.skipped}, f)
        os.replace(temporary, self.filepath)

    @staticmethod
    def counts(code, paid_class, early_timeout):
        """Whether an end counts (D153): 59 or 63 in every class; 20, 62 or
        an early 33 on a paid class; a default-class 20 or 62 not."""
        code = int(code)
        if code in LOCAL_LIST_EVERY_CLASS:
            return True
        if not paid_class:
            return False
        return code in LOCAL_LIST_PAID_CLASS or (code == 33 and bool(early_timeout))

    def note(self, image, order_id, code, paid_class, early_timeout, at):
        """Adds the end of order `order_id` of `image` (its key, hex) at `at`
        when it counts; returns whether it did."""
        if not self.counts(code, paid_class, early_timeout):
            return False
        ends = [e for e in self.ends.get(image, []) if int(e[1]) + LOCAL_LIST_WINDOW_SECONDS > int(at)
                and int(e[0]) != int(order_id)]
        ends.append([int(order_id), int(at)])
        self.ends[image] = ends
        self.__write()
        return True

    def skip_due(self, image, now):
        """(until, order ids) of the skip the agent sends for `image` at `now`:
        3 counted ends within the last 7 days, none sent for them; None
        otherwise."""
        ends = sorted((e for e in self.ends.get(image, []) if int(e[1]) + LOCAL_LIST_WINDOW_SECONDS > int(now)),
                      key=lambda e: e[1])
        if len(ends) < LOCAL_LIST_COUNT:
            return None
        chosen = ends[-LOCAL_LIST_COUNT:]
        ids = [int(e[0]) for e in chosen]
        if sorted(ids) == sorted(self.skipped.get(image, [])):
            return None
        return int(now) + SKIP_SECONDS, ids

    def skip_sent(self, image, order_ids):
        self.skipped[image] = [int(i) for i in order_ids]
        self.__write()


# ----------------------------------------------------------- fetching

def fetch_closure_due(now, approved_at, fetch_try_seconds):
    """Whether an order whose objects did not download is closed as not
    fetchable (7.14, D85): only once fetchTrySeconds have passed since its
    approval."""
    return int(now) >= int(approved_at) + int(fetch_try_seconds)


def hint_word(band, roll_index, listed_ordinal, step_ordinal):
    """A ballot or proof hint (3.13): band << 80 | rollIndex << 48 |
    listedOrdinal << 24 | stepOrdinal."""
    return (int(band) << 80) | (int(roll_index) << 48) | (int(listed_ordinal) << 24) | int(step_ordinal)


def wide_hints(request, eligible, count=3):
    """The hints of `count` matching nodes of the list of the round of an
    order's approval (proveWide; 4.9 wide, PoxMatchingResults._wide): the
    first by the list's order, each with its listed ordinal as both
    ordinals; None when the list holds fewer."""
    order = [e for e in matching.list_order(request, eligible) if e.match]
    if len(order) < count:
        return None
    return [hint_word(e.band, e.index, e.listed_ordinal, e.listed_ordinal) for e in order[:count]]
