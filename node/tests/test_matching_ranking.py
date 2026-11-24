"""matching (MATCHING-DESIGN.md 4.4, 4.5, 7.5; D183, D191, D198): the ranking
rule on a copy of the roll, the EPC filter and fit included, against the
shared fixtures of pox-smart-contract-litvm spec/ at 2f600f6 (ranking.json
871f3429, matching-rank.json 2ece369d, matching.md fb8dbe67, result-fields.json
67318ca6; test_spec_manifest.py compares the copies with the source
commit). spec/ranking.json holds, per ranking case, the inputs the
copy holds and the reader's answers on them (candidates read from the top
of the roll and on with `beyond`, each eligible entry with the band and the
index of its place, and each roll node's turnOf in one page): the port's
answers on those inputs, rendered as the generator renders the reader's,
equal them. spec/matching-rank.json holds the reference rule's
results on each case's own inputs: the seed, the round's stamp, the
template bit, the image key, the list hash, the matching count, the stop
band, the mandated sequence, each listed node's mandate and first allowed
step, and the steps of the case's blocks."""
import json
from pathlib import Path

import pytest

import matching

SPEC = Path(__file__).parent / "spec" / "matching"
RANKING = json.loads((SPEC / "ranking.json").read_text())
RANK = json.loads((SPEC / "matching-rank.json").read_text())


# ------------------------------------------------------------- the inputs

def params_of(entry):
    return matching.parameters_of({name: (int(v) if isinstance(v, str) else v) for name, v in entry.items()})


def period_of(p):
    return matching.Period(p["ordinal"], p["from"], p["to"], p["toBlock"], p["endReason"], p["dpRequestId"],
                           p["cpu"], p["memGB"], p["storageGB"], p["bandwidth"], p["gflops"], p["enclaveMemoryGB"],
                           p["tzMask"], int(p["tipWei"]), p.get("chainFirst", p["ordinal"]), p["epcUnits"])


def copy_of(case, image):
    copy = matching.RollCopy()
    copy.set_roll(case["roll"]["slot"], {
        b["band"]: [matching.RollEntry(e["node"], e["score"], e["beatTime"]) for e in b["entries"]]
        for b in case["roll"]["bands"]})
    for node, periods in case["periods"].items():
        copy.set_periods(node, [period_of(p) for p in periods])
    for node, s in case["skips"].items():
        copy.set_skip(node, image, matching.Skip(s["at"], s["until"], s["prevAt"], s["prevUntil"]))
    return copy


def ranking_inputs(case):
    """(copy, request, round, slotSeconds) of a spec/ranking.json case."""
    q = case["request"]
    params = params_of(case["parameterSet"])
    image = bytes.fromhex(q["imageKey"][2:])
    request = matching.Request(q["id"], bytes.fromhex(q["seed"][2:]), q["cpu"], q["memGB"], q["storageGB"],
                               q["bandwidth"], q["needGflops"], q["ceilGflops"], q["hours"], q["G"], q["tz"], image,
                               q["ranked"], params, q["epcUnits"])
    r = case["rounds"][q["round"]]
    rnd = matching.Round(r["round"], r["block"], r["time"], int(r["bidWei"]), int(r["baseWei"]))
    return copy_of(case, image), request, rnd, case["deployment"]["slotSeconds"]


def render_read(read, settled):
    """A ListRead as spec/make/ranking.js renders the reader's candidates:
    each eligible entry's place, band << 32 | index, as its band and its
    index."""
    return {
        "eligible": [{"node": e.node, "score": e.score, "fit": e.fit, "match": e.match,
                      "listedOrdinal": e.listed_ordinal, "dpRequestId": e.dp_request_id, "band": e.band,
                      "index": e.index, "tipWei": str(e.tip)} for e in read.eligible],
        "listHash": "0x" + read.list_hash.hex(),
        "matchFound": read.match_found,
        "nextBand": read.next_band,
        "nextFrom": read.next_from,
        "done": read.done,
        "settled": settled,
    }


def render_turn(entry, turn, settled):
    return {
        "node": entry["node"], "band": entry["band"], "index": entry["index"],
        "eligible": turn.eligible, "fit": turn.fit, "matches": turn.matches,
        "matchAbove": turn.match_above, "fitBefore": turn.fit_before, "restBefore": turn.rest_before,
        "fitInBand": turn.fit_in_band, "restInBand": turn.rest_in_band,
        "done": turn.done, "mandateStep": turn.mandate_step, "excused": turn.excused,
        "nextBand": turn.next_band, "nextFrom": turn.next_from, "settled": settled,
    }


def text(value):
    return json.dumps(value, indent=2)


CASES = [c["name"] for c in RANKING["cases"]]


def ranking_case(name):
    return next(c for c in RANKING["cases"] if c["name"] == name)


def test_the_vectors_hold_every_ranking_case_of_the_reference():
    assert CASES == [c["name"] for c in RANK["ranking"]]
    assert len(CASES) == RANKING["groups"]["cases"] == 41
    assert {"epc-below-at-above", "epc-any", "epc-fit-bound", "epc-fit-any"} <= set(CASES)


@pytest.mark.parametrize("name", CASES)
def test_candidates_read_from_the_top_equal_the_readers(name):
    case = ranking_case(name)
    copy, request, rnd, slot_seconds = ranking_inputs(case)
    read = matching.candidates(copy, request, rnd, slot_seconds)
    assert text(render_read(read, case["request"]["settled"])) == text(case["candidates"]["read"])


@pytest.mark.parametrize("name", [n for n in CASES if "beyond" in ranking_case(n)["candidates"]])
def test_candidates_read_on_with_beyond_equal_the_readers(name):
    case = ranking_case(name)
    copy, request, rnd, slot_seconds = ranking_inputs(case)
    first = matching.candidates(copy, request, rnd, slot_seconds)
    on = matching.candidates(copy, request, rnd, slot_seconds, first.next_band, first.next_from, None,
                             first.list_hash, first.match_found, True)
    assert text(render_read(on, case["request"]["settled"])) == text(case["candidates"]["beyond"])


@pytest.mark.parametrize("name", CASES)
def test_every_roll_nodes_turn_equals_the_readers(name):
    case = ranking_case(name)
    copy, request, rnd, slot_seconds = ranking_inputs(case)
    for want in case["turns"]:
        turn = matching.turn_of(copy, request, rnd, slot_seconds, want["node"], want["band"], want["index"])
        assert text(render_turn(want, turn, case["request"]["settled"])) == text(want)


def test_the_beyond_reads_cover_the_cases_whose_list_stops_above_the_rolls_end():
    stops = [n for n in CASES if "beyond" in ranking_case(n)["candidates"]]
    assert stops == ["more-than-max-steps-fit", "parameter-set-before-request-second", "paging-roll"]


# ------------------------------------------------- the reference's results

def rank_inputs(case):
    """(copy, request, round, slotSeconds, expected) of a
    spec/matching-rank.json ranking case, the request assembled as the
    agent assembles it: the seed from the seed stamp, the need from the
    maxGflops argument, the minimum EPC as doStamp's third value answers it
    (bits 32 to 47 of the argument, which the PoX records at the add), the
    template bit from the templates registered before the add, the image
    key from metadata1, the parameter set the index requestInfo's
    parameterSet answers (the reference's parameterSetIndex)."""
    q = case["request"]
    e = case["expected"]
    params = params_of(case["parameterSets"][e["parameterSetIndex"]])
    copy = matching.RollCopy()
    copy.params = [params_of(p) for p in case["parameterSets"]]
    for t in case["templates"]:
        copy.templates[matching.keccak(t["name"].encode())] = (t["bit"], t["registeredAt"])
    image = matching.image_key(q["metadata1"])
    filled = copy_of(case, image)
    copy.rolls, copy.periods, copy.first_ordinal, copy.skips = (filled.rolls, filled.periods, filled.first_ordinal,
                                                                 filled.skips)
    need, ceil = matching.need_gflops(q["maxGflopsArg"])
    seed = matching.seed_of(bytes.fromhex(q["seedStamp"][2:]), q["id"])
    tz = copy.template_bit(matching.template_key(q["metadata1"]), q["createdAt"])
    request = matching.Request(q["id"], seed, q["cpu"], q["memGB"], q["storageGB"], q["bandwidth"], need, ceil,
                               q["durationSec"] // 3600, q["escrowGflops"], tz, image, e["ranked"], params,
                               (q["maxGflopsArg"] >> 32) & 0xFFFF)
    r = case["rounds"][case["round"]]
    rnd = matching.Round(r["round"], r["block"], r["time"], min(int(r["bidWei"]), matching.UINT128_MAX),
                         int(r["baseWei"]))
    return copy, request, rnd, case["deployment"]["slotSeconds"], e


RANK_CASES = [c["name"] for c in RANK["ranking"]]


def rank_case(name):
    return next(c for c in RANK["ranking"] if c["name"] == name)


@pytest.mark.parametrize("name", RANK_CASES)
def test_the_requests_values_are_the_references(name):
    case = rank_case(name)
    copy, request, rnd, slot_seconds, e = rank_inputs(case)
    q = case["request"]
    assert "0x" + request.seed.hex() == e["seed"]
    assert "0x" + request.image.hex() == e["imageKey"]
    assert request.tz == e["tz"]
    assert (request.need, request.ceil, request.hours, request.g, request.epc) == (
        e["needGflops"], e["ceilGflops"], e["hours"], e["G"], e["epcUnits"])
    stamp = matching.round_stamp(q["id"], bytes.fromhex(q["seedStamp"][2:]), rnd.number, rnd.block, rnd.time,
                                 rnd.bid, rnd.base)
    assert "0x" + stamp.hex() == e["roundStamp"]
    assert matching.roll_slot(rnd, slot_seconds) == e["rollSlot"]


@pytest.mark.parametrize("name", RANK_CASES)
def test_the_list_and_the_schedule_are_the_references(name):
    case = rank_case(name)
    copy, request, rnd, slot_seconds, e = rank_inputs(case)
    read = matching.candidates(copy, request, rnd, slot_seconds)
    eligible, list_hash, match_found = matching.full_list(copy, request, rnd, slot_seconds)
    sequence = matching.mandated(request, read.eligible)
    assert ("0x" + list_hash.hex(), match_found) == (e["listHash"], e["matchFound"])
    assert [m.node for m in sequence] == e["mandated"]
    assert len(sequence) == e["M"]
    stop_band = read.next_band if read.match_found >= request.params.maxSteps and request.ranked else 0
    assert stop_band == e["stopBand"]
    order = matching.list_order(request, eligible)
    assert [x.node for x in order] == [x["node"] for x in e["list"]]
    assert [(x.fit, x.match, x.listed_ordinal, x.dp_request_id) for x in order] == \
        [(x["fit"], x["match"], x["listedOrdinal"], x["dpRequestId"]) for x in e["list"]]
    assert matching.allowed_from(request, eligible, sequence, "0x" + "00" * 20) == e["allowedFromOutsideList"]


@pytest.mark.parametrize("name", RANK_CASES)
def test_each_listed_nodes_turn_is_the_references(name):
    case = rank_case(name)
    copy, request, rnd, slot_seconds, e = rank_inputs(case)
    eligible, _, _ = matching.full_list(copy, request, rnd, slot_seconds)
    sequence = matching.mandated(request, matching.candidates(copy, request, rnd, slot_seconds).eligible)
    for node, want in e["turns"].items():
        band, index = copy.place_of(case["roll"]["slot"], node)
        turn = matching.turn_of(copy, request, rnd, slot_seconds, node, band, index)
        assert turn.mandate_step == want["mandateStep"], node
        assert (turn.match_above, turn.fit_before, turn.rest_before, turn.fit_in_band, turn.rest_in_band) == \
            (want["matchAbove"], want["fitBefore"], want["restBefore"], want["fitInBand"], want["restInBand"]), node
        assert matching.allowed_from(request, eligible, sequence, node) == want["allowedFrom"], node


@pytest.mark.parametrize("name", RANK_CASES)
def test_the_steps_of_the_cases_blocks_are_the_references(name):
    case = rank_case(name)
    _, request, rnd, _, e = rank_inputs(case)
    for want in e["steps"]:
        got = {"block": want["block"], "step": matching.step_at(want["block"], rnd.block, request.params),
               "closedStep": matching.closed_step(want["block"], rnd.block, request.params),
               "storedStep": matching.stored_step(matching.step_at(want["block"], rnd.block, request.params),
                                                  request.params)}
        assert got == want


def test_the_word_decoders_read_the_contracts_layouts():
    """A period's two words, a skip and a roll entry as PoxMatchingState
    packs them (spec/make/ranking.js reads the same bits)."""
    first = (1795001000 | (1795002000 << 32) | (1234 << 64) | (501 << 112) | (250 << 160) | (4 << 176) | (8 << 184)
             | (40 << 192) | (100 << 200) | (3 << 208) | (6 << 224) | (7 << 232))
    second = (200000000000000000 | (5 << 128) | (64 << 152) | (65535 << 160))
    p = matching.decode_period(7, first, second)
    assert p == matching.Period(7, 1795001000, 1795002000, 1234, 6, 501, 4, 8, 40, 100, 250, 64, 3,
                                200000000000000000, 5, 65535)
    assert matching.period_ordinal(first) == 7
    assert matching.decode_skip(0) is None
    assert matching.decode_skip(10 | (20 << 32) | (30 << 64) | (40 << 96)) == matching.Skip(10, 20, 30, 40)
    word = int(RANK["ranking"][0]["roll"]["bands"][0]["entries"][0]["word"], 16)
    assert matching.decode_roll_entry(word) == matching.RollEntry(
        "0x98e201ed54a8584f302ab37558743e64d194c6c4", 24600, 1794960624)
