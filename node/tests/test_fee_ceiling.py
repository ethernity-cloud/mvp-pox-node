"""The fee ceiling of an EIP-1559 network (config.NetworkConfig
max_fee_per_gas, <NETWORK>_MAX_FEE_PER_GAS in the network's
GAS_PRICE_MEASURE): etny-node.py get_transaction_build, given no fee fields
of an order, gives a transaction maxFeePerGas int(baseFee * 1.1) plus the
network's priority fee and raises "Network base fee is too high!" above the
ceiling, not at it; a network without EIP-1559 is built at its gas price
whatever its ceiling. node/.env, the release's defaults, gives
polygon_mainnet 500 gwei over a priority fee of 32 gwei: 308.1 gwei at a
base fee of 251 gwei, and a base fee up to 425.4 gwei is admitted. A
variable the process's environment carries stands before the file's (config
loads node/.env with load_dotenv, which sets the variables the environment
lacks), and the --<network>_max_fee_per_gas argument before both
(config.parse_networks); a variable or an argument that is no integer is
refused."""
import argparse
import importlib.util
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from web3 import Web3

import config

NODE = Path(__file__).resolve().parents[1]
GWEI = 10**9
VARIABLE = "POLYGON_MAINNET_MAX_FEE_PER_GAS"
ARGUMENT = "--polygon_mainnet_max_fee_per_gas"
ADDRESS = "0x" + "11" * 20
# <NETWORK>_MAX_FEE_PER_GAS of node/.env, each in its network's
# GAS_PRICE_MEASURE.
CEILINGS = {
    "polygon_mainnet": 500,
    "polygon_amoy": 64,
    "polygon_amoy_unsafe": 64,
    "bloxberg_mainnet": 0,
    "bloxberg_testnet": 0,
    "bloxberg_testnet_unsafe": 0,
    "iotex_testnet": 1500,
    "ethereum_sepolia": 30,
    "ethereum_sepolia_unsafe": 30,
    "litvm_liteforge": 2,
    "litvm_liteforge_unsafe": 2,
}
# The lines of etny-node.py's entry point that load the networks, after
# conftest.py's stand-in for dependency_manager: the program of a process
# started in node/ with the agent's arguments, which prints the ceiling of
# the one network they name.
START = """
import sys, types
sys.modules["dependency_manager"] = types.SimpleNamespace(check_dependencies=lambda: None)
import config
network_names = list(config.NETWORKS.keys())
parser = config.parse_arguments(network_names)
args, unknown_args = parser.parse_known_args()
(network,) = config.parse_networks(args, parser, network_names)
print(network.max_fee_per_gas)
"""


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(monkeypatch, network, environment=None, arguments=()):
    """The NetworkConfig of `network` that config.parse_networks gives from
    the command line's `arguments` and an environment holding the variables
    `environment` and, for the variables it lacks, node/.env's: the helper
    loads the file into it with load_dotenv, called without override as
    config.py calls it at import (started runs config.py's own call)."""
    monkeypatch.setattr(os, "environ", dict(environment or {}))
    config.load_dotenv(NODE / ".env")
    family = network.split("_")[0].upper()
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", nargs="+")
    config.add_network_override_arguments(parser, [family])
    parsed = parser.parse_args(["--network", network, *arguments])
    (loaded,) = config.parse_networks(parsed, parser, [family])
    return loaded


def started(variable=None):
    """polygon_mainnet's ceiling in a process started in node/, where
    config.py's own load of .env runs: the process runs START with the
    agent's arguments -k (required; the text given is no key) and -n, in
    this process's environment with POLYGON_MAINNET_MAX_FEE_PER_GAS at
    `variable`, without the variable for None."""
    environment = {name: value for name, value in os.environ.items() if name != VARIABLE}
    if variable is not None:
        environment[VARIABLE] = variable
    done = subprocess.run([sys.executable, "-c", START, "-k", "none", "-n", "polygon_mainnet"], cwd=NODE,
                          env=environment, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return int(done.stdout)


def node_at(agent, network, base_fee):
    """An EtnyPoXNode of `network` (a NetworkConfig) built without __init__,
    whose wallet has sent 7 transactions and whose chain's latest block
    carries `base_fee` wei (None: a block without a base fee): no RPC, no
    key."""
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__network_config = network
    node._EtnyPoXNode__started_row_pending = None
    node._EtnyPoXNode__address = ADDRESS
    node._EtnyPoXNode__acct = SimpleNamespace(address=ADDRESS)
    node._EtnyPoXNode__w3 = SimpleNamespace(
        eth=SimpleNamespace(get_block=lambda block: SimpleNamespace(baseFeePerGas=base_fee),
                            get_transaction_count=lambda address: 7),
        to_wei=Web3.to_wei, from_wei=Web3.from_wei)
    return node


def test_the_releases_file_gives_each_network_its_ceiling(monkeypatch):
    ceilings = {name: load(monkeypatch, name).max_fee_per_gas for name in config.available_networks()}
    assert ceilings == CEILINGS


def test_polygon_mainnets_ceiling_is_500_gwei_over_a_priority_fee_of_32(monkeypatch):
    network = load(monkeypatch, "polygon_mainnet")
    assert (network.eip1559, network.gas_price_measure) == (True, "gwei")
    assert (network.max_fee_per_gas, network.max_priority_fee_per_gas) == (500, 32)


def test_a_variable_of_the_environment_stands_before_the_files(monkeypatch):
    ceilings = [load(monkeypatch, "polygon_mainnet", environment).max_fee_per_gas
                for environment in ({}, {VARIABLE: "350"})]
    assert ceilings == [500, 350]
    # A variable that is no integer is refused: the file's 500 does not stand
    # in for it.
    for text in ("abc", "", "500.5"):
        with pytest.raises(EnvironmentError, match="Invalid integer value for POLYGON_MAINNET_MAX_FEE_PER_GAS"):
            load(monkeypatch, "polygon_mainnet", {VARIABLE: text})


def test_the_argument_stands_before_the_environment_and_the_file(monkeypatch):
    ceilings = [load(monkeypatch, "polygon_mainnet", environment, arguments).max_fee_per_gas
                for environment, arguments in (({}, ()), ({}, (ARGUMENT, "650")),
                                               ({VARIABLE: "350"}, (ARGUMENT, "650")))]
    assert ceilings == [500, 650, 650]
    # An argument that is no integer ends the parse: neither the
    # environment's 350 nor the file's 500 stands in for it.
    with pytest.raises(SystemExit):
        load(monkeypatch, "polygon_mainnet", {VARIABLE: "350"}, (ARGUMENT, "abc"))


def test_a_process_started_in_node_loads_the_files_ceiling_and_the_environments_before_it():
    assert [started(), started("350")] == [500, 350]


def test_at_a_base_fee_of_251_gwei_the_transaction_is_built_at_308_1_gwei(agent, monkeypatch):
    built = node_at(agent, load(monkeypatch, "polygon_mainnet"), 251 * GWEI).get_transaction_build()
    assert built == {"type": "0x2", "nonce": 7, "chainId": 137, "from": ADDRESS,
                     "maxFeePerGas": 308_100_000_000, "maxPriorityFeePerGas": 32 * GWEI}


def test_a_base_fee_up_to_425_4_gwei_is_admitted(agent, monkeypatch):
    network = load(monkeypatch, "polygon_mainnet")
    admitted = node_at(agent, network, 425_400_000_000).get_transaction_build()
    assert admitted["maxFeePerGas"] == 499_940_000_000
    with pytest.raises(Exception, match="Network base fee is too high!"):
        node_at(agent, network, 425_500_000_000).get_transaction_build()


def test_the_ceiling_refuses_a_fee_above_it_on_an_eip1559_network_alone(agent, monkeypatch):
    polygon = load(monkeypatch, "polygon_mainnet")
    assert node_at(agent, polygon, 425_454_545_455).get_transaction_build()["maxFeePerGas"] == 500 * GWEI
    with pytest.raises(Exception, match="Network base fee is too high!"):
        node_at(agent, polygon, 425_454_545_456).get_transaction_build()
    bloxberg = load(monkeypatch, "bloxberg_mainnet")
    assert (bloxberg.eip1559, bloxberg.max_fee_per_gas) == (False, 0)
    assert node_at(agent, bloxberg, None).get_transaction_build() == {
        "nonce": 7, "chainId": 8995, "from": ADDRESS, "gasPrice": 2_000_000, "gas": 2_000_000}
