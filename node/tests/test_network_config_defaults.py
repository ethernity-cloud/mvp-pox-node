"""config.NetworkConfig's defaulted fields (PHASE-E-PLAN.md E7): a network
loaded from the required variables alone carries tip_per_cu_hour_wei 0,
accept_reserved True, farm_invite '', farm_accept True and the relay gas
budget default; <NETWORK>_<FIELD> sets each, a bool through strtobool."""
import argparse
from dataclasses import MISSING, fields

import pytest

import config


def required_values(prefix):
    """{<PREFIX>_<FIELD>: value} for every field without a default, as
    parse_networks reads them from the environment."""
    values = {}
    for f in fields(config.NetworkConfig):
        if f.default is not MISSING or f.name == "name":
            continue
        if f.type == int:
            value = "1"
        elif f.type == bool:
            value = "0"
        elif f.name == "trustedzone_images":
            value = "etny-pynithy-testnet:dcap"
        elif f.name == "integration_test_images":
            value = "etny-pynithy-testnet"
        elif f.name == "middleware":
            value = "geth_poa"
        else:
            value = "x"
        values[f"{prefix}_{f.name.upper()}"] = value
    return values


def load(monkeypatch, extra=None):
    prefix = "BLOXBERG_TESTNET"
    for name, value in {**required_values(prefix), **(extra or {})}.items():
        monkeypatch.setenv(name, value)
    for f in fields(config.NetworkConfig):
        if f.default is not MISSING and f"{prefix}_{f.name.upper()}" not in (extra or {}):
            monkeypatch.delenv(f"{prefix}_{f.name.upper()}", raising=False)
    parser = argparse.ArgumentParser()
    arguments = argparse.Namespace(network=["bloxberg_testnet"])
    (network,) = config.parse_networks(arguments, parser, ["BLOXBERG"])
    return network


def test_the_fields_from_the_required_values_alone_take_their_defaults():
    network = config.NetworkConfig(**{f.name: "" for f in fields(config.NetworkConfig) if f.default is MISSING})
    assert network.tip_per_cu_hour_wei == 0
    assert network.accept_reserved is True
    assert network.farm_invite == ""
    assert network.farm_accept is True
    assert network.esr_relay_gas_budget_wei == 10**17


def test_a_network_without_the_variables_keeps_the_defaults(monkeypatch):
    network = load(monkeypatch)
    assert network.tip_per_cu_hour_wei == 0
    assert network.accept_reserved is True
    assert network.farm_invite == ""
    assert network.farm_accept is True


def test_the_variables_set_the_fields(monkeypatch):
    network = load(monkeypatch, {
        "BLOXBERG_TESTNET_TIP_PER_CU_HOUR_WEI": "25000000000000000",
        "BLOXBERG_TESTNET_ACCEPT_RESERVED": "0",
        "BLOXBERG_TESTNET_FARM_INVITE": "0x" + "ab" * 20 + ":1800000000:0x" + "cd" * 65,
        "BLOXBERG_TESTNET_FARM_ACCEPT": "false",
    })
    assert network.tip_per_cu_hour_wei == 25 * 10**15
    assert network.accept_reserved is False
    assert network.farm_invite == "0x" + "ab" * 20 + ":1800000000:0x" + "cd" * 65
    assert network.farm_accept is False


def test_a_bad_boolean_is_refused(monkeypatch):
    with pytest.raises(EnvironmentError):
        load(monkeypatch, {"BLOXBERG_TESTNET_ACCEPT_RESERVED": "maybe"})


def test_the_command_line_arguments_set_the_fields_as_bools(monkeypatch):
    """The start script maps the operator's config to the --<network>_<field>
    arguments; a bool field parsed there is a bool, as the environment's."""
    prefix = "BLOXBERG_TESTNET"
    for name, value in required_values(prefix).items():
        monkeypatch.setenv(name, value)
    for f in fields(config.NetworkConfig):
        if f.default is not MISSING:
            monkeypatch.delenv(f"{prefix}_{f.name.upper()}", raising=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", nargs="+")
    config.add_network_override_arguments(parser, ["BLOXBERG"])
    arguments = parser.parse_args([
        "--network", "bloxberg_testnet",
        "--bloxberg_testnet_accept_reserved", "0",
        "--bloxberg_testnet_farm_accept", "false",
        "--bloxberg_testnet_tip_per_cu_hour_wei", "7",
    ])
    (network,) = config.parse_networks(arguments, parser, ["BLOXBERG"])
    assert network.accept_reserved is False
    assert network.farm_accept is False
    assert network.tip_per_cu_hour_wei == 7


def test_a_bad_boolean_argument_is_refused():
    parser = argparse.ArgumentParser()
    config.add_network_override_arguments(parser, ["BLOXBERG"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--bloxberg_testnet_accept_reserved", "maybe"])
