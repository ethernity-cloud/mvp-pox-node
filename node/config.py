import os, sys, signal
import argparse
import logging.handlers
from os.path import expanduser
from dataclasses import dataclass, fields
from typing import List
from pathlib import Path
from utils import HardwareInfoProvider
import dependency_manager
from distutils.util import strtobool

dependency_manager.check_dependencies()

import psutil
from minio import Minio
from dotenv import load_dotenv

# Load variables from .env and .env.conf if it exists
if os.path.exists('.env'):
    load_dotenv('.env')

ipfs_default = os.environ.get('IPFS_HOST')
client_connect_url_default = os.environ.get('CLIENT_CONNECT_URL')
client_bootstrap_url = os.environ.get('CLIENT_BOOTSTRAP_URL')
gas_limit = int(os.environ.get('GAS_LIMIT'))
gas_price_value = os.environ.get('GAS_PRICE_VALUE')
skip_integration_test = strtobool(os.environ.get('SKIP_INTEGRATION_TEST'))

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
    task_execution_price_default: int
    integration_test_image: str
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

NETWORKS = {
    "POLYGON": ["MAINNET", "AMOY"],
    "BLOXBERG": ["MAINNET", "TESTNET"],
    "IOTEX": ["TESTNET"],
}


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
                arg_name = f"--{network_suffix.lower()}-{field.name.lower()}"
                
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
        List[NetworkConfig]: A list of network configurations.
    """
    AVAILABLE_NETWORKS = []
    for network in network_names:
        suffixes = NETWORKS.get(network, [])
        for suffix in suffixes:
            network_suffix = f"{network}_{suffix}" if suffix else network
            AVAILABLE_NETWORKS.append(network_suffix.lower())
    
    ALL_NETWORKS = ["all"]
    CURRENT_NETWORKS = ["auto", "openbeta"]
    LEGACY_NETWORKS = ["bloxberg", "testnet", "polygon"]

    lower_networks = [n.lower() for n in arguments.network]
    # Determine which networks to load
    if any(n in ALL_NETWORKS for n in lower_networks):
        # If any special keyword is specified, load all networks
        selected_networks = AVAILABLE_NETWORKS
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
        networks.append(network_config)
        logger.info(f"Loaded configuration for network: {network_suffix}")

    return networks

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
        help="Networks the node runs on. Specify multiple networks separated by space (e.g., polygon_mainnet polygon_amoy bloxberg_mainnet bloxberg_testnet iotex_testnet). If not specified, all available networks are loaded.",
        nargs='+',
        default=["all"],
        required=False
    )
    parser.add_argument(
        "-i",
        "--ipfshost",
        help="Default IPFS gateway",
        type=str,
        default=str(ipfs_default),
        required=False
    )
    parser.add_argument(
        "-l",
        "--ipfslocal",
        help="Local IPFS connect URL",
        type=str,
        default=str(client_connect_url_default),
        required=False
    )

    add_network_override_arguments(parser, network_names)

    return parser

parser = parse_arguments(list(NETWORKS.keys()))

arguments = {
    str: [
       'privatekey', 'endpoint', 'access_key', 'secret_key', 'network', 'ipfshost', 'ipfslocal'
    ],
    int: ['cpu', 'memory', 'storage', 'bandwidth', 'duration'],
    float: ['price']
}
