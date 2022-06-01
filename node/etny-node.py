#!/usr/bin/python3

import time
import os

from web3 import Web3
from web3 import exceptions
from eth_account import Account
from web3.middleware import geth_poa_middleware

from . import config
from .models import *
from .utils import get_or_generate_uuid, run_subprocess, retry, Storage, Cache

logger = config.logger


class EtnyPoXNode:
    # class variables
    __user = None
    __address = None
    __privatekey = None
    __resultaddress = None
    __resultprivatekey = None

    __contract_abi = None
    __etny = None
    __acct = None
    __w3 = None
    __nonce = None
    __uuid = None

    def __init__(self):
        arguments = config.parser.parse_args()
        self.__parse_arguments(arguments)

        with open(config.abi_filepath) as f:
            self.__contract_abi = f.read()

        self.__w3 = Web3(Web3.HTTPProvider(config.http_provider))
        self.__w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        self.__acct = Account.privateKeyToAccount(self.__privatekey)
        self.__etny = self.__w3.eth.contract(
            address=self.__w3.toChecksumAddress(config.contract_address),
            abi=self.__contract_abi)
        self.__nonce = self.__w3.eth.getTransactionCount(self.__address)

        self.__dprequest = 0
        self.__order = 0

        self.__uuid = get_or_generate_uuid(config.uuid_filepath)
        self.cache = Cache(config.cache_filepath)

    def __parse_arguments(self, arguments):
        for arg in config.string_args:
            setattr(self, "_" + self.__class__.__name__ + "__" + arg, getattr(arguments, arg))

        for arg in config.int_args:
            setattr(self, "_" + self.__class__.__name__ + "__" + arg, int(getattr(arguments, arg)))

    def cleanup_dp_requests(self):
        count = self.__etny.functions._getDPRequestsCount().call()
        for i in reversed(range(count)):
            logger.debug("Cleaning up DP request %s" % i)
            req = DPRequest(self.__etny.caller()._getDPRequest(i))
            req_uuid = self.__etny.caller()._getDPRequestMetadata(i)[1]
            if req_uuid == self.__uuid and req.dproc == self.__address:
                if req.status == RequestStatus.BOOKED:
                    logger.debug("Request %s already assigned to order" % i)
                    self.__dprequest = i
                    self.process_dp_request()
                if req.status == RequestStatus.AVAILABLE:
                    self.cancel_dp_request(i)
            else:
                logger.debug("Skipping DP request %s, not mine" % i)

    def add_dp_request(self):
        unicorn_txn = self.__etny.functions._addDPRequest(
            self.__cpu, self.__memory, self.__storage, self.__bandwidth,
            self.__duration, 0, self.__uuid, "", "", ""
        ).buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)

        try:
            receipt = self.__w3.eth.waitForTransactionReceipt(_hash)
            processed_logs = self.__etny.events._addDPRequestEV().processReceipt(receipt)
            self.__dprequest = processed_logs[0].args._rowNumber
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info("DP request created successfully!")
        logger.info("TX Hash: %s" % _hash)

    def cancel_dp_request(self, req):
        logger.info("Cancelling DP request %s" % req)
        unicorn_txn = self.__etny.functions._cancelDPRequest(req).buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)

        try:
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info("DP request %s cancelled successfully!" % req)
        logger.info("TX Hash: %s" % _hash)

    def process_order(self, order_id):
        order = Order(self.__etny.caller()._getOrder(order_id))
        self.add_processor_to_order(order_id)
        logger.info("Downloading IPFS content...")
        metadata = self.__etny.caller()._getDORequestMetadata(order.do_req)
        template = metadata[1].split(':')
        if not retry(Storage.download_many, [template[0], metadata[2], metadata[3]], attempts=10):
            logger.info("Cannot download data from IPFS, cancelling processing")
            return

        logger.info("Stopping previous docker registry")
        run_subprocess(['docker', 'stop', 'registry'])

        logger.info("Cleaning up docker registry")
        run_subprocess(['docker', 'system', 'prune', '-a', '-f'])

        logger.info("Running new docker registry")
        logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry')
        run_subprocess([
             'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
             os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry', 'registry:2'
        ])

        logger.info("Cleaning up docker image")
        run_subprocess(['docker', 'rm', 'etny-pynithy-' + str(order_id)])

        logger.info("Running docker-compose")
        run_subprocess([
             'docker-compose', '-f', 'docker/docker-compose-etny-pynithy.yml', 'run', '--rm', '-d', '--name',
             'etny-pynity-' + str(order_id), 'etny-pynithy', str(order_id), metadata[2], metadata[3],
             self.__resultaddress, self.__resultprivatekey
        ])

        time.sleep(10)

        logger.info("Attaching to docker process")
        run_subprocess(['docker', 'attach', 'etny-pynithy-' + str(order_id)])

    def process_dp_request(self):
        req = DPRequest(self.__etny.caller()._getDPRequest(self.__dprequest))

        order_id = self.find_order_by_dp_req()
        if order_id is not None:
            order = Order(self.__etny.caller()._getOrder(order_id))
            if order.status == OrderStatus.CLOSED:
                logger.info("DP request %s completed successfully!" % self.__dprequest)
            if order.status == OrderStatus.PROCESSING:
                logger.info("DP request never finished, processing order %s" % order_id)
                self.process_order(order_id)
            if order.status == OrderStatus.OPEN:
                logger.info("Order was never approved, skipping")
            return

        logger.info("Processing NEW DP request %s" % self.__dprequest)

        checked = 0
        seconds = 0

        while True:
            found = False
            count = self.__etny.caller()._getDORequestsCount()
            for i in reversed(range(checked, count)):
                doreq = DORequest(self.__etny.caller()._getDORequest(i))
                if doreq.status != RequestStatus.AVAILABLE:
                    continue
                logger.info("Checking DO request: %s" % i)
                if not (doreq.cpu <= req.cpu and doreq.memory <= req.memory and
                        doreq.storage <= req.storage and doreq.bandwidth <= req.bandwidth):
                    logger.info("Order doesn't meet requirements, skipping to next request")
                    continue
                logger.info("Placing order...")
                try:
                    self.place_order(i)
                except (exceptions.SolidityError, IndexError) as error:
                    logger.info("Order already created, skipping to next request")
                    continue
                found = True
                logger.info("Waiting for order %s approval..." % self.__order)
                if not retry(self.wait_for_order_approval, attempts=10, delay=5):
                    logger.info("Order was not approved in the last ~10 blocks, skipping to next request")
                    break
                self.process_order(self.__order)
                logger.info("Order %s, with DO request %s and DP request %s processed successfully" % (
                    self.__order, i, self.__dprequest))
                break
            if found:
                logger.info("Finished processing order %s" % self.__order)
                break
            checked = count - 1
            time.sleep(5)

            seconds += 5
            if seconds >= config.dp_request_timeout:
                logger.info("DP request timed out!")
                self.cancel_dp_request(self.__dprequest)
                break

    def add_processor_to_order(self, order):
        unicorn_txn = self.__etny.functions._addProcessorToOrder(order, self.__resultaddress).\
            buildTransaction(self.get_transaction_build())
        _hash = self.send_transaction(unicorn_txn)

        try:
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(ex)
            raise

        logger.info("Added the enclave processor to the order!")
        logger.info("TX Hash: %s" % _hash)

    def wait_for_order_approval(self):
        order = Order(self.__etny.caller()._getOrder(self.__order))
        return order.status != OrderStatus.OPEN

    def find_order_by_dp_req(self):
        logger.info("Finding order with DP request %s " % self.__dprequest)
        order_id = self.cache.get(self.__dprequest)
        if order_id is not None:
            logger.info("Found in cache, order_id = %s " % order_id)
            return order_id

        count = self.__etny.functions._getOrdersCount().call()
        cached_ids = self.cache.get_values()
        for i in [id for id in reversed(range(1, count)) if id not in cached_ids]:
            order = Order(self.__etny.caller()._getOrder(i))
            self.cache.add(order.dp_req, i)
            logger.debug("Checking order %s " % i)
            if order.dp_req == self.__dprequest:
                return i
        return None

    def place_order(self, doreq):
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
        logger.info("TX Hash: %s" % _hash)

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
