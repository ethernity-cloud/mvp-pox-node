"""gas_profile.GasProfile (PHASE-E-PLAN.md E9, E11, D.1; MATCHING-SYNC-PLAN.md
B05, B06): the seed is the first sample, a status-1 receipt moves the
average by GAS_PROFILE_ALPHA (the EIP-1559 and legacy receipt shapes alike,
only gasUsed read), a status-0 receipt is ignored, the file round-trips and
wins over the seed, a zero or absent seed is an empty kind; the kinds the
tip is priced from on a claim network and elsewhere, the claim counted per
order won; and node/gas_profile_seed.json carries the DP add's gas with
the two records measured at pox-smart-contract-litvm 2f600f6
(tests/gas_seed_measured.json) on Amoy and bloxberg testnet."""
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import gas_profile
from constants import GAS_PROFILE_ALPHA

AMOY = {"dpRequest": 1158938, "placement": 681380, "startedRow": 110101}
MEASURED = json.loads((Path(__file__).parent / "gas_seed_measured.json").read_text())
BENCH = json.loads((Path(__file__).parent / "spec" / "bench-record.json").read_text())
RECORD = json.loads((Path(__file__).parent / "spec" / "capability-record.json").read_text())


def receipt_1559(gas_used, status=1):
    return {"status": status, "gasUsed": gas_used, "effectiveGasPrice": 30_000_000_000, "type": 2}


def receipt_legacy(gas_used, status=1):
    return {"status": status, "gasUsed": gas_used, "gasPrice": 20_000_000_000}


def test_the_seed_is_the_first_sample(tmp_path):
    profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", AMOY)
    assert profile.figures() == AMOY
    assert profile.entries["claim"] == {"gas": 0, "samples": 0}
    assert profile.entries["dpRequest"] == {"gas": 1158938, "samples": 1}
    assert profile.gas("placement") == 681380


def test_two_receipts_move_the_average_by_a_quarter(tmp_path):
    profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", AMOY)
    assert profile.record("dpRequest", receipt_1559(1_200_000)) is True
    first = int(1158938 + GAS_PROFILE_ALPHA * (1_200_000 - 1158938))
    assert first == 1169203
    assert profile.gas("dpRequest") == first
    assert profile.record("dpRequest", receipt_legacy(1_100_000)) is True
    second = int(first + GAS_PROFILE_ALPHA * (1_100_000 - first))
    assert second == 1151902
    assert profile.gas("dpRequest") == second
    assert profile.entries["dpRequest"]["samples"] == 3
    assert profile.figures()["placement"] == 681380


def test_a_status_zero_receipt_is_ignored(tmp_path):
    path = tmp_path / "gas_profile.json"
    profile = gas_profile.GasProfile.load(path, AMOY)
    assert profile.record("placement", receipt_1559(900_000, status=0)) is False
    assert profile.entries["placement"] == {"gas": 681380, "samples": 1}
    assert not path.exists()


def test_an_unknown_kind_and_a_missing_receipt_are_ignored(tmp_path):
    profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", AMOY)
    assert profile.record("result", receipt_1559(900_000)) is False
    assert profile.record("placement", None) is False


def test_the_file_round_trips_and_wins_over_the_seed(tmp_path):
    path = tmp_path / "net" / "gas_profile.json"
    profile = gas_profile.GasProfile.load(path, AMOY)
    profile.record("startedRow", receipt_1559(120_000))
    on_disk = json.loads(path.read_text())
    assert on_disk["startedRow"] == {"gas": int(110101 + GAS_PROFILE_ALPHA * (120_000 - 110101)), "samples": 2}
    assert on_disk["dpRequest"] == {"gas": 1158938, "samples": 1}
    assert on_disk[gas_profile.CLAIMS_PER_ORDER] == {"sent": 0, "won": 0}
    assert not path.with_name("gas_profile.json.tmp").exists()
    reloaded = gas_profile.GasProfile.load(path, {"dpRequest": 1, "placement": 1, "startedRow": 1})
    assert reloaded.entries == {kind: on_disk[kind] for kind in gas_profile.KINDS}
    assert reloaded.claims == on_disk[gas_profile.CLAIMS_PER_ORDER]


def test_a_zero_or_absent_seed_is_an_empty_kind(tmp_path):
    profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", {"dpRequest": 0})
    assert profile.entries == {kind: {"gas": 0, "samples": 0} for kind in gas_profile.KINDS}
    assert profile.record("dpRequest", receipt_1559(1_000_000)) is True
    assert profile.entries["dpRequest"] == {"gas": 1_000_000, "samples": 1}
    empty = gas_profile.GasProfile.load(tmp_path / "other.json", None)
    assert sum(empty.figures().values()) == 0


def test_an_unreadable_file_starts_from_the_seed(tmp_path):
    path = tmp_path / "gas_profile.json"
    path.write_text("{not json")
    assert gas_profile.GasProfile.load(path, AMOY).figures() == AMOY


def empty_but(**figures):
    return {**{kind: 0 for kind in gas_profile.KINDS}, **figures}


def test_the_seed_file_carries_the_measured_dp_add_of_two_records():
    """MATCHING-SYNC-PLAN.md B05: Amoy's and bloxberg testnet's dpRequest
    seed is the DP add carrying the trustedzone and the bench record,
    measured under each network's rules (tests/gas_seed_measured.json);
    the -unsafe twins, whose offers carry no record, keep their seeds."""
    assert Path(config.gas_profile_seed_filepath).name == "gas_profile_seed.json"
    rules = MEASURED["ruleSets"]
    for network, rule_set in MEASURED["seeds"].items():
        assert gas_profile.load_seed(config.gas_profile_seed_filepath, network)["dpRequest"] == \
            rules[rule_set]["twoRecords"]
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "polygon_amoy") == empty_but(
        dpRequest=1798880, placement=681380, startedRow=110101)
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "bloxberg_testnet") == empty_but(dpRequest=1657180)
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "polygon_amoy_unsafe") == empty_but(**AMOY)
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "bloxberg_mainnet") == empty_but()
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "litvm_liteforge") == empty_but()
    assert gas_profile.load_seed("/nowhere/seed.json", "polygon_amoy") == {}


def test_the_measured_strings_are_the_fixtures_two_and_one_records():
    sizes = MEASURED["metadataBytes"]
    assert sizes["twoRecords"] == {"metadata3": len(BENCH["metadata3"]["value"].encode()),
                                   "metadata4": len(BENCH["metadata4"]["value"].encode())}
    assert sizes["oneRecord"] == {"metadata3": len(RECORD["metadata3"]["value"].encode()),
                                  "metadata4": len(RECORD["metadata4"]["value"].encode())}
    rules = MEASURED["ruleSets"]
    assert rules["default"]["twoRecords"] - rules["default"]["oneRecord"] == 569576
    assert rules["istanbul"]["twoRecords"] - rules["istanbul"]["oneRecord"] == 517076


def test_the_seed_file_names_known_networks():
    seeds = json.loads(Path(config.gas_profile_seed_filepath).read_text())
    assert set(seeds) <= set(config.available_networks())
    for entry in seeds.values():
        assert set(entry) <= set(gas_profile.KINDS)


def test_the_tip_is_priced_from_the_kinds_the_node_sends_on_the_network():
    """MATCHING-DESIGN.md 7 item 9; MATCHING-SYNC-PLAN.md B06, OD32 A: on a
    claim network claim, announce, beat, pause and askCorrection join
    dpRequest and startedRow and placement leaves them."""
    assert gas_profile.kinds_for(False) == ("dpRequest", "placement", "startedRow")
    assert gas_profile.kinds_for(True) == ("dpRequest", "startedRow", "claim", "announce", "beat", "pause",
                                           "askCorrection")
    assert set(gas_profile.KINDS) == set(gas_profile.kinds_for(False)) | set(gas_profile.kinds_for(True))


def test_the_claim_counts_once_per_claim_sent_per_order_won(tmp_path):
    path = tmp_path / "gas_profile.json"
    profile = gas_profile.GasProfile.load(path, AMOY)
    for _ in range(5):
        profile.record("claim", receipt_1559(150_000))
    assert profile.claims == {"sent": 5, "won": 0} and profile.claims_per_order() == 5
    profile.record_won()
    profile.record_won()
    assert profile.claims_per_order() == 3
    claim = profile.gas("claim")
    assert profile.figures(gas_profile.kinds_for(True))["claim"] == 3 * claim
    assert "claim" not in profile.figures(gas_profile.kinds_for(False))
    assert gas_profile.GasProfile.load(path, AMOY).claims == {"sent": 5, "won": 2}
    fresh = gas_profile.GasProfile.load(tmp_path / "fresh.json", AMOY)
    assert fresh.claims_per_order() == 1


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", Path(__file__).resolve().parents[1] / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tip_node(agent, tmp_path, claims):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__network_config = SimpleNamespace(tip_per_cu_hour_wei=0)
    node._EtnyPoXNode__reader = SimpleNamespace(caller=lambda: SimpleNamespace(
        quote=lambda: (0, 10**18, 10**9, 0, 0, 0, 0, None)))
    node._EtnyPoXNode__matching = None if claims is None else SimpleNamespace(claims_on=lambda: claims)
    profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", {**AMOY, "claim": 150_000, "beat": 100_000})
    node._EtnyPoXNode__gas_profile = profile
    return node


@pytest.mark.parametrize("claims, kinds", [(True, gas_profile.CLAIM_KINDS), (False, gas_profile.PLACEMENT_KINDS),
                                           (None, gas_profile.PLACEMENT_KINDS)])
def test_the_nodes_tip_carries_the_kinds_of_its_network(agent, tmp_path, claims, kinds):
    node = tip_node(agent, tmp_path, claims)
    gas = sum(node._EtnyPoXNode__gas_profile.figures(kinds).values())
    assert node._EtnyPoXNode__current_tip(100) == gas * 10**9
    if claims is True:
        assert gas == AMOY["dpRequest"] + AMOY["startedRow"] + 150_000 + 100_000
    else:
        assert gas == sum(AMOY.values())


def test_the_matching_sends_are_recorded_by_their_kind(agent, tmp_path):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__gas_profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", AMOY)
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=SimpleNamespace(
        wait_for_transaction_receipt=lambda tx_hash, timeout: receipt_1559(90_000)))
    node.get_transaction_build = lambda: {"gasPrice": 1}
    node.send_transaction = lambda txn: "0xhash"
    fn = SimpleNamespace(build_transaction=lambda opts: opts)
    for label in ("beat", "announce", "pause", "claim", "askCorrection", "skipImage"):
        node._EtnyPoXNode__matching_transact(fn, label, None, 0)
    entries = node._EtnyPoXNode__gas_profile.entries
    assert all(entries[kind] == {"gas": 90_000, "samples": 1} for kind in gas_profile.MATCHING_KINDS)
    assert node._EtnyPoXNode__gas_profile.claims["sent"] == 1
