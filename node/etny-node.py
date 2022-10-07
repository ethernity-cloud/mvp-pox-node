#!/usr/bin/python3

import os, time, json

from eth_account import Account
from web3 import Web3
from web3 import exceptions
from web3.middleware import geth_poa_middleware

import config
from utils import get_or_generate_uuid, run_subprocess, retry, Storage, Cache, subprocess
from models import *
from error_messages import errorMessages

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
        self.__order_id = 0

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

    def _limited_arg(self, item, allowed_max = 255):
        return allowed_max if item > allowed_max else item

    def add_dp_request(self, waiting_period_on_error = 15, beginning_of_recursion = None):
        params = [
            self._limited_arg(self.__cpu), 
            self._limited_arg(self.__memory), 
            self._limited_arg(self.__storage), 
            self._limited_arg(self.__bandwidth),
            self.__duration, 
            0, 
            self.__uuid, 
            "", 
            "", 
            ""
        ]

        unicorn_txn = self.__etny.functions._addDPRequest(*params).buildTransaction(self.get_transaction_build())
        _hash = ''
        error = ''
        try:
            _hash = self.send_transaction(unicorn_txn)
        except ValueError as e:
            error = [key for key, value in errorMessages.items() if (str(e) in value or value in str(e))][0]
        except Exception as e:
            logger.info(f'error = {e}, type = {type(e)}')

        if error:
            logger.info(f'error: {error}')

            # to trigger this error nonce should be duplicated
            if error and error == 'low_nonce':
                t = beginning_of_recursion if beginning_of_recursion else int(time.time())
                while int(time.time()) - t < (waiting_period_on_error * 60):
                    time.sleep(10)
                    logger.info(f'waiting for: {time.time() - t}')
                    nonce = self.__w3.eth.getTransactionCount(self.__address)
                    if nonce != self.__nonce:
                        return self.add_dp_request(beginning_of_recursion=t)

                logger.error('Node retried transaction too many times. Exiting!')
                raise
                
            
        try:
            waiting_seconds = 120
            throw_error = False
            if error and error == 'duplicated':
                waiting_seconds *= 7
                throw_error = True
                
            receipt = self.__w3.eth.waitForTransactionReceipt(transaction_hash = _hash, timeout = waiting_seconds)

            processed_logs = self.__etny.events._addDPRequestEV().processReceipt(receipt)
            self.__dprequest = processed_logs[0].args._rowNumber

        except Exception as e:
            logger.error(f"{e} f- {type(e)}")
            if throw_error:
                raise
            return

            
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
        _hash = self.send_transaction(unicorn_txn)
        try:
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except:
            raise
        logger.info("Request has been cancelled")

    def process_order(self, order_id, metadata = None):
        # this line should be checked later
        if not metadata:
            order = Order(self.__etny.caller()._getOrder(order_id))
            metadata = self.__etny.caller()._getDORequestMetadata(order.do_req)
        self.add_processor_to_order(order_id)
        [enclaveImage, *etny_pinithy] = metadata[1].split(':')
        try:
            logger.info(f"Downloading IPFS Image: {enclaveImage}")
            logger.info(f"Downloading IPFS Payload Hash: {metadata[2]}")
            logger.info(f"Downloading IPFS FileSet Hash: {metadata[3]}")
        except Exception as e: 
            logger.info(str(e))
        self.storage.download_many([enclaveImage])
        if not self.storage.download_many([enclaveImage, metadata[2], metadata[3]]):
            logger.info("Cannot download data from IPFS, cancelling processing")
            self.ipfs_timeout_cancel(order_id)
            return

        logger.info("Stopping previous docker registry")
        
        run_subprocess(['docker', 'stop', 'registry'], logger)
        logger.info("Cleaning up docker registry")
        run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
        logger.info("Running new docker registry")
        logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + enclaveImage + ':/var/lib/registry')
        run_subprocess([
             'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
             os.path.dirname(os.path.realpath(__file__)) + '/' + enclaveImage + ':/var/lib/registry', 'registry:2'
        ], logger)

        logger.info("Cleaning up docker container")
        run_subprocess(['docker', 'rm', '-f', 'etny-pynithy-' + str(order_id)], logger)

        logger.info("Running docker-compose")

        yaml_file = '-initial-image' if enclaveImage in ['QmeQiSC1dLMKv4BvpvjWt1Zeak9zj6TWgWhN7LLiRznJqC'] else ''

        run_subprocess([
             'docker-compose', '-f', f'docker/docker-compose-etny-pynithy{yaml_file}.yml', 'run', '--rm', '-d', '--name',
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

    def _getOrder(self):
        order_id = self.find_order_by_dp_req()
        if order_id is not None:
            order = Order(self.__etny.caller()._getOrder(order_id))
            return [order_id, order]
        return None

    def process_dp_request(self):
        order_details = self._getOrder()
        if order_details is not None:
            [order_id, order] = order_details
            if order.status == OrderStatus.CLOSED:
                logger.info(f"DP request {self.__dprequest} completed successfully!")
            if order.status == OrderStatus.PROCESSING:
                logger.info(f"DP request never finished, processing order {order_id}")
                self.process_order(order_id)
            if order.status == OrderStatus.OPEN:
                logger.info("Order was never approved, skipping")
            return

        logger.info(f"Processing NEW DP request {self.__dprequest}")
        resp, req = retry(self.__etny.caller()._getDPRequest, self.__dprequest, attempts=10, delay=3)
        if resp is False:
            logger.info(f"DP {self.__dprequest} wasn't found")
            return
        req = DPRequest(req)
        checked = 0
        seconds = 0
        while seconds < config.dp_request_timeout:
            count = self.__etny.caller()._getDORequestsCount()
            found = False
            cached_do_requests = self.doreq_cache.get_values()
            for i in reversed(list(set(range(checked, count)) - set(cached_do_requests))):
                _doreq = self.__etny.caller()._getDORequest(i)
                doreq = DORequest(_doreq)
                self.doreq_cache.add(i, i)
                if doreq.status != RequestStatus.AVAILABLE:
                    continue
                logger.info(f"Checking DO request: {i}")
                if not (doreq.cpu <= req.cpu and doreq.memory <= req.memory and
                        doreq.storage <= req.storage and doreq.bandwidth <= req.bandwidth):
                    logger.info("Order doesn't meet requirements, skipping to next request")
                    continue

                metadata = self.__etny.caller()._getDORequestMetadata(i)
                
                if metadata[4] != '' and metadata[4] != self.__address:
                    logger.info(f'Skipping DORequst: {i}. Request is delegated to a different Node.')
                    continue

                logger.info("Placing order...")
                try:
                    self.place_order(i)
                except (exceptions.SolidityError, IndexError) as error:
                    logger.info(f"Order already created, skipping to next DO request - {type(error)}")
                    continue
                found = True
                logger.info(f"Waiting for order {self.__order_id} approval...")
                if retry(self.wait_for_order_approval, attempts=20, delay=2)[0] is False:
                    logger.info("Order was not approved in the last ~20 blocks, skipping to next request")
                    break

                # performance improvement, to avoid duplication
                # self.process_order(self.__order_id, metadata=metadata)
                self.process_order(self.__order_id)
                logger.info(f"Order {self.__order_id}, with DO request {i} and DP request {self.__dprequest} processed successfully")
                break
            if found:
                logger.info(f"Finished processing order {self.__order_id}")
                return
            checked = count - 1
            time.sleep(5)
            
            seconds += 5
            if seconds >= 60 * 60 * 24:
                logger.info("DP request timed out!")
                self.cancel_dp_request(self.__dprequest)
                break

        logger.info("DP request timed out!")
        # self.cancel_dp_request(self.__dprequest)

    def add_processor_to_order(self, order_id):
        unicorn_txn = self.__etny.functions._addProcessorToOrder(order_id, self.__resultaddress).buildTransaction(self.get_transaction_build())

        try:
            _hash = self.send_transaction(unicorn_txn)
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info(f"Added the enclave processor to the order!")
        logger.info(f"TX Hash: {_hash}")

    def wait_for_order_approval(self):
        _order = self.__etny.caller()._getOrder(self.__order_id)
        order = Order(_order)
        logger.info('Waiting...')
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
        for _order_id in reversed(list(set(my_orders) - set(cached_order_ids))):
            _order = self.__etny.caller()._getOrder(_order_id)
            order = Order(_order)
            self.orders_cache.add(order.dp_req, _order_id)
            if order.dp_req == self.__dprequest:
                return _order_id
        logger.info(f"Could't find order with DP request {self.__dprequest}")
        return None

    def place_order(self, doreq):
        unicorn_txn = self.__etny.functions._placeOrder(
            int(doreq), 
            int(self.__dprequest),
        ).buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)
        try:
            receipt = self.__w3.eth.waitForTransactionReceipt(_hash)
            processed_logs = self.__etny.events._placeOrderEV().processReceipt(receipt)
            self.__order_id = processed_logs[0].args._orderNumber
        except Exception as e:
            logger.error(e)
            raise

        logger.info("Order placed successfully!")
        logger.info(f"TX Hash: {_hash}")

    def get_transaction_build(self, existing_nonce = None):
        self.__nonce = existing_nonce if existing_nonce else self.__w3.eth.getTransactionCount(self.__address)

        return {
            'chainId': config.chain_id,
            'gas': config.gas_limit,
            'nonce': self.__nonce,
            'gasPrice': self.__w3.toWei(config.gas_price_value, config.gas_price_measure),
        }

    def send_transaction(self, unicorn_txn, get_only_hash = False):
        signed_txn = self.__w3.eth.account.sign_transaction(unicorn_txn, private_key=self.__acct.key)
        if not get_only_hash:
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