#!/usr/bin/python3

import io, os, time, json, sys, argparse, threading
from types import SimpleNamespace
from collections import defaultdict
import concurrent.futures
import shutil
from pathlib import Path

import logging
import config

from eth_account import Account
from web3 import Web3
from web3 import exceptions
from web3.middleware import ExtraDataToPOAMiddleware
from web3 import middleware
from web3.gas_strategies.time_based import fast_gas_price_strategy
from web3.gas_strategies.rpc import rpc_gas_price_strategy

from utils import get_or_generate_uuid, run_subprocess, retry, Storage, Cache, ListCache, ListCacheWithTimestamp, MergedOrdersCache, subprocess, get_node_geo, HardwareInfoProvider
from models import *
from error_messages import errorMessages
from swift_stream_service import SwiftStreamService
from cache_config import CacheConfig

logger = config.logger 
task_running_on = None
task_lock = threading.Lock()
integration_test_complete = False
integration_test_lock = threading.Lock()

stop_event = threading.Event()

class NetworkLoggerAdapter(logging.LoggerAdapter):
    def __init__(self, logger, network):
        super().__init__(logger, {})
        self.network = network

    def process(self, msg, kwargs):
        """
        Prepend the network name to the log message.
        """
        return f"[{self.network}] {msg}", kwargs

class EtnyPoXNode:
    logger = None

    def __init__(self, network):

        self.__address = None
        self.__privatekey = None
        self.__resultaddress = None
        self.__resultprivatekey = None
        self.__cpu = None
        self.__memory = None
        self.__storage = None
        self.__bandwidth = None
        self.__duration = None
        self.__endpoint = None
        self.__access_key = None
        self.__secret_key = None
        self.__network = None
        self.__ipfs_host = None
        self.__ipfs_port = None
        self.__ipfs_id = None
        self.__ipfs_connect_url = None
        self.__ipfs_timeout = None
        self.__price = None
        self.__orders = defaultdict(lambda: None)
        self.__do_requests_build_pending = True


        self.parse_arguments(config.arguments, config.parser)
        self.__network = network.name
        self.logger = NetworkLoggerAdapter(config.logger, self.__network)
        logger = self.logger

        logger.info(f"Initializing Ethernity CLOUD Agent v{config.version}");

        logger.info(f"Configured network is: {self.__network}")
        self.__network_config = network
        self.__price = int(network.task_execution_price);

        try:
            with open(config.abi_filepath) as f:
                self.__contract_abi = f.read()

            self.__w3 = Web3(Web3.HTTPProvider(self.__network_config.rpc_url, request_kwargs={'timeout': 120}))

            if network.middleware is not None:
                self.__w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            self.__acct = Account.from_key(self.__privatekey)
            self.__address = self.__acct.address
            self.__etny = self.__w3.eth.contract(
                address=self.__w3.to_checksum_address(self.__network_config.contract_address),
                abi=self.__contract_abi
            )

            balance = self.__w3.eth.get_balance(self.__address)

            if balance < int(network.minimum_gas_at_start):
               logger.error(f"Not enough gas at {self.__address} to run node agent, exiting")
               raise Exception(f"Not enough gas on network {self.__network}: {balance}")

        except Exception as e:
            logger.info(f"Error: {e}")
            raise Exception(e)

        self.__node_geo = get_node_geo();
        self.__number_of_cpus = int(HardwareInfoProvider.get_number_of_cpus());
        self.__free_memory = int(HardwareInfoProvider.get_free_memory());
        self.__free_storage = int(HardwareInfoProvider.get_free_storage());

        with open(config.image_registry_abi_filepath) as f:
            self.__image_registry_abi = f.read()

        with open(config.heart_beat_abi_filepath) as f:
            self.__heart_beat_abi = f.read()

        self.__image_registry = self.__w3.eth.contract(
            address=self.__w3.to_checksum_address(self.__network_config.image_registry_contract_address),
            abi=self.__image_registry_abi)
        self.__heart_beat = self.__w3.eth.contract(
            address=self.__w3.to_checksum_address(self.__network_config.heartbeat_contract_address),
            abi=self.__heart_beat_abi)
        self.__nonce = self.__w3.eth.get_transaction_count(self.__address)
        self.__dprequest = 0
        self.__order_id = 0
        self.__total_nodes_count = 0
        self.__is_first_cycle = defaultdict(lambda: True)
        self.can_run_under_sgx = False

        logger.info(f"NodeID: {self.__address}");
        logger.info(f"Network: {self.__address}");
        logger.info(f"RPC URL: {self.__network_config.rpc_url}");
        logger.info(f"ChainID: {self.__network_config.chain_id}");
        logger.info(f"Protocol Contract Address: %s", self.__network_config.contract_address);
        logger.info(f"Heartbeat Contract Address: %s", self.__network_config.heartbeat_contract_address);
        logger.info(f"Image Registry Address: %s", self.__network_config.image_registry_contract_address);
        logger.info(f"Minimum reward for order processing: %d %s / hour", self.__price, self.__network_config.token_name);
        logger.info(f"IPFS Host: %s", self.__ipfs_host);
        logger.info(f"IPFS Connect URL: %s", self.__ipfs_connect_url);
        logger.info(f"Node number of cpus: %s", self.__number_of_cpus);
        logger.info(f"Node free memory: %s", self.__free_memory);
        logger.info(f"Node free storage: %s", self.__free_storage);
        logger.info(f"Node geo: %s", self.__node_geo);


        [enclave_image_hash, _, docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey(self.__network_config.integration_test_image, 'v3')
        logger.info(f"Docker registry hash: {enclave_image_hash}")
        logger.info(f"Docker composer hash: {docker_compose_hash}")

        self.cache_config = CacheConfig(network.name)
        self.network_cache = Cache(self.cache_config.network_cache_limit, self.cache_config.network_cache_filepath)

        if self.network_cache.get("NETWORK") == "BLOXBERG" and self.__network == "bloxberg_mainnet":
           self.__migrate_cache()
           self.network_cache.add("NETWORK","MIGRATED_FROM_BLOXBERG")

        if self.network_cache.get("NETWORK") == "TESTNET" and self.__network == "bloxberg_testnet":
           self.__migrate_cache()
           self.network_cache.add("NETWORK","MIGRATED_FROM_TESTNET")

        if self.network_cache.get("NETWORK") == "POLYGON" and self.__network == "polygon_mainnet":
           self.__migrate_cache()
           self.network_cache.add("NETWORK","MIGRATED_FROM_POLYGON")

        while get_task_running_on() is not None:
           time.sleep(1)

        set_task_running_on(self.__network)

        os.chdir(self.cache_config.base_path)

        self.orders_cache = Cache(self.cache_config.orders_cache_limit, self.cache_config.orders_cache_filepath)
        self.dpreq_cache = ListCache(self.cache_config.dpreq_cache_limit, self.cache_config.dpreq_filepath)
        self.doreq_cache = ListCache(self.cache_config.doreq_cache_limit, self.cache_config.doreq_filepath)
        self.ipfs_cache = ListCacheWithTimestamp(self.cache_config.ipfs_cache_limit, self.cache_config.ipfs_cache_filepath)
        self.storage = Storage(self.__ipfs_host, self.__ipfs_port, self.__ipfs_id, self.__ipfs_timeout, self.__ipfs_connect_url,
                               self.ipfs_cache, logger, self.cache_config.base_path)
        self.merged_orders_cache = MergedOrdersCache(self.cache_config.merged_orders_cache_limit, self.cache_config.merged_orders_cache)
        self.swift_stream_service = SwiftStreamService(self.__endpoint,
                                                       self.__access_key,
                                                       self.__secret_key)
        self.process_order_data = {}


        self.__uuid = get_or_generate_uuid(config.uuid_filepath)

        if config.skip_integration_test == True or get_integration_test_complete():

           if config.skip_integration_test:
               logger.warning('Agent skipped SGX integration test, SGX capabilitties overwritten by configuration')
           else:
               logger.info('SGX integration test completed already')

           order_id = 'integration_test'
           docker_compose_file = f'{self.cache_config.base_path}/{docker_compose_hash}'
           self.integration_bucket_name = 'etny-bucket-integration'
           self.build_prerequisites_integration_test(self.integration_bucket_name, order_id, docker_compose_file)
           self.__clean_up_integration_test()

           self.can_run_under_sgx = True
        else:
           self.__run_integration_test()


        self.__clear_ipfs_cache()
        reset_task_running_on()

          
    def __migrate_cache(self):
        logger = self.logger

        logger.info(f"Migrating cache from legacy cache {self.network_cache.get('NETWORK')} to network dir {self.__network}")
        self.cache_config_legacy = CacheConfig('./')
        self.ipfs_cache_legacy = ListCache(self.cache_config_legacy.ipfs_cache_limit, self.cache_config_legacy.ipfs_cache_filepath)

        self.storage_legacy = Storage(self.__ipfs_host, self.__ipfs_port, self.__ipfs_id, self.__ipfs_timeout, self.__ipfs_connect_url,
                               self.ipfs_cache_legacy, logger, self.cache_config_legacy.base_path)

        for hash in list(self.ipfs_cache_legacy.get_values):
            self.storage_legacy.mig(hash, self.cache_config.base_path)

        for attr in dir(self.cache_config_legacy):
            if attr.startswith('_'):
                continue

            if attr == 'base_path':
                continue

            if attr == 'network_cache_filepath':
                continue

            legacy_path = getattr(self.cache_config_legacy, attr, None)

            if isinstance(legacy_path, Path) and legacy_path.is_absolute():
                src = getattr(self.cache_config_legacy, attr)
                dest = getattr(self.cache_config, attr)

            try:
                shutil.copy2(src, dest)
                logger.debug(f"Copied '{src}' to '{dest}'")
            except FileNotFoundError:
                logger.warning(f"Source file '{src}' does not exist and was skipped.")
            except Exception as e:
                logger.error(f"Failed to copy '{src}' to '{dest}': {e}")
       

    def __clear_ipfs_cache(self):
        logger = self.logger

        logger.info(f"Cleaning up ipfs cache")

        ONE_WEEK_SECONDS = 7 * 24 * 60 * 60  # Number of seconds in one week
        current_time = time.time()
        threshold_time = current_time - ONE_WEEK_SECONDS
        
        trustedzone_images = self.__network_config.trustedzone_images.split(',')

        keep_hashes = []

        for image in trustedzone_images:
            while True:
                try:
                    time.sleep(self.__network_config.rpc_delay/1000)
                    [enclave_image_hash, _,
                     docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey(image, 'v3')
                    break
                except Exception as e:
                    continue

            keep_hashes.append(enclave_image_hash)
            keep_hashes.append(docker_compose_hash)

        for hash in list(self.ipfs_cache.get_values):
          if hash not in keep_hashes:
            timestamp = self.ipfs_cache.get_timestamp(hash)
            if timestamp:
                age = current_time - timestamp
                if age > ONE_WEEK_SECONDS:
                    logger.debug(f"Deleting {hash} (Age: {age / 3600:.2f} hours)")
                    try:
                        self.storage.pin_rm(hash)
                        self.storage.rm(hash)
                        logger.debug(f"Successfully deleted {hash}")
                    except Exception as e:
                        logger.debug(f"Failed to delete {hash}: {e}")
                else:
                    logger.debug(f"Hash {hash} is not older than one week (Age: {age / 3600:.2f} hours). Keeping pin.")
            else:
                logger.warning(f"No timestamp found for {hash}. Unable to determine age. Skipping deletion.")
          else:
            self.storage.pin_add(hash)
            self.ipfs_cache.add(hash)

    def generate_process_order_data(self, write=False):

        if not os.path.exists(self.cache_config.process_orders_cache_filepath) or write == True:
            self.process_order_data = {"process_order_retry_counter": 0,
                                       "order_id": self.__order_id,
                                       "uuid": self.__uuid}

            json_object = json.dumps(self.process_order_data, indent=4)

            with open(self.cache_config.process_orders_cache_filepath, "w") as outfile:
                outfile.write(json_object)

        else:
            with open(self.cache_config.process_orders_cache_filepath, 'r') as openfile:
                self.process_order_data = json.load(openfile)

    def parse_arguments(self, arguments, parser):
        parser, unknown_args = parser.parse_known_args()
        for args_type, args in arguments.items():
            for arg in args:
                setattr(self, "_" + self.__class__.__name__ + "__" + arg, args_type(getattr(parser, arg)))

    def cache_dp_requests(self):
        logger = self.logger

        if not stop_event.is_set():
            try:
                time.sleep(self.__network_config.rpc_delay/1000)
                my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
                cached_ids = self.dpreq_cache.get_values
                req_to_process = sorted(set(my_dp_requests) - set(cached_ids))
       
                total_requests = len(req_to_process)
                threshold = 0

                for idx, req_id in enumerate(req_to_process, start=1):

                    self.__call_heart_beat()

                    if stop_event.is_set():
                        break

                    percent_complete = (idx * 100) // total_requests
                
                    if percent_complete >= threshold and total_requests > 1:
                        logger.info(f"Building DP requests cache [STAGE 1]: {percent_complete}% ({idx} / {total_requests})")
                        threshold += 10  # Increment to the next threshold
                  
                    logger.debug(f"Cleaning up DP request {req_id}")
                    time.sleep(self.__network_config.rpc_delay/1000)
                    req_uuid = self.__etny.caller()._getDPRequestMetadata(req_id)[1]
                    if req_uuid != self.__uuid:
                        logger.debug(f"Skipping DP request {req_id}, not mine")
                        self.__dprequest = req_id
                        order_details = self._getOrder()
                        self.dpreq_cache.add(req_id)
                        continue
                    time.sleep(self.__network_config.rpc_delay/1000)
                    req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
                    if req.status == RequestStatus.CANCELED:
                        self.__dprequest = req_id
                        order_details = self._getOrder()
                        self.dpreq_cache.add(req_id)
                    elif req.status == RequestStatus.BOOKED:
                        logger.debug(f"DP Request {req_id} already assigned to order")
                        self.__dprequest = req_id
                        order_details = self._getOrder()
                        [order_id, order] = order_details
                        if order.status == OrderStatus.CLOSED:
                            logger.debug(f"DP request {self.__dprequest} completed successfully!")
                            self.dpreq_cache.add(self.__dprequest)
                        if order.status == OrderStatus.OPEN:
                            logger.debug("Order was never approved, skipping")

                if total_requests > 1 and not stop_event.is_set():
                    logger.info(f"Building DP requests cache [STAGE 1]: 100%")
                    logger.info(f"Finished building DP requests cache [STAGE 1]")
                        
            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')

    def resume_pending_dp_requests(self):
        logger = self.logger

        if not stop_event.is_set():
            try:
                time.sleep(self.__network_config.rpc_delay/1000)
                my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
                cached_ids = self.dpreq_cache.get_values
                req_to_process = sorted(set(my_dp_requests) - set(cached_ids))

                total_requests = len(req_to_process)
                threshold = 0

                for idx, req_id in enumerate(req_to_process, start=1):

                    balance = self.__w3.eth.get_balance(self.__address)

                    if balance < int(self.__network_config.minimum_gas_at_start):
                        logger.error("Not enough gas to run on this network, exiting")
                        break

                    if stop_event.is_set():
                        break

                    percent_complete = (idx * 100) // total_requests

                    if percent_complete >= threshold and total_requests > 1:
                        logger.info(f"Building DP requests cache [STAGE 2]: {percent_complete}% ({idx} / {total_requests})")
                        threshold += 10  # Increment to the next threshold

                    time.sleep(self.__network_config.rpc_delay/1000)
                    req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
                    if req.status == RequestStatus.BOOKED:
                        logger.debug(f"DP Request {req_id} already assigned to order")
                        self.__dprequest = req_id
                        self.process_dp_request()

                if total_requests > 1 and not stop_event.is_set():
                    logger.info(f"Building DP requests cache [STAGE 2]: 100%")
                    logger.info(f"Finished building DP requests cache [STAGE 2]")


            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')

    def resume_available_dp_requests(self):
        logger = self.logger

        if not stop_event.is_set():
            try:
                time.sleep(self.__network_config.rpc_delay/1000)
                my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
                cached_ids = self.dpreq_cache.get_values
                req_to_process = sorted(set(my_dp_requests) - set(cached_ids))


                for idx, req_id in enumerate(req_to_process, start=1):
                    if stop_event.is_set():
                        break

                    time.sleep(self.__network_config.rpc_delay/1000)
                    req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
                    if req.status == RequestStatus.AVAILABLE:
                        logger.info(f"DP Request {req_id} resumed. Unlocking the value of decentralization. ")
                        self.__dprequest = req_id
                        self.process_dp_request()
                    else:
                        logger.debug(f"DP Request {req_id} should be in cache already with status {req.status}")

            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')



    def _limited_arg(self, item, allowed_max=255):
        return allowed_max if item > allowed_max else item

    def add_dp_request(self, waiting_period_on_error=15, beginning_of_recursion=None):
        logger = self.logger

        if self.__price is None:
            self.__price = 1


        # Getting available hardware resources
        self.__number_of_cpus = int(HardwareInfoProvider.get_number_of_cpus());
        self.__free_memory = int(HardwareInfoProvider.get_free_memory());
        self.__free_storage = int(HardwareInfoProvider.get_free_storage());

        params = [
            self._limited_arg(self.__number_of_cpus),
            self._limited_arg(self.__free_memory),
            self._limited_arg(self.__free_storage),
            self._limited_arg(self.__bandwidth),
            self.__duration,
            self.__price,
            self.__uuid,
            "v3",
            self.__node_geo,
            ""
        ]

        max_retries = 20
        retries = 0

        while True: 
          try:
            logger.info("Preparing transaction for new DP request")
            time.sleep(self.__network_config.rpc_delay/1000)
            unicorn_txn = self.__etny.functions._addDPRequest(*params).build_transaction(self.get_transaction_build())
            _hash = self.send_transaction(unicorn_txn)
            logger.info(f"TXID {_hash} pending... ")
            receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
            processed_logs = self.__etny.events._addDPRequestEV().process_receipt(receipt)
            self.__dprequest = processed_logs[0].args._rowNumber
            if receipt.status == 1:
                logger.info(f"TXID {_hash} confirmed!")
                break
          except Exception as ex:
            retries += 1
            logger.warning(f"Warning while adding DP request. Retry {retries}/{max_retries}. Message: {ex}")
            if retries == max_retries:
              logger.error("Maximum retries reached. Aborting.")
              raise
            time.sleep(5)

        logger.info(f"DP Request {self.__dprequest} initialized. Unlocking the value of decentralization.")


    def cancel_dp_request(self, req):
        logger = self.logger

        logger.info(f"Cancelling DP request {req}")

        while True:
            try:
                logger.info("Preparing transaction for DO request cancellation")
                time.sleep(self.__network_config.rpc_delay/1000)
                unicorn_txn = self.__etny.functions._cancelDPRequest(req).build_transaction(self.get_transaction_build())
                _hash = self.send_transaction(unicorn_txn)
                logger.info(f"TXID {_hash} pending... ")
                receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
                if receipt.status == 1:
                    logger.info(f"TXID {_hash} confirmed!")
                    break
            except Exception as ex:
                logger.warning(f"Unable to cancel  DP request - {req}: Error: {ex}")
                logger.warning(f"Retrying")

        logger.info(f"DP request {req} cancelled successfully!")
        time.sleep(5)

    def ipfs_timeout_cancel(self, order_id):
        result = 'Error: cannot download files from IPFS'
        self.add_result_to_order(order_id, result)


    def calculate_reward(self):
        logger = self.logger

        [order_id, order] = self._getOrder()

        do_req = DORequest(self.__etny.caller()._getDORequest(order.do_req))
        if self.__network_config.reward_type == 1:
            total_amount = do_req.price * do_req.duration
            network_fee = total_amount * self.__network_config.network_fee / 100
            enclave_fee = total_amount * self.__network_config.enclave_fee / 100
            operator_fee = total_amount - network_fee - enclave_fee
            reward = round(operator_fee, 2)
        elif self.__network_config.reward_type == 2:
            total_amount = do_req.price * do_req.duration
            base_amount = (total_amount * 100) / ( 100 + self.__network_config.network_fee + self.__network_config.enclave_fee )
            network_fee = base_amount * self.__network_config.network_fee / 100
            enclave_fee = base_amount * self.__network_config.enclave_fee / 100
            operator_fee = total_amount - network_fee - enclave_fee
            reward = round(operator_fee, 2)

        logger.info("***")
        logger.info(f"Reward: {reward} {self.__network_config.token_name}. You’ve earned it. ")
        logger.info("***")

        if self.__network_config.network_type == "MAINNET":
            logger.info(f"HODL your {self.__network_config.token_name} for long-term growth. Payout after validation.")

    def process_order(self, order_id, metadata=None):
        logger = self.logger


        logger.debug(f"Processing order {order_id}")

        try:
            with open(self.cache_config.process_orders_cache_filepath, 'r') as openfile:
                self.process_order_data = json.load(openfile)
        except Exception as e:
            pass
         
        if not self.process_order_data or self.process_order_data["order_id"] != order_id:
            self.process_order_data["order_id"] = order_id
            self.process_order_data["process_order_retry_counter"] = 0

        # this line should be checked later
        if not metadata:
            while True:
                try:
                    time.sleep(self.__network_config.rpc_delay/1000)
                    order = Order(self.__etny.caller()._getOrder(order_id))
                    metadata = self.__etny.caller()._getDORequestMetadata(order.do_req)
                    break
                except Exceptiona as e:
                    logger.warning(f"Unable to get order metadata: {e}")
                    logger.werning("Retrying")

        if self.process_order_data['process_order_retry_counter'] > 10:
            if metadata[1].startswith('v1:') == 1:
                logger.debug('Building result ')
                result = self.build_result_format_v1("[Warn]",
                                                     f'Too many retries for the current order_id: {order_id}')
                logger.debug(f'Result is: {result}')
                self.add_result_to_order(order_id, result)
                return

            else:
                logger.debug('Building result ')
                logger.warn('Too many retries for the current order_id: %d', order_id)
                logger.info('Adding result to order')
                result_msg='[Warn] Order execution failed more than 10 times'
                self.add_result_to_order(order_id, result_msg)
                return

        self.process_order_data['process_order_retry_counter'] += 1
        json_object = json.dumps(self.process_order_data, indent=4)
        with open(self.cache_config.process_orders_cache_filepath, "w") as outfile:
            outfile.write(json_object)

        #self.add_processor_to_order(order_id)
        try:
            version = 0
            if metadata[1].startswith('v3:'):
                version = 3
                [v3, enclave_image_hash, enclave_image_name, docker_compose_hash, challenge_hash, public_cert] = metadata[
                    1].split(':')
                 
        except Exception as e:
            pass

        logger.debug(f'Running version v{version}')
        if version == 3:
            try:
                logger.debug(f"Downloading IPFS Image: {enclave_image_hash}")
                logger.debug(f"Downloading IPFS docker yml file: {docker_compose_hash}")
                logger.debug(f"Downloading IPFS Payload Hash: {metadata[2]}")
                logger.debug(f"Downloading IPFS FileSet Hash: {metadata[3]}")
                logger.debug(f"Downloading IPFS Challenge Hash: {challenge_hash}")
            except Exception as e:
                logger.info(str(e))

            payload_hash = metadata[2].split(':')[1]
            input_hash = metadata[3].split(':')[1]
            list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash, challenge_hash, payload_hash]
            if input_hash is not None and len(input_hash) > 0:
                list_of_ipfs_hashes.append(input_hash)

            if self.process_order_data['process_order_retry_counter'] <= 10:
                logger.info(f"Fetching task data for DO Request {order.do_req} from IPFS.")
                if not self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3):
                    logger.info("Cannot download data from IPFS, cancelling processing")
                    self.ipfs_timeout_cancel(order_id)
                    self.dpreq_cache.add(order.dp_req)
                    return

            payload_file = f'{self.cache_config.base_path}/{payload_hash}'
            if input_hash is not None and len(input_hash) > 0:
                input_file = f'{self.cache_config.base_path}/{input_hash}'
                logger.info('input hash is not none: ', input_file)
            else:
                input_file = None


            os.chdir(self.cache_config.base_path)

            logger.info("Task preloaded. Preparing docker environment")
            run_subprocess(
                ['docker-compose', '-f', f'../docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
                logger)

            docker_compose_file = f'{self.cache_config.base_path}/{docker_compose_hash}'
            challenge_file = f'{self.cache_config.base_path}/{challenge_hash}'
            challenge_content = self.read_file(challenge_file)
            bucket_name = f'{enclave_image_name}-{v3}'
            logger.debug(f'Preparing prerequisites for {v3}')
            self.build_prerequisites_v3(bucket_name, order_id, payload_file, input_file,
                                        docker_compose_file, challenge_content)

            logger.debug("Stopping previous docker registry")
            run_subprocess(['docker', 'stop', 'registry'], logger)
            logger.debug("Cleaning up docker registry")
            run_subprocess(['docker', 'stop', 'etny-securelock'], logger)
            run_subprocess(['docker', 'stop', 'etny-trustedzone'], logger)
            run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
            logger.debug("Running new docker registry")
            logger.debug(str(self.cache_config.base_path) + '/' + enclave_image_hash + ':/var/lib/registry')

            logger.debug("Stopping previous docker las")
            run_subprocess(['docker', 'stop', 'las'], logger)
            logger.debug("Removing previous docker las")
            run_subprocess(['docker', 'rm', 'las'], logger)
            run_subprocess([
                'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
                str(self.cache_config.base_path) + '/' + enclave_image_hash + ':/var/lib/registry',
                'registry:2'
            ], logger)

            os.chdir(self.cache_config.base_path)
            logger.debug("Cleaning up docker container")
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down', '-d'
            ], logger)

            logger.debug("Started enclave execution")

            os.chdir(self.cache_config.base_path)

            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'up', '-d'
            ], logger)

            logger.info('Docker environment ready. Execution started in SGX enclave')
            status_enclave = self.wait_for_enclave_v2(bucket_name, 'result.txt', 3600)
            status_enclave = self.wait_for_enclave_v2(bucket_name, 'transaction.txt', 60)
            logger.info('Enclave finished the execution')

            if status_enclave == True:
                logger.debug(f'Uploading result to {enclave_image_name}-{v3} bucket')
                status, result_data = self.swift_stream_service.get_file_content(bucket_name, "result.txt")
                if not status:
                    logger.debug(result_data)

                with open(f'{self.order_folder}/result.txt', 'w') as f:
                    f.write(result_data)
                logger.debug(f'[v3] Result file successfully downloaded to {self.order_folder}/result.txt')
                result_hash = self.upload_result_to_ipfs(f'{self.order_folder}/result.txt')
                logger.debug(f'[v3] Result file successfully uploaded to IPFS with hash: {result_hash}')
                logger.debug(f'Result file successfully uploaded to {enclave_image_name}-{v3} bucket')
                logger.debug('Reading transaction from file')
                status, transaction_data = self.swift_stream_service.get_file_content(bucket_name, "transaction.txt")
                if not status:
                   logger.debug(transaction_data)
                logger.debug('Building result for v3')
                result = self.build_result_format_v3(result_hash, transaction_data)
                logger.debug(f'Result is: {result}')
                self.add_result_to_order(order_id, result)
                logger.info("ZK proof added. Task integrity submitted for validation.")
                self.calculate_reward()
            else:
                result = self.build_result_format_v3("[WARN]","Task execution timed out");
                self.add_result_to_order(order_id, result);

            self.dpreq_cache.add(self.__dprequest)
            logger.debug('Cleaning up environment')
            logger.debug('Cleaning up SecureLock and TrustedZone containers.')
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)

    def wait_for_enclave(self, timeout=120):
        logger = self.logger

        i = 0
        while True:
            time.sleep(1)
            i = i + 1
            if i > timeout:
                break
            if os.path.exists(f'{self.order_folder}/result.txt'):
                break

        logger.info('enclave finished the execution')

    def wait_for_enclave_v2(self, bucket_name, object_name, timeout=120):
        logger = self.logger

        i = 0
        logger.debug(f'Checking if object {object_name} exists in bucket {bucket_name}')
        while True:
            time.sleep(1)
            i = i + 1
            if i > timeout:
                break
            (status, result) = self.swift_stream_service.is_object_in_bucket(bucket_name, object_name)
            if status:
                logger.debug(f'Object found!')
                return True

        logger.info('Enclave execution timed out')
        return False

    def build_result_format_v1(self, result_hash, transaction_hex):
        return f'v1:{transaction_hex}:{result_hash}'

    def build_result_format_v2(self, result_hash, transaction_hex):
        return f'v2:{transaction_hex}:{result_hash}'

    def build_result_format_v3(self, result_hash, transaction_hex):
        return f'v3:{transaction_hex}:{result_hash}'

    def add_result_to_order(self, order_id, result):
        logger = self.logger

        logger.info(f'Packaging results for blockchain submission.')

        max_retries = 20
        retries = 0

        while True:
            try:
                unicorn_txn = self.__etny.functions._addResultToOrder(
                    order_id, result
                ).build_transaction(self.get_transaction_build())

                _hash = self.send_transaction(unicorn_txn)
                logger.info(f"TXID {_hash} pending... ")
                receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
                if receipt.status == 1:
                    logger.info(f"TXID {_hash} confirmed!")
                    self.dpreq_cache.add(self.__dprequest)
                    break
            except Exception as ex:
                retries += 1
                logger.warning(f"Warning while adding result to Order. Retry {retries}/{max_retries}. Warning Message: {ex}")
                if retries == max_retries:
                    logger.error("Maximum retries reached. Aborting.")
                    raise
                time.sleep(5)
        
    def upload_result_to_ipfs(self, result_file):
        response = self.storage.upload(result_file)
        return response

    def create_folder_v1(self, order_directory):
        if not os.path.exists(order_directory):
            os.makedirs(order_directory)

    def read_file(self, chanllenge_file):
        with open(chanllenge_file, "r") as file:
            contents = file.read()

        return contents

    def __create_empty_file(self, file_path: str) -> bool:
        logger = self.logger

        try:
            open(file_path, 'w').close()
        except OSError:
            logger.error('Failed creating the file')
            return False

        logger.info('File created')
        return True

    def build_prerequisites_v1(self, order_id, payload_file, input_file, docker_compose_file, challenge):
        logger = self.logger

        self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
        self.create_folder_v1(self.order_folder)
        self.copy_order_files(payload_file, f'{self.order_folder}/payload.py')
        if input_file is not None:
            self.copy_order_files(input_file, f'{self.order_folder}/input.txt')
        else:
            status = self.__create_empty_file(f'{self.order_folder}/input.txt')
            if not status:
                raise "Could not create context."

        self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'

        self.copy_order_files(docker_compose_file, self.order_docker_compose_file)
        self.set_retry_policy_on_fail_for_compose()

        self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)
        env_content = self.get_enclave_env_dictionary(order_id, challenge)
        self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

    def build_prerequisites_v2(self, bucket_name, order_id, payload_file, input_file, docker_compose_file, challenge):
        logger = self.logger

        logger.debug('Cleaning up swift-stream bucket.')
        self.swift_stream_service.delete_bucket(bucket_name)
        logger.debug('Creating new bucket.')
        self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
        self.create_folder_v1(self.order_folder)
        (status, msg) = self.swift_stream_service.create_bucket(bucket_name)
        if not status:
            logger.error(msg)

        self.payload_file_name = "payload.etny"
        (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                              self.payload_file_name,
                                                              payload_file)
        if not status:
            logger.error(msg)

        self.input_file_name = "input.txt"
        if input_file is None:
            (status, msg) = self.swift_stream_service.put_file_content(bucket_name,
                                                                       self.input_file_name,
                                                                       "",
                                                                       io.BytesIO(b""))
        else:
            (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                                  self.input_file_name,
                                                                  input_file)
        if (not status):
            logger.error(msg)

        self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'
        self.copy_order_files(docker_compose_file, self.order_docker_compose_file)

        self.set_retry_policy_on_fail_for_compose()
        self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)

        env_content = self.get_enclave_env_dictionary(order_id, challenge)
        self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

        (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                              ".env",
                                                              f'{self.order_folder}/.env')
        if not status:
            logger.error(msg)

    def build_prerequisites_v3(self, bucket_name, order_id, payload_file, input_file, docker_compose_file, challenge):
        logger = self.logger

        logger.debug('Cleaning up swift-stream bucket.')
        self.swift_stream_service.delete_bucket(bucket_name)
        logger.debug('Creating new bucket.')
        self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
        self.create_folder_v1(self.order_folder)
        (status, msg) = self.swift_stream_service.create_bucket(bucket_name)
        if not status:
            logger.error(msg)

        self.payload_file_name = "payload.etny"
        (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                              self.payload_file_name,
                                                              payload_file)
        if not status:
            logger.error(msg)

        self.input_file_name = "input.txt"
        if input_file is None:
            (status, msg) = self.swift_stream_service.put_file_content(bucket_name,
                                                                       self.input_file_name,
                                                                       "",
                                                                       io.BytesIO(b""))
        else:
            (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                                  self.input_file_name,
                                                                  input_file)
        if (not status):
            logger.error(msg)

        self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'
        self.copy_order_files(docker_compose_file, self.order_docker_compose_file)

        self.set_retry_policy_on_fail_for_compose()
        self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)

        env_content = self.get_enclave_env_dictionary(order_id, challenge)
        self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

        (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                              ".env",
                                                              f'{self.order_folder}/.env')
        if not status:
            logger.error(msg)

    def set_retry_policy_on_fail_for_compose(self):
        with open(self.order_docker_compose_file, "r") as f:
            content = f.read()
        content = content.replace("restart: on-failure", "restart: on-failure:20")
        with open(self.order_docker_compose_file, "w") as f:
            f.write(content)

    def copy_order_files(self, source, dest):
        logger = self.logger

        if os.path.isfile(source):
            shutil.copy(source, dest)
        else:
            logger.debug('The copied path is not a file')

    def generate_enclave_env_file(self, env_file, env_dictionary):
        with open(env_file, 'w') as f:
            for key, value in env_dictionary.items():
                f.write(f'{key}={value}\n')
        f.close()

    def get_enclave_env_dictionary(self, order_id, challenge):
        env_vars = {
            "ETNY_CHAIN_ID": self.__network_config.chain_id,
            "ETNY_SMART_CONTRACT_ADDRESS": self.__network_config.contract_address,
            "ETNY_WEB3_PROVIDER": self.__network_config.rpc_url,
            "ETNY_CLIENT_CHALLENGE": challenge,
            "ETNY_ORDER_ID": order_id,
            "ETNY_NGROK_AUTHTOKEN": "DEFAULT"
        }
        return env_vars

    def update_enclave_docker_compose(self, docker_compose_file, order):
        with open(docker_compose_file, 'r') as f:
            contents = f.read()

        contents = contents.replace('[ETNY_ORDER_ID]', str(order))
        with open(docker_compose_file, 'w') as f:
            f.write(contents)

    def _getOrder(self):
        logger = self.logger

        order_id = self.find_order_by_dp_req()
        if order_id is not None:
            order = Order(self.__etny.caller()._getOrder(order_id))
            return [order_id, order]
        return None

    def __can_place_order(self, dp_req_id: int, do_req_id: int) -> bool:
        logger = self.logger

        """
        Determines if we can place an order at the current block,
        with special handling if we're still in the 'first cycle'.

        :param dp_req_id: Data Processesor request id
        :param do_req_id: Data Owner request id
        :return: True if this node can place an order now; otherwise False.
        """

        current_block_number = self.__w3.eth.block_number

        # Decide how many nodes can place orders per block
        # Use max(1, ...) to avoid zero if total_nodes_count < 25
        if self.__network == 'TESTNET':
            dispersion_factor = 1
        else:
            dispersion_factor = max(1, self.__total_nodes_count // 25)
            logger.debug(
                f"Dispersion factor set to {dispersion_factor} for "
                f"{self.__total_nodes_count} registered nodes"
            )

        # Compute an integer offset from current block + dp_req_id
        offset = current_block_number + dp_req_id

        # Compare offset's position in the cycle to do_req_id's position
        offset_mod = offset % dispersion_factor
        do_req_id_mod = do_req_id % dispersion_factor

        # difference_raw tells us how far 'offset_mod' is from 'do_req_id_mod':
        #   == 0 => aligned now
        #   >  0 => we haven't reached do_req_id_mod yet in this cycle
        #   <  0 => we've already passed do_req_id_mod in this cycle
        difference_raw = do_req_id_mod - offset_mod

        # CASE 1: Perfect alignment this block
        if difference_raw == 0:
            return True

        # CASE 2: difference_raw > 0 => we are still "early"
        if difference_raw > 0:
            next_block = current_block_number + difference_raw
            logger.debug(
                f"Offset={offset_mod}, required={do_req_id_mod}; "
                f"waiting {difference_raw} more block(s). Next block: {next_block}."
            )
            logger.info(f"DO Request {do_req_id} will be processed after block #{next_block}, current block is #{current_block_number}")
            # Mark __is_first_cycle as False once we pass do_req_id_mod
            self.__is_first_cycle[do_req_id] = False
            return False

        # CASE 3: difference_raw < 0 => we've missed our slot in the current cycle
        if self.__is_first_cycle[do_req_id]:
            # If it's the first cycle, we choose NOT to skip it, and wait for the next slot
            # We wait for the next cycle.
            difference_next_cycle = difference_raw % dispersion_factor
            next_block = current_block_number + difference_next_cycle
            logger.debug(
                f"Offset={offset_mod}, required={do_req_id_mod}; "
                f"we missed our slot in the FIRST cycle (diff={difference_raw}). "
                f"Next block: {next_block}."
            )
            logger.info(f"Request will be processed after block #{next_block}, current block is #{current_block_number}")
            return False
        else:
            # On subsequent cycles, if we missed our slot, skip waiting.
            logger.debug(
                f"Offset={offset_mod}, required={do_req_id_mod}; "
                f"we missed our slot (diff={difference_raw}), "
                f"but it's NOT the first cycle, so place the order now."
            )
            self.__is_first_cycle[do_req_id] = False
            return True

    def process_dp_request(self):
        logger = self.logger
       
        order_details = self._getOrder()
        timeout_in_seconds = int(self.__network_config.block_time) - 1.3

        if order_details is not None:
            [order_id, order] = order_details
            if order.status == OrderStatus.PROCESSING:
                logger.debug(f"DP request never finished, processing order {order_id}")

                while not stop_event.is_set():
                    time.sleep(timeout_in_seconds)

                    if stop_event.is_set():
                        return

                    if get_task_running_on():
                        continue

                    break

                self.process_order(order_id)
            if order.status == OrderStatus.CLOSED:
                logger.debug(f"DP request {self.__dprequest} completed successfully!")
                self.dpreq_cache.add(self.__dprequest)
            if order.status == OrderStatus.OPEN:
                logger.debug("Order was never approved, skipping")
            return

        logger.debug(f"Processing DP request {self.__dprequest}")
        time.sleep(self.__network_config.rpc_delay/1000)
        resp, req_id = retry(self.__etny.caller()._getDPRequest, self.__dprequest, attempts=10, delay=3)
        if resp is False:
            logger.info(f"DP {self.__dprequest} wasn't found")
            return

        req = DPRequest(req_id)


        if req.status != RequestStatus.AVAILABLE:
            logger.debug(
                f'''Skipping Order, DORequestId = {_doreq[i]}, DPRequestId = {i}, Order has different status: '{RequestStatus._status_as_string(doreq[i].status)}' ''')
            return

        checked = 0
        seconds = 0

        self.__total_nodes_count = self.__heart_beat.caller().getNodesCount()

        _doreq = {}
        doreq = {}
        metadata = {}
        logger.info(f"System ready for the next DO request")

        next_dp_request = False

        while not stop_event.is_set():

            time.sleep(timeout_in_seconds)

            try:
                self.__call_heart_beat()

                if get_task_running_on():
                     continue

                if stop_event.is_set():
                     break

                time.sleep(self.__network_config.rpc_delay/1000)
                count = self.__etny.caller()._getDORequestsCount()
                checked = 0
            except Exception as e:
                logger.warning(f"Warning while trying to get DORequestCount, Message: {e}")
                continue

            if count == 0:
                continue;

            cached_do_requests = self.doreq_cache.get_values

            req_to_process = list(set(range(checked, count)) - set(cached_do_requests))

            total_requests = len(req_to_process)
            threshold = 0

            for idx, i in enumerate(reversed(req_to_process), start=1):

                self.__call_heart_beat()

                if stop_event.is_set():
                    break

                percent_complete = (idx * 100) // total_requests

                if percent_complete >= threshold and self.__do_requests_build_pending:
                    logger.info(f"Building DO Requests cache: {percent_complete}% ({idx} / {total_requests})")
                    threshold += 1   # Increment to the next threshold

                if i not in metadata:
                    metadata[i] = [None, None, None, None, None]

                if metadata[i][4] is None:
                    while True:
                        try:
                            time.sleep(self.__network_config.rpc_delay/1000)
                            _doreq[i] = self.__etny.caller()._getDORequest(i)
                            doreq[i] = DORequest(_doreq[i])
                            time.sleep(self.__network_config.rpc_delay/1000)
                            metadata[i] = self.__etny.caller()._getDORequestMetadata(i)
                            break
                        except Exception as e:
                            logger.warning(f"Failed to read DO request metadata")

                if not (doreq[i].cpu <= req.cpu and doreq[i].memory <= req.memory and
                        doreq[i].storage <= req.storage and doreq[i].bandwidth <= req.bandwidth and doreq[i].price >= req.price):
                    self.doreq_cache.add(i)
                    logger.debug("Not enough resources to process this DO request, skipping to next request")
                    continue


                if metadata[i][4] != '' and metadata[i][4] != self.__address:
                    logger.debug(f'Skipping DO Request: {i}. Request is delegated to a different Node.')
                    self.doreq_cache.add(i)
                    continue

                if metadata[i][4] == '':
                    status = self.__can_place_order(self.__dprequest, i)
                    if not status:
                        continue

                while True:
                    try:
                        time.sleep(self.__network_config.rpc_delay/1000)
                        _doreq[i] = self.__etny.caller()._getDORequest(i)
                        doreq[i] = DORequest(_doreq[i])
                        break
                    except Exception as e:
                        logger.warning(f"Failed to read DO request metadata")

                if not self.can_run_under_sgx:
                    logger.error('SGX is not enabled or correctly configured, skipping DO request')
                if doreq[i].status != RequestStatus.AVAILABLE:
                    logger.debug(
                        f'''Skipping Order, DORequestId = {_doreq[i]}, DPRequestId = {i}, Order has different status: '{RequestStatus._status_as_string(doreq[i].status)}' ''')

                    logger.info(f"DO request {i} is matched with another operator, skipping processing")
                    self.doreq_cache.add(i)
                    continue

                if self._check_installed_drivers():
                    logger.error('SGX configuration error. Both isgx drivers are installed. Skipping order placing ...')
                    self.doreq_cache.add(i)
                    continue

                if not self.can_run_under_sgx:
                    logger.error('SGX is not enabled or correctly configured, skipping DO request')
                    self.doreq_cache.add(i)
                    continue

                set_task_running_on(self.__network)

                logger.info(f"DO Request {i} detected. Starting order placement. ")
                try:
                    self.place_order(i)
                    self.doreq_cache.add(i)

                    # store merged log
                    self.merged_orders_cache.add(do_req_id=i, dp_req_id=self.__dprequest, order_id=self.__order_id)

                except (exceptions.ContractLogicError, IndexError) as e:
                    logger.warning(f"Falied placing order: {e}")
                    reset_task_running_on()
                    continue

                if metadata[i][4] == '':
                    logger.info(f"Awaiting approval for order")
                    attempts = int(60 / self.__network_config.block_time)
                    if retry(self.wait_for_order_approval, attempts=attempts, delay=self.__network_config.block_time)[0] is False:
                        logger.info(f"Order was not approved in the last ~{attempts} blocks, skipping to next DP request")
                        next_dp_request = True
                        reset_task_running_on()
                        break

                    logger.info(f"Approval granted. Order processing continues.")

                try:
                    self.process_order(self.__order_id)
                    logger.info(
                        f"Order {self.__order_id} (DO request {i}, DP request {self.__dprequest}) completed.")
                    reset_task_running_on()
                    next_dp_request = True
                    break
                except Exception as e:
                    logger.error(f"Unable to process order {self.__order_id}: {e}")
                    reset_task_running_on()

           
            if self.__do_requests_build_pending and threshold > 0:
                logger.info(f"Building DO Requests cache: 100%")
                logger.info("Finished building DO requests cache")
                logger.info("System ready for the next DO request")

            self.__do_requests_build_pending = False

            if next_dp_request == True:
                break

        self.storage.repo_gc() # Running garbage colleciton on ipfs before exiting


    def wait_for_order_approval(self):
        logger = self.logger
        
        
        _order = self.__etny.caller()._getOrder(self.__order_id)
        order = Order(_order)
        #logger.info('Waiting...')
        if order.status != OrderStatus.PROCESSING:
            raise Exception("Order has not been yet approved")

    def find_order_by_dp_req(self):
        logger = self.logger

        logger.debug(f"Checking if DP request {self.__dprequest} has an order associated")

        order_id = self.orders_cache.get(str(self.__dprequest))
        if order_id is not None:
            logger.debug(f"Found in cache, order_id = {order_id}")
            return order_id

        my_orders = self.__etny.functions._getMyDOOrders().call({'from': self.__address})
        cached_order_ids = self.orders_cache.get_values

        orders_to_process = list(set(my_orders))
        total_requests = len(orders_to_process)
        threshold = 0
        building = False

        for idx, _order_id in enumerate(reversed(orders_to_process), start=1):

            self.__call_heart_beat()

            if stop_event.is_set():
                break

            if _order_id in cached_order_ids:
                dp_req = self.orders_cache.get_key(_order_id)
                order = {'dp_req': dp_req}
                self.__orders[_order_id] = order
                order_dp_req = dp_req
            else:
              try:

                if _order_id not in self.__orders or self.__orders[_order_id] is None:
                    building = True
                    percent_complete = (idx * 100) // total_requests

                    if percent_complete >= threshold and idx > 1:
                        logger.info(f"Building orders cache: {percent_complete}% ({idx} / {total_requests})")
                        threshold += 10

                    time.sleep(self.__network_config.rpc_delay/1000)
                    self.__orders[_order_id] = self.__etny.caller()._getOrder(_order_id)

                order = Order(self.__orders[_order_id])
                self.orders_cache.add(order.dp_req, _order_id)
                order_dp_req = order.dp_req

              except Exception as e:
                logger.error(f"Unable to find order: {e}")

            if order_dp_req == self.__dprequest:
                return _order_id

        if total_requests > 1 and building == True and not stop_event.is_set():
            logger.info(f"Building orders cache: 100%")
            logger.info(f"Finished building orders cache")

        logger.debug(f"DP request {self.__dprequest} hash no order associated")
        return None

    def place_order(self, doreq):
        logger = self.logger

        order_id = 0
        max_retries = 20
        retries = 0

        unicorn_txn = self.__etny.functions._placeOrder(
                int(doreq),
                int(self.__dprequest),
        ).build_transaction(self.get_transaction_build())

        while True:
          try:
            time.sleep(self.__network_config.rpc_delay/1000)
            _hash = self.send_transaction(unicorn_txn)
            logger.info(f"TXID {_hash} pending... fingers crossed")
            receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
            if receipt.status == 1:
                logger.info(f"TXID {_hash} confirmed!")
                break
            else:
              logger.info(f"TXID {_hash} is reverted")
              _doreq = self.__etny.caller()._getDORequest(doreq)

              doreqid = DORequest(_doreq)

              if doreq.status != RequestStatus.AVAILABLE:
                  logger.debug(f"DO request {doreqid} is matched with another operator, skipping processing")
                  self.doreq_cache.add(doreqid)
                  raise

          except (exceptions.ContractLogicError, IndexError) as e:
              logger.warning(f"ContractLogicError: {e}");
              raise
          except Exception as ex:
              retries += 1
              logger.warning(f"Error while placing Order. Retry {retries}/{max_retries}. Error Message: {ex}")
              if retries == max_retries:
                  logger.error("Maximum retries reached. Aborting.")
                  raise
              time.sleep(5)
          continue

        while True:
          try:
            time.sleep(self.__network_config.rpc_delay/1000)
            processed_logs = self.__etny.events._placeOrderEV().process_receipt(receipt)
            order_id = processed_logs[0].args._orderNumber
            if order_id != None:
                self.__order_id = order_id
                break
          except Exception as e:
            logger.warn(f"Exception while parsing transaction receipt: {e}")
            logger.warn(f"{receipt}")
            continue

        logger.info(f"Order {self.__order_id} secured!")

    def get_transaction_build(self, existing_nonce=None):
        logger = self.logger

        self.__nonce = existing_nonce if existing_nonce else self.__w3.eth.get_transaction_count(self.__address)

        if self.__network_config.eip1559 == True:
            latest_block = self.__w3.eth.get_block("latest")
            max_fee_per_gas = int(latest_block.baseFeePerGas * 1.1) + self.__w3.to_wei(self.__network_config.max_priority_fee_per_gas, self.__network_config.gas_price_measure) # 10% increase in previous block gas price + priority fee

            if max_fee_per_gas > self.__w3.to_wei(self.__network_config.max_fee_per_gas, self.__network_config.gas_price_measure):
                raise Exception("Network base fee is too high!")
                
            transaction_options = {
                "type": 2,
                "nonce": self.__nonce,
                "chainId": self.__network_config.chain_id,
                "from": self.__acct.address,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': self.__w3.to_wei(self.__network_config.max_priority_fee_per_gas, self.__network_config.gas_price_measure),
            }
            
            gas_price_value = max_fee_per_gas
            
        else:
            transaction_options = {
                "nonce": self.__nonce,
                "chainId": self.__network_config.chain_id,
                "from": self.__acct.address,
                "gasPrice": self.__w3.to_wei(self.__network_config.gas_price, self.__network_config.gas_price_measure),
                "gas": self.__network_config.gas_limit,
            }
 
            gas_price_value = self.__w3.to_wei(self.__network_config.gas_price, self.__network_config.gas_price_measure)


        logger.debug(f"Sending transaction using eip1559 = {self.__network_config.eip1559}, gasPrice = {self.__w3.from_wei(gas_price_value, 'gwei')} gwei");

        return transaction_options


    def send_transaction(self, unicorn_txn):
        try:
            signed_txn = self.__w3.eth.account.sign_transaction(unicorn_txn, private_key=self.__acct.key)
            self.__w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            _hash = self.__w3.to_hex(self.__w3.keccak(signed_txn.raw_transaction))
            return _hash
        except Exception as e:
            logger.error(f"Error sending Transaction, Error Message: {e}")
            raise

    def resume_processing(self):
        while True and not stop_event.is_set():

            balance = self.__w3.eth.get_balance(self.__address)

            if balance < int(self.__network_config.minimum_gas_at_start):
                logger.error("Not enough gas to run on this network, exiting")
                break

            self.add_dp_request()
            self.process_dp_request()

    def _check_installed_drivers(self):
        driver_list = os.listdir('/dev')
        return 'isgx' in driver_list and 'sgx_enclave' in driver_list

    def get_env_for_integration_test(self):
        env_vars = {
            "ETNY_CHAIN_ID": self.__network_config.chain_id,
            "ETNY_SMART_CONTRACT_ADDRESS": self.__network_config.contract_address,
            "ETNY_WEB3_PROVIDER": self.__network_config.rpc_url,
            "ETNY_RUN_INTEGRATION_TEST": 1,
            "ETNY_ORDER_ID": 0
        }
        return env_vars

    def build_prerequisites_integration_test(self, bucket_name, order_id, docker_compose_file):
        logger = self.logger

        try:
            logger.debug('Cleaning up swift-stream bucket.')
            self.swift_stream_service.delete_bucket(bucket_name)
            logger.debug('Creating new bucket.')
            self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
            self.create_folder_v1(self.order_folder)
            (status, msg) = self.swift_stream_service.create_bucket(bucket_name)
            if not status:
                logger.error(msg)

            self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'
            self.copy_order_files(docker_compose_file, self.order_docker_compose_file)

            self.set_retry_policy_on_fail_for_compose()
            self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)

            env_content = self.get_env_for_integration_test()
            self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

            (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                                  ".env",
                                                                  f'{self.order_folder}/.env')
            if not status:
                logger.error(msg)
        except Exception as e:
            logger.warning(f"Unable to preapre for integration test: {e}")

    def __clean_up_integration_test(self):
        logger = self.logger
        try: 
            logger.debug('Cleaning up containers after integration test.')
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)
            logger.debug('Cleaning up swift-stream integration bucket.')
            self.swift_stream_service.delete_bucket(self.integration_bucket_name)
        except Exception as e:
            logger.warning(f"Unable to clean container: {e}")

    def __run_integration_test(self):
        logger = self.logger

        logger.info('Running integration test.')

        [enclave_image_hash, _,
         docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey(self.__network_config.integration_test_image,
                                                                                                      'v3')
        self.integration_bucket_name = 'etny-bucket-integration'
        order_id = 'integration_test'
        integration_test_file = 'context_test.etny'

        try:
            logger.debug(f"Downloading IPFS Image: {enclave_image_hash}")
            logger.debug(f"Downloading IPFS docker yml file: {docker_compose_hash}")
        except Exception as e:
            logger.warning(str(e))

        list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash]
        if not self.storage.download_many(list_of_ipfs_hashes, attempts=10, delay=3):
            logger.info("Cannot download data from IPFS, stopping test")
            return

        logger.debug("Running docker swift-stream")
        run_subprocess(
            ['docker-compose', '-f', f'../docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
            logger)

        docker_compose_file = f'{self.cache_config.base_path}/{docker_compose_hash}'
        logger.debug(f'Preparing prerequisites for integration test')

        self.build_prerequisites_integration_test(self.integration_bucket_name, order_id, docker_compose_file)

        logger.debug("Stopping previous docker registry and containers")
        run_subprocess(['docker', 'stop', 'registry'], logger)
        run_subprocess(['docker', 'stop', 'etny-securelock'], logger)
        run_subprocess(['docker', 'stop', 'etny-trustedzone'], logger)
        run_subprocess(['docker', 'stop', 'las'], logger)
        logger.debug("Cleaning up docker registry")
        run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
        logger.debug("Running new docker registry")
        logger.debug( "{self.cache_config.base_path} / {enclave_image_hash} :/var/lib/registry")

        logger.debug("Stopping previous docker las")
        run_subprocess(['docker', 'stop', 'las'], logger)
        logger.debug("Removing previous docker las")
        run_subprocess(['docker', 'rm', 'las'], logger)
        run_subprocess([
            'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
            f'{self.cache_config.base_path}/{enclave_image_hash}' + ':/var/lib/registry',
            'registry:2'
        ], logger)

        logger.debug("Started enclaves by running ETNY docker-compose")
        run_subprocess([
            'docker-compose', '-f', self.order_docker_compose_file, 'up', '-d'
        ], logger)

        logger.debug('Waiting for execution of integration test enclave')
        self.wait_for_enclave_v2(self.integration_bucket_name, integration_test_file, 300)
        status, result_data = self.swift_stream_service.get_file_content(self.integration_bucket_name,
                                                                         integration_test_file)
        if not status:
            logger.warning('The node is not properly running under SGX. Please check the configuration.')
            self.can_run_under_sgx = False
            self.__clean_up_integration_test()
            return

        self.can_run_under_sgx = True
        set_integration_test_complete(True)
        logger.info('Agent SGX capabilities tested and enabled successfuly')
        self.__clean_up_integration_test()

    def __can_run_auto_update(self, file_path, interval):
        current_timestamp = int(time.time())
        if os.path.exists(file_path):
            with open(file_path, 'r') as file:
                value = file.read().strip()
                if not value.isdigit():
                    saved_timestamp = 0
                else:
                    saved_timestamp = int(value)

            if current_timestamp - saved_timestamp >= interval:
                return True
            else:
                return False
        else:
            return True


    def __write_auto_update_cache(self, file_path, interval):
        current_timestamp = int(time.time())
        if os.path.exists(file_path):
            with open(file_path, 'r') as file:
                value = file.read().strip()
                if not value.isdigit():
                    saved_timestamp = 0
                else:
                    saved_timestamp = int(value)

            if current_timestamp - saved_timestamp >= interval:
                with open(file_path, 'w') as file:
                    file.write(str(current_timestamp))
                return True
            else:
                return False
        else:
            with open(file_path, 'w') as file:
                file.write(str(current_timestamp))

            return True


    def __call_heart_beat(self):
        logger = self.logger

        if self.__network == 'TESTNET':
            heartbeat_frequency = 1 * 60 * 60 - 60;
        elif self.__network == 'POLYGON':
            heartbeat_frequency = 12 * 60 * 60 - 60;
        else:
            heartbeat_frequency = 12 * 60 * 60 - 60;

        if self.__can_run_auto_update(self.cache_config.heart_beat_log_file_path, heartbeat_frequency):
            logger.info('Calling hearbeat...')
            params = [
                "v3"
            ]

            max_retries = 20
            retries = 0

            while True:
              try:
                time.sleep(self.__network_config.rpc_delay/1000)
                unicorn_txn = self.__heart_beat.functions.logCall(*params).build_transaction(self.get_transaction_build())
                _hash = self.send_transaction(unicorn_txn)
                logger.info(f"{_hash} pending... ")
                receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
                if receipt.status == 1:
                    logger.info(f"{_hash} confirmed!")
                    logger.info('Heart beat successfully called...')
                    self.__write_auto_update_cache(self.cache_config.heart_beat_log_file_path, heartbeat_frequency);
                    break
              except Exception as e:
                retries += 1
                logger.warning(f"Warning while sending heartbeat. Retry {retries}/{max_retries}. Message: {e}")
                if retries == max_retries:
                    logger.error("Maximum retries reached. Aborting.")
                    raise
                time.sleep(5)

class SGXDriver:
    def __init__(self):
        try:
            subprocess.call(['bash','../ubuntu/etny-node-provision-sgx.sh'])
        except Exception as e:
            pass

def process_network(network):
    """
    Processes a single network configuration.
    
    Args:
        network (NetworkConfig): The network configuration to process.
    
    Raises:
        Exception: Propagates exceptions after logging.
    """

    if stop_event.is_set():
        config.logger.warning(f"[{network.name}] Stopping network processing due to interrupt.")
        return

    try:
        while not stop_event.is_set():
           app = EtnyPoXNode(network)
           app.cache_dp_requests()
           app.resume_pending_dp_requests()
           app.resume_available_dp_requests()
           app.resume_processing()

        logger.info(f"[{network.name}] Exiting")
        return(f"[{network.name}] Exiting")

    except Exception as e:
        logger.error(f"[{network.name}] An error occurred: {e}")
        raise  # Re-raise the exception after logging

def set_task_running_on(name):
    """
    Sets the shared network name in a thread-safe way.
    """
    global task_running_on
    with task_lock:
        task_running_on = name

def get_task_running_on():
    """
    Gets the shared network name in a thread-safe way.
    """
    global task_running_on 
    with task_lock:
        return task_running_on 

def reset_task_running_on():
    """
    Resets the shared network name to None in a thread-safe way.
    """
    global task_running_on
    with task_lock:
        task_running_on = None

def set_integration_test_complete(value):
    """
    Sets the shared value for integration test in a thread-safe way.
    """
    global integration_test_complete
    with integration_test_lock:
        integration_test_complete = value

def get_integration_test_complete():
    """
    Sets the shared value for integration test in a thread-safe way.
    """
    global integration_test_complete
    with integration_test_lock:
        return integration_test_complete

class TaskManager:
    def __init__(self):
        self.executor = None
        self.futures = []

    def start_threads(self, network_configs):
        """
        Creates a new ThreadPoolExecutor and starts tasks.
        Stores them in self.executor/self.futures.
        """
        logger.info("Starting new threads...")
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.futures = [
            self.executor.submit(process_network, net) for net in network_configs
        ]

def initiate_restart(network_configs, task_manager):
    """
    1) Signal the old tasks to stop.
    2) Wait for them to finish.
    3) Shutdown the old executor.
    4) Clear stop_event and start fresh tasks in the same TaskManager.
    """
    logger.info("Initiating restart...")

    # 1) Tell current tasks to stop
    stop_event.set()

    # 2) Wait until all futures are done
    while not all(f.done() for f in task_manager.futures):
        logger.info("Waiting for current tasks to finish...")
        time.sleep(2)

    logger.info("All current tasks have stopped.")

    # 3) Shut down the old executor
    task_manager.executor.shutdown(wait=True)
    logger.info("Old executor shut down.")

    # 4) Clear the stop flag
    stop_event.clear()

    reset_task_running_on()

    # 5) Start fresh threads (re-using the same TaskManager object)
    task_manager.start_threads(network_configs)
    logger.info("New threads started after restart.")

def run_scheduler(interval, network_configs, task_manager):
    """
    Runs in a background thread. Every `interval` seconds, calls initiate_restart().
    This is an infinite loop, so adjust as needed or provide a break condition.
    """
    while True:
        time.sleep(interval)
        initiate_restart(network_configs, task_manager)


if __name__ == '__main__':

    network_names = list(config.NETWORKS.keys())
    
    parser = config.parse_arguments(network_names)
    args, unknown_args = parser.parse_known_args()

    if unknown_args:
       config.logger.warning(f"Ignored unrecognized arguments: {' '.join(unknown_args)}")

    try:
        sgx = SGXDriver()
        network_configs = config.parse_networks(args, parser, network_names)

        # Create a TaskManager to hold executor/futures
        task_manager = TaskManager()

        # Start the first batch of threads
        task_manager.start_threads(network_configs)

        # Create a background scheduler that restarts every 20 seconds
        scheduler_thread = threading.Thread(
            target=run_scheduler,
            args=(24 * 60 * 60, network_configs, task_manager),
            daemon=True  # daemon=True so it won't block process exit
        )
        scheduler_thread.start()

        # Keep main alive (or do other work)
        logger.info("Main thread running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)

    except EnvironmentError as env_err:
        logger.error(f"Environment configuration error: {env_err}")
        sys.exit(1)
    except argparse.ArgumentError as arg_err:
        logger.error(f"Argument parsing error: {arg_err}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        sys.exit(1)
