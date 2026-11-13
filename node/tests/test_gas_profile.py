"""gas_profile.GasProfile (PHASE-E-PLAN.md E9, E11, D.1): the seed is the
first sample, a status-1 receipt moves the average by GAS_PROFILE_ALPHA (the
EIP-1559 and legacy receipt shapes alike, only gasUsed read), a status-0
receipt is ignored, the file round-trips and wins over the seed, a zero or
absent seed is an empty kind, and node/gas_profile_seed.json carries the
Amoy figures."""
import json
from pathlib import Path

import config
import gas_profile
from constants import GAS_PROFILE_ALPHA

AMOY = {"dpRequest": 1158938, "placement": 681380, "startedRow": 110101}


def receipt_1559(gas_used, status=1):
    return {"status": status, "gasUsed": gas_used, "effectiveGasPrice": 30_000_000_000, "type": 2}


def receipt_legacy(gas_used, status=1):
    return {"status": status, "gasUsed": gas_used, "gasPrice": 20_000_000_000}


def test_the_seed_is_the_first_sample(tmp_path):
    profile = gas_profile.GasProfile.load(tmp_path / "gas_profile.json", AMOY)
    assert profile.figures() == AMOY
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
    assert not path.with_name("gas_profile.json.tmp").exists()
    reloaded = gas_profile.GasProfile.load(path, {"dpRequest": 1, "placement": 1, "startedRow": 1})
    assert reloaded.entries == on_disk


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


def test_the_seed_file_carries_the_amoy_figures():
    assert Path(config.gas_profile_seed_filepath).name == "gas_profile_seed.json"
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "polygon_amoy") == AMOY
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "polygon_amoy_unsafe") == AMOY
    bloxberg = gas_profile.load_seed(config.gas_profile_seed_filepath, "bloxberg_mainnet")
    assert bloxberg == {"dpRequest": 0, "placement": 0, "startedRow": 0}
    assert gas_profile.load_seed(config.gas_profile_seed_filepath, "litvm_liteforge") == {
        "dpRequest": 0, "placement": 0, "startedRow": 0}
    assert gas_profile.load_seed("/nowhere/seed.json", "polygon_amoy") == {}


def test_the_seed_file_names_known_networks():
    seeds = json.loads(Path(config.gas_profile_seed_filepath).read_text())
    assert set(seeds) <= set(config.available_networks())
    for entry in seeds.values():
        assert set(entry) == set(gas_profile.KINDS)
