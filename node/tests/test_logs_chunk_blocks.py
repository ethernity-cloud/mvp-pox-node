"""config.NetworkConfig.logs_chunk_blocks: the widest eth_getLogs block
range one scan of the agent requests from a network's RPC. 9000 without
<NETWORK>_LOGS_CHUNK_BLOCKS, which sets it (the --<network>_logs_chunk_blocks
argument too); litvm_liteforge and litvm_liteforge_unsafe take
config.network_field_defaults' 3000 without the variable (the LiteForge RPC,
rpc.testnet.liteforge.zeeve.net, refuses a range of more than 3000 blocks);
a value below 1 block or no integer is refused."""
import argparse
from dataclasses import MISSING, fields

import pytest

import config


def required_values(network):
    """{<PREFIX>_<FIELD>: value} for every field without a default, as
    parse_networks reads them from the environment; an -unsafe network's
    trustedzone carries the -unsafe suffix and the sgx requirement."""
    prefix = network.upper()
    if config.is_unsafe_network(network):
        trustedzone = "ecld-pynithy-litvm-testnet-unsafe:sgx"
    else:
        trustedzone = "etny-pynithy-testnet:dcap"
    values = {}
    for f in fields(config.NetworkConfig):
        if f.default is not MISSING or f.name == "name":
            continue
        if f.type == int:
            value = "1"
        elif f.type == bool:
            value = "0"
        elif f.name == "trustedzone_images":
            value = trustedzone
        elif f.name == "integration_test_images":
            value = trustedzone.split(":")[0]
        elif f.name == "middleware":
            value = "geth_poa"
        else:
            value = "x"
        values[f"{prefix}_{f.name.upper()}"] = value
    return values


def load(monkeypatch, network, extra=None, arguments=()):
    """The NetworkConfig of `network` loaded from the required values, the
    `extra` variables and the command-line `arguments`; every other
    defaulted field's variable is unset."""
    prefix = network.upper()
    for name, value in {**required_values(network), **(extra or {})}.items():
        monkeypatch.setenv(name, value)
    for f in fields(config.NetworkConfig):
        if f.default is not MISSING and f"{prefix}_{f.name.upper()}" not in (extra or {}):
            monkeypatch.delenv(f"{prefix}_{f.name.upper()}", raising=False)
    family = network.split("_")[0].upper()
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", nargs="+")
    config.add_network_override_arguments(parser, [family])
    parsed = parser.parse_args(["--network", network, *arguments])
    (loaded,) = config.parse_networks(parsed, parser, [family])
    return loaded


def test_the_field_from_the_required_values_alone_is_9000():
    network = config.NetworkConfig(**{f.name: "" for f in fields(config.NetworkConfig) if f.default is MISSING})
    assert network.logs_chunk_blocks == 9000
    assert config.logs_chunk_blocks_default == 9000


def test_a_network_without_the_variable_takes_9000(monkeypatch):
    assert load(monkeypatch, "bloxberg_testnet").logs_chunk_blocks == 9000


def test_the_variable_sets_the_field(monkeypatch):
    network = load(monkeypatch, "bloxberg_testnet", {"BLOXBERG_TESTNET_LOGS_CHUNK_BLOCKS": "2000"})
    assert network.logs_chunk_blocks == 2000


@pytest.mark.parametrize("network", ["litvm_liteforge", "litvm_liteforge_unsafe"])
def test_the_litvm_pair_takes_3000_without_the_variable(monkeypatch, network):
    assert load(monkeypatch, network).logs_chunk_blocks == 3000
    assert config.network_field_defaults[network.upper()] == {"logs_chunk_blocks": 3000}


def test_the_litvm_variable_overrides_the_pairs_default(monkeypatch):
    network = load(monkeypatch, "litvm_liteforge", {"LITVM_LITEFORGE_LOGS_CHUNK_BLOCKS": "1500"})
    assert network.logs_chunk_blocks == 1500


def test_the_command_line_argument_sets_the_field(monkeypatch):
    network = load(monkeypatch, "litvm_liteforge", arguments=["--litvm_liteforge_logs_chunk_blocks", "1200"])
    assert network.logs_chunk_blocks == 1200


def test_a_value_below_one_block_is_refused(monkeypatch):
    with pytest.raises(EnvironmentError):
        load(monkeypatch, "bloxberg_testnet", {"BLOXBERG_TESTNET_LOGS_CHUNK_BLOCKS": "0"})


def test_a_text_that_is_no_integer_is_refused(monkeypatch):
    with pytest.raises(EnvironmentError):
        load(monkeypatch, "bloxberg_testnet", {"BLOXBERG_TESTNET_LOGS_CHUNK_BLOCKS": "many"})
