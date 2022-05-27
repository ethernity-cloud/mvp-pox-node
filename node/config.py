import os
import logging.handlers
from os.path import expanduser

from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env.

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
abi_filepath = os.path.dirname(os.path.realpath(__file__)) + '/pox.abi'
uuid_filepath = expanduser("~") + "/opt/etny/node/UUID"
dp_request_timeout = 60 * 60 * 24

# logger
logger = logging.getLogger("ETNY NODE")
handler = logging.handlers.RotatingFileHandler('/var/log/etny-node.log', maxBytes=2048000, backupCount=5)
formatter = logging.Formatter('%(asctime)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)



