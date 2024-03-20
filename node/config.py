import os, sys, signal
import argparse
import logging.handlers
from os.path import expanduser

def onImportError():
    os.system("pip3 install psutil==5.9.2")
    os.system("pip3 install python-dotenv==0.21.0")
    os.system("pip3 install minio==7.1.13")
    os.system("pip3 install configobj==5.0.6")
    os.killpg(os.getpgid(), signal.SIGCHLD)
    sys.exit()


try:
    import psutil
    from minio import Minio
    from dotenv import load_dotenv
    from configobj import ConfigObj
except ImportError as e:
    onImportError()

from utils import HardwareInfoProvider

# Load variables from .env and .env.conf if it exists
if os.path.exists('.env'):
    load_dotenv('.env')

if os.path.exists('/home/vagrant/etny/node/config'):
    custom_config = ConfigObj('/home/vagrant/etny/node/config')
    custom_bloxberg_rpc_url = custom_config.get('BLOXBERG_RPC_URL')
    custom_testnet_rpc_url = custom_config.get('TESTNET_RPC_URL')
    custom_polygon_rpc_url = custom_config.get('POLYGON_RPC_URL')
    custom_mumbai_rpc_url = custom_config.get('MUMBAI_RPC_URL')

ipfs_default = os.environ.get('IPFS_HOST')
client_connect_url_default = os.environ.get('CLIENT_CONNECT_URL')
client_bootstrap_url = os.environ.get('CLIENT_BOOTSTRAP_URL')
gas_limit = int(os.environ.get('GAS_LIMIT'))
gas_price_value = os.environ.get('GAS_PRICE_VALUE')

if custom_bloxberg_rpc_url:
   bloxberg_rpc_url = custom_bloxberg_rpc_url;
else:
   bloxberg_rpc_url = os.environ.get('BLOXBERG_RPC_URL');
bloxberg_chain_id = os.environ.get('BLOXBERG_CHAIN_ID');
bloxberg_contract_address = os.environ.get('BLOXBERG_CONTRACT_ADDRESS');
bloxberg_heartbeat_address = os.environ.get('BLOXBERG_HEARTBEAT_CONTRACT_ADDRESS');
bloxberg_image_registry_address = os.environ.get('BLOXBERG_IMAGE_REGISTRY');
bloxberg_gas_price_measure = os.environ.get('BLOXBERG_GAS_PRICE_MEASURE')
bloxberg_task_execution_price_default = os.environ.get('BLOXBERG_TASK_EXECUTION_PRICE_DEFAULT');

if custom_testnet_rpc_url:
   testnet_rpc_url = custom_testnet_rpc_url;
else:
   testnet_rpc_url = os.environ.get('TESTNET_RPC_URL');
testnet_chain_id = os.environ.get('TESTNET_CHAIN_ID');
testnet_contract_address = os.environ.get('TESTNET_CONTRACT_ADDRESS');
testnet_heartbeat_address = os.environ.get('TESTNET_HEARTBEAT_CONTRACT_ADDRESS');
testnet_image_registry_address = os.environ.get('TESTNET_IMAGE_REGISTRY');
testnet_gas_price_measure = os.environ.get('TESTNET_GAS_PRICE_MEASURE')
testnet_task_execution_price_default = os.environ.get('TESTNET_TASK_EXECUTION_PRICE_DEFAULT');

if custom_polygon_rpc_url:
   polygon_rpc_url = custom_polygon_rpc_url;
else:
   polygon_rpc_url = os.environ.get('POLYGON_RPC_URL');
polygon_chain_id = os.environ.get('POLYGON_CHAIN_ID');
polygon_contract_address = os.environ.get('POLYGON_CONTRACT_ADDRESS');
polygon_heartbeat_address = os.environ.get('POLYGON_HEARTBEAT_CONTRACT_ADDRESS');
polygon_image_registry_address = os.environ.get('POLYGON_IMAGE_REGISTRY');
polygon_gas_price_measure = os.environ.get('POLYGON_GAS_PRICE_MEASURE')
polygon_task_execution_price_default = os.environ.get('POLYGON_TASK_EXECUTION_PRICE_DEFAULT');

if custom_mumbai_rpc_url:
   mumbai_rpc_url = custom_mumbai_rpc_url;
else:
   mumbai_rpc_url = os.environ.get('MUMBAI_RPC_URL');
mumbai_chain_id = os.environ.get('MUMBAI_CHAIN_ID');
mumbai_contract_address = os.environ.get('MUMBAI_CONTRACT_ADDRESS');
mumbai_heartbeat_address = os.environ.get('MUMBAI_HEARTBEAT_CONTRACT_ADDRESS');
mumbai_image_registry_address = os.environ.get('MUMBAI_IMAGE_REGISTRY');
mumbai_gas_price_measure = os.environ.get('MUMBAI_GAS_PRICE_MEASURE')
mumbai_task_execution_price_default = os.environ.get('MUMBAI_TASK_EXECUTION_PRICE_DEFAULT');


network_default = "AUTO"
task_price_default = 3
network = None
heart_beat_address = None
gas_price_measure = None
image_registry_address = None

# constants
abi_filepath = os.path.dirname(os.path.realpath(__file__)) + '/docker/pox.abi'
image_registry_abi_filepath = os.path.dirname(os.path.realpath(__file__)) + '/image_registry.abi'
heart_beat_abi_filepath = os.path.dirname(os.path.realpath(__file__)) + '/heart_beat.abi'
auto_update_file_path = os.path.dirname(os.path.realpath(__file__)) + '/auto_update.etny'
heart_beat_log_file_path = os.path.dirname(os.path.realpath(__file__)) + '/heartbeat.etny'
uuid_filepath = expanduser("~") + "/opt/etny/node/UUID"
network_cache_limit = 1
network_cache_filepath = os.path.dirname(os.path.realpath(__file__)) + '/network_cache.txt'
orders_cache_limit = 10000000
orders_cache_filepath = os.path.dirname(os.path.realpath(__file__)) + '/orders_cache.txt'
ipfs_cache_limit = 10000000
ipfs_cache_filepath = os.path.dirname(os.path.realpath(__file__)) + '/ipfs_cache.txt'
dpreq_cache_limit = 10000000
dpreq_filepath = os.path.dirname(os.path.realpath(__file__)) + '/dpreq_cache.txt'
doreq_cache_limit = 10000000
doreq_filepath = os.path.dirname(os.path.realpath(__file__)) + '/doreq_cache.txt'

merged_orders_cache = os.path.dirname(os.path.realpath(__file__)) + '/merged_orders_cache.json'
merged_orders_cache_limit = 10000000

process_orders_cache_filepath = os.path.dirname(os.path.realpath(__file__)) + '/process_order_data.json'

# logger
logger = logging.getLogger("ETNY NODE")
handler = logging.handlers.RotatingFileHandler('/var/log/etny-node.log', maxBytes=2048000, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG if os.environ.get('LOG_LEVEL') == 'debug' else logging.INFO)

contract_call_frequency = int(os.environ.get('CONTRACT_CALL_FREQUENCY', 43200))

# parser
parser = argparse.ArgumentParser(description="Ethernity PoX request")
parser.add_argument("-a", "--address", help="Etherem DP address (0xf17f52151EbEF6C7334FAD080c5704D77216b732)",
                    required=True)
parser.add_argument("-k", "--privatekey",
                    help="Etherem DP privatekey "
                         "(AE6AE8E5CCBFB04590405997EE2D52D2B330726137B875053C36D94E974D162F)",
                    required=True)
parser.add_argument("-r", "--resultaddress",
                    help="Etherem RP address (0xC5fdf4076b8F3A5357c5E395ab970B5B54098Fef)", required=True)
parser.add_argument("-j", "--resultprivatekey",
                    help="Etherem RP privatekey "
                         "(0DBBE8E4AE425A6D2687F1A7E3BA17BC98C673636790F1B8AD91193C05875EF1)",
                    required=True)
parser.add_argument("-c", "--cpu", help="Number of CPUs (count)", required=False,
                    default=str(HardwareInfoProvider.get_number_of_cpus()))
parser.add_argument("-m", "--memory", help="Amount of memory (GB)", required=False,
                    default=str(HardwareInfoProvider.get_free_memory()))
parser.add_argument("-s", "--storage", help="Amount of storage (GB)", required=False,
                    default=str(HardwareInfoProvider.get_free_storage()))
parser.add_argument("-b", "--bandwidth", help="Amount of bandwidth (GB)", required=False, default="1")
parser.add_argument("-t", "--duration", help="Amount of time allocated for task (minutes)", required=False,
                    default="60")
parser.add_argument("-e", "--endpoint", help="Hostname of a S3 service", required=False, default="localhost:9000")
parser.add_argument("-u", "--access_key", help="Access key (aka user ID) of your account in S3 service.",
                    default="swiftstreamadmin",
                    required=False)
parser.add_argument("-p", "--secret_key", help="Secret Key (aka password) of your account in S3 service.",
                    default="swiftstreamadmin",
                    required=False)
parser.add_argument("-v", "--price", help="Task price(per hour).",
                    default=str(task_price_default),
                    required=False)
parser.add_argument("-n", "--network", help="Network the node runs on.",
                    default=str(network_default),
                    required=False)
parser.add_argument("-i", "--ipfshost", help="Default ipfs gateway",
                    default=str(ipfs_default),
                    required=False)
parser.add_argument("-l", "--ipfslocal", help="Local ipfs connect url",
                    default=str(client_connect_url_default),
                    required=False)


arguments = {
    str: ['address', 'privatekey', 'resultaddress', 'resultprivatekey', 'endpoint', 'access_key', 'secret_key', 'network', 'ipfshost', 'ipfslocal'],
    int: ['cpu', 'memory', 'storage', 'storage', 'bandwidth', 'duration', 'price']
}

