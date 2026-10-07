import os, sys, signal
import argparse
import logging.handlers
from os.path import expanduser
from dataclasses import dataclass, fields, replace
from typing import List
from pathlib import Path
import dependency_manager
from distutils.util import strtobool

dependency_manager.check_dependencies()

import psutil
from utils import HardwareInfoProvider
from minio import Minio
from dotenv import load_dotenv

# Load variables from .env and .env.conf if it exists
if os.path.exists('.env'):
    load_dotenv('.env')

version = os.environ.get('VERSION', "LEGACY")
ipfs_swarm_default = os.environ.get('IPFS_SWARM', "/dns4/ipfs.ethernity.cloud/tcp/4001/p2p/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5")
ipfs_connect_url_default = os.environ.get('IPFS_CONNECT_URL', "/ip4/127.0.0.1/tcp/5001/http")
ipfs_timeout_default = int(os.environ.get('IPFS_TIMEOUT', 30))
ipfs_gateway_url_default=os.environ.get('IPFS_GATEWAY_URL', 'https://ipfs.io')
# How long a locally pinned IPFS object is kept before the periodic cleanup
# unpins and removes it. Objects the node still needs are exempt regardless of
# age: the trustedzone images, and any CID that is still the CURRENT ESR state
# for some enclave/key (only superseded state versions expire).
# 0 disables age-based cleanup entirely.
ipfs_pin_retention_days_default = float(os.environ.get('IPFS_PIN_RETENTION_DAYS', 7))
# Minimum minutes between cleanup runs. The cleanup is triggered from the
# order-processing loop (the same place that calls the heartbeat), so this
# throttles it to at most once per interval instead of once per order.
ipfs_cleanup_interval_minutes_default = float(os.environ.get('IPFS_CLEANUP_INTERVAL_MINUTES', 60))
skip_integration_test = strtobool(os.environ.get('SKIP_INTEGRATION_TEST', "False"))
kubo_url_default = os.environ.get('KUBO_URL')
kubo_version_default =  os.environ.get('KUBO_VERSION')

@dataclass(frozen=True)
class NetworkConfig:
    name: str
    network_type: str
    rpc_url: str
    rpc_delay: int
    chain_id: int
    block_time: int
    contract_address: str
    heartbeat_contract_address: str
    image_registry_contract_address: str
    token_name: str
    gas_price_measure: str
    minimum_gas_at_start: int
    task_execution_price: int
    # The trustedzones the node tests (comma-separated names from
    # trustedzone_images); a tested trustedzone runs once its own test passed.
    integration_test_images: str
    # The network's trustedzones, comma-separated "name:requirement" (see
    # trustedzone_requirements); the node runs an untested one once a passed
    # integration test covers its requirement.
    trustedzone_images: str
    eip1559: bool
    middleware: str
    gas_price: int
    gas_limit: int
    max_priority_fee_per_gas: int
    max_fee_per_gas: int
    reward_type: int
    network_fee: int
    enclave_fee: int

# Platform requirements a trustedzone names in <NETWORK>_TRUSTEDZONE_IMAGES:
#   dcap  provisioned by a CAS, which admits the enclave on a DCAP quote: the
#         platform must produce one.
#   sgx   runs without a CAS (the -unsafe testnet variants): any platform with
#         hardware SGX.
TRUSTEDZONE_REQUIREMENTS = ("dcap", "sgx")


def trustedzone_requirements(spec: str) -> dict:
    """{trustedzone name: platform requirement} from a TRUSTEDZONE_IMAGES
    value: comma-separated "name:requirement", where a bare name is "dcap"."""
    requirements = {}
    for item in spec.split(','):
        name, _, requirement = item.strip().partition(':')
        if not name:
            continue
        requirement = requirement.strip() or "dcap"
        if requirement not in TRUSTEDZONE_REQUIREMENTS:
            raise EnvironmentError(
                f"trustedzone {name}: unknown platform requirement {requirement!r} "
                f"(one of {', '.join(TRUSTEDZONE_REQUIREMENTS)})")
        requirements[name.strip()] = requirement
    return requirements


def integration_test_images(network_config) -> list:
    """The trustedzones a network's integration tests run, in order."""
    return [name.strip() for name in network_config.integration_test_images.split(',') if name.strip()]


NETWORKS = {
    "POLYGON": ["MAINNET", "AMOY"],
    "BLOXBERG": ["MAINNET", "TESTNET", "TESTNET_UNSAFE"],
    "IOTEX": ["TESTNET"],
    "ETHEREUM": ["SEPOLIA"],
    "LITVM": ["LITEFORGE", "LITEFORGE_UNSAFE"],
}

# An -unsafe network has the chain and contracts of the network it is named
# after and runs only that network's -unsafe trustedzones: the ones that run
# without a CAS, for hardware SGX platforms the CAS cannot attest. It is run
# only when named; "all", "auto" and the legacy names never select one.
UNSAFE_NETWORK_SUFFIX = "_unsafe"
UNSAFE_TRUSTEDZONE_SUFFIX = "-unsafe"


def available_networks() -> list:
    """Every network NETWORKS defines, lowercase, as --network takes them."""
    return [f"{network}_{suffix}".lower() for network, suffixes in NETWORKS.items()
            for suffix in suffixes]


def is_unsafe_network(name: str) -> bool:
    return name.lower().endswith(UNSAFE_NETWORK_SUFFIX)


def unsafe_twin(name: str):
    """The -unsafe network named after `name`, if NETWORKS defines one."""
    twin = f"{name.lower()}{UNSAFE_NETWORK_SUFFIX}"
    return twin if twin in available_networks() else None


def check_trustedzone_names(network_config) -> None:
    """An -unsafe network's trustedzones all end in -unsafe and no other
    network's does, so a CAS-less trustedzone runs only where it was chosen."""
    unsafe = is_unsafe_network(network_config.name)
    for name in trustedzone_requirements(network_config.trustedzone_images):
        if name.endswith(UNSAFE_TRUSTEDZONE_SUFFIX) != unsafe:
            raise EnvironmentError(
                f"{network_config.name}: trustedzone {name} "
                + (f"does not end in {UNSAFE_TRUSTEDZONE_SUFFIX}; an -unsafe network runs "
                   f"only -unsafe trustedzones" if unsafe else
                   f"runs without a CAS; it belongs to "
                   f"{network_config.name}{UNSAFE_NETWORK_SUFFIX}"))


def join_unsafe_twins(networks: list) -> list:
    """One configuration per protocol contract. An -unsafe network selected
    together with the network it is named after joins that network: one agent
    handle on their shared contract, running both networks' trustedzones and
    integration tests. Selected alone, it runs under its own name."""
    by_name = {n.name: n for n in networks}
    joined = []
    for n in networks:
        if is_unsafe_network(n.name) and n.name[:-len(UNSAFE_NETWORK_SUFFIX)] in by_name:
            continue
        twin = by_name.get(f"{n.name}{UNSAFE_NETWORK_SUFFIX}")
        if twin is not None:
            if (twin.chain_id, twin.contract_address.lower()) != (n.chain_id, n.contract_address.lower()):
                raise EnvironmentError(
                    f"{twin.name} must name the chain and protocol contract of {n.name}")
            n = replace(n,
                        trustedzone_images=f"{n.trustedzone_images},{twin.trustedzone_images}",
                        integration_test_images=f"{n.integration_test_images},{twin.integration_test_images}")
        joined.append(n)
    return joined


task_price_default = 3
network = None
heart_beat_address = None
gas_price_measure = None
image_registry_address = None

base_path = Path(__file__).parent

abi_filepath = base_path / 'docker/pox.abi'
image_registry_abi_filepath = base_path / 'image_registry.abi'
heart_beat_abi_filepath = base_path / 'heart_beat.abi'
uuid_filepath = Path(expanduser("~")) / "opt/etny/node/UUID"

# --- Enclave State Registry (ESR) ------------------------------------------
# Enclaves publish an IPFS CID of their encrypted state to this registry. Nodes
# replicate that state by pinning the CIDs locally, so a dApp's state survives
# any single node going away.
esr_abi_filepath = base_path / 'esr.abi'
# Canonical deployments, keyed by network name as it appears in networks.ini.
# "" (or an absent entry) means ESR is not deployed on that network and state
# replication is simply skipped there.
#
# Bloxberg mainnet and testnet are the SAME CHAIN (both chainId 8995), separated
# by different protocol contracts, so both use the one deployment.
esr_contract_addresses = {
    # Nonce-aware enumerable registries (2026-08-15): commitFor relay, the
    # enumeration API (commitSeq + entryCount/getEntriesFrom) and the PUBLIC
    # per-(enclave, key) nonce (getNonce view), advancing by exactly 1 on
    # every commit -- omitted (wire 0) is auto-assigned by the registry, a
    # pinned value must be exactly stored + 1 (no gaps, no reuse).
    "BLOXBERG_MAINNET": os.environ.get('ESR_CONTRACT_ADDRESS', "0x4Bf5cDE3BFD73dd10B707f8B123Ba631D2EBEAD2"),
    "BLOXBERG_TESTNET": os.environ.get('ESR_CONTRACT_ADDRESS', "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"),
    "BLOXBERG_TESTNET_UNSAFE": os.environ.get('ESR_CONTRACT_ADDRESS', "0x0Ea1728EAE108FD3B9340ae91451348E2Cc6b4E4"),
    "LITVM_LITEFORGE": os.environ.get('ESR_CONTRACT_ADDRESS', "0x709052Fe77Af543d3d9FE2Ac06a15c635c8D4Be5"),
    "LITVM_LITEFORGE_UNSAFE": os.environ.get('ESR_CONTRACT_ADDRESS', "0x709052Fe77Af543d3d9FE2Ac06a15c635c8D4Be5"),
}
# ethernity-cas SessionRegistry (CAS sessions -- distinct from the ESR
# interactive-session rows replicated above): CAS sessions registered ON-CHAIN, bodies on
# IPFS as CIDv1/raw/sha2-256 (the ESR blob recipe). The replication loop pins
# every registered session body so the material distributes across operator
# nodes after a pipeline deployment. Same chain note as the ESR: bloxberg
# mainnet and testnet share chainId 8995. "" / absent = not deployed there,
# replication skipped. An -unsafe network has no CAS, so no sessions.
cas_session_registry_addresses = {
    "BLOXBERG_TESTNET": os.environ.get('CAS_SESSION_REGISTRY_ADDRESS', "0x444429681fCb26470Cb12B5436E48402cF7478A3"),
    "LITVM_LITEFORGE": os.environ.get('LITVM_LITEFORGE_CAS_SESSION_REGISTRY_ADDRESS', "0x8ad24b3F406A41a0F8D3440021792EB203957F43"),
}
# How far back the FIRST SessionRegistered scan reaches (later rounds continue
# incrementally from where the previous one stopped).
cas_session_registry_scan_blocks = int(os.environ.get('CAS_SESSION_REGISTRY_SCAN_BLOCKS', 200000))
# Registered images (ECImageRegistryV2 ImageRegistered / TrustedZoneImageRegistered):
# the replication loop pins each registered image that scone_image.py verifies,
# with its compose, from the publisher's IPFS node. How far back the first scan
# reaches, and how long one pin attempt of an image tree (about 1.3 GB) may take.
image_registry_scan_blocks = int(os.environ.get('IMAGE_REGISTRY_SCAN_BLOCKS', 200000))
registered_image_pin_timeout_seconds = int(os.environ.get('REGISTERED_IMAGE_PIN_TIMEOUT_SECONDS', 3600))
# A registered image is pinned only when its tree declares at most
# REGISTERED_IMAGE_MAX_BYTES and its compose at most REGISTERED_COMPOSE_MAX_BYTES,
# and scone_image.py finds the enclave's SCONE binary-fs within the first
# REGISTERED_IMAGE_SCAN_MAX_BYTES of its layers, newest first, decompressing at
# most REGISTERED_IMAGE_INFLATE_MAX_BYTES of them. A securelock image tree is
# about 1.3 GB, its binary-fs layer about 175 MB and the library in it about
# 441 MB. A pin that grows the repository by more than twice what the tree
# declares, plus 2 GiB, is ended and tried again later.
registered_image_max_bytes = int(os.environ.get('REGISTERED_IMAGE_MAX_BYTES', 3 * 1024 ** 3))
registered_compose_max_bytes = int(os.environ.get('REGISTERED_COMPOSE_MAX_BYTES', 1024 ** 2))
registered_image_scan_max_bytes = int(os.environ.get('REGISTERED_IMAGE_SCAN_MAX_BYTES', 1024 ** 3))
registered_image_inflate_max_bytes = int(os.environ.get('REGISTERED_IMAGE_INFLATE_MAX_BYTES', 2 * 1024 ** 3))
# How long one verification may read a registered image before it is ended and
# tried again on a later round; an SDK image's verification reads about 200 MB.
registered_image_verify_timeout_seconds = int(os.environ.get('REGISTERED_IMAGE_VERIFY_TIMEOUT_SECONDS', 900))

# ethernity-cas ValidatorRegistry (validator identity + governance + endpoints).
# When set for a network, the node RESOLVES its CAS from chain before each v3
# task: enumerate active validators, dial their published multiaddrs (onion
# first), attest the answerer against its on-chain record, and rewrite
# SCONE_CAS_ADDR in the order's compose. "" / absent = keep the compose's
# baked-in CAS address (the pre-Sprint-4 behaviour). An -unsafe network's
# entry only enumerates the validators for IPFS peering: its trustedzones'
# composes name no CAS, so none is resolved.
validator_registry_addresses = {
    "BLOXBERG_TESTNET": os.environ.get('VALIDATOR_REGISTRY_ADDRESS', "0x0014aB102FEa6b02e3D793FF4b9a0Af309fB4E14"),
    "BLOXBERG_TESTNET_UNSAFE": os.environ.get('VALIDATOR_REGISTRY_ADDRESS', "0x0014aB102FEa6b02e3D793FF4b9a0Af309fB4E14"),
    "LITVM_LITEFORGE": os.environ.get('LITVM_LITEFORGE_VALIDATOR_REGISTRY_ADDRESS', "0xbE3759f327e8643fC5A1717fb2072A0dAcBAD29E"),
    "LITVM_LITEFORGE_UNSAFE": os.environ.get('LITVM_LITEFORGE_VALIDATOR_REGISTRY_ADDRESS', "0xbE3759f327e8643fC5A1717fb2072A0dAcBAD29E"),
}
# ethernity-cas ValidatorIpfsEndpoints: where each validator's IPFS node is
# reached (multiaddrs with peer id). When set for a network, the node keeps
# every active validator's IPFS node in its Kubo peering list, refreshed every
# IPFS_PEER_SYNC_SECONDS, so task artefacts and results travel directly between
# the node and the validators. "" / absent = peer only with IPFS_SWARM. The
# validators judge an -unsafe network's orders too, so it peers with them.
ipfs_endpoints_addresses = {
    "BLOXBERG_TESTNET": os.environ.get('IPFS_ENDPOINTS_ADDRESS', "0x5C1e2D74b8BD941f3e25d1ff52283d465922aB00"),
    "BLOXBERG_TESTNET_UNSAFE": os.environ.get('IPFS_ENDPOINTS_ADDRESS', "0x5C1e2D74b8BD941f3e25d1ff52283d465922aB00"),
    "LITVM_LITEFORGE": os.environ.get('LITVM_LITEFORGE_IPFS_ENDPOINTS_ADDRESS', "0x7d3446736AD53Aab790A9A0eE486940107e30086"),
    "LITVM_LITEFORGE_UNSAFE": os.environ.get('LITVM_LITEFORGE_IPFS_ENDPOINTS_ADDRESS', "0x7d3446736AD53Aab790A9A0eE486940107e30086"),
}
ipfs_peer_sync_seconds = int(os.environ.get('IPFS_PEER_SYNC_SECONDS', 300))

# REPLICATION_ONLY=True runs the agent as a mirror: no SGX, no DP requests, no
# task execution. For every configured network it pins recent results, ESR
# state and CAS session bodies from chain into the local Kubo, and keeps that
# Kubo peered with the validators' IPFS nodes. The bootnode runs this mode. A
# private key is still required for the chain reads' account context; it
# sends no transaction.
replication_only = strtobool(os.environ.get('REPLICATION_ONLY', "False"))
# IPFS_INTAKE_BIND=host:port starts the payload intake (ipfs_intake.py) in a
# replication-only agent: runners deliver task artefacts to
# POST /payload/<network>/<doRequestId>/<cid>, accepted only when the DO
# request on chain names the CID and the bytes hash to it. "" = no intake.
ipfs_intake_bind = os.environ.get('IPFS_INTAKE_BIND', '')
ipfs_intake_max_bytes = int(os.environ.get('IPFS_INTAKE_MAX_BYTES', 100 * 1024 * 1024))
ipfs_intake_retention_seconds = int(os.environ.get('IPFS_INTAKE_RETENTION_SECONDS', 24 * 3600))
# Probe timeout per endpoint, seconds.
cas_resolver_probe_timeout = int(os.environ.get('CAS_RESOLVER_PROBE_TIMEOUT', 10))

# ESR relay: the enclave signs each state commit (commitFor) and the NODE
# submits it and pays gas, so no enclave wallet needs funding. To stop a
# malicious payload from draining the operator, the node caps the CUMULATIVE
# gas it will spend relaying commits for ONE order, in wei of the native gas
# token. A commit that would push the order total over this is refused and the
# refusal is surfaced to the trustedzone as evidence (which terminates the
# order). 0.1 POL default; on ~free chains (bloxberg, testnets) this is never
# reached in practice.
esr_relay_gas_budget_wei = int(os.environ.get('ESR_RELAY_GAS_BUDGET_WEI', 10**17))  # 0.1 * 1e18
# How far back to scan for StateCommitted events on the first pass, in blocks.
# After that the node continues from the last block it processed.
esr_scan_lookback_blocks = int(os.environ.get('ESR_SCAN_LOOKBACK_BLOCKS', 50_000))
# Keep pinning replicated state only while at least this much free disk (in GB)
# remains, so replication can never fill the disk the node needs to run tasks.
esr_min_free_storage_gb = float(os.environ.get('ESR_MIN_FREE_STORAGE_GB', 10))
# How often the dedicated replication thread pins current ESR state + recent
# protocol-contract result CIDs into the node's IPFS (seconds). Runs on its own
# cadence rather than piggybacking on the hourly cache cleanup, so freshly
# committed state is backed up promptly.
esr_replication_interval_seconds = int(os.environ.get('ESR_REPLICATION_INTERVAL_SECONDS', 300))
# How many of the most recent protocol orders the replication thread scans for
# result CIDs each round.
esr_result_scan_orders = int(os.environ.get('ESR_RESULT_SCAN_ORDERS', 200))
# How many of the most recent DO requests the replication thread scans for
# payload/input/challenge/image CIDs to pin network-wide (0 disables).
do_request_scan_requests = int(os.environ.get('DO_REQUEST_SCAN_REQUESTS', 200))
# ESR state blobs are produced on another node and must propagate to ours.
# Per replication cycle we try a missing CID up to esr_pin_attempts_per_cycle
# times (a quick double-tap for propagation lag); the attempt counter persists
# across cycle restarts, so retries accumulate until esr_pin_max_attempts total,
# after which the CID is marked failed and never retried again.
esr_pin_attempts_per_cycle = int(os.environ.get('ESR_PIN_ATTEMPTS_PER_CYCLE', 2))
esr_pin_max_attempts = int(os.environ.get('ESR_PIN_MAX_ATTEMPTS', 10))
# Per-attempt timeout (seconds) for replication pin_add. A replicated ESR/result
# CID may not be fetchable yet; pinning it forces a bitswap fetch that otherwise
# blocks for the full IPFS timeout (600s). With many replication threads sharing
# one local daemon, those long blocks pile up and starve the order-processing
# threads' own pins. Keep this SHORT so a not-yet-available CID fails fast,
# counts as one attempt, and frees the daemon -- the retry policy handles the
# rest across cycles.
esr_pin_attempt_timeout_seconds = int(os.environ.get('ESR_PIN_ATTEMPT_TIMEOUT_SECONDS', 30))

# logger
logger = logging.getLogger("ETNY NODE")
handler = logging.handlers.RotatingFileHandler('/var/log/etny-node.log', maxBytes=2048000, backupCount=5)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG if os.environ.get('LOG_LEVEL') == 'debug' else logging.INFO)

contract_call_frequency = int(os.environ.get('CONTRACT_CALL_FREQUENCY', 43200))

def add_network_override_arguments(parser: argparse.ArgumentParser, network_names: list):
    """
    Dynamically add command-line arguments to override environment variables
    based on the fields defined in the NetworkConfig data class.
    """
    for network in network_names:
        suffixes = NETWORKS.get(network, [])
        for suffix in suffixes:
            network_suffix = f"{network}_{suffix}" if suffix else network
            prefix = network_suffix.upper()

            for field in fields(NetworkConfig):
                if field.name == "name":
                    continue  # Skip the 'name' field as it's already specified

                # Construct environment variable name
                env_var = f"{prefix}_{field.name.upper()}"

                # Construct command-line argument name
                arg_name = f"--{network_suffix.lower()}_{field.name.lower()}"
                
                # Determine the argument type based on the field typea

                if field.type == bool:
                    arg_type = strtobool
                else:
                    arg_type = field.type if field.type != int else int

                # Add the argument to the parser
                parser.add_argument(
                    arg_name,
                    help=f"Override for {env_var}",
                    type=arg_type,
                    required=False,
                )

def parse_networks(arguments: argparse.Namespace, parser: argparse.ArgumentParser, network_names: list) -> List[NetworkConfig]:
    """
    Parse and construct network configurations based on command-line arguments and environment variables.

    Args:
        arguments (argparse.Namespace): Parsed command-line arguments.
        parser (argparse.ArgumentParser): The argument parser instance.
        network_names (list): List of available network names.

    Returns:
        List[NetworkConfig]: A list of network configurations, an -unsafe
        network joined to the network it is named after when both are
        selected (join_unsafe_twins).
    """
    AVAILABLE_NETWORKS = [n for n in available_networks()
                          if n.split('_')[0].upper() in network_names]

    ALL_NETWORKS = ["all", "auto"]
    CURRENT_NETWORKS = ["openbeta"]
    LEGACY_NETWORKS = ["bloxberg", "testnet", "polygon"]

    lower_networks = [n.lower() for n in arguments.network]
    # Determine which networks to load
    if any(n in ALL_NETWORKS for n in lower_networks):
        # Every network but the -unsafe ones, which run only when named.
        selected_networks = [n for n in AVAILABLE_NETWORKS if not is_unsafe_network(n)]
    elif any(n in CURRENT_NETWORKS for n in lower_networks):
        # If any special keyword is specified, load all networks
        selected_networks = [ "polygon_mainnet", "bloxberg_mainnet" ]
    elif len(lower_networks) == 1 and lower_networks[0] in LEGACY_NETWORKS:
        # If there's exactly one network and it's one of the SPECIFIC_NETWORKS
        single_network = lower_networks[0]
        if single_network == "bloxberg":
            selected_networks = ["bloxberg_mainnet"]
        elif single_network == "testnet":
            selected_networks = ["bloxberg_testnet"]
        elif single_network == "polygon":
            selected_networks = ["polygon_mainnet"]
    else:
        # Otherwise, load only the specified networks
        selected_networks = [network.lower() for network in arguments.network]
        # Validate selected networks
        invalid_networks = set(selected_networks) - set(AVAILABLE_NETWORKS)
        if invalid_networks:
            parser.error(
                f"Invalid network(s) specified: {', '.join(invalid_networks)}. "
                f"Available networks are: {', '.join(AVAILABLE_NETWORKS)}."
           )

    networks = []

    for network_suffix in selected_networks:
        prefix = network_suffix.upper()

        config_kwargs = {}
        missing_vars = []

        # Iterate through NetworkConfig fields to fetch values
        for field in fields(NetworkConfig):
            if field.name == "name":
                config_kwargs["name"] = network_suffix
                continue  # Skip the 'name' field as it's already specified

            # Construct environment variable name
            env_var = f"{prefix}_{field.name.upper()}"

            # Construct command-line argument name
            arg_name = f"{network_suffix}-{field.name}".replace('_', '-').lower()

            # Fetch the override value from command-line arguments
            cli_value = getattr(arguments, arg_name.replace("-", "_"), None)

            if cli_value is not None:
                config_kwargs[field.name] = cli_value
            else:
                # Fetch the value from environment variables
                value = os.environ.get(env_var)
                if value is None:
                    missing_vars.append(env_var)
                else:
                    # Convert the type if necessary
                    if field.type == bool:
                        try:
                            value = strtobool(value)
                        except argparse.ArgumentTypeError as e:
                            logger.error(f"Invalid boolean for {env_var}: {value}")
                            raise EnvironmentError(f"Invalid boolean value for {env_var}: {value}") from e
                    elif field.type == int:
                        try:
                            value = int(value)
                        except ValueError:
                            logger.error(f"Invalid integer for {env_var}: {value}")
                            raise EnvironmentError(f"Invalid integer value for {env_var}: {value}")
                    config_kwargs[field.name] = value

        if missing_vars:
            logger.error(
                f"Missing environment variables for network '{network_suffix}': {', '.join(missing_vars)}"
            )
            raise EnvironmentError(
                f"Required environment variables are missing for network '{network_suffix}'. "
                f"Please set: {', '.join(missing_vars)}"
            )

        # Create a NetworkConfig instance
        network_config = NetworkConfig(**config_kwargs)
        trustedzones = trustedzone_requirements(network_config.trustedzone_images)
        for image in integration_test_images(network_config):
            if image not in trustedzones:
                raise EnvironmentError(
                    f"{prefix}_INTEGRATION_TEST_IMAGES names {image}, which "
                    f"{prefix}_TRUSTEDZONE_IMAGES does not list")
        check_trustedzone_names(network_config)
        networks.append(network_config)
        logger.info(f"Loaded configuration for network: {network_suffix}")

    return join_unsafe_twins(networks)

def parse_arguments(network_names: list) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ethernity PoX request")
    parser.add_argument(
        "-k",
        "--privatekey",
        help="Etherem DP privatekey (AE6AE8E5CCBFB04590405997EE2D52D2B330726137B875053C36D94E974D162F)",
        required=True
    )
    parser.add_argument(
        "-c",
        "--cpu",
        help="Number of CPUs (count)",
        type=int,
        required=False,
        default=HardwareInfoProvider.get_number_of_cpus()
    )
    parser.add_argument(
        "-m",
        "--memory",
        help="Amount of memory (GB)",
        type=int,
        required=False,
        default=HardwareInfoProvider.get_free_memory()
    )
    parser.add_argument(
        "-s",
        "--storage",
        help="Amount of storage (GB)",
        type=int,
        required=False,
        default=HardwareInfoProvider.get_free_storage()
    )
    parser.add_argument(
        "-b",
        "--bandwidth",
        help="Amount of bandwidth (GB)",
        type=int,
        required=False,
        default=1
    )
    parser.add_argument(
        "-t",
        "--duration",
        help="Amount of time allocated for task (minutes)",
        type=int,
        required=False,
        default=60
    )
    parser.add_argument(
        "-e",
        "--endpoint",
        help="Hostname of a S3 service",
        type=str,
        required=False,
        default="localhost:9000"
    )
    parser.add_argument(
        "-u",
        "--access_key",
        help="Access key (aka user ID) of your account in S3 service.",
        type=str,
        default="swiftstreamadmin",
        required=False
    )
    parser.add_argument(
        "-p",
        "--secret_key",
        help="Secret Key (aka password) of your account in S3 service.",
        type=str,
        default="swiftstreamadmin",
        required=False
    )
    parser.add_argument(
        "-v",
        "--price",
        help="Task price(per hour).",
        type=float,
        default=str(task_price_default),  # Replace with actual default value if available
        required=False
    )
    parser.add_argument(
        "-n",
        "--network",
        help="Networks the node runs on. Specify multiple networks separated by space (e.g., polygon_mainnet polygon_amoy bloxberg_mainnet bloxberg_testnet iotex_testnet). If not specified, all available networks are loaded except the -unsafe ones (bloxberg_testnet_unsafe, litvm_liteforge_unsafe), which run trustedzones without a CAS and are loaded only when named.",
        nargs='+',
        default=["all"],
        required=False
    )
    parser.add_argument(
        "--ipfs_swarm",
        help="IPFS swarm peers list",
        type=str,
        default=str(ipfs_swarm_default),
        required=False
    )
    parser.add_argument(
        "-o",
        "--kubo_url",
        help="Kubo download url",
        type=str,
        default=str(kubo_url_default),
        required=False
    )
    parser.add_argument(
        "-f",
        "--kubo_version",
        help="kubo minimum version",
        type=str,
        default=str(kubo_version_default),
        required=False
    )
    parser.add_argument(
        "-l",
        "--ipfs_connect_url",
        help="IPFS connect URL",
        type=str,
        default=str(ipfs_connect_url_default),
        required=False
    )
    parser.add_argument(
        "--ipfs_gateway_url",
        help="IPFS gateway URL",
        type=str,
        default=str(ipfs_gateway_url_default),
        required=False
    )
    parser.add_argument(
        "-d",
        "--ipfs_timeout",
        help="IPFS timeout",
        type=int,
        default=str(ipfs_timeout_default),
        required=False
    )
    add_network_override_arguments(parser, network_names)

    return parser

parser = parse_arguments(list(NETWORKS.keys()))

arguments = {
    str: [
       'privatekey', 'endpoint', 'access_key', 'secret_key', 'network',  'ipfs_connect_url', 'ipfs_gateway_url', 'ipfs_swarm', 'kubo_url', 'kubo_version'
    ],
    int: ['cpu', 'memory', 'storage', 'bandwidth', 'duration', 'ipfs_timeout'],
    float: ['price']
}
