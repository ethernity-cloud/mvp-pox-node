"""The testnet copies of the mainnets' PoX 1.0 (config.POX10_TESTNETS):
bloxberg_testnet_pox10, bloxberg mainnet's 1.0 contract code on chain 8995
beside bloxberg_mainnet and bloxberg_testnet, and polygon_amoy_pox10, Polygon
mainnet's on Amoy (chain 80002) beside polygon_amoy. node/.env gives each a
block of the variables its mainnet has: the copy's addresses, token name and
trustedzones (and, on Amoy, RPC and chain id), every other value the
mainnet's, NETWORK_TYPE included. A copy is selected when --network names it
or is "all"; "auto", "openbeta" and the legacy names select what they
selected in 3.8.18. The agent runs a copy on the legacy path: a PoX without
pricingVersion(), no validator registry, no CAS session registry, the Enclave
State Registry of bloxberg testnet for bloxberg_testnet_pox10 and none for
polygon_amoy_pox10, createdAt read from the PoX itself (v05) on the bloxberg
copy and from the storage its PoX names (cloud) on the Amoy copy."""
import argparse
import configparser
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import asdict, fields
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import ContractLogicError

import config
import do_request_age
import gas_profile
from models import RequestStatus
from utils import Cache, ListCache, ResourceRefusals

NODE = Path(__file__).resolve().parents[1]
REPOSITORY = NODE.parent
GWEI = 10**9
DAY = 86400
# The latest block's timestamp of the recorded chains: 2026-11-21T12:00:00Z.
NOW = 1795262400
HEAD = 30_900_000
NODE_ADDRESS = Web3.to_checksum_address("0x" + "11" * 20)
CLIENT = Web3.to_checksum_address("0x" + "22" * 20)
DP_REQUEST = 7

# The variables of each copy's block in node/.env, without the prefix.
BLOCKS = {
    "BLOXBERG_TESTNET_POX10": {
        "IMAGE_REGISTRY_CONTRACT_ADDRESS": "0x7dBC1b591cc3b7136D863bc7430C6Db50E929665",
        "CONTRACT_ADDRESS": "0x69A7bEB5Da2d3dA711d52E491D71e51296b08745",
        "HEARTBEAT_CONTRACT_ADDRESS": "0x4426C87484038F9B720a6012A1191DA73bDa0ad3",
        "TOKEN_NAME": "tE10",
        "NETWORK_TYPE": "MAINNET",
        "RPC_URL": "https://bloxberg.ethernity.cloud",
        "RPC_DELAY": "200",
        "CHAIN_ID": "8995",
        "MIDDLEWARE": "POA",
        "BLOCK_TIME": "5",
        "MINIMUM_GAS_AT_START": "100000000000",
        "GAS_PRICE_MEASURE": "wei",
        "EIP1559": "False",
        "GAS_PRICE": "2000000",
        "GAS_LIMIT": "2000000",
        "MAX_FEE_PER_GAS": "0",
        "MAX_PRIORITY_FEE_PER_GAS": "0",
        "TASK_EXECUTION_PRICE": "1",
        "INTEGRATION_TEST_IMAGES": "etny-pynithy-testnet-pox10",
        "TRUSTEDZONE_IMAGES": "etny-pynithy-testnet-pox10,etny-nodenithy-testnet-pox10",
        "REWARD_TYPE": "1",
        "NETWORK_FEE": "5",
        "ENCLAVE_FEE": "10",
    },
    "POLYGON_AMOY_POX10": {
        "IMAGE_REGISTRY_CONTRACT_ADDRESS": "0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC",
        "CONTRACT_ADDRESS": "0x9Aad82557c84c11C4031382d8Bf2cacF8f256a02",
        "HEARTBEAT_CONTRACT_ADDRESS": "0x937F74419E31c1200e86B6D2439D9f12b3Fa12c9",
        "TOKEN_NAME": "tECLD",
        "NETWORK_TYPE": "MAINNET",
        "RPC_URL": "https://polygon-amoy-bor-rpc.publicnode.com",
        "RPC_DELAY": "200",
        "CHAIN_ID": "80002",
        "MIDDLEWARE": "POA",
        "BLOCK_TIME": "2",
        "MINIMUM_GAS_AT_START": "100000000000000000",
        "GAS_PRICE_MEASURE": "gwei",
        "EIP1559": "True",
        "GAS_PRICE": "0",
        "GAS_LIMIT": "0",
        "MAX_FEE_PER_GAS": "500",
        "MAX_PRIORITY_FEE_PER_GAS": "32",
        "TASK_EXECUTION_PRICE": "1",
        "INTEGRATION_TEST_IMAGES": "ecld-pynithy-amoy-pox10",
        "TRUSTEDZONE_IMAGES": "ecld-pynithy-amoy-pox10,ecld-nodenithy-amoy-pox10",
        "REWARD_TYPE": "1",
        "NETWORK_FEE": "5",
        "ENCLAVE_FEE": "10",
    },
}
# The matching fields of a NetworkConfig, at their defaults on a copy: its
# PoX 1.0 answers no pricingVersion(), so no matching is bound there.
MATCHING_FIELDS = dict(
    match_reader_address="", payout_address="", claim_ahead_blocks=0, claim_spread_nodes=8, claim_stand_limit=2,
    claim_hold_back=False, claim_marked_images=False, tip_keep_percent=10, fee_ceiling_pause_percent=80,
    minimum_score_hold=True, beat_gas_allowance=1200000, matching_page_entries=800, enclave_memory_gib=0)
# The NetworkConfig config.parse_networks gives each copy from node/.env.
LOADED = {
    "bloxberg_testnet_pox10": dict(
        name="bloxberg_testnet_pox10", network_type="MAINNET", rpc_url="https://bloxberg.ethernity.cloud",
        rpc_delay=200, chain_id=8995, block_time=5,
        contract_address="0x69A7bEB5Da2d3dA711d52E491D71e51296b08745",
        heartbeat_contract_address="0x4426C87484038F9B720a6012A1191DA73bDa0ad3",
        image_registry_contract_address="0x7dBC1b591cc3b7136D863bc7430C6Db50E929665",
        token_name="tE10", gas_price_measure="wei", minimum_gas_at_start=100_000_000_000, task_execution_price=1,
        integration_test_images="etny-pynithy-testnet-pox10",
        trustedzone_images="etny-pynithy-testnet-pox10,etny-nodenithy-testnet-pox10",
        eip1559=False, middleware="POA", gas_price=2_000_000, gas_limit=2_000_000, max_priority_fee_per_gas=0,
        max_fee_per_gas=0, reward_type=1, network_fee=5, enclave_fee=10, esr_relay_gas_budget_wei=10**17,
        logs_chunk_blocks=9000, do_request_max_age_seconds=86400, tip_per_cu_hour_wei=0, accept_reserved=True,
        farm_invite="", farm_accept=True, **MATCHING_FIELDS),
    "polygon_amoy_pox10": dict(
        name="polygon_amoy_pox10", network_type="MAINNET", rpc_url="https://polygon-amoy-bor-rpc.publicnode.com",
        rpc_delay=200, chain_id=80002, block_time=2,
        contract_address="0x9Aad82557c84c11C4031382d8Bf2cacF8f256a02",
        heartbeat_contract_address="0x937F74419E31c1200e86B6D2439D9f12b3Fa12c9",
        image_registry_contract_address="0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC",
        token_name="tECLD", gas_price_measure="gwei", minimum_gas_at_start=10**17, task_execution_price=1,
        integration_test_images="ecld-pynithy-amoy-pox10",
        trustedzone_images="ecld-pynithy-amoy-pox10,ecld-nodenithy-amoy-pox10",
        eip1559=True, middleware="POA", gas_price=0, gas_limit=0, max_priority_fee_per_gas=32,
        max_fee_per_gas=500, reward_type=1, network_fee=5, enclave_fee=10, esr_relay_gas_budget_wei=10**17,
        logs_chunk_blocks=9000, do_request_max_age_seconds=86400, tip_per_cu_hour_wei=0, accept_reserved=True,
        farm_invite="", farm_accept=True, **MATCHING_FIELDS),
}
# Each copy's mainnet, and the NetworkConfig fields in which the copy holds a
# value of its own; in every other field it holds the mainnet's.
MAINNET_OF = {"bloxberg_testnet_pox10": "bloxberg_mainnet", "polygon_amoy_pox10": "polygon_mainnet"}
OWN_FIELDS = {
    "bloxberg_testnet_pox10": {"name", "contract_address", "heartbeat_contract_address",
                               "image_registry_contract_address", "token_name", "integration_test_images",
                               "trustedzone_images"},
    "polygon_amoy_pox10": {"name", "contract_address", "heartbeat_contract_address",
                           "image_registry_contract_address", "token_name", "integration_test_images",
                           "trustedzone_images", "rpc_url", "chain_id"},
}
# The storage polygon_amoy_pox10's PoX names with ethStorageAddress().
AMOY_COPY_STORAGE = Web3.to_checksum_address("0x20B9fDBDF3BA99A4FA2e73413AB6f928B73d6734")
# Bloxberg testnet's Enclave State Registry.
BLOXBERG_TESTNET_ESR = "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"

# What 3.8.18 selects for "all", "auto" and no --network.
SERVED_3818 = ["polygon_mainnet", "polygon_amoy", "bloxberg_mainnet", "bloxberg_testnet", "iotex_testnet",
               "ethereum_sepolia", "litvm_liteforge"]
# What "all" and no --network select: each copy after its family's networks.
SERVED_ALL = ["polygon_mainnet", "polygon_amoy", "polygon_amoy_pox10", "bloxberg_mainnet", "bloxberg_testnet",
              "bloxberg_testnet_pox10", "iotex_testnet", "ethereum_sepolia", "litvm_liteforge"]
# The networks 3.8.18 defines, each of which selects itself when named alone.
NETWORKS_3818 = ["polygon_mainnet", "polygon_amoy", "polygon_amoy_unsafe", "bloxberg_mainnet", "bloxberg_testnet",
                 "bloxberg_testnet_unsafe", "iotex_testnet", "ethereum_sepolia", "ethereum_sepolia_unsafe",
                 "litvm_liteforge", "litvm_liteforge_unsafe"]
# (--network words, the networks selected): None for no --network. The rows
# without a copy in either column give what 3.8.18 gives.
SELECTIONS = [
    (None, SERVED_ALL),
    (["all"], SERVED_ALL),
    (["ALL"], SERVED_ALL),
    (["auto"], SERVED_3818),
    (["AUTO"], SERVED_3818),
    (["openbeta"], ["polygon_mainnet", "bloxberg_mainnet"]),
    (["OPENBETA"], ["polygon_mainnet", "bloxberg_mainnet"]),
    (["bloxberg"], ["bloxberg_mainnet"]),
    (["testnet"], ["bloxberg_testnet"]),
    (["polygon"], ["polygon_mainnet"]),
    (["bloxberg_testnet_pox10"], ["bloxberg_testnet_pox10"]),
    (["polygon_amoy_pox10"], ["polygon_amoy_pox10"]),
    (["BLOXBERG_TESTNET_POX10", "POLYGON_AMOY_POX10"], ["bloxberg_testnet_pox10", "polygon_amoy_pox10"]),
    (["bloxberg_mainnet", "bloxberg_testnet", "bloxberg_testnet_pox10"],
     ["bloxberg_mainnet", "bloxberg_testnet", "bloxberg_testnet_pox10"]),
    # An -unsafe network joins the network it is named after; a copy joins none.
    (["polygon_amoy", "polygon_amoy_pox10", "polygon_amoy_unsafe"], ["polygon_amoy", "polygon_amoy_pox10"]),
    (["bloxberg_testnet_unsafe", "bloxberg_testnet_pox10"], ["bloxberg_testnet_unsafe", "bloxberg_testnet_pox10"]),
    (["bloxberg_testnet", "bloxberg_testnet_unsafe"], ["bloxberg_testnet"]),
    (["bloxberg_mainnet", "bloxberg_testnet"], ["bloxberg_mainnet", "bloxberg_testnet"]),
    # "all", "auto" and "openbeta" beside network names select the word's
    # networks alone, "all" before "auto".
    (["all", "bloxberg_testnet_unsafe"], SERVED_ALL),
    (["all", "polygon_amoy_pox10"], SERVED_ALL),
    (["auto", "all"], SERVED_ALL),
    (["auto", "bloxberg_testnet_unsafe"], SERVED_3818),
    (["auto", "bloxberg_testnet_pox10"], SERVED_3818),
    (["openbeta", "bloxberg_testnet"], ["polygon_mainnet", "bloxberg_mainnet"]),
    (["openbeta", "polygon_amoy_pox10"], ["polygon_mainnet", "bloxberg_mainnet"]),
] + [([name], [name]) for name in NETWORKS_3818]
REFUSED = [["bloxberg", "polygon"], ["bloxberg", "bloxberg_testnet_pox10"], ["bloxberg_mainnet_pox10"],
           ["polygon_mainnet_pox10"], ["bloxberg_testnet_pox10_unsafe"], ["pox10"]]

# The program of a process started in node/, where config.py loads .env: it
# prints the Enclave State Registry config.py names for four networks.
ESR_PROGRAM = """
import sys, types, json
sys.modules["dependency_manager"] = types.SimpleNamespace(check_dependencies=lambda: None)
import config
print(json.dumps([config.esr_contract_addresses.get(name) for name in (
    "BLOXBERG_TESTNET_POX10", "BLOXBERG_TESTNET", "BLOXBERG_MAINNET", "POLYGON_AMOY_POX10")]))
"""


class Sent(BaseException):
    """Raised by the recorded send: the loop's first transaction ends it."""


class Stuck(BaseException):
    """Raised at a node's 2000th sleep: its loop reached neither its stop nor
    a send."""


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_all(monkeypatch, networks, environment=None, arguments=()):
    """{name: NetworkConfig} of `networks`, of one family, that
    config.parse_networks gives from the command line's `arguments` and an
    environment holding `environment` and, for the variables it lacks,
    node/.env's."""
    monkeypatch.setattr(os, "environ", dict(environment or {}))
    config.load_dotenv(NODE / ".env")
    family = networks[0].split("_")[0].upper()
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", nargs="+")
    config.add_network_override_arguments(parser, [family])
    parsed = parser.parse_args(["--network", *networks, *arguments])
    return {network.name: network for network in config.parse_networks(parsed, parser, [family])}


def load(monkeypatch, network, environment=None, arguments=()):
    """The NetworkConfig of `network` alone (load_all)."""
    return load_all(monkeypatch, [network], environment, arguments)[network]


def selected(monkeypatch, words):
    """The names config.parse_networks selects for the agent's arguments -k
    and, unless `words` is None, -n `words`, from node/.env."""
    monkeypatch.setattr(os, "environ", {})
    config.load_dotenv(NODE / ".env")
    families = list(config.NETWORKS.keys())
    parser = config.parse_arguments(families)
    arguments, _ = parser.parse_known_args(["-k", "none"] + ([] if words is None else ["-n", *words]))
    return [network.name for network in config.parse_networks(arguments, parser, families)]


def test_the_release_file_holds_exactly_these_variables_for_each_copy():
    file = dotenv_values(NODE / ".env")
    for prefix, block in BLOCKS.items():
        held = {name[len(prefix) + 1:]: value for name, value in file.items() if name.startswith(prefix + "_")}
        assert held == block, prefix
    # No other variable of the file names a copy.
    assert sorted(name for name in file if "POX10" in name) == sorted(
        f"{prefix}_{name}" for prefix, block in BLOCKS.items() for name in block)


def test_a_block_holds_the_variables_of_its_mainnets_block():
    file = dotenv_values(NODE / ".env")
    for prefix, mainnet in (("BLOXBERG_TESTNET_POX10", "BLOXBERG_MAINNET"), ("POLYGON_AMOY_POX10", "POLYGON_MAINNET")):
        assert set(BLOCKS[prefix]) == {name[len(mainnet) + 1:] for name in file if name.startswith(mainnet + "_")}


@pytest.mark.parametrize("network", config.POX10_TESTNETS)
def test_a_copy_loads_with_its_values(monkeypatch, network):
    assert asdict(load(monkeypatch, network)) == LOADED[network]


@pytest.mark.parametrize("network", config.POX10_TESTNETS)
def test_a_copy_holds_its_mainnets_value_in_every_field_but_its_own(monkeypatch, network):
    copy, mainnet = load(monkeypatch, network), load(monkeypatch, MAINNET_OF[network])
    differing = {f.name for f in fields(config.NetworkConfig) if getattr(copy, f.name) != getattr(mainnet, f.name)}
    assert differing == OWN_FIELDS[network]
    assert copy.network_type == mainnet.network_type == "MAINNET"


@pytest.mark.parametrize("copy, beside", [("bloxberg_testnet_pox10", "bloxberg_testnet"),
                                          ("polygon_amoy_pox10", "polygon_amoy")])
def test_a_copys_variables_and_arguments_set_the_copy_alone(monkeypatch, copy, beside):
    """<PREFIX>_<FIELD> and --<network>_<field> of a copy, whose prefix
    starts with that of the network it stands beside, set the copy's field
    and leave that network's; that network's set its own alone."""
    plain = load_all(monkeypatch, [beside, copy])
    delays = {"bloxberg_testnet": 0, "polygon_amoy": 200}
    assert (plain[beside].rpc_delay, plain[copy].rpc_delay) == (delays[beside], 200)
    by_variable = load_all(monkeypatch, [beside, copy], {f"{copy.upper()}_RPC_DELAY": "50"})
    assert (by_variable[beside], by_variable[copy].rpc_delay) == (plain[beside], 50)
    by_variable = load_all(monkeypatch, [beside, copy], {f"{beside.upper()}_RPC_DELAY": "70"})
    assert (by_variable[beside].rpc_delay, by_variable[copy]) == (70, plain[copy])
    by_argument = load_all(monkeypatch, [beside, copy], arguments=(f"--{copy}_rpc_delay", "50"))
    assert (by_argument[beside], by_argument[copy].rpc_delay) == (plain[beside], 50)
    by_argument = load_all(monkeypatch, [beside, copy], arguments=(f"--{beside}_rpc_delay", "70"))
    assert (by_argument[beside].rpc_delay, by_argument[copy]) == (70, plain[copy])


def test_the_copies_are_networks_of_their_own():
    assert config.POX10_TESTNETS == ("bloxberg_testnet_pox10", "polygon_amoy_pox10")
    assert config.available_networks() == [
        "polygon_mainnet", "polygon_amoy", "polygon_amoy_unsafe", "polygon_amoy_pox10", "bloxberg_mainnet",
        "bloxberg_testnet", "bloxberg_testnet_unsafe", "bloxberg_testnet_pox10", "iotex_testnet", "ethereum_sepolia",
        "ethereum_sepolia_unsafe", "litvm_liteforge", "litvm_liteforge_unsafe"]
    for name in config.available_networks():
        assert config.is_pox10_testnet(name) == (name in config.POX10_TESTNETS), name
    assert config.is_pox10_testnet("BLOXBERG_TESTNET_POX10")
    for name in config.POX10_TESTNETS:
        assert not config.is_unsafe_network(name)
        assert config.unsafe_twin(name) is None


def test_no_other_network_holds_a_copys_contracts_or_trustedzones(monkeypatch):
    networks = {name: load(monkeypatch, name) for name in config.available_networks()}
    for name in config.POX10_TESTNETS:
        copy = networks[name]
        names = set(config.trustedzone_requirements(copy.trustedzone_images))
        for field in ("contract_address", "heartbeat_contract_address", "image_registry_contract_address"):
            assert Web3.is_checksum_address(getattr(copy, field)), field
        for other_name, other in networks.items():
            if other_name == name:
                continue
            assert not names & set(config.trustedzone_requirements(other.trustedzone_images)), other_name
            if other.chain_id != copy.chain_id:
                continue
            for field in ("contract_address", "heartbeat_contract_address", "image_registry_contract_address"):
                assert getattr(other, field).lower() != getattr(copy, field).lower(), (other_name, field)


@pytest.mark.parametrize("network, tested, untested", [
    ("bloxberg_testnet_pox10", "etny-pynithy-testnet-pox10", "etny-nodenithy-testnet-pox10"),
    ("polygon_amoy_pox10", "ecld-pynithy-amoy-pox10", "ecld-nodenithy-amoy-pox10"),
])
def test_a_copys_trustedzones_require_dcap_and_its_pynithy_one_is_tested(monkeypatch, network, tested, untested):
    copy = load(monkeypatch, network)
    assert config.trustedzone_requirements(copy.trustedzone_images) == {tested: "dcap", untested: "dcap"}
    assert config.integration_test_images(copy) == [tested]
    config.check_trustedzone_names(copy)


@pytest.mark.parametrize("words, networks", SELECTIONS, ids=lambda value: " ".join(value) if value else "none")
def test_a_selection_selects(monkeypatch, words, networks):
    assert selected(monkeypatch, words) == networks


@pytest.mark.parametrize("words", REFUSED, ids=" ".join)
def test_a_selection_naming_no_network_is_refused(monkeypatch, words):
    with pytest.raises(SystemExit):
        selected(monkeypatch, words)


def test_auto_and_the_shorthands_select_no_copy_and_all_adds_the_two(monkeypatch):
    assert selected(monkeypatch, ["auto"]) == SERVED_3818
    assert [name for name in selected(monkeypatch, ["all"]) if name not in config.POX10_TESTNETS] == SERVED_3818
    assert set(selected(monkeypatch, ["all"])) - set(SERVED_3818) == set(config.POX10_TESTNETS)
    for word in ("auto", "openbeta", "bloxberg", "testnet", "polygon"):
        assert not set(selected(monkeypatch, [word])) & set(config.POX10_TESTNETS), word


def test_a_copy_has_no_validator_set_and_the_bloxberg_one_bloxberg_testnets_state_registry():
    for table in (config.cas_session_registry_addresses, config.validator_registry_addresses,
                  config.ipfs_endpoints_addresses):
        assert "BLOXBERG_TESTNET_POX10" not in table and "POLYGON_AMOY_POX10" not in table
    assert config.esr_contract_addresses["BLOXBERG_TESTNET_POX10"] == BLOXBERG_TESTNET_ESR
    assert config.esr_contract_addresses["BLOXBERG_TESTNET"] == BLOXBERG_TESTNET_ESR
    assert "POLYGON_AMOY_POX10" not in config.esr_contract_addresses
    assert "POLYGON_MAINNET" not in config.esr_contract_addresses
    assert "BLOXBERG_TESTNET_POX10" not in config.network_field_defaults
    assert "POLYGON_AMOY_POX10" not in config.network_field_defaults


def test_the_bloxberg_copys_state_registry_has_a_variable_of_its_own():
    def started(variables):
        environment = {name: value for name, value in os.environ.items() if "ESR_CONTRACT_ADDRESS" not in name}
        environment.update(variables)
        done = subprocess.run([sys.executable, "-c", ESR_PROGRAM], cwd=NODE, env=environment, capture_output=True,
                              text=True)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    mainnet = "0x4Bf5cDE3BFD73dd10B707f8B123Ba631D2EBEAD2"
    other = "0x" + "ab" * 20
    assert started({}) == [BLOXBERG_TESTNET_ESR, BLOXBERG_TESTNET_ESR, mainnet, None]
    assert started({"BLOXBERG_TESTNET_POX10_ESR_CONTRACT_ADDRESS": other}) == [
        other, BLOXBERG_TESTNET_ESR, mainnet, None]
    # ESR_CONTRACT_ADDRESS moves the bloxberg networks that read it, not the copy.
    assert started({"ESR_CONTRACT_ADDRESS": other}) == [BLOXBERG_TESTNET_ESR, other, other, None]


def test_the_seed_file_gives_a_copy_no_gas_figure():
    empty = {"dpRequest": 0, "placement": 0, "startedRow": 0}
    seeds = json.loads(Path(config.gas_profile_seed_filepath).read_text())
    assert seeds["bloxberg_testnet_pox10"] == seeds["bloxberg_mainnet"] == empty
    assert "polygon_amoy_pox10" not in seeds and "polygon_mainnet" not in seeds
    for name in config.POX10_TESTNETS:
        assert gas_profile.load_seed(config.gas_profile_seed_filepath, name) == empty


def test_the_installers_list_names_each_copy_with_the_release_files_rpc_and_gas():
    listed = configparser.ConfigParser(interpolation=None)
    listed.read(REPOSITORY / "networks.ini", encoding="utf-8")
    file = dotenv_values(NODE / ".env")
    entries = {listed[section]["shortname"]: (section, listed[section]) for section in listed.sections()}
    assert {shortname.lower() for shortname in entries} <= set(config.available_networks())
    assert (entries["BLOXBERG_TESTNET_POX10"][0], entries["POLYGON_AMOY_POX10"][0]) == ("11", "12")
    for shortname in ("BLOXBERG_TESTNET_POX10", "POLYGON_AMOY_POX10"):
        _, entry = entries[shortname]
        assert entry["RPC_URL"] == file[f"{shortname}_RPC_URL"]
        assert entry["MINIMUM_GAS_AT_START"] == file[f"{shortname}_MINIMUM_GAS_AT_START"]
        # The installer cuts a line at ';' or '#' and passes it through
        # xargs, which refuses an apostrophe.
        for key in ("name", "description"):
            assert not set(entry[key]) & set(";#'\""), (shortname, key)
    # The installer's AUTO leaves out the shortnames its case names.
    installer = (REPOSITORY / "etny-node-installer.sh").read_text(encoding="utf-8")
    assert "*_UNSAFE|BLOXBERG_TESTNET_POX10|POLYGON_AMOY_POX10) ;;" in installer


def bare_node(agent, network, eth):
    """An EtnyPoXNode of `network` (a NetworkConfig) built without __init__
    over the web3 eth namespace `eth`: no RPC, no key."""
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("pox10-testnets")
    node._EtnyPoXNode__network_config = network
    node._EtnyPoXNode__network = network.name
    node._EtnyPoXNode__address = NODE_ADDRESS
    node._EtnyPoXNode__acct = SimpleNamespace(address=NODE_ADDRESS)
    node._EtnyPoXNode__started_row_pending = None
    node._EtnyPoXNode__w3 = SimpleNamespace(eth=eth, to_wei=Web3.to_wei, from_wei=Web3.from_wei)
    return node


def test_the_bloxberg_copys_transaction_is_bloxbergs(agent, monkeypatch):
    eth = SimpleNamespace(get_block=lambda block: SimpleNamespace(baseFeePerGas=None),
                          get_transaction_count=lambda address: 7)
    built = bare_node(agent, load(monkeypatch, "bloxberg_testnet_pox10"), eth).get_transaction_build()
    assert built == {"nonce": 7, "chainId": 8995, "from": NODE_ADDRESS, "gasPrice": 2_000_000, "gas": 2_000_000}


def test_the_amoy_copys_fee_ceiling_is_polygon_mainnets(agent, monkeypatch):
    def at(base_fee):
        eth = SimpleNamespace(get_block=lambda block: SimpleNamespace(baseFeePerGas=base_fee),
                              get_transaction_count=lambda address: 7)
        return bare_node(agent, load(monkeypatch, "polygon_amoy_pox10"), eth).get_transaction_build()

    assert at(30 * GWEI) == {"type": "0x2", "nonce": 7, "chainId": 80002, "from": NODE_ADDRESS,
                             "maxFeePerGas": 65 * GWEI, "maxPriorityFeePerGas": 32 * GWEI}
    assert at(425_454_545_455)["maxFeePerGas"] == 500 * GWEI
    with pytest.raises(Exception, match="Network base fee is too high!"):
        at(425_454_545_456)


@pytest.mark.parametrize("network", config.POX10_TESTNETS)
def test_a_copys_placements_take_turns_as_its_mainnets(agent, monkeypatch, network):
    def places(name, nodes, block):
        """Whether a node of `name` places DO request 4 with its DP request
        DP_REQUEST at block `block`, `nodes` nodes registered with the
        heartbeat."""
        node = bare_node(agent, load(monkeypatch, name), SimpleNamespace(block_number=block))
        node._EtnyPoXNode__total_nodes_count = nodes
        node._EtnyPoXNode__is_first_cycle = defaultdict(lambda: True)
        return node._EtnyPoXNode__can_place_order(DP_REQUEST, 4)

    # Fewer than 50 registered nodes: a cycle of one block, every block the
    # node's.
    for nodes in (0, 1, 49):
        assert places(network, nodes, 100) and places(network, nodes, 101)
    # From 50 on the cycle is nodes // 25 blocks, as on the copy's mainnet;
    # a network of NETWORK_TYPE TESTNET has no cycle.
    for name in (network, MAINNET_OF[network]):
        assert (places(name, 50, 100), places(name, 50, 101)) == (False, True)
    assert places("bloxberg_testnet", 50, 100) and places("polygon_amoy", 50, 100)


@pytest.mark.parametrize("network, token", [("bloxberg_testnet_pox10", "tE10"), ("polygon_amoy_pox10", "tECLD")])
def test_a_copys_reward_is_logged_in_its_token_with_the_mainnets_payout_line(agent, monkeypatch, caplog, network,
                                                                               token):
    caplog.set_level(logging.INFO)
    node = bare_node(agent, load(monkeypatch, network), SimpleNamespace())
    node._EtnyPoXNode__reader = None
    # DO request 1: 60 minutes at a price of 3.
    node._EtnyPoXNode__etny = SimpleNamespace(caller=lambda: SimpleNamespace(
        _getDORequest=lambda request: (CLIENT, 1, 1, 1, 1, 60, 3, RequestStatus.BOOKED)))
    node._getOrder = lambda: [5, SimpleNamespace(do_req=1)]
    node.calculate_reward()
    # Reward type 1: the price less the network's 5 % and the enclaves' 10 %.
    assert f"Reward: 153.0 {token}. You’ve earned it. " in caplog.messages
    assert f"HODL your {token} for long-term growth. Payout after validation." in caplog.messages


class Chain:
    """A legacy PoX of DO requests, each AVAILABLE, unpinned, created at
    `created` (createdAt by id) and naming the trustedzone `trustedzones`
    gives its id; laid out as `layout`, 'cloud' in the storage `storage`
    that ethStorageAddress() names or 'v05' in the PoX `pox` itself, which
    has no ethStorageAddress(); this node's AVAILABLE DP request; one node
    registered with the heartbeat contract; the latest block HEAD at NOW,
    its base fee 30 gwei. It stands for the PoX contract, the heartbeat
    contract and web3's eth namespace; each read appends its name to
    `events` and each placement's transaction fields are appended to
    `built`."""
    LAYOUTS = {"cloud": (3, 10, 1, 0), "v05": (19, 9, 0, 224)}

    def __init__(self, pox, layout, storage, created, trustedzones):
        self.events = []
        self.built = []
        self.address = pox
        self.layout = layout
        self.storage = storage
        self.trustedzones = trustedzones
        self.holder = storage if layout == "cloud" else pox
        length_slot, size, field, shift = self.LAYOUTS[layout]
        start = int.from_bytes(keccak(length_slot.to_bytes(32, "big")), "big")
        self.words = {length_slot: len(created)}
        for request_id, timestamp in enumerate(created):
            self.words[start + request_id * size + field] = timestamp << shift
        self.functions = SimpleNamespace(
            _getMyDOOrders=lambda: SimpleNamespace(call=lambda transaction: self.read("_getMyDOOrders", [])),
            _placeOrder=self.placement,
            ethStorageAddress=lambda: SimpleNamespace(call=self.storage_address))

    def read(self, name, answer):
        self.events.append(name)
        return answer

    def caller(self):
        return self

    def storage_address(self):
        if self.layout == "cloud":
            return self.read("ethStorageAddress", self.storage)
        self.read("ethStorageAddress", None)
        raise ContractLogicError("execution reverted")

    def get_storage_at(self, address, slot):
        word = self.words.get(slot, 0) if address == self.holder else 0
        return self.read(f"eth_getStorageAt({address})", word.to_bytes(32, "big"))

    @property
    def block_number(self):
        return self.read("eth_blockNumber", HEAD)

    def get_block(self, block):
        return self.read("eth_getBlockByNumber", SimpleNamespace(number=HEAD, timestamp=NOW,
                                                                 baseFeePerGas=30 * GWEI))

    def get_balance(self, address):
        return self.read("eth_getBalance", 10**18)

    def get_transaction_count(self, address):
        return self.read("eth_getTransactionCount", 3)

    def _getDORequestsCount(self):
        return self.read("_getDORequestsCount", len(self.trustedzones))

    def _getDORequest(self, request):
        return self.read(f"_getDORequest({request})", (CLIENT, 1, 1, 1, 1, 60, 3, RequestStatus.AVAILABLE))

    def _getDORequestMetadata(self, request):
        image = f"v3:QmImage:{self.trustedzones[request]}:QmCompose:QmChallenge:certificate"
        return self.read(f"_getDORequestMetadata({request})", (CLIENT, image, "", "", ""))

    def _getDPRequest(self, request):
        return self.read(f"_getDPRequest({request})", (NODE_ADDRESS, 1, 1, 1, 1, 60, 3, RequestStatus.AVAILABLE))

    def placement(self, do_request, dp_request):
        name = f"_placeOrder({do_request}, {dp_request})"

        def build(fields):
            self.built.append(fields)
            return self.read(f"{name} built", fields)

        return SimpleNamespace(call=lambda transaction: self.read(f"{name} simulated", None), build_transaction=build)

    def getNodesCount(self):
        return self.read("getNodesCount", 1)


def serving(agent, monkeypatch, directory, network, chain):
    """An EtnyPoXNode of `network` whose process_dp_request serves `chain`
    with its standing DP request DP_REQUEST, working in `directory` with an
    empty doreq_cache.txt: the heartbeat and the IPFS upkeep done now, the
    integration tests skipped, no SGX driver conflict, no sleep slept; a
    send is appended to the chain's events as 'sent' and raises Sent."""
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2000:
            raise Stuck(f"the last events: {chain.events[-12:]}")

    monkeypatch.setattr(agent, "time", SimpleNamespace(sleep=sleep, time=time.time, monotonic=time.monotonic))
    monkeypatch.setattr(agent, "stop_event", threading.Event())
    monkeypatch.setattr(agent, "acquire_task_slot", lambda name: None)
    monkeypatch.setattr(agent, "release_task_slot", lambda name: None)
    monkeypatch.setattr(agent, "do_request_age_logged", {})
    monkeypatch.setattr(agent.config, "skip_integration_test", True)
    monkeypatch.chdir(directory)
    (directory / "heartbeat.etny").write_text(str(int(time.time())))
    node = bare_node(agent, network, chain)
    node._EtnyPoXNode__etny = chain
    node._EtnyPoXNode__heart_beat = chain
    node.cache_config = SimpleNamespace(heart_beat_log_file_path=directory / "heartbeat.etny",
                                        accepted_handover_filepath=directory / "accepted.json")
    node.storage = SimpleNamespace(connect=lambda attempts: True, repo_gc=lambda: None)
    node.orders_cache = Cache(1000, directory / "orders_cache.txt")
    node.doreq_cache = ListCache(1000, directory / "doreq_cache.txt")
    node.resource_refusals = ResourceRefusals(1000, directory / "doreq_resources.json")
    node.merged_orders_cache = SimpleNamespace(add=lambda **record: None, rem=lambda **record: None)
    node._EtnyPoXNode__dprequest = DP_REQUEST
    node._EtnyPoXNode__orders = {}
    node._EtnyPoXNode__is_first_cycle = defaultdict(lambda: True)
    node._EtnyPoXNode__replication_only = False
    node._EtnyPoXNode__trustedzones = config.trustedzone_requirements(network.trustedzone_images)
    node._EtnyPoXNode__integration_images = config.integration_test_images(network)
    node._EtnyPoXNode__pox_is_20 = False
    node._EtnyPoXNode__reader = None
    node._EtnyPoXNode__broker = None
    node._EtnyPoXNode__do_requests_build_pending = False
    node._EtnyPoXNode__last_ipfs_cleanup_at = time.time()
    node._EtnyPoXNode__last_ipfs_peer_sync_at = time.time()
    node._EtnyPoXNode__do_request_ages = None
    node._EtnyPoXNode__do_request_boundary = 0

    def send(transaction):
        chain.events.append("sent")
        raise Sent

    node.send_transaction = send
    node._check_installed_drivers = lambda: False
    return node


@pytest.mark.parametrize("network, layout, mainnets_trustedzone, own_trustedzone", [
    ("bloxberg_testnet_pox10", "v05", "etny-pynithy", "etny-nodenithy-testnet-pox10"),
    ("polygon_amoy_pox10", "cloud", "ecld-pynithy", "ecld-pynithy-amoy-pox10"),
])
def test_a_node_of_a_copy_places_the_request_naming_its_trustedzone_from_the_copys_storage(
        agent, monkeypatch, tmp_path, caplog, network, layout, mainnets_trustedzone, own_trustedzone):
    caplog.set_level(logging.INFO)
    copy = load(monkeypatch, network)
    pox = Web3.to_checksum_address(copy.contract_address)
    # Request 0 is older than the limit, request 1 names one of the copy's
    # trustedzones, request 2 the mainnet's; the loop judges the newest first.
    chain = Chain(pox, layout, AMOY_COPY_STORAGE, [NOW - 2 * DAY, NOW - 3600, NOW - 60],
                  ["x", own_trustedzone, mainnets_trustedzone])
    node = serving(agent, monkeypatch, tmp_path, copy, chain)
    with pytest.raises(Sent):
        node.process_dp_request()
    holder = pox if layout == "v05" else AMOY_COPY_STORAGE
    layouts = {"v05": do_request_age.V05, "cloud": do_request_age.CLOUD}
    assert node._EtnyPoXNode__do_request_ages == (holder, layouts[layout])
    assert f"DO request ages: createdAt is read from {holder} (layout {layout})" in caplog.messages
    # Every storage word is read from the copy's own holder.
    assert {event for event in chain.events if event.startswith("eth_getStorageAt")} == {f"eth_getStorageAt({holder})"}
    assert node._EtnyPoXNode__do_request_boundary == 1
    assert f"Skipping DO Request 2: '{mainnets_trustedzone}' is not a trustedzone of this network" in caplog.messages
    assert not any(event.startswith(("_getDORequest(0)", "_placeOrder(0", "_placeOrder(2")) for event in chain.events)
    # The placement of request 1 is the loop's one transaction, on the copy's
    # chain.
    assert chain.events[-2:] == [f"_placeOrder(1, {DP_REQUEST}) built", "sent"]
    assert len(chain.built) == 1
    assert chain.built[0]["chainId"] == copy.chain_id
