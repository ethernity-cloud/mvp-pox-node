"""The identifiers of PoX 1.0 as the agent holds them (pox-smart-contract-litvm
MAINNET-UPGRADE.md: decision D3 and the name test of stage 3.1). Four
networks run PoX 1.0: bloxberg_mainnet, polygon_mainnet and the testnet
copies bloxberg_testnet_pox10 and polygon_amoy_pox10. Pinned here for each:
the --network words that select it; its chain id, its PoX, image registry and
heartbeat contracts, its token name and its trustedzones, which node/.env
gives under the prefix of the network's name; the Enclave State Registry
config.py names for it and the validator-set tables that name none; its entry
in the installer's list; the object-store buckets of its trustedzones and the
name and version its image registry is read under; and the selectors of the
calls the agent makes on a legacy PoX, a V1 image registry and a heartbeat.
The addresses and the trustedzone names are those of that document's
current-state table (the mainnets) and table T (the copies)."""
import configparser
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values
from eth_utils import keccak
from eth_utils.abi import collapse_if_tuple
from web3 import Web3

import config
import constants
import utils
from test_enclave_compose_runs import TREE, TREE_COMPOSE, agent, compose_of, recorded_node  # noqa: F401
from test_pox10_testnets import NODE, REPOSITORY, load, selected

# {network name: its identifiers}: the generation word (the name itself for a
# copy), the installer's choice, the chain id, the three contracts in checksum
# form, the token name, the trustedzones, the integration-tested one first,
# and the Enclave State Registry (None for none).
POX10 = {
    "bloxberg_mainnet": dict(
        word="bloxberg_pox10", choice="3", chain_id=8995,
        contract_address="0x549A6E06BB2084100148D50F51CF77a3436C3Ae7",
        image_registry_contract_address="0x15D73a742529C3fb11f3FA32EF7f0CC3870ACA31",
        heartbeat_contract_address="0x5c190f7253930C473822AcDED40B2eF1936B4075",
        token_name="ETNY", trustedzones=("etny-pynithy", "etny-nodenithy"),
        esr="0x4Bf5cDE3BFD73dd10B707f8B123Ba631D2EBEAD2"),
    "polygon_mainnet": dict(
        word="polygon_pox10", choice="1", chain_id=137,
        contract_address="0x439945BE73fD86fcC172179021991E96Beff3Cc4",
        image_registry_contract_address="0x689f3806874d3c8A973f419a4eB24e6fBA7E830F",
        heartbeat_contract_address="0x2BADdAe93fDB8faE61A60587B789f27bf407406f",
        token_name="ECLD", trustedzones=("ecld-pynithy", "ecld-nodenithy"), esr=None),
    "bloxberg_testnet_pox10": dict(
        word="bloxberg_testnet_pox10", choice="11", chain_id=8995,
        contract_address="0x69A7bEB5Da2d3dA711d52E491D71e51296b08745",
        image_registry_contract_address="0x7dBC1b591cc3b7136D863bc7430C6Db50E929665",
        heartbeat_contract_address="0x4426C87484038F9B720a6012A1191DA73bDa0ad3",
        token_name="tE10", trustedzones=("etny-pynithy-testnet-pox10", "etny-nodenithy-testnet-pox10"),
        esr="0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"),
    "polygon_amoy_pox10": dict(
        word="polygon_amoy_pox10", choice="12", chain_id=80002,
        contract_address="0x9Aad82557c84c11C4031382d8Bf2cacF8f256a02",
        image_registry_contract_address="0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC",
        heartbeat_contract_address="0x937F74419E31c1200e86B6D2439D9f12b3Fa12c9",
        token_name="tECLD", trustedzones=("ecld-pynithy-amoy-pox10", "ecld-nodenithy-amoy-pox10"), esr=None),
}
ADDRESS_FIELDS = ("contract_address", "image_registry_contract_address", "heartbeat_contract_address")
# The bucket of an order of each trustedzone of POX10, in POX10's order.
BUCKETS = ["etny-pynithy-v3", "etny-nodenithy-v3", "ecld-pynithy-v3", "ecld-nodenithy-v3",
           "etny-pynithy-testnet-pox10-v3", "etny-nodenithy-testnet-pox10-v3", "ecld-pynithy-amoy-pox10-v3",
           "ecld-nodenithy-amoy-pox10-v3"]
# {ABI file: {signature: selector}} of the functions etny-node.py calls that
# the PoX ABI of the releases before the 2.0 union holds (node/docker/pox.abi
# of commit a6d68ac), of the V1 image registry's read and of the heartbeat's
# two.
SELECTORS = {
    config.abi_filepath: {
        "_addDPRequest(uint8,uint8,uint8,uint8,uint16,uint8,string,string,string,string)": "3701f582",
        "_addMetadataToDPRequest(uint256,string,string)": "b5d11dfa",
        "_addResultToOrder(uint256,string)": "14e9f384",
        "_approveOrder(uint256)": "efa09d85",
        "_cancelDPRequest(uint256)": "676a96d8",
        "_getDORequest(uint256)": "4f74787b",
        "_getDORequestMetadata(uint256)": "d9edc139",
        "_getDORequestsCount()": "7aed1214",
        "_getDPRequest(uint256)": "00d74970",
        "_getDPRequestMetadata(uint256)": "98455999",
        "_getMetadataCountForDPRequest(uint256)": "e2eef9c3",
        "_getMetadataCountForRequest(uint256)": "5d87b6e6",
        "_getMetadataValueForDPRequest(uint256,uint256)": "2118c02c",
        "_getMetadataValueForRequest(uint256,uint256)": "f4de5305",
        "_getMyDOOrders()": "a8203abb",
        "_getMyDPRequests()": "4c1782e5",
        "_getOrder(uint256)": "b3bb3da3",
        "_getOrdersCount()": "246439e6",
        "_getResultFromOrder(uint256)": "008a81a6",
        "_placeOrder(uint256,uint256)": "9818e66c",
    },
    config.image_registry_abi_filepath: {
        "getLatestTrustedZoneImageCertPublicKey(string,string)": "20349449",
    },
    config.heart_beat_abi_filepath: {
        "logCall(string)": "7b712492",
        "getNodesCount()": "61ea7208",
    },
}


def test_the_networks_of_pox_10():
    assert list(POX10) == ["bloxberg_mainnet", "polygon_mainnet", *config.POX10_TESTNETS]
    assert set(POX10) <= set(config.available_networks())
    assert config.POX10_TESTNETS == ("bloxberg_testnet_pox10", "polygon_amoy_pox10")


def test_the_words_that_end_in_pox10():
    """A mainnet's generation word stands beside its name in
    config.GENERATION_WORDS; a copy's is its name. No other word of
    --network ends in _pox10."""
    assert {word: name for word, name in config.GENERATION_WORDS.items() if word.endswith("_pox10")} == {
        "bloxberg_pox10": "bloxberg_mainnet", "polygon_pox10": "polygon_mainnet"}
    words = config.available_networks() + list(config.GENERATION_WORDS)
    assert sorted(word for word in words if "pox10" in word) == sorted(
        identifiers["word"] for identifiers in POX10.values())
    for name, identifiers in POX10.items():
        assert config.network_name(identifiers["word"]) == name
        assert config.network_name(name) == name


@pytest.mark.parametrize("name", POX10)
def test_a_network_of_pox_10_is_selected_by_its_name_and_by_its_generation_word(monkeypatch, name):
    word = POX10[name]["word"]
    for given in (name, name.upper(), word, word.upper(), word.title()):
        assert selected(monkeypatch, [given]) == [name], given


def test_the_shorthands_select_the_mainnets_of_pox_10(monkeypatch):
    assert selected(monkeypatch, ["bloxberg"]) == ["bloxberg_mainnet"]
    assert selected(monkeypatch, ["polygon"]) == ["polygon_mainnet"]
    assert selected(monkeypatch, ["openbeta"]) == ["polygon_mainnet", "bloxberg_mainnet"]
    # The third shorthand selects bloxberg testnet's PoX 2.0 network.
    assert selected(monkeypatch, ["testnet"]) == ["bloxberg_testnet"]


@pytest.mark.parametrize("name", POX10)
def test_a_network_of_pox_10_loads_its_identifiers_from_the_variables_of_its_name(monkeypatch, name):
    identifiers, network = POX10[name], load(monkeypatch, name)
    assert network.name == name
    assert network.network_type == "MAINNET"
    assert network.chain_id == identifiers["chain_id"]
    for field in ADDRESS_FIELDS:
        assert Web3.to_checksum_address(getattr(network, field)) == identifiers[field], field
    assert network.token_name == identifiers["token_name"]
    tested, untested = identifiers["trustedzones"]
    assert config.trustedzone_requirements(network.trustedzone_images) == {tested: "dcap", untested: "dcap"}
    assert config.integration_test_images(network) == [tested]
    # The variables are the ones named <NAME>_<FIELD> in node/.env.
    file = dotenv_values(NODE / ".env")
    prefix = name.upper()
    assert int(file[f"{prefix}_CHAIN_ID"]) == network.chain_id
    for field in ADDRESS_FIELDS + ("token_name", "trustedzone_images", "integration_test_images"):
        assert file[f"{prefix}_{field.upper()}"] == getattr(network, field), field


def test_no_two_networks_of_pox_10_on_a_chain_share_a_contract_or_a_trustedzone():
    for field in ADDRESS_FIELDS:
        held = [(identifiers["chain_id"], identifiers[field]) for identifiers in POX10.values()]
        assert len(set(held)) == len(held), field
    trustedzones = [name for identifiers in POX10.values() for name in identifiers["trustedzones"]]
    assert len(set(trustedzones)) == len(trustedzones)


def test_config_names_the_state_registry_of_a_network_of_pox_10_and_no_validator_set():
    for name, identifiers in POX10.items():
        assert config.esr_contract_addresses.get(name.upper()) == identifiers["esr"], name
        for table in (config.cas_session_registry_addresses, config.validator_registry_addresses,
                      config.ipfs_endpoints_addresses, config.network_field_defaults):
            assert name.upper() not in table, name


def test_the_installers_list_holds_each_network_of_pox_10_under_its_name():
    listed = configparser.ConfigParser(interpolation=None)
    listed.read(REPOSITORY / "networks.ini", encoding="utf-8")
    for name, identifiers in POX10.items():
        entry = listed[identifiers["choice"]]
        assert entry["shortname"] == name.upper()
        assert entry["name"].endswith(f", {identifiers['word']}"), name
        assert "PoX 1.0" in entry["name"], name


def test_the_seed_file_keys_a_network_of_pox_10_by_its_name():
    seeds = json.loads(Path(config.gas_profile_seed_filepath).read_text())
    assert sorted(name for name in seeds if name in POX10) == ["bloxberg_mainnet", "bloxberg_testnet_pox10"]
    assert not [name for name in seeds if name in config.GENERATION_WORDS]


def test_the_buckets_of_the_trustedzones_of_pox_10():
    trustedzones = [name for identifiers in POX10.values() for name in identifiers["trustedzones"]]
    assert [utils.order_bucket_name(name, "v3") for name in trustedzones] == BUCKETS
    assert constants.INTEGRATION_BUCKET == "etny-bucket-integration"
    assert utils.DP_REQUEST_PROTOCOL == "v3"


@pytest.mark.parametrize("name", POX10)
def test_an_integration_test_reads_the_registry_under_the_trustedzones_name_and_runs_in_the_integration_bucket(
        agent, monkeypatch, tmp_path, name):
    tested = POX10[name]["trustedzones"][0]
    node, _ = recorded_node(agent, monkeypatch, tmp_path, False, "integration_test")
    read, buckets = [], []

    def latest(image, version):
        read.append((image, version))
        return [TREE, "certificate", TREE_COMPOSE]

    def prerequisites(bucket_name, order_id, docker_compose_file):
        buckets.append(bucket_name)
        node.order_docker_compose_file = compose_of(order_id)

    def waited(bucket_name, object_name, timeout):
        buckets.append(bucket_name)
        return True

    def content(bucket_name, object_name):
        buckets.append(bucket_name)
        return True, "written"

    node._EtnyPoXNode__image_registry = SimpleNamespace(
        caller=lambda: SimpleNamespace(getLatestTrustedZoneImageCertPublicKey=latest))
    node._EtnyPoXNode__network_config = SimpleNamespace(rpc_delay=0)
    node._EtnyPoXNode__network = name
    node._EtnyPoXNode__trustedzones = {tested: "dcap"}
    node.build_prerequisites_integration_test = prerequisites
    node.wait_for_enclave_v2 = waited
    node.storage = SimpleNamespace(download_many=lambda hashes, attempts, delay: True)
    node.swift_stream_service = SimpleNamespace(get_file_content=content, delete_bucket=buckets.append)
    assert node._EtnyPoXNode__run_integration_test(tested) == (TREE, "written", "written")
    assert read == [(tested, "v3")]
    # The prerequisites, the wait, the result, the two capability objects and
    # the clean-up.
    assert buckets == ["etny-bucket-integration"] * 6


def signatures(path):
    """{signature: selector} of the functions of the ABI file `path`."""
    found = {}
    for entry in json.loads(Path(path).read_text()):
        if entry.get("type") == "function":
            signature = f"{entry['name']}({','.join(collapse_if_tuple(i) for i in entry['inputs'])})"
            found[signature] = keccak(text=signature)[:4].hex()
    return found


@pytest.mark.parametrize("path", SELECTORS, ids=lambda path: Path(path).name)
def test_the_calls_the_agent_makes_on_pox_10_keep_their_selectors(path):
    held = signatures(path)
    names = [signature.split("(")[0] for signature in held]
    for signature, selector in SELECTORS[path].items():
        assert held.get(signature) == selector, signature
        # web3 binds the call by name: the file holds one function of it.
        assert names.count(signature.split("(")[0]) == 1, signature


def test_the_selectors_are_those_of_functions_the_agent_names():
    """Every function of SELECTORS is called by its name in etny-node.py, or
    named in utils.py by its signature for a call etny-node.py makes by the
    signature it is given (_addDPRequest: utils.dp_request_arguments)."""
    agent_source = (NODE / "etny-node.py").read_text(encoding="utf-8")
    utils_source = (NODE / "utils.py").read_text(encoding="utf-8")
    for table in SELECTORS.values():
        for signature in table:
            name = signature.split("(")[0]
            assert f".{name}(" in agent_source or f"'{signature}'" in utils_source, name
