"""config.NetworkConfig.esr_relay_gas_budget_wei: the per-order relay gas
budget is a network setting (<NETWORK>_ESR_RELAY_GAS_BUDGET_WEI) with the
global ESR_RELAY_GAS_BUDGET_WEI as its default, so a network's environment
without the variable still loads (Amoy, 2026-10-29: the 0.1 POL default held
eight session output rows at ~0.012 POL each, and the ninth was refused).
It is one of the six defaulted fields, with logs_chunk_blocks and the
Phase E four."""
from dataclasses import MISSING, fields

import config


def required_fields():
    return {f.name: "" for f in fields(config.NetworkConfig) if f.default is MISSING}


def test_the_defaulted_fields_are_the_relay_budget_the_logs_chunk_and_the_phase_e_four():
    optional = [f.name for f in fields(config.NetworkConfig) if f.default is not MISSING]
    assert optional == ["esr_relay_gas_budget_wei", "logs_chunk_blocks", "tip_per_cu_hour_wei",
                        "accept_reserved", "farm_invite", "farm_accept"]


def test_a_network_without_the_variable_takes_the_global_default():
    network = config.NetworkConfig(**required_fields())
    assert network.esr_relay_gas_budget_wei == config.esr_relay_gas_budget_wei_default
    assert config.esr_relay_gas_budget_wei_default == 10**17


def test_a_network_variable_sets_its_own_budget():
    network = config.NetworkConfig(**required_fields(), esr_relay_gas_budget_wei=10**18)
    assert network.esr_relay_gas_budget_wei == 10**18
