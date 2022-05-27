#!/usr/bin/python3

import time
import argparse
import uuid
import errno
import os
# import sys
import ipfshttpclient
import socket
import subprocess

from . import config
from web3 import Web3
from web3 import exceptions
from eth_account import Account
from web3.middleware import geth_poa_middleware

# from web3.exceptions import (
#    BlockNotFound,
#    TimeExhausted,
#    TransactionNotFound,
# )

logger = config.logger


class DPInProcessing(Exception):
    def __init__(self, *args):
        self.message = args[0] if args else None

    def __str__(self):
        if self.message:
            return 'DPInProcessing, {0} '.format(self.message)
        return 'DP is already processing another task'


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
        arguments = self.__read_arguments()
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

        self.create_uuid(config.uuid_filepath)

    def create_uuid(self, filename):
        if not os.path.exists(os.path.dirname(filename)):
            try:
                os.makedirs(os.path.dirname(filename))
            except OSError as exc:  # Guard against race condition
                if exc.errno != errno.EEXIST:
                    raise
        try:
            f = open(filename)
            # Do something with the file
        except FileNotFoundError:
            with open(filename, "w+") as f:
                f.write(uuid.uuid4().hex)
            f = open(filename)
        except OSError as ex:
            logger.error(ex.errno)
            raise
        finally:
            self.__uuid = f.read()
            f.close()

    @staticmethod
    def __read_arguments():
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
        parser.add_argument("-c", "--cpu", help="Number of CPUs (count)", required=False, default="1")
        parser.add_argument("-m", "--memory", help="Amount of memory (GB)", required=False, default="1")
        parser.add_argument("-s", "--storage", help="Amount of storage (GB)", required=False, default="40")
        parser.add_argument("-b", "--bandwidth", help="Amount of bandwidth (GB)", required=False, default="1")
        parser.add_argument("-t", "--duration", help="Amount of time allocated for task (minutes)", required=False,
                            default="60")
        return parser.parse_args()

    def __parse_arguments(self, arguments):
        # string attributes
        for arg in ['address', 'privatekey', 'resultaddress', 'resultprivatekey']:
            setattr(self, "_" + self.__class__.__name__ + "__" + arg, getattr(arguments, arg))
        # int attributes
        for arg in ['cpu', 'memory', 'storage', 'storage', 'bandwidth', 'duration']:
            setattr(self, "_" + self.__class__.__name__ + "__" + arg, int(getattr(arguments, arg)))

    def cleanup_dp_requests(self):
        count = self.__etny.functions._getDPRequestsCount().call()
        for i in reversed(range(count)):
            logger.debug("Cleaning up DP request %s" % i)
            req = self.__etny.caller()._getDPRequest(i)
            metadata = self.__etny.caller()._getDPRequestMetadata(i)
            if metadata[1] == self.__uuid and req[0] == self.__address:
                if req[7] == 1:
                    logger.debug("Request %s already assigned to order" % i)
                    self.__dprequest = i
                    self.process_dp_request()
                if req[7] == 0:
                    self.cancel_dp_request(i)
            else:
                logger.debug("Skipping DP request %s, not mine" % i)

    def add_dp_request(self):
        unicorn_txn = self.__etny.functions._addDPRequest(
            self.__cpu, self.__memory, self.__storage, self.__bandwidth, self.__duration, 0, self.__uuid, "", "",
            ""
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

    @staticmethod
    def run_subprocess(args):
        out = subprocess.Popen(args,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
        stdout, stderr = out.communicate()
        logger.debug(stdout)
        logger.debug(stderr)

    def process_order(self, orderid):
        order = self.__etny.caller()._getOrder(orderid)
        self.add_processor_to_order(orderid)
        logger.info("Downloading IPFS content...")
        metadata = self.__etny.caller()._getDORequestMetadata(order[2])
        template = metadata[1].split(':')
        for attempt in range(10):
            try:
                self.__download_ipfs(template[0])
                self.__download_ipfs(metadata[2])
                self.__download_ipfs(metadata[3])
                break
            except Exception as ex:
                logger.error(ex)
                if attempt == 9:
                    logger.info("Cannot download data from IPFS, cancelling processing")
                    return

        logger.info("Stopping previous docker registry")
        self.run_subprocess(['docker', 'stop', 'registry'])

        logger.info("Cleaning up docker registry")
        self.run_subprocess(['docker', 'system', 'prune', '-a', '-f'])

        logger.info("Running new docker registry")
        logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry')
        self.run_subprocess([
             'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
             os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry', 'registry:2'
        ])

        logger.info("Cleaning up docker image")
        self.run_subprocess(['docker', 'rm', 'etny-pynithy-' + str(orderid)])

        logger.info("Running docker-compose")
        self.run_subprocess([
             'docker-compose', '-f', 'docker/docker-compose-etny-pynithy.yml', 'run', '--rm', '-d', '--name',
             'etny-pynity-' + str(orderid), 'etny-pynithy', str(orderid), metadata[2], metadata[3],
             self.__resultaddress, self.__resultprivatekey
        ])

        time.sleep(10)

        logger.info("Attaching to docker process")
        self.run_subprocess(['docker', 'attach', 'etny-pynithy-' + str(orderid)])

    def process_dp_request(self):
        req = self.__etny.caller()._getDPRequest(self.__dprequest)
        metadata = self.__etny.caller()._getDPRequestMetadata(self.__dprequest)
        dproc, cpu, memory, storage, bandwidth, duration, price, status = req[0:8]
        uuid = metadata[1]

        orderid = self.find_order_by_dp_req()

        if orderid is not None:
            order = self.__etny.caller()._getOrder(orderid)
            if order[4] == 2:
                logger.info("DP request %s completed successfully!" % self.__dprequest)
            if order[4] == 1:
                logger.info("DP request never finished, processing order %s" % orderid)
                self.process_order(orderid)
            if order[4] == 0:
                logger.info("Order was never approved, skipping")
            return

        logger.info("Processing NEW DP request %s" % self.__dprequest)

        checked = 0
        seconds = 0

        while True:
            found = False
            count = self.__etny.caller()._getDORequestsCount()
            for i in reversed(range(checked, count)):
                doreq = self.__etny.caller()._getDORequest(i)
                if doreq[7] != 0:
                    continue

                logger.info("Checking DO request: %s" % i)
                if doreq[1] <= cpu and doreq[2] <= memory and doreq[3] <= storage and doreq[4] <= bandwidth:
                    logger.info("Placing order...")
                    try:
                        self.place_order(i)
                    except (exceptions.SolidityError, IndexError) as error:
                        print(error)
                        logger.info("Order already created, skipping to next request")
                        continue
                    found = True
                    logger.info("Waiting for order %s approval..." % self.__order)
                    if not self.wait_for_order_approval():
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

    @staticmethod
    def __download_ipfs(hashvalue):
        ipfsnode = socket.gethostbyname(config.ipfs_host)
        client = ipfshttpclient.connect(config.client_connect_url)
        client.bootstrap.add(config.client_bootstrap_url % ipfsnode)
        # client.swarm.connect('/ip4/%s/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5' % ipfsnode)
        # bug tracked under https://github.com/ipfs-shipyard/py-ipfs-http-client/issues/246

        client.get(hashvalue)

        return None

    def wait_for_order_approval(self):
        for o in range(0, 10):
            order = self.__etny.caller()._getOrder(self.__order)
            if order[4] > 0:
                return True
            time.sleep(5)
        return False

    def find_order(self, doreq):
        logger.info("Finding order match for %s and %s" % (doreq, self.__dprequest))
        count = self.__etny.functions._getOrdersCount().call()
        for i in reversed(range(count)):
            order = self.__etny.caller()._getOrder(i)
            logger.debug("Checking order %s " % i)
            if order[2] == doreq and order[3] == self.__dprequest and order[4] == 0:
                return i
        return None

    def find_order_by_dp_req(self):
        logger.info("Finding order with DP request %s " % self.__dprequest)
        count = self.__etny.functions._getOrdersCount().call()
        for i in reversed(range(1, count)):
            order = self.__etny.caller()._getOrder(i)
            logger.debug("Checking order %s " % i)
            if order[3] == self.__dprequest:
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
    app = EtnyPoXNode()

    logger.info("Cleaning up previous DP requests...")
    try:
        app.cleanup_dp_requests()
    except Exception as e:
        logger.error(e)
        raise
    logger.info("[DONE]")

    try:
        app.resume_processing()
    except Exception as e:
        logger.error(e)
        raise
