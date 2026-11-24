"""matching_rules (MATCHING-DESIGN.md section 7): the heartbeat once per
slot at the address's offset at 43,200 and 3,600 seconds and its legs, the
heartbeat's gas limit for the worst-case settlement, the tip kept while a
mandate or a claim stands, the holds, the claim's statement and when a
mandated node sends it ahead, the rule of 7.6 for a node that is not
mandated with its two settings, the full-window test on the shared
fixture's windows, the correction list sent at most once an hour, the
higher gas price in full blocks, the rewards wallet and the farm step, the
local list of images and its skip, the 10 minutes of fetching and the
proof's hints."""
import json
from pathlib import Path

import pytest

import matching
import matching_rules as rules

SPEC = Path(__file__).parent / "spec" / "matching"
RANK = json.loads((SPEC / "matching-rank.json").read_text())
NODE = "0x8fd495ceeb1bc3345e6729c491b70ea3679ca429"
OTHER = "0x" + "22" * 20
WALLET = "0x" + "33" * 20
FARM = "0x" + "44" * 20


def params(**overrides):
    base = dict(effectAt=0, claims=True, fitPercent=20, maxSteps=8, maxRounds=5, fullBlockPercent=90, settleMax=1,
                pendingMax=16, correctionMaxEntries=16, stepBlocks=1, leadBlocks=2, banSlots=60, gain=137,
                missUnits=1918, lateUnits=548, bandWidth=1000, minListedSeconds=60, maxRequestAgeSeconds=86400,
                startTimeoutSeconds=900, minPauseSeconds=3600, servingGraceSeconds=3600,
                correctionWindowSeconds=86400, correctionAskSeconds=3600, fullWindowSeconds=120,
                fetchTrySeconds=600, heldMarginSeconds=300, nodeFaultCodes=0, defaultFaultCodes=0,
                ambiguousCodes=0, runBilledCodes=0)
    base.update(overrides)
    return matching.Parameters(**base)


# ------------------------------------------------------------ the heartbeat

@pytest.mark.parametrize("slot_seconds", [43200, 3600])
def test_the_offset_spreads_the_nodes_over_the_first_half_of_the_slot(slot_seconds):
    offsets = {rules.beat_offset("0x%040x" % i, slot_seconds) for i in range(1, 400)}
    assert all(0 <= o < slot_seconds // 2 for o in offsets)
    assert len(offsets) > 300
    word = int.from_bytes(matching.keccak(bytes.fromhex(NODE[2:])), "big")
    assert rules.beat_offset(NODE, slot_seconds) == word % (slot_seconds // 2)


@pytest.mark.parametrize("slot_seconds", [43200, 3600])
def test_the_heartbeat_is_due_from_the_offset_in_every_slot(slot_seconds):
    offset = rules.beat_offset(NODE, slot_seconds)
    slot = 41550
    start = slot * slot_seconds
    assert not rules.beat_due(start + offset - 1, slot_seconds, offset)
    assert rules.beat_due(start + offset, slot_seconds, offset)
    assert rules.beat_due(start + slot_seconds - 1, slot_seconds, offset)
    # The next slot starts over: due again only from its own offset.
    assert rules.slot_of(start + slot_seconds, slot_seconds) == slot + 1
    assert rules.beat_due(start + slot_seconds + offset, slot_seconds, offset)
    assert rules.beat_due(start + slot_seconds, slot_seconds, offset) == (offset == 0)


@pytest.mark.parametrize("slot_seconds, quarter", [(43200, 32400), (3600, 2700)])
def test_the_last_quarter_of_a_slot(slot_seconds, quarter):
    start = 41550 * slot_seconds
    assert not rules.in_last_quarter(start + quarter - 1, slot_seconds)
    assert rules.in_last_quarter(start + quarter, slot_seconds)


@pytest.mark.parametrize("state, action", [
    (dict(counts=True, beat_age_slots=1, banned=True), "banned"),
    (dict(counts=True, beat_age_slots=0), "done"),
    (dict(counts=True, beat_age_slots=1), "beat"),
    (dict(counts=False, beat_age_slots=1, offer_expired=True), "refresh"),
    (dict(counts=False, beat_age_slots=1, placed_unstarted=True), "wait"),
    (dict(counts=False, beat_age_slots=2, period_open=False, holding=False), "announce"),
    (dict(counts=False, beat_age_slots=2, period_open=False, holding=True), "wait"),
    (dict(counts=False, beat_age_slots=2, period_open=True), "wait"),
])
def test_the_heartbeats_two_legs_and_the_refresh_and_announce_before_it(state, action):
    full = dict(counts=False, beat_age_slots=1, banned=False, offer_expired=False, period_open=True, holding=False,
                placed_unstarted=False)
    full.update(state)
    assert rules.beat_action(**full) == action


def test_the_heartbeats_gas_limit_covers_the_worst_settlement_under_the_bound():
    # MATCHING-DESIGN.md 6.4, D184: the worst case under settleMax is 329,085
    # gas above a heartbeat on bloxberg's rules, 474,630 on the default rules
    # and 645,990 on Polygon.
    assert rules.beat_gas_limit(84535, 0, 690000) == 84535
    assert rules.beat_gas_limit(84535, 3, 690000) - 84535 >= 329085
    assert rules.beat_gas_limit(93935, 1, 1200000) - 93935 >= 645990
    assert rules.beat_gas_limit(93935, 1, 1200000) - 93935 >= 474630
    assert rules.BEAT_ATTEMPTS_PER_SLOT == 20


def test_the_ban_lasts_to_its_end():
    assert rules.banned(99, 100) and not rules.banned(100, 100) and not rules.banned(5, 0)


# ------------------------------------------------------------ the offer

def test_the_offer_shape_is_the_setting_or_the_hosts_total_less_the_reserve():
    assert rules.offer_shape(2, 64, 4) == 2
    assert rules.offer_shape(0, 64, 4) == 60
    assert rules.offer_shape(0, 900, 4) == 255
    assert rules.offer_shape(0, 2, 4) == 1


@pytest.mark.parametrize("computed, period_tip, standing, tip", [
    (105, 100, False, 100),
    (110, 100, False, 100),
    (111, 100, False, 111),
    (89, 100, False, 89),
    (300, 100, True, 100),
    (0, 100, True, 100),
    (120, None, False, 120),
])
def test_the_refresh_keeps_the_periods_tip_within_ten_percent_and_while_a_mandate_or_claim_stands(
        computed, period_tip, standing, tip):
    assert rules.refresh_tip(computed, period_tip, standing, 10) == tip


def test_the_offers_gflops_argument_carries_the_enclave_memory_above_the_gflops():
    assert rules.gflops_argument(250, 0) == 250
    assert rules.gflops_argument(250, 64) == 250 | (64 << 16)
    assert rules.gflops_argument(65535, 2) & 0xFFFF == 65535


@pytest.mark.parametrize("state, due", [
    (dict(), True),
    (dict(period_open=True), False),
    (dict(period_open=True, tip_changed=True), True),
    (dict(available=False), False),
    (dict(expired=True), False),
    (dict(holding=True), False),
    (dict(paused_at=1000, now=1000 + 3599), False),
    (dict(paused_at=1000, now=1000 + 3600), True),
])
def test_the_announce_after_an_add_a_pause_and_a_tip_change(state, due):
    full = dict(available=True, expired=False, period_open=False, holding=False, paused_at=None, min_pause=3600,
                now=5000, tip_changed=False)
    full.update(state)
    assert rules.announce_due(**full) is due


def test_the_holds():
    assert rules.fee_near_ceiling(80, 100, 80) and not rules.fee_near_ceiling(79, 100, 80)
    assert not rules.fee_near_ceiling(10**12, 0, 80)
    assert rules.below_default_minimum(True, 4999, True)
    assert not rules.below_default_minimum(True, 5000, True)
    assert not rules.below_default_minimum(False, 0, True)
    assert not rules.below_default_minimum(True, 0, False)
    assert rules.hold_reasons(False, False, False, False) == []
    assert len(rules.hold_reasons(True, True, True, True)) == 4


# --------------------------------------------------------------- claims

def test_the_claims_statement_packs_four_bits_a_round():
    assert rules.encode_steps([3]) == 3
    assert rules.encode_steps([2, 0, 5]) == 2 | (5 << 8)
    with pytest.raises(ValueError):
        rules.encode_steps([16])
    turn = matching.Turn(True, True, True, 0, 0, 0, 1, 0, True, 2, False, 0, 24, 1)
    assert rules.stated_step(turn) == 2
    assert rules.stated_step(turn._replace(excused=True)) == 0
    assert rules.stated_step(turn._replace(matches=False)) == 0
    assert rules.stated_step(None) == 0


def test_a_claim_enters_the_pending_list_when_mined_after_its_step_or_stating_an_earlier_round():
    assert not rules.adds_pending([3], 3)
    assert rules.adds_pending([3], 4)
    assert not rules.adds_pending([0], 40)
    assert rules.adds_pending([2, 0], 0)


@pytest.mark.parametrize("step, first_read", [(1, 1000), (2, 1001), (3, 1002), (8, 1007)])
def test_bloxbergs_mandated_node_sends_claim_ahead_blocks_plus_one_before_its_step(step, first_read):
    # leadBlocks 2, stepBlocks 1, claimAheadBlocks 2: step s is block
    # B + 2 + s, and the claim goes at the read of block B + s - 1.
    p = params(leadBlocks=2, stepBlocks=1)
    assert not rules.mandated_send_ready(first_read - 1, step, 1000, p, 2)
    assert rules.mandated_send_ready(first_read, step, 1000, p, 2)


@pytest.mark.parametrize("step", [1, 2, 5])
def test_elsewhere_a_mandated_node_sends_at_the_last_block_before_its_step(step):
    p = params(leadBlocks=0, stepBlocks=8)
    first = matching.first_block_of_step(step, 5000, p)
    assert matching.step_at(first, 5000, p) == step and matching.step_at(first - 1, 5000, p) == step - 1
    assert not rules.mandated_send_ready(first - 2, step, 5000, p, 0)
    assert rules.mandated_send_ready(first - 1, step, 5000, p, 0)


def band_roll(count, band=24, fit_count=0):
    """A copy with `count` nodes in one band, all matching a request of
    the shape of Example 1; the first `fit_count` by index fit it."""
    copy = matching.RollCopy()
    entries = []
    for i in range(count):
        node = "0x%040x" % (i + 1)
        entries.append(matching.RollEntry(node, band * 1000 + 10, 1000))
        mem, gflops = (8, 250) if i < fit_count else (64, 400)
        copy.set_periods(node, [matching.Period(0, 1000, 0, 0, 0, 500 + i, 16, mem, 48, 100, gflops, 64, 1,
                                                10**17, 0)])
    copy.set_roll(0, {band: entries})
    p = params()
    request = matching.Request(1, matching.keccak(b"seed"), 4, 8, 40, 10, 250, 400, 1, 400, 1, b"\x01" * 31, True,
                               p)
    rnd = matching.Round(0, 7000, 3700, 10**18, 5 * 10**17)
    return copy, request, rnd


def unmandated_sends(copy, request, rnd, spread, limit, standing=lambda node: 0):
    """{step: the number of nodes of the list without a mandate whose claim
    the rule sends first at that step}."""
    first = {}
    read = matching.candidates(copy, request, rnd, 3600)
    sequence = matching.mandated(request, read.eligible)
    for e in read.eligible:
        turn = matching.turn_of(copy, request, rnd, 3600, e.node, e.band, e.index)
        if turn.mandate_step:
            continue
        a = rules.nodes_ahead(turn, request.params.maxSteps, len(sequence), True, 0)
        for step in range(1, 40):
            if rules.unmandated_claim_ready(step, len(sequence), a, spread, limit, standing(e.node)):
                first[step] = first.get(step, 0) + 1
                break
    return first


def test_beyond_the_mandates_eight_list_nodes_send_per_step():
    copy, request, rnd = band_roll(28, fit_count=3)
    assert unmandated_sends(copy, request, rnd, 8, 2) == {9: 8, 10: 8, 11: 4}


def test_with_the_spread_off_every_node_sends_once_the_mandates_are_exhausted():
    copy, request, rnd = band_roll(28, fit_count=3)
    assert unmandated_sends(copy, request, rnd, 0, 2) == {9: 20}


def test_two_standing_claims_ahead_hold_a_node_back_unless_the_limit_is_off():
    copy, request, rnd = band_roll(12)
    assert unmandated_sends(copy, request, rnd, 8, 2, standing=lambda node: 2) == {}
    assert unmandated_sends(copy, request, rnd, 8, 2, standing=lambda node: 1) == {9: 4}
    assert unmandated_sends(copy, request, rnd, 8, 0, standing=lambda node: 5) == {9: 4}


def test_a_node_outside_the_list_counts_every_matching_entry_ahead_of_it():
    assert rules.nodes_ahead(None, 8, 8, False, 28) == 20
    assert rules.nodes_ahead(None, 8, 0, False, 0) == 0
    # A request with no schedule: M and a are 0; only the standing claims hold.
    assert rules.unmandated_claim_ready(1, 0, 0, 8, 2, 0)
    assert not rules.unmandated_claim_ready(1, 0, 0, 8, 2, 2)
    assert not rules.unmandated_claim_ready(8, 8, 0, 8, 2, 0)


def test_nodes_ahead_follow_the_mandates_of_the_nodes_band():
    # matchAbove 3, maxSteps 8: the band holds the other 5 mandates, its
    # fitting nodes first.
    turn = matching.Turn(True, False, True, 3, 4, 6, 4, 9, True, 0, False, 0, 0, 0)
    assert rules.nodes_ahead(turn, 8, 8, True, 0) == (3 + 4 + 6) - 3 - (4 + 1)
    turn = turn._replace(match_above=9)
    assert rules.nodes_ahead(turn, 8, 8, True, 0) == (9 + 4 + 6) - 8


@pytest.mark.parametrize("claim_step, stated, cut, passed, units", [
    (3, 1, None, 2, 1096),
    (2, 1, None, 1, 548),
    (4, 1, None, 3, 1644),
    (9, 1, None, 8, 1918),
    (1, 1, None, 0, 0),
    (5, 3, 3, 0, 0),
    (6, 3, 5, 2, 1096),
])
def test_the_late_count_of_a_stated_step(claim_step, stated, cut, passed, units):
    assert rules.late_passed(stated, claim_step, cut) == passed
    assert rules.late_units(passed, params()) == units


def test_the_fill_cuts_the_count_only_for_a_node_mandated_before():
    p = params()
    assert rules.fill_cut(OTHER, 1, 0, 1003, NODE, 3, 0, 1000, p) == matching.step_at(1003, 1000, p) + 1
    assert rules.fill_cut(OTHER, 3, 0, 1003, NODE, 3, 0, 1000, p) is None
    assert rules.fill_cut(OTHER, 0, 0, 1003, NODE, 3, 0, 1000, p) is None
    assert rules.fill_cut(NODE, 1, 0, 1003, NODE, 3, 0, 1000, p) is None
    assert rules.fill_cut(OTHER, 1, 1, 1003, NODE, 3, 0, 1000, p) is None
    assert rules.fill_cut(None, 1, 0, 1003, NODE, 3, 0, 1000, p) is None
    assert rules.recount_due(1096, 548) and not rules.recount_due(548, 548)


# --------------------------------------------------------- the full blocks

@pytest.mark.parametrize("case", RANK["windows"], ids=[w["name"] for w in RANK["windows"]])
def test_the_full_window_test_of_the_shared_fixture(case):
    p = case["parameters"]
    if case["kind"] == 4:
        window = rules.block_window(case["blocks"], case["fromBlock"], case["toBlock"])
    else:
        window = rules.time_window(case["blocks"], case["end"], p["fullWindowSeconds"])
    assert [b["number"] for b in window] == case["expected"]["inWindow"]
    assert rules.window_full(window, p["fullBlockPercent"]) is case["expected"]["full"]


def test_the_price_doubles_at_each_full_block_up_to_the_ceiling():
    full = {"gasUsed": 95, "gasLimit": 100}
    empty = {"gasUsed": 10, "gasLimit": 100}
    assert rules.full_streak([full, empty], 90) == 0
    assert rules.full_streak([empty, full, full], 90) == 2
    assert rules.full_streak([full], 0) == 0
    assert rules.full_block_price(2_000_000, 0, 10**9) == 2_000_000
    assert rules.full_block_price(2_000_000, 1, 10**9) == 4_000_000
    assert rules.full_block_price(2_000_000, 3, 10**9) == 16_000_000
    assert rules.full_block_price(2_000_000, 30, 10**9) == 10**9
    assert rules.full_block_price(2_000_000, 3, 0) == 2_000_000


def test_the_correction_entries_are_the_specs_words():
    assert rules.correction_entry(1, request_id=301, rnd=1, step=3) == (1 << 248) | (301 << 16) | (1 << 8) | 3
    assert rules.correction_entry(6, request_id=301, rnd=0, step=0) == (6 << 248) | (301 << 16)
    assert rules.correction_entry(2, order_id=77) == (2 << 248) | 77
    assert rules.correction_entry(4, order_id=77) == (4 << 248) | 77
    assert rules.correction_entry(5, slot=41550) == (5 << 248) | 41550
    with pytest.raises(ValueError):
        rules.correction_entry(7)
    assert rules.CORRECTION_OF_FAILURE == {2: 2, 4: 3, 5: 4}


def test_a_correction_list_goes_at_most_once_an_hour_and_keeps_what_is_left():
    book = rules.CorrectionBook()
    now = 1_800_000_000
    assert book.due(now, 3600, 16) == []
    entries = [rules.correction_entry(1, request_id=i, rnd=0, step=1) for i in range(20)]
    for i, e in enumerate(entries):
        book.add(e, now + 86400 + i)
    first = book.due(now, 3600, 16)
    assert first == entries[:16]
    book.asked(now, first)
    assert book.due(now + 3599, 3600, 16) == []
    # Nothing was corrected: the whole list is due again an hour later, the
    # oldest first, and the four left over with it.
    assert book.due(now + 3600, 3600, 16) == entries[:16]
    for e in entries[:16]:
        book.corrected(e)
    assert book.due(now + 3600, 3600, 16) == entries[16:]
    # An entry past its 24 hours is dropped.
    assert book.due(now + 86400 + 100, 3600, 16) == []


def test_a_recount_entry_leaves_the_book_once_asked():
    book = rules.CorrectionBook()
    recount = rules.correction_entry(6, request_id=5)
    book.add(recount, 10**10)
    assert book.due(100, 3600, 16) == [recount]
    book.asked(100, [recount])
    assert book.due(100 + 3600, 3600, 16) == []


# ------------------------------------------------- the rewards wallet

def test_the_payout_setting_names_the_wallet_once_and_never_changes_it():
    assert rules.payout_action("", NODE, NODE) == "none"
    assert rules.payout_action(WALLET, NODE, NODE) == "set"
    assert rules.payout_action(WALLET, NODE, WALLET) == "none"
    assert rules.payout_action(WALLET, NODE, OTHER) == "differs"
    assert rules.payout_action(WALLET.upper().replace("0X", "0x"), NODE, WALLET) == "none"


def test_the_farm_step_waits_for_the_wallets_approval():
    assert rules.farm_join_ready(NODE, NODE, None, FARM)
    assert rules.farm_join_ready("0x" + "00" * 20, NODE, None, FARM)
    assert not rules.farm_join_ready(WALLET, NODE, None, FARM)
    assert not rules.farm_join_ready(WALLET, NODE, OTHER, FARM)
    assert rules.farm_join_ready(WALLET, NODE, FARM, FARM)


ZERO = "0x" + "00" * 20
BROKER = "0x" + "55" * 20
OLD_BROKER = "0x" + "56" * 20


def test_the_payout_settings_the_pox_refuses():
    pox = "0x" + "ee" * 20
    assert rules.payout_setting_refused(ZERO, pox)
    assert rules.payout_setting_refused(pox.upper().replace("0X", "0x"), pox)
    assert not rules.payout_setting_refused(WALLET, pox)


@pytest.mark.parametrize("join, payout_of, wallet", [
    ((ZERO, ZERO, False), NODE, NODE),
    ((ZERO, ZERO, False), WALLET, WALLET),
    ((WALLET, BROKER, True), OTHER, WALLET),
    ((ZERO, BROKER, True), OTHER, ZERO),
])
def test_the_wallet_a_join_reads(join, payout_of, wallet):
    assert rules.farm_wallet(join, payout_of) == wallet


@pytest.mark.parametrize("join, left, route", [
    ((ZERO, ZERO, False), False, None),
    ((ZERO, BROKER, True), False, None),
    ((ZERO, OLD_BROKER, True), True, ('set', WALLET)),
    ((NODE, OLD_BROKER, True), True, ('set', WALLET)),
    ((OTHER, OLD_BROKER, True), True, ('wallet', OTHER)),
])
def test_each_stored_join_state_after_a_broker_replacement(join, left, route):
    assert rules.join_left_behind(join, BROKER) is left
    if left:
        assert rules.left_behind_route(join, NODE, WALLET) == route
    assert rules.left_behind_route((ZERO, OLD_BROKER, True), NODE, '') == ('set', NODE)


# ------------------------------------------- the local list of images

def test_the_local_list_counts_the_ends_the_image_likely_caused(tmp_path):
    assert rules.ImageFailureList.counts(59, False, False)
    assert rules.ImageFailureList.counts(63, False, False)
    assert rules.ImageFailureList.counts(20, True, False)
    assert rules.ImageFailureList.counts(62, True, False)
    assert rules.ImageFailureList.counts(33, True, True)
    assert not rules.ImageFailureList.counts(33, True, False)
    assert not rules.ImageFailureList.counts(20, False, False)
    assert not rules.ImageFailureList.counts(62, False, False)
    assert not rules.ImageFailureList.counts(44, True, False)


def test_three_ends_in_seven_days_skip_the_image_for_seven_days(tmp_path):
    path = tmp_path / "image_failures.json"
    ends = rules.ImageFailureList(path)
    day = 86400
    t = 1_800_000_000
    assert ends.note("img", 1, 59, False, False, t)
    assert not ends.note("img", 2, 20, False, False, t + 1)
    assert ends.note("img", 3, 62, True, False, t + day)
    assert ends.skip_due("img", t + day) is None
    assert ends.note("img", 4, 63, False, False, t + 2 * day)
    assert ends.skip_due("img", t + 2 * day) == (t + 2 * day + 7 * day, [1, 3, 4])
    ends.skip_sent("img", [1, 3, 4])
    again = rules.ImageFailureList(path)
    assert again.skip_due("img", t + 2 * day) is None
    # The first end ages out: two are left, no skip is due.
    assert again.skip_due("img", t + 7 * day) is None
    assert again.note("img", 5, 59, False, False, t + 7 * day)
    assert again.skip_due("img", t + 7 * day) == (t + 14 * day, [3, 4, 5])


def test_an_order_whose_objects_did_not_download_is_closed_only_after_ten_minutes():
    assert not rules.fetch_closure_due(1000 + 599, 1000, 600)
    assert rules.fetch_closure_due(1000 + 600, 1000, 600)


def test_the_proofs_hints_name_three_matching_nodes_of_the_list():
    copy, request, rnd = band_roll(4)
    read = matching.candidates(copy, request, rnd, 3600)
    hints = rules.wide_hints(request, read.eligible)
    order = matching.list_order(request, read.eligible)
    assert hints == [rules.hint_word(e.band, e.index, e.listed_ordinal, e.listed_ordinal) for e in order[:3]]
    assert rules.hint_word(24, 3, 1, 2) == (24 << 80) | (3 << 48) | (1 << 24) | 2
    copy2, request2, rnd2 = band_roll(2)
    assert rules.wide_hints(request2, matching.candidates(copy2, request2, rnd2, 3600).eligible) is None
