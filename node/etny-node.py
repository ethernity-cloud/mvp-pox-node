#!/usr/bin/python3

import os, time, json

from eth_account import Account
from web3 import Web3
from web3 import exceptions
from web3.middleware import geth_poa_middleware

import config
from utils import get_or_generate_uuid, run_subprocess, retry, Storage, Cache, subprocess
from models import *

logger = config.logger


class EtnyPoXNode:
    __address = None
    __privatekey = None
    __resultaddress = None
    __resultprivatekey = None
    __cpu = None
    __memory = None
    __storage = None
    __bandwidth = None
    __duration = None

    def __init__(self):
        self.parse_arguments(config.arguments, config.parser)
        with open(config.abi_filepath) as f:
            self.__contract_abi = f.read()
        self.__w3 = Web3(Web3.HTTPProvider(config.http_provider))
        self.__w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.__acct = Account.privateKeyToAccount(self.__privatekey)
        self.__etny = self.__w3.eth.contract(
            address=self.__w3.toChecksumAddress(config.contract_address),
            abi=self.__contract_abi
        )
        self.__nonce = self.__w3.eth.getTransactionCount(self.__address)
        self.__dprequest = 0
        self.__order = 0

        self.__uuid = get_or_generate_uuid(config.uuid_filepath)
        self.orders_cache = Cache(config.orders_cache_limit, config.orders_cache_filepath)
        self.dpreq_cache = Cache(config.dpreq_cache_limit, config.dpreq_filepath)
        self.doreq_cache = Cache(config.doreq_cache_limit, config.doreq_filepath)
        self.ipfs_cache = Cache(config.ipfs_cache_limit, config.ipfs_cache_filepath)
        self.storage = Storage(config.ipfs_host, config.client_connect_url, config.client_bootstrap_url, self.ipfs_cache, config.logger)

    def parse_arguments(self, arguments, parser):
        parser = parser.parse_args()
        for args_type, args in arguments.items():
            for arg in args:
                setattr(self, "_" + self.__class__.__name__ + "__" + arg, args_type(getattr(parser, arg)))

    def cleanup_dp_requests(self):
        my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
        cached_ids = self.dpreq_cache.get_values()
        for req_id in set(my_dp_requests) - set(cached_ids):
            req_uuid = self.__etny.caller()._getDPRequestMetadata(req_id)[1]
            if req_uuid != self.__uuid:
                logger.info(f"Skipping DP request {req_id}, not mine")
                self.dpreq_cache.add(req_id, req_id)
                continue
            req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
            if req.status == RequestStatus.BOOKED:
                logger.info(f"Request {req_id} already assigned to order")
                self.__dprequest = req_id
                self.process_dp_request()
            if req.status == RequestStatus.AVAILABLE:
                self.cancel_dp_request(req_id)
            self.dpreq_cache.add(req_id, req_id)

    def add_dp_request(self):
        unicorn_txn = self.__etny.functions._addDPRequest(
            self.__cpu, 
            self.__memory, 
            self.__storage, 
            self.__bandwidth,
            self.__duration, 
            0, 
            self.__uuid, 
            "", "", ""
        ).buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)
        try:
            receipt = self.__w3.eth.waitForTransactionReceipt(_hash)
            processed_logs = self.__etny.events._addDPRequestEV().processReceipt(receipt)
            self.__dprequest = processed_logs[0].args._rowNumber
        except Exception as ex:
            logger.info('before error---')
            logger.error(ex)
            raise

        logger.info("DP request created successfully!")
        logger.info(f"TX Hash: {_hash}")

    def cancel_dp_request(self, req):
        logger.info(f"Cancelling DP request {req}")
        unicorn_txn = self.__etny.functions._cancelDPRequest(req).buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)

        try:
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info(f"DP request {req} cancelled successfully!")
        logger.info(f"TX Hash: {_hash}")
        time.sleep(5)

    def ipfs_timeout_cancel(self, order_id):
        error_hash = self.storage.add("Error: cannot download files from IPFS")

        unicorn_txn = self.__etny.functions._addResultToOrder(
            order_id, error_hash
        ).buildTransaction(self.get_transaction_build())
        hash = self.send_transaction(unicorn_txn)
        try:
            self.__w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        logger.info("Request has been cancelled")

    def process_order(self, order_id, method_name = ''):
        order = Order(self.__etny.caller()._getOrder(order_id))
        self.add_processor_to_order(order_id)
        logger.info(f"Downloading IPFS content... {method_name}")
        metadata = self.__etny.caller()._getDORequestMetadata(order.do_req)
        template = metadata[1].split(':')
        self.storage.download_many([template[0]], from_bootstrap=True)
        if not self.storage.download_many([template[0], metadata[2], metadata[3]]):
            logger.info("Cannot download data from IPFS, cancelling processing")
            self.ipfs_timeout_cancel(order_id)
            return

        logger.info("Stopping previous docker registry")
        
        run_subprocess(['docker', 'stop', 'registry'], logger)
        logger.info("Cleaning up docker registry")
        run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
        logger.info("Running new docker registry")
        logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry')
        run_subprocess([
             'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
             os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry', 'registry:2'
        ], logger)

        logger.info("Cleaning up docker container")
        run_subprocess(['docker', 'rm', '-f', 'etny-pynithy-' + str(order_id)], logger)

        logger.info("Running docker-compose")
        run_subprocess([
             'docker-compose', '--env-file', 'docker/.env.andrei', '-f', 'docker/docker-compose-etny-pynithy.yml', 'run', '--rm', '-d', '--name',
             'etny-pynithy-' + str(order_id), 'etny-pynithy', str(order_id), metadata[2], metadata[3],
             self.__resultaddress, self.__resultprivatekey, config.contract_address
        ], logger)
        


        '''new version'''
        '''
        logger.info("Running new docker registry - 4 ")
        subprocess.call('docker rm -f $(sudo docker ps -aq)', shell=True)
        run_subprocess(['docker', 'build', '-t', 'docker_etny-pynithy1', '-f', 'docker/etny-pynithy.Dockerfile', './docker'], logger)

        logger.info("Running docker-compose")
        run_subprocess([
             'docker-compose', '-f', 'docker/docker-compose-without-registry.yaml', 'run', '--rm', '-d', '--name',
             'etny-pynithy-' + str(order_id), 'etny-pynithy', str(order_id), metadata[2], metadata[3],
             self.__resultaddress, self.__resultprivatekey, config.contract_address
        ], logger)
        '''
        '''new version'''


        time.sleep(10)
        logger.info("Attaching to docker process")
        run_subprocess(['docker', 'attach', 'etny-pynithy-' + str(order_id)], logger)
        time.sleep(3)

    def process_dp_request(self):
        order_id = self.find_order_by_dp_req()
        if order_id is not None:
            order = Order(self.__etny.caller()._getOrder(order_id))
            if order.status == OrderStatus.CLOSED:
                logger.info(f"DP request {self.__dprequest} completed successfully!")
            if order.status == OrderStatus.PROCESSING:
                logger.info(f"DP request never finished, processing order {order_id}")
                self.process_order(order_id, method_name = 'process_dp_request')
            if order.status == OrderStatus.OPEN:
                logger.info("Order was never approved, skipping")
            return

        logger.info(f"Processing NEW DP request {self.__dprequest}")
        resp, req = retry(self.__etny.caller()._getDPRequest, self.__dprequest, attempts=10, delay=3, callback = lambda x: logger.info(f"there we are 0.......{x}"))
        if resp is False:
            logger.info(f"DP {self.__dprequest} wasn't found")
            return
        req = DPRequest(req)
        checked = 0
        seconds = 0
        logger.info(f"seconds < config.dp_request_timeout {seconds} - {config.dp_request_timeout}")
        while seconds < config.dp_request_timeout:
            count = self.__etny.caller()._getDORequestsCount()
            found = False
            logger.info(f'loop __ count = {count}, seconds = {seconds}')
            cached_do_requests = self.doreq_cache.get_values()
            _l = list(reversed(list(set(range(checked, count)) - set(cached_do_requests))))
            for i in _l:
                logger.info(f"ddd 2 {i}")
                _doreq = self.__etny.caller()._getDORequest(i)
                doreq = DORequest(_doreq)
                logger.info(f"inline loop _ddd__ 2 {doreq.status}")
                self.doreq_cache.add(i, i)
                if doreq.status != RequestStatus.AVAILABLE:
                    continue
                logger.info(f"Checking DO request: {i}")
                if not (doreq.cpu <= req.cpu and doreq.memory <= req.memory and
                        doreq.storage <= req.storage and doreq.bandwidth <= req.bandwidth):
                    logger.info("Order doesn't meet requirements, skipping to next request")
                    continue
                logger.info("Placing order...")
                try:
                    self.place_order(i)
                except (exceptions.SolidityError, IndexError) as error:
                    logger.info("Order already created, skipping to next DO request")
                    continue
                found = True
                logger.info(f"Waiting for order {self.__order} approval...")
                if retry(self.wait_for_order_approval, attempts=20, delay=2, callback = lambda x: logger.info(f"there we are.......{x}"))[0] is False:
                    logger.info("Order was not approved in the last ~10 blocks, skipping to next request")
                    break
                self.process_order(self.__order, method_name = 'process_dp_request-2')
                logger.info(f"Order {self.__order}, with DO request {i} and DP request {self.__dprequest} processed successfully")
                break
            if found:
                logger.info(f"Finished processing order {self.__order}")
                return
            checked = count - 1
            time.sleep(5)
            seconds += 5

        logger.info("DP request timed out!")
        self.cancel_dp_request(self.__dprequest)

    def add_processor_to_order(self, order_id):
        unicorn_txn = self.__etny.functions._addProcessorToOrder(order_id, self.__resultaddress).buildTransaction(self.get_transaction_build())

        try:
            _hash = self.send_transaction(unicorn_txn)
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info(f"Added the enclave processor to the order! - {order_id} {self.__resultaddress}")
        logger.info(f"TX Hash: {_hash}")

    def wait_for_order_approval(self):
        _order = self.__etny.caller()._getOrder(self.__order)
        order = Order(_order)
        logger.info('---while waiting')
        logger.info(_order)
        logger.info('---while waitingd')
        if order.status != OrderStatus.PROCESSING:
            raise Exception("Order has not been yet approved")

    def find_order_by_dp_req(self):
        logger.info(f"Finding order with DP request {self.__dprequest}")
        order_id = self.orders_cache.get(str(self.__dprequest))
        if order_id is not None:
            logger.info(f"Found in cache, order_id = {order_id}")
            return order_id
        my_orders = self.__etny.functions._getMyDOOrders().call({'from': self.__address})
        cached_order_ids = self.orders_cache.get_values()
        logger.info('getting object here')
        logger.info(json.dumps(my_orders))
        logger.info(cached_order_ids)
        logger.info(json.dumps(list(reversed(list(set(my_orders) - set(cached_order_ids))))))
        logger.info('getting object here----')
        for _order_id in reversed(list(set(my_orders) - set(cached_order_ids))):
            _order = self.__etny.caller()._getOrder(_order_id)
            order = Order(_order)
            self.orders_cache.add(order.dp_req, _order_id)


            logger.info(f'self.__dprequest = {self.__dprequest}')
            logger.info(f'order_id = {_order_id}')
            logger.info(f'order.dp_req = {order.dp_req}')
            logger.info(_order)
            
            logger.info(f"Checking order {_order_id} - {order.dp_req}")
            if order.dp_req == self.__dprequest:
                return _order_id
        logger.info(f"Could't find order with DP request {self.__dprequest} - {order_id}")
        return None

    def place_order(self, doreq):
        logger.info('********')
        logger.info(f"place order doreq = {doreq} - self.__dprequest = {self.__dprequest}")
        logger.info('********')
        unicorn_txn = self.__etny.functions._placeOrder(
            int(doreq), int(self.__dprequest),
        ).buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)
        try:
            receipt = self.__w3.eth.waitForTransactionReceipt(_hash)
            processed_logs = self.__etny.events._placeOrderEV().processReceipt(receipt)
            self.__order = processed_logs[0].args._orderNumber
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info("Order placed successfully!")
        logger.info(f"TX Hash: {_hash}")

    def get_transaction_build(self):
        self.__nonce = self.__w3.eth.getTransactionCount(self.__address)
        return {
            'chainId': config.chain_id,
            'gas': config.gas_limit,
            'nonce': self.__nonce,
            'gasPrice': self.__w3.toWei(config.gas_price_value, config.gas_price_measure),
        }

    def send_transaction(self, unicorn_txn):
        signed_txn = self.__w3.eth.account.sign_transaction(unicorn_txn, private_key=self.__acct.key)
        self.__w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        _hash = self.__w3.toHex(self.__w3.sha3(signed_txn.rawTransaction))
        return _hash

    def resume_processing(self):
        while True:
            self.add_dp_request()
            self.process_dp_request()


if __name__ == '__main__':
    try:
        app = EtnyPoXNode()
        logger.info("Cleaning up previous DP requests...")
        app.cleanup_dp_requests()
        logger.info("[DONE]")
        app.resume_processing()
    except Exception as e:
        logger.error(e)
        raise