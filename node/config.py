import os
import argparse
import logging.handlers
from os.path import expanduser
from dotenv import load_dotenv

from utils import HardwareInfoProvider

load_dotenv('.env' if os.path.exists('.env') else '.env.config')  # take environment variables from .env.

# env variables
http_provider = os.environ.get('HTTP_PROVIDER')
contract_address = os.environ.get('CONTRACT_ADDRESS')
ipfs_host = os.environ.get('IPFS_HOST')
client_connect_url = os.environ.get('CLIENT_CONNECT_URL')
client_bootstrap_url = os.environ.get('CLIENT_BOOTSTRAP_URL')
chain_id = int(os.environ.get('CHAIN_ID'))
gas_limit = int(os.environ.get('GAS_LIMIT'))
gas_price_value = os.environ.get('GAS_PRICE_VALUE')
gas_price_measure = os.environ.get('GAS_PRICE_MEASURE')

# constants
abi_filepath = os.path.dirname(os.path.realpath(__file__)) + '/docker/pox.abi'
uuid_filepath = expanduser("~") + "/opt/etny/node/UUID"
orders_cache_limit = 10000000
orders_cache_filepath = os.path.dirname(os.path.realpath(__file__)) + '/orders_cache.txt'
ipfs_cache_limit = 10000000
ipfs_cache_filepath = os.path.dirname(os.path.realpath(__file__)) + '/ipfs_cache.txt'
dpreq_cache_limit = 10000000
dpreq_filepath = os.path.dirname(os.path.realpath(__file__)) + '/dpreq_cache.txt'
doreq_cache_limit = 10000000
doreq_filepath = os.path.dirname(os.path.realpath(__file__)) + '/doreq_cache.txt'
dp_request_timeout = 60 * 60 * 24

# logger
logger = logging.getLogger("ETNY NODE")
handler = logging.handlers.RotatingFileHandler('/var/log/etny-node.log', maxBytes=2048000, backupCount=5)
formatter = logging.Formatter('%(asctime)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

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
arguments = {
    str: ['address', 'privatekey', 'resultaddress', 'resultprivatekey'],
    int: ['cpu', 'memory', 'storage', 'storage', 'bandwidth', 'duration']
} 