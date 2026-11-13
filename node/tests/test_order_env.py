"""utils.integration_env / utils.order_env (PHASE-E-PLAN.md E5): both
dictionaries carry ETNY_NODE_ADDRESS as the wallet; the integration one
never carries ETNY_PROBE_FIRST and the order one only when asked, as the
string 1; neither carries ETNY_NGROK_AUTHTOKEN."""
from types import SimpleNamespace

from utils import integration_env, order_env

NETWORK = SimpleNamespace(chain_id=80002, contract_address="0x3Aa1A0d7A8A9DaE9e13345D3261afe86D67326A4",
                          rpc_url="https://polygon-amoy-bor-rpc.publicnode.com")
WALLET = "0x470D8B388f4a0AE6bD512b74038c7e87957bb8FE"


def test_the_integration_env_names_the_wallet_and_runs_the_test():
    env = integration_env(NETWORK, WALLET)
    assert env == {
        "ETNY_CHAIN_ID": 80002,
        "ETNY_SMART_CONTRACT_ADDRESS": NETWORK.contract_address,
        "ETNY_WEB3_PROVIDER": NETWORK.rpc_url,
        "ETNY_RUN_INTEGRATION_TEST": 1,
        "ETNY_ORDER_ID": 0,
        "ETNY_NODE_ADDRESS": WALLET,
    }


def test_the_integration_env_never_forces_the_probe():
    assert "ETNY_PROBE_FIRST" not in integration_env(NETWORK, WALLET)


def test_the_order_env_names_the_wallet_the_order_and_the_challenge():
    env = order_env(NETWORK, 74, "challenge-b64", WALLET, False)
    assert env == {
        "ETNY_CHAIN_ID": 80002,
        "ETNY_SMART_CONTRACT_ADDRESS": NETWORK.contract_address,
        "ETNY_WEB3_PROVIDER": NETWORK.rpc_url,
        "ETNY_CLIENT_CHALLENGE": "challenge-b64",
        "ETNY_ORDER_ID": 74,
        "ETNY_NODE_ADDRESS": WALLET,
    }


def test_the_order_env_forces_the_probe_only_when_asked_and_as_the_string_1():
    assert "ETNY_PROBE_FIRST" not in order_env(NETWORK, 74, "c", WALLET, False)
    assert order_env(NETWORK, 74, "c", WALLET, True)["ETNY_PROBE_FIRST"] == "1"


def test_neither_env_carries_the_ngrok_token():
    assert "ETNY_NGROK_AUTHTOKEN" not in integration_env(NETWORK, WALLET)
    assert "ETNY_NGROK_AUTHTOKEN" not in order_env(NETWORK, 74, "c", WALLET, True)


def test_the_wallet_is_passed_as_given():
    assert order_env(NETWORK, 1, "c", WALLET, False)["ETNY_NODE_ADDRESS"] is WALLET
    assert integration_env(NETWORK, WALLET)["ETNY_NODE_ADDRESS"] is WALLET
