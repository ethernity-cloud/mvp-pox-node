"""matching_node.MatchingNode (MATCHING-DESIGN.md section 7) on a chain that
FakeChain stands in for: the copy of the roll rebuilt by one paged scan and
fed from the Beat, Announced, Left and Skipped logs, equal to a rebuild at
the later block (D183); the heartbeat once per slot at the node's offset at
43,200 and 3,600 seconds, its two legs, the refresh before it, the announce
as the heartbeat, its retries and its gas limit; the announce after a tip
change and the pause of a hold; the claim on a settled read with the
round's stamp and the steps of every round, sent ahead by a mandated node,
under 7.6's rule by a node that is not mandated, after a heartbeat when the
pending list is full, never while banned or on a request marked at its
add; the corrections of full blocks and the recount; the skip of an image,
the proof of three nodes and the rewards wallet."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import matching
import matching_node
import matching_rules as rules
from constants import RPC_REPLICA_LAG_BLOCKS

SPEC = Path(__file__).parent / "spec" / "matching"
RANKING = json.loads((SPEC / "ranking.json").read_text())
LOG = logging.getLogger("test")
WALLET = "0x" + "33" * 20
SETTINGS = matching_node.Settings(claim_ahead_blocks=2, claim_spread_nodes=8, claim_stand_limit=2,
                                  claim_hold_back=False, claim_marked_images=False, tip_keep_percent=10,
                                  minimum_score_hold=True, beat_gas_allowance=690000, page_entries=3)


def params(**overrides):
    base = dict(effectAt=0, claims=True, fitPercent=20, maxSteps=8, maxRounds=5, fullBlockPercent=90, settleMax=1,
                pendingMax=16, correctionMaxEntries=16, stepBlocks=1, leadBlocks=1, banSlots=60, gain=137,
                missUnits=1918, lateUnits=548, bandWidth=1000, minListedSeconds=60, maxRequestAgeSeconds=86400,
                startTimeoutSeconds=900, minPauseSeconds=3600, servingGraceSeconds=3600,
                correctionWindowSeconds=86400, correctionAskSeconds=3600, fullWindowSeconds=120,
                fetchTrySeconds=600, heldMarginSeconds=300, nodeFaultCodes=0, defaultFaultCodes=0,
                ambiguousCodes=0, runBilledCodes=0)
    base.update(overrides)
    return matching.Parameters(**base)


def period_words(p):
    first = (p.start | (p.to << 32) | (p.to_block << 64) | (p.dp_request_id << 112) | (p.gflops << 160)
             | (p.cpu << 176) | (p.mem << 184) | (p.storage << 192) | (p.bandwidth << 200) | (p.tz_mask << 208)
             | (p.end_reason << 224) | (p.ordinal << 232))
    second = p.tip | (p.chain_first << 128) | (p.enclave_memory << 152)
    return first, second


def roll_word(e):
    return (int(e.node, 16) << 96) | (e.score << 80) | (e.beat_time << 40)


class FakeChain:
    """PoxMatching's state as the driver reads it: the rolls, every node's
    periods and skips, the logs written, the node's account and offer, the
    reader's answers of the requests the test sets, and the transactions
    the driver sends, each mined at once with the status `status`."""

    def __init__(self, me, slot_seconds=43200, head=1000, t=1795008231, parameter_sets=None):
        self.me = matching.address_text(me)
        self.slot = slot_seconds
        self.head = head
        self.times = {}
        self.t = t
        self.gas = {}
        self.sets = parameter_sets or [params()]
        self.rolls = {}
        self.periods = {}
        self.skips = {}
        self.logs = []
        self.score, self.age, self.banned_until = 24700, 1, 0
        self.counts = True
        self.pending = 0
        self.minimum = False
        self.requests = {}
        self.rounds = {}
        self.claim_words = {}
        self.claimer_rows = {}
        self.sent = []
        self.status = 1
        self.receipt_logs = []
        self.fills = {}
        self.charges = {}
        self.payout = self.me
        self.times_of_orders = {}
        self.ambiguous_words = {}
        self.simulate_refusal = set()
        self.estimates = 80000

    # --- the chain
    def head_number(self):
        return self.head

    def block(self, n):
        return {'number': n, 'timestamp': self.times.get(n, self.t - (self.head - n) * 5),
                'gasUsed': self.gas.get(n, 1_000_000), 'gasLimit': 10_000_000}

    def mine(self, blocks=1, seconds=5):
        for _ in range(blocks):
            self.head += 1
            self.t += seconds
            self.times[self.head] = self.t

    def blocks_in_time(self, t0, t1, head):
        return [self.block(n) for n in range(0, head + 1) if t0 <= self.block(n)['timestamp'] < t1]

    # --- PoxMatching
    def slot_seconds(self):
        return self.slot

    def parameter_set_count(self):
        return len(self.sets)

    def parameter_sets(self):
        return list(self.sets)

    def template(self, key):
        return (0, 1)

    def roll_lengths(self, slot, block):
        roll = self.rolls.get(slot, {})
        return [len(roll.get(b, [])) for b in range(50)]

    def current(self, node):
        periods = self.periods.get(matching.address_text(node), [])
        return periods[-1] if periods else None

    def current_period(self, node, block=None):
        p = self.current(node)
        return period_words(p) if p else (0, 0)

    def roll_periods(self, slot, band, start, count, image, block):
        out = []
        for e in self.rolls.get(slot, {}).get(band, [])[start:start + count]:
            out.extend([roll_word(e), *self.current_period(e.node), self.skip_word(e.node, image)])
        return out

    def period_at(self, node, ordinal, block):
        return period_words(self.periods[matching.address_text(node)][ordinal])

    def skip_word(self, node, image):
        s = self.skips.get((matching.address_text(node), bytes(image)))
        return 0 if s is None else s.at | (s.until << 32) | (s.prev_at << 64) | (s.prev_until << 96)

    def skip_of(self, node, image, block):
        return self.skip_word(node, image)

    def standing(self):
        return self.score, self.age, self.banned_until

    def beat_counts(self):
        return self.counts

    def record(self):
        return (0, 0, 0, 0, 0, self.pending, 0)

    def minimum_on(self):
        return self.minimum

    def request_info(self, request_id, block):
        return self.requests[request_id](block)

    def round_at(self, request_id, number, block):
        return self.rounds[(request_id, number)]

    def request_image(self, request_id):
        return b"\x6f" * 31, 0

    def stamp(self, request_id):
        return 0, 400

    def claimers(self, request_id):
        return self.claimer_rows.get(request_id, [])

    def claims_of(self, request_id, nodes):
        return [self.claim_words.get((request_id, matching.address_text(n)), (0, False)) for n in nodes]

    def fill(self, request_id):
        return self.fills.get(request_id, 0)

    def charge(self, request_id, number):
        return self.charges.get((request_id, number), 0)

    def ambiguous(self, order_id):
        return self.ambiguous_words.get(order_id, 0)

    def payout_of(self):
        return self.payout

    def order_times(self, order_id):
        return self.times_of_orders[order_id]

    def events(self, names, start, stop, own=False):
        return [log for log in self.logs if log.event in names and start <= log.blockNumber <= stop
                and (not own or matching.address_text(log.args.get('node', '0x' + '00' * 20)) == self.me)]

    def receipt_events(self, receipt, names):
        return [log for log in self.receipt_logs if log.event in names]

    # --- sends
    def function(self, name, *args):
        return (name, args)

    def pox_function(self, name, *args):
        return ('pox.' + name, args)

    def estimate(self, fn):
        return self.estimates

    def simulate(self, fn):
        if fn[0] in self.simulate_refusal:
            raise RuntimeError("execution reverted")

    def transact(self, fn, label, gas, streak):
        self.sent.append((fn[0], fn[1], gas, streak))
        if self.status == 'raise':
            raise RuntimeError("the RPC refused the send")
        self.mine()
        return {'status': self.status, 'blockNumber': self.head}

    def names(self):
        return [s[0] for s in self.sent]

    # --- the state a test writes, with its logs
    def log(self, event, **args):
        self.logs.append(SimpleNamespace(event=event, args=args, blockNumber=self.head, logIndex=len(self.logs)))

    def beat(self, node, score):
        node = matching.address_text(node)
        slot = self.t // self.slot
        band = matching.band_of(score, 1000)
        entries = self.rolls.setdefault(slot, {}).setdefault(band, [])
        entries.append(matching.RollEntry(node, score, self.t))
        self.log('Beat', node=node, slot=slot, score=score, band=band, index=len(entries) - 1)

    def announce(self, node, dp, gflops=250, tip=10**17, memory=8, enclave=64):
        node = matching.address_text(node)
        periods = self.periods.setdefault(node, [])
        chain_first = len(periods)
        if periods and periods[-1].to == 0:
            self.leave(node, matching.END_REPLACED)
            chain_first = periods[-1].chain_first
        p = matching.Period(len(periods), self.t, 0, 0, 0, dp, 4, memory, 40, 100, gflops, enclave, 1, tip,
                            chain_first)
        periods.append(p)
        self.log('Announced', node=node, dpRequestId=dp, **{'from': self.t}, cpu=4, memGB=memory, storageGB=40,
                 bandwidth=100, gflops=gflops, tipWei=tip, tzMask=1, enclaveMemoryGB=enclave)

    def leave(self, node, reason):
        node = matching.address_text(node)
        p = self.periods[node][-1]
        self.periods[node][-1] = p._replace(to=self.t, to_block=self.head, end_reason=reason)
        self.log('Left', node=node, reason=reason, at=self.t)

    def skip(self, node, image, until):
        node = matching.address_text(node)
        s = matching.Skip(self.t, until, 0, 0)
        self.skips[(node, bytes(image))] = s
        self.log('Skipped', node=node, imageKey=bytes(image), at=s.at, until=s.until, prevAt=0, prevUntil=0)


ME = "0x" + "0a" * 20
NODES = ["0x%040x" % (i + 1) for i in range(6)]


def node_on(chain, tmp_path, settings=SETTINGS, address=ME):
    return matching_node.MatchingNode(chain, address, settings, LOG, tmp_path / "image_failures.json")


def copies_equal(a, b):
    assert {s: r for s, r in a.rolls.items()} == {s: r for s, r in b.rolls.items()}
    assert {n: [p._replace(chain_first=0) for p in ps] for n, ps in a.periods.items()} == \
        {n: [p._replace(chain_first=0) for p in ps] for n, ps in b.periods.items()}
    assert {k: v for k, v in a.skips.items() if v is not None} == {k: v for k, v in b.skips.items() if v is not None}


# ------------------------------------------------------------- the copy

def test_the_copy_fed_from_the_logs_equals_a_rebuild_at_the_later_block(tmp_path):
    chain = FakeChain(ME, slot_seconds=3600, head=500, t=3600 * 100 + 100)
    for i, node in enumerate(NODES[:4]):
        chain.announce(node, 10 + i)
        chain.beat(node, 5000 + 100 * i)
    chain.mine(30, 60)
    node = node_on(chain, tmp_path)
    node.start()
    image = b"\x6f" * 31
    node._know_image(image)
    # Logs after the scan: a replacement, a pause and an announce after it, a
    # new node, a skip, and heartbeats of the next slot.
    chain.mine(20, 60)
    chain.announce(NODES[0], 20, tip=2 * 10**17)
    chain.leave(NODES[1], matching.END_PAUSED)
    chain.mine(5, 60)
    chain.announce(NODES[4], 30)
    chain.beat(NODES[4], 400)
    chain.skip(NODES[2], image, chain.t + 7 * 86400)
    chain.mine(40, 60)
    for n in NODES[:3]:
        chain.beat(n, 6000)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    fresh = node_on(chain, tmp_path)
    fresh.start()
    fresh._know_image(image)
    copies_equal(node.copy, fresh.copy)
    assert node.copy.block == fresh.copy.block == chain.head - RPC_REPLICA_LAG_BLOCKS


def test_the_rebuild_reads_the_periods_an_open_round_can_find_and_no_older(tmp_path):
    chain = FakeChain(ME, slot_seconds=3600, head=500, t=3600 * 100)
    chain.announce(NODES[0], 1)
    chain.t += 86400
    chain.announce(NODES[0], 2)
    chain.t += 3 * 86400
    chain.announce(NODES[0], 3)
    chain.beat(NODES[0], 1000)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node = node_on(chain, tmp_path)
    node.start()
    ordinals = [p.ordinal for p in node.copy.periods[matching.address_text(NODES[0])]]
    # Ordinal 1 started three days before the oldest open request: it is the
    # period an open round before ordinal 2 finds, and ordinal 0 is never.
    assert ordinals == [1, 2]


# -------------------------------------------------------- the heartbeat

def offer(dp=7, available=True, expired=False, tip=10**17, placed_unstarted=False):
    return matching_node.Offer(dp, available, expired, tip, placed_unstarted)


@pytest.mark.parametrize("slot_seconds", [43200, 3600])
def test_one_heartbeat_per_slot_from_the_nodes_offset(slot_seconds, tmp_path):
    offset = rules.beat_offset(ME, slot_seconds)
    chain = FakeChain(ME, slot_seconds=slot_seconds, head=900, t=41550 * slot_seconds + offset - 10)
    chain.announce(ME, 7)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 1)
    node = node_on(chain, tmp_path)
    node.start()
    chain.t = 41550 * slot_seconds + offset - 1
    chain.times[chain.head] = chain.t
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'idle'
    chain.t += 1
    chain.times[chain.head] = chain.t
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'beat'
    assert chain.names() == ['beat']
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'idle'
    # The next slot: once more from the offset.
    chain.t = 41551 * slot_seconds + offset
    chain.times[chain.head] = chain.t
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'beat'
    assert chain.names() == ['beat', 'beat']


def beat_node(tmp_path, **chain_state):
    chain = FakeChain(ME, slot_seconds=3600, head=900, t=41550 * 3600 + 1799)
    chain.announce(ME, 7)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 1)
    for k, v in chain_state.items():
        setattr(chain, k, v)
    node = node_on(chain, tmp_path)
    node.start(["ecld-nodenithy-matching"])
    return chain, node


def test_the_announce_names_the_template_bits_of_the_nodes_trustedzones(tmp_path):
    chain, node = beat_node(tmp_path)
    assert node.tz_mask == 1
    chain.template = lambda key: (3, 1) if key == matching.keccak(b"etny-pynithy-testnet") else None
    node.set_trustedzones(["etny-pynithy-testnet", "unregistered"])
    assert node.tz_mask == 1 << 3


def test_a_counted_slot_and_a_ban_send_nothing(tmp_path):
    chain, node = beat_node(tmp_path, age=0)
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'done'
    chain, node = beat_node(tmp_path, banned_until=10**10)
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'banned'
    assert chain.sent == []


def test_an_expired_offer_is_refreshed_before_the_heartbeat(tmp_path):
    chain, node = beat_node(tmp_path, counts=False)
    refreshed = []
    assert node.heartbeat_tick(offer(expired=True), False, lambda: refreshed.append(1)) == 'refresh'
    assert refreshed == [1] and chain.sent == []


def test_a_node_with_no_period_announces_and_the_announce_is_the_heartbeat(tmp_path):
    chain, node = beat_node(tmp_path, counts=False)
    chain.leave(ME, matching.END_BUSY)
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'announce'
    assert chain.sent[0][:2] == ('announce', (7, 1))
    assert node.heartbeat_tick(offer(), False, lambda: None) == 'idle'


def test_no_heartbeat_between_a_placement_and_its_start_row_or_while_holding(tmp_path):
    chain, node = beat_node(tmp_path, counts=False)
    assert node.heartbeat_tick(offer(placed_unstarted=True), False, lambda: None) == 'wait'
    chain.leave(ME, matching.END_PAUSED)
    assert node.heartbeat_tick(offer(), True, lambda: None) == 'wait'
    assert chain.sent == []


def test_the_heartbeats_gas_limit_carries_the_settlement_allowance_while_claims_are_pending(tmp_path):
    chain, node = beat_node(tmp_path, pending=3)
    node.heartbeat_tick(offer(), False, lambda: None)
    assert chain.sent[0][2] == 80000 + 690000
    chain, node = beat_node(tmp_path, pending=0)
    node.heartbeat_tick(offer(), False, lambda: None)
    assert chain.sent[0][2] == 80000


def test_a_failed_heartbeat_is_sent_again_at_each_tick_twenty_times_at_most(tmp_path):
    chain, node = beat_node(tmp_path, status='raise')
    for _ in range(25):
        node.heartbeat_tick(offer(), False, lambda: None)
    assert len(chain.sent) == rules.BEAT_ATTEMPTS_PER_SLOT
    # The slot ended unbeaten: its kind 5 entry needs a full window.
    chain.t = 41551 * 3600 + 3000
    chain.times[chain.head] = chain.t
    chain.status = 1
    node.heartbeat_tick(offer(), False, lambda: None)
    assert 41550 in node.unbeaten


def test_a_heartbeat_in_the_last_quarter_takes_the_full_block_price(tmp_path):
    chain, node = beat_node(tmp_path)
    chain.t = 41550 * 3600 + 2800
    chain.times[chain.head] = chain.t
    for n in range(chain.head - 3, chain.head + 1):
        chain.gas[n] = 9_500_000
    node.heartbeat_tick(offer(), False, lambda: None)
    assert chain.sent[0][3] == 4


# ------------------------------------------------- announce and holds

def test_a_hold_pauses_and_the_announce_waits_out_min_pause(tmp_path):
    chain, node = beat_node(tmp_path)
    assert node.hold_tick(offer(), ['the task slot is taken on another network']) == 'paused'
    chain.leave(ME, matching.END_PAUSED)
    assert node.hold_tick(offer(), []) == 'idle'
    chain.t += 3600
    chain.times[chain.head] = chain.t
    assert node.hold_tick(offer(), []) == 'announced'
    assert chain.names() == ['pause', 'announce']


def test_a_changed_tip_is_announced_again(tmp_path):
    chain, node = beat_node(tmp_path)
    assert node.hold_tick(offer(), []) == 'idle'
    assert node.hold_tick(offer(tip=2 * 10**17), []) == 'announced'


def test_the_hold_below_the_runners_default_minimum(tmp_path):
    chain, node = beat_node(tmp_path, score=4999, minimum=True)
    assert node.hold_reasons(False, False, 0, 0, 80) == ["its score is below the runner's default minimum"]
    chain.score = 5000
    assert node.hold_reasons(False, False, 0, 0, 80) == []
    off = node_on(chain, tmp_path, SETTINGS._replace(minimum_score_hold=False))
    chain.score = 0
    assert off.hold_reasons(False, False, 0, 0, 80) == []
    chain.score = 5000
    assert node.hold_reasons(False, False, 80, 100, 80) == ['the fee it would offer is near its fee ceiling']
    assert node.hold_reasons(True, True, 0, 0, 80) == ['the task slot is taken on another network',
                                                        'its acceptance of a hand-over stands']


# ------------------------------------------------------------- claims

def example_one(tmp_path, me, settings=SETTINGS, read_block=1000, rounds=None):
    """The chain of ranking.json's example-1 (3.8 Example 1) with the node
    `me` one of its five, read at `read_block`."""
    case = next(c for c in RANKING["cases"] if c["name"] == "example-1")
    p = params(**{k: (int(v) if isinstance(v, str) else v) for k, v in case["parameterSet"].items()})
    chain = FakeChain(me, slot_seconds=43200, head=read_block + RPC_REPLICA_LAG_BLOCKS, t=case["readTime"],
                      parameter_sets=[p])
    slot = case["roll"]["slot"]
    for b in case["roll"]["bands"]:
        chain.rolls.setdefault(slot, {})[b["band"]] = [matching.RollEntry(e["node"], e["score"], e["beatTime"])
                                                       for e in b["entries"]]
    for n, ps in case["periods"].items():
        chain.periods[n] = [matching.Period(q["ordinal"], q["from"], q["to"], q["toBlock"], q["endReason"],
                                            q["dpRequestId"], q["cpu"], q["memGB"], q["storageGB"], q["bandwidth"],
                                            q["gflops"], q["enclaveMemoryGB"], q["tzMask"], int(q["tipWei"]),
                                            q["ordinal"]) for q in ps]
    q = case["request"]
    rnds = rounds or [{"block": 1000, "time": q["createdAt"], "bid": int(case["rounds"][0]["bidWei"]),
                       "base": int(case["rounds"][0]["baseWei"])}]

    def info(block):
        r = rnds[-1]
        return {'claims': True, 'ranked': True, 'imageMarked': False, 'settled': True, 'isFinal': False,
                'round': len(rnds) - 1, 'roundStamp': b"\x5a" * 32, 'seed': bytes.fromhex(q["seed"][2:]),
                'roundBlock': r["block"], 'roundTime': r["time"], 'createdAt': q["createdAt"],
                'rollSlot': slot, 'bidWei': r["bid"], 'baseWei': r["base"],
                'stepNow': matching.step_at(block, r["block"], p), 'closedStep': 0, 'lastOpenStep': 0,
                'filledStep': 0, 'claimCount': 0, 'claimedInRound': False}
    chain.requests[301] = info
    for i, r in enumerate(rnds[:-1]):
        chain.rounds[(301, i)] = {'roundStamp': b"\x01" * 32, 'roundBlock': r["block"], 'roundTime': r["time"],
                                  'bidWei': r["bid"], 'baseWei': r["base"], 'lastOpenStep': 0, 'closed': True,
                                  'judged': False}
    node = node_on(chain, tmp_path, settings, address=me)
    node.slot_seconds = 43200
    node.offset = rules.beat_offset(me, 43200)
    node.copy.params = [p]
    node.copy.templates = {}
    node.oldest_time = q["createdAt"] - 86400
    node.rebuild()
    node.copy.block = read_block
    own = chain.current(me)
    terms = {'cpu': q["cpu"], 'memory': q["memGB"], 'storage': q["storageGB"], 'bandwidth': q["bandwidth"],
             'duration': q["hours"] * 3600, 'max_gflops': (q["needGflops"] << 16) | q["ceilGflops"],
             'metadata1': "v3:QmImage:ecld-nodenithy-matching:1.0.0", 'with_terms': True}
    return chain, node, terms, offer(own.dp_request_id, tip=own.tip)


N1 = "0x8fd495ceeb1bc3345e6729c491b70ea3679ca429"   # mandated for step 1
N3 = "0x98e201ed54a8584f302ab37558743e64d194c6c4"   # mandated for step 4


def test_the_node_mandated_for_step_one_claims_on_reading_the_rounds_block(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    assert node.serve(301, terms, own) == 'claimed'
    name, args, _, _ = chain.sent[0]
    assert name == 'claim' and args == (301, 501, b"\x5a" * 32, 1)
    assert node.serve(301, terms, own) == 'done'
    assert node.standing_claim_or_mandate() is False


@pytest.mark.parametrize("read_block, sends", [(1001, False), (1002, True)])
def test_a_node_mandated_for_step_four_sends_claim_ahead_blocks_plus_one_before_it(tmp_path, read_block, sends):
    # leadBlocks 1: step 4 is block 1005; with claimAheadBlocks 2 the claim
    # goes at the read of block 1002.
    chain, node, terms, own = example_one(tmp_path, N3, read_block=read_block)
    assert node.serve(301, terms, own) == ('claimed' if sends else 'wait')
    if sends:
        assert chain.sent[0][1][3] == 4
    else:
        assert node.mandates and node.standing_claim_or_mandate()


def test_no_claim_on_a_read_that_is_not_settled(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    base = chain.requests[301]
    chain.requests[301] = lambda block: {**base(block), 'settled': False}
    assert node.serve(301, terms, own) == 'wait'
    assert chain.sent == []


def test_claims_off_is_served_by_the_agents_own_placement(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    base = chain.requests[301]
    chain.requests[301] = lambda block: {**base(block), 'claims': False}
    assert node.serve(301, terms, own) == 'placement'
    assert chain.sent == []


def test_no_claim_while_banned_or_on_an_image_marked_at_its_add(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    chain.banned_until = 10**10
    assert node.serve(301, terms, own) == 'done'
    chain.banned_until = 0
    base = chain.requests[301]
    chain.requests[301] = lambda block: {**base(block), 'imageMarked': True}
    assert node.serve(301, terms, own) == 'done'
    marked = node_on(chain, tmp_path, SETTINGS._replace(claim_marked_images=True), address=N1)
    marked.slot_seconds, marked.copy, marked.synced = 43200, node.copy, True
    assert marked.serve(301, terms, own) == 'claimed'


def test_a_claim_states_the_nodes_step_in_every_round(tmp_path):
    # Round 0 at the bid of 1.2, then a raise in block 1010: N3 is mandated
    # for step 4 in both rounds and states 4 for each.
    case = next(c for c in RANKING["cases"] if c["name"] == "example-1")
    created = case["request"]["createdAt"]
    rounds = [{"block": 1000, "time": created, "bid": 12 * 10**17, "base": 6 * 10**17},
              {"block": 1010, "time": created + 300, "bid": 13 * 10**17, "base": 6 * 10**17}]
    chain, node, terms, own = example_one(tmp_path, N3, read_block=1012, rounds=rounds)
    assert node.serve(301, terms, own) == 'claimed'
    assert chain.sent[0][1][3] == rules.encode_steps([4, 4])


def test_a_claim_that_reverts_is_built_again_from_the_next_read(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    chain.status = 0
    assert node.serve(301, terms, own) == 'wait'
    assert node.stated == {}
    chain.status = 1
    assert node.serve(301, terms, own) == 'claimed'


def test_no_claim_into_a_full_pending_list_without_a_heartbeat_first(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1, read_block=1006)
    chain.pending = 16
    chain.counts = False
    assert node.serve(301, terms, own) == 'wait'
    assert chain.sent == []
    chain.counts = True
    beat_then = []
    original = chain.transact

    def transact(fn, label, gas, streak):
        beat_then.append(fn[0])
        if fn[0] == 'beat':
            chain.pending = 15
        return original(fn, label, gas, streak)
    chain.transact = transact
    assert node.serve(301, terms, own) == 'claimed'
    assert beat_then == ['beat', 'claim']


def test_an_offer_without_enclave_memory_covers_no_request(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    me = matching.address_text(N1)
    chain.periods[me][-1] = chain.periods[me][-1]._replace(enclave_memory=0)
    assert node.serve(301, terms, own) == 'wait'


def unmandated_chain(tmp_path, count, me_index, settings=SETTINGS):
    """28 matching nodes in one band, the node `me_index`-th by address."""
    nodes = ["0x%040x" % (i + 1) for i in range(count)]
    me = nodes[me_index]
    p = params(leadBlocks=1)
    chain = FakeChain(me, slot_seconds=3600, head=1000 + RPC_REPLICA_LAG_BLOCKS, t=3600 * 10 + 100,
                      parameter_sets=[p])
    slot = 9
    chain.rolls[slot] = {24: [matching.RollEntry(n, 24010, 3600 * 9) for n in nodes]}
    for i, n in enumerate(nodes):
        chain.periods[n] = [matching.Period(0, 3600 * 9, 0, 0, 0, 500 + i, 16, 64, 48, 100, 400, 64, 1, 10**17, 0)]
    seed = matching.keccak(b"seed")

    def info(block):
        return {'claims': True, 'ranked': True, 'imageMarked': False, 'settled': True, 'isFinal': False,
                'round': 0, 'roundStamp': b"\x5a" * 32, 'seed': seed, 'roundBlock': 1000, 'roundTime': 3600 * 10,
                'createdAt': 3600 * 10, 'rollSlot': slot, 'bidWei': 10**18, 'baseWei': 5 * 10**17,
                'stepNow': matching.step_at(block, 1000, p), 'closedStep': 0, 'lastOpenStep': 0, 'filledStep': 0,
                'claimCount': 0, 'claimedInRound': False}
    chain.requests[5] = info
    node = node_on(chain, tmp_path, settings, address=me)
    node.slot_seconds = 3600
    node.copy.params = [p]
    node.oldest_time = 3600 * 10 - 86400
    node.rebuild()
    terms = {'cpu': 4, 'memory': 8, 'storage': 40, 'bandwidth': 10, 'duration': 3600, 'max_gflops': (250 << 16) | 400,
             'metadata1': "v3:QmImage:ecld-nodenithy-matching", 'with_terms': True}
    return chain, node, terms, offer(500 + me_index), nodes


def first_claim_step(tmp_path, me_index, **kwargs):
    chain, node, terms, own, nodes = unmandated_chain(tmp_path, 28, me_index, **kwargs)
    for step in range(1, 30):
        node.copy.block = 1000 + 1 + step
        if node.serve(5, terms, own) == 'claimed':
            return step
    return None


def test_a_node_that_is_not_mandated_claims_at_its_place_after_the_mandates():
    import tempfile
    steps = []
    for i in range(28):
        with tempfile.TemporaryDirectory() as d:
            steps.append(first_claim_step(Path(d), i))
    unmandated = [s for s in steps if s is None or s > 8]
    assert sorted(unmandated) == [9] * 8 + [10] * 8 + [11] * 4


def test_two_standing_claims_ahead_hold_an_unmandated_node(tmp_path):
    chain, node, terms, own, nodes = unmandated_chain(tmp_path, 12, 0)
    block = 1000 + 1 + 9
    info = chain.requests[5](block)
    request = node.request_of(5, info, terms)
    rnd = node.rounds_of(5, info)[-1]
    sequence = matching.mandated(request, matching.candidates(node.copy, request, rnd, 3600).eligible)
    index = next(i for i, n in enumerate(nodes) if matching.address_text(n) not in {m.node for m in sequence})
    chain, node, terms, own, nodes = unmandated_chain(tmp_path, 12, index)
    node.copy.block = block
    # Two approvable claims of mandated nodes stand: they are ahead of it.
    chain.claimer_rows[5] = [(m.node, 0, True) for m in sequence[:2]]
    assert node.serve(5, terms, own) == 'wait'
    off = node_on(chain, tmp_path, SETTINGS._replace(claim_stand_limit=0), address=nodes[index])
    off.slot_seconds, off.copy, off.synced = 3600, node.copy, True
    assert off.serve(5, terms, own) == 'claimed'
    chain.claimer_rows[5] = [(m.node, 0, False) for m in sequence[:2]]
    one = node_on(chain, tmp_path, address=nodes[index])
    one.slot_seconds, one.copy, one.synced = 3600, node.copy, True
    assert one.serve(5, terms, own) == 'claimed'


# --------------------------------------------------------- corrections

def correction_node(tmp_path):
    chain = FakeChain(ME, slot_seconds=3600, head=2000, t=3600 * 500)
    chain.announce(ME, 7)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node = node_on(chain, tmp_path)
    node.start()
    return chain, node


def test_a_late_charge_whose_step_was_full_is_asked_back_once_an_hour(tmp_path):
    chain, node = correction_node(tmp_path)
    chain.rounds[(9, 0)] = {'roundBlock': 1900}
    # leadBlocks 1: block 1901 is the lead, step 2 is block 1903, step 3
    # block 1904; only step 2's block was full.
    chain.gas[1903] = 9_500_000
    chain.log('Charged', doRequestId=9, round=0, node=ME, kind=1, mandateStep=2, passed=2, units=1096, taken=1096,
              scoreAfter=1000)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    assert node.correction_tick() == 'sent'
    entry = rules.correction_entry(1, request_id=9, rnd=0, step=2)
    assert chain.sent[-1][:2] == ('askCorrection', ([entry],))
    assert node.correction_tick() == 'idle'
    chain.t += 3600
    chain.times[chain.head] = chain.t
    assert node.correction_tick() == 'sent'
    chain.log('Corrected', node=ME, entry=entry, unitsBack=548, scoreAfter=1548)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    chain.t += 3600
    chain.times[chain.head] = chain.t
    assert node.correction_tick() == 'idle'


def test_a_never_started_order_whose_last_two_minutes_were_full_is_asked_back(tmp_path):
    chain, node = correction_node(tmp_path)
    approved = chain.t - 2000
    chain.times_of_orders[77] = (approved, approved - 10, 0)
    end = approved + 900
    for n in range(0, chain.head + 1):
        if end - 120 <= chain.block(n)['timestamp'] < end:
            chain.gas[n] = 9_900_000
    chain.log('OrderFailed', orderId=77, node=ME, kind=rules.FAIL_NEVER_STARTED, units=1918, scoreAfter=0)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    assert node.correction_tick() == 'sent'
    assert chain.sent[-1][1] == ([rules.correction_entry(2, order_id=77)],)


def test_a_failure_whose_window_was_not_full_is_not_asked(tmp_path):
    chain, node = correction_node(tmp_path)
    chain.times_of_orders[77] = (chain.t - 2000, chain.t - 2010, 0)
    chain.log('OrderFailed', orderId=77, node=ME, kind=rules.FAIL_NEVER_STARTED, units=1918, scoreAfter=0)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    assert node.correction_tick() == 'idle'


def test_a_ballot_that_writes_a_lower_count_adds_a_recount(tmp_path):
    chain, node = correction_node(tmp_path)
    chain.rounds[(9, 0)] = {'roundBlock': 1900}
    chain.log('Charged', doRequestId=9, round=0, node=ME, kind=1, mandateStep=3, passed=3, units=1644, taken=1644,
              scoreAfter=0)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    # The claim was mined in step 5 (block 1906); the ballot writes the
    # approved node's step 1, its claim in block 1902 of step 1: the count
    # stops at step 2, below the stated step 3, so nothing is late.
    chain.charges[(9, 0)] = 1 | (3 << 8) | (3 << 16) | (1644 << 24) | (chain.t << 40)
    chain.fills[9] = int(NODES[0], 16) | (1902 << 160) | (0 << 208) | (1 << 216)
    chain.claim_words[(9, ME)] = ((1906 << 48) | (0 << 128), False)
    chain.log('ListJudged', doRequestId=9, round=0, listHash=b"\x00" * 32)
    chain.mine(RPC_REPLICA_LAG_BLOCKS + 1, 5)
    node.follow()
    chain.t += 3600
    chain.times[chain.head] = chain.t
    node.correction_tick()
    assert rules.correction_entry(6, request_id=9, rnd=0, step=0) in chain.sent[-1][1][0]


# ------------------------------------------- skip, proof, the wallet

def test_three_counted_ends_send_the_skip_of_the_image(tmp_path):
    chain, node = correction_node(tmp_path)
    image = b"\x6f" * 31
    for order_id, code in ((1, 59), (2, 63), (3, 59)):
        node.note_order_end(order_id, 9, image, code, False, False)
    now = node.now()
    assert node.skip_tick() == 1
    name, args, _, _ = chain.sent[-1]
    assert name == 'skipImage' and args[0] == image and args[2] == [1, 2, 3]
    assert args[1] == now + 7 * 86400
    assert node.skip_tick() == 0


def test_a_skip_the_contract_refuses_is_not_sent(tmp_path):
    chain, node = correction_node(tmp_path)
    chain.simulate_refusal.add('skipImage')
    for order_id in (1, 2, 3):
        node.note_order_end(order_id, 9, b"\x6f" * 31, 59, False, False)
    assert node.skip_tick() == 0 and chain.sent == []


def test_the_rewards_wallet_is_named_once_and_never_changed(tmp_path):
    chain, node = correction_node(tmp_path)
    assert node.payout_tick('') == 'none'
    assert node.payout_tick(WALLET) == 'set'
    assert chain.sent[-1][:2] == ('pox.setPayoutAddress', (WALLET,))
    chain.payout = WALLET
    assert node.payout_tick(WALLET) == 'none'
    chain.payout = "0x" + "44" * 20
    assert node.payout_tick(WALLET) == 'differs'
    assert len(chain.sent) == 1


def test_an_order_end_of_the_image_health_is_proven_wide_once_its_record_admits_it(tmp_path):
    chain, node, terms, own = example_one(tmp_path, N1)
    node.terms_of = lambda request_id: terms
    chain.fills[301] = int(N1, 16)
    node.note_order_end(55, 301, b"\x6f" * 31, 62, True, False)
    chain.simulate_refusal.add('proveWide')
    node.wide_tick()
    assert chain.sent == []
    chain.simulate_refusal.clear()
    node.wide_tick()
    name, args, _, _ = chain.sent[-1]
    assert name == 'proveWide' and args[0] == 55 and len(args[1]) == 3
    assert node.wide_pending == {}
