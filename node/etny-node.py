#!/usr/bin/python3

import time
import argparse
import uuid
import errno
import os
import sys
import ipfshttpclient
import socket
from os.path import expanduser
import subprocess
import logging

from web3 import Web3
from eth_account import Account
from web3.middleware import geth_poa_middleware
from web3.exceptions import (
    BlockNotFound,
    TimeExhausted,
    TransactionNotFound,
)


logging.basicConfig(filename='/var/log/etny-node.log', format='%(asctime)s %(message)s', level=logging.INFO)


class DPInProcessing(Exception):
    def __init__(self, *args):
        if args:
            self.message = args[0]
        else:
            self.message = None

    def __str__(self):
        if self.message:
            return 'DPInProcessing, {0} '.format(self.message)
        else:
            return 'DP is already processing another task'


class etnyPoX:
    def __init__(self):
        parser = argparse.ArgumentParser(description = "Ethernity PoX request")
        parser.add_argument("-a", "--address", help = "Etherem DP address (0xf17f52151EbEF6C7334FAD080c5704D77216b732)", required = True)
        parser.add_argument("-k", "--privatekey", help = "Etherem DP privatekey (AE6AE8E5CCBFB04590405997EE2D52D2B330726137B875053C36D94E974D162F)", required = True)
        parser.add_argument("-r", "--resultaddress", help = "Etherem RP address (0xC5fdf4076b8F3A5357c5E395ab970B5B54098Fef)", required = True)
        parser.add_argument("-j", "--resultprivatekey", help = "Etherem RP privatekey (0DBBE8E4AE425A6D2687F1A7E3BA17BC98C673636790F1B8AD91193C05875EF1)", required = True)
        parser.add_argument("-c", "--cpu", help = "Number of CPUs (count)", required = False, default = "1")
        parser.add_argument("-m", "--memory", help = "Amount of memory (GB)", required = False, default = "1")
        parser.add_argument("-s", "--storage", help = "Amount of storage (GB)", required = False, default = "40")
        parser.add_argument("-b", "--bandwidth", help = "Amount of bandwidth (GB)", required = False, default = "1")
        parser.add_argument("-t", "--duration", help = "Amount of time allocated for task (minutes)", required = False, default = "60")




        argument = parser.parse_args()
        status = False

        if argument.address:
            etnyPoX.address = format(argument.address)
            status = True
        if argument.privatekey:
            etnyPoX.privatekey = format(argument.privatekey)
            status = True
        if argument.resultaddress:
            etnyPoX.resultaddress = format(argument.resultaddress)
            status = True
        if argument.resultprivatekey:
            etnyPoX.resultprivatekey = format(argument.resultprivatekey)
            status = True
        if argument.cpu:
            etnyPoX.cpu = int(format(argument.cpu))
            status = True
        if argument.memory:
            etnyPoX.memory = int(format(argument.memory))
            status = True
        if argument.storage:
            etnyPoX.storage = int(format(argument.storage))
            status = True
        if argument.bandwidth:
            etnyPoX.bandwidth = int(format(argument.bandwidth))
            status = True
        if argument.duration:
            etnyPoX.duration = int(format(argument.duration))
            status = True

        f = open(os.path.dirname(os.path.realpath(__file__)) + '/pox.abi')
        etnyPoX.contract_abi = f.read()
        f.close()

        etnyPoX.w3 = Web3(Web3.HTTPProvider("https://core.bloxberg.org"))
        etnyPoX.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        etnyPoX.acct = Account.privateKeyToAccount(etnyPoX.privatekey)
        etnyPoX.etny = etnyPoX.w3.eth.contract(address=etnyPoX.w3.toChecksumAddress("0x99738e909a62e2e4840a59214638828E082A9A2b"), abi=etnyPoX.contract_abi)
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        etnyPoX.dprequest = 0
        etnyPoX.order = 0

        home = expanduser("~")
        filename = home + "/opt/etny/node/UUID"

        if not os.path.exists(os.path.dirname(filename)):
            try:
                os.makedirs(os.path.dirname(filename))
            except OSError as exc: # Guard against race condition
                if exc.errno != errno.EEXIST:
                    raise
        
        try:
            f = open(filename)
            # Do something with the file
        except FileNotFoundError:
            f = open(filename,"w+")
            f.write((uuid.uuid4().hex))
            f.close()
            f = open(filename)
        except OSError as e:
            logging.error(e.errno)
        finally:
            etnyPoX.uuid=f.read()
            f.close()

    def cleanupDPRequests():
        count=etnyPoX.etny.functions._getDPRequestsCount().call()
        for i in range(count-1, -1, -1):
            logging.debug("Cleaning up DP request %s" % i)
            req = etnyPoX.etny.caller()._getDPRequest(i)
            metadata = etnyPoX.etny.caller()._getDPRequestMetadata(i)
            if metadata[1] == etnyPoX.uuid and req[0] == etnyPoX.address:
                if req[7] == 1:
                    logging.debug("Request %s already assigned to order" % i)
                    etnyPoX.dprequest = i
                    etnyPoX.processDPRequest()
                    continue
                if req[7] == 0:
                    etnyPoX.cancelDPRequest(i)
            else:
                logging.debug("Skipping DP request %s, not mine" % i)


    def addDPRequest():
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)
        unicorn_txn = etnyPoX.etny.functions._addDPRequest(
            etnyPoX.cpu, etnyPoX.memory, etnyPoX.storage, etnyPoX.bandwidth, etnyPoX.duration, 0, etnyPoX.uuid, "", "", ""
        ).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1", "wei"),
        })


        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))

        try:
            receipt = etnyPoX.w3.eth.waitForTransactionReceipt(hash)
            processed_logs = etnyPoX.etny.events._addDPRequestEV().processReceipt(receipt)
            etnyPoX.dprequest = processed_logs[0].args._rowNumber
        except:
            raise
        else:
            logging.info("DP request created successfully!")
            logging.info("TX Hash: %s" % hash)


    def cancelDPRequest(req):
        logging.info("Cancelling DP request %s" % req)
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        unicorn_txn = etnyPoX.etny.functions._cancelDPRequest(req).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1", "wei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))


        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            logging.info("DP request %s cancelled successfully!" % req)
            logging.info("TX Hash: %s" % hash)


    def processOrder(orderID):
        order = etnyPoX.etny.caller()._getOrder(orderID)
        etnyPoX.addProcessorToOrder(orderID)
        logging.info("Downloading IPFS content...")
        metadata = etnyPoX.etny.caller()._getDORequestMetadata(order[2])
        template = metadata[1].split(':')
        for attempt in range(10):
            try:
                etnyPoX.downloadIPFS(template[0])
                etnyPoX.downloadIPFS(metadata[2])
                etnyPoX.downloadIPFS(metadata[3])
            except:
                if attempt == 10:
                    logging.info("Cannot download data from IPFS, cancelling processing")
                    return
                continue
            else:
                break


        logging.info("Stopping previous docker registry")
        out = subprocess.Popen(['docker', 'stop', 'registry'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        stdout,stderr = out.communicate()
        logging.debug(stdout)
        logging.debug(stderr)
        logging.info("Cleaning up docker registry")
        out = subprocess.Popen(['docker', 'system', 'prune', '-a', '-f'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        stdout,stderr = out.communicate()
        logging.debug(stdout)
        logging.debug(stderr)
        logging.info("Running new docker registry")
        logging.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry')
        out = subprocess.Popen(['docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v', os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry', 'registry:2'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        stdout,stderr = out.communicate()
        logging.debug(stdout)
        logging.debug(stderr)
        out = subprocess.Popen(['docker-compose', '-f', 'docker/docker-compose-etny-pynithy.yml', 'run', 'etny-pynithy', str(orderID), metadata[2], metadata[3], etnyPoX.resultaddress, etnyPoX.resultprivatekey],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        stdout,stderr = out.communicate()
        logging.debug(stdout)
        logging.debug(stderr)

    def processDPRequest():
        req = etnyPoX.etny.caller()._getDPRequest(etnyPoX.dprequest)
        metadata = etnyPoX.etny.caller()._getDPRequestMetadata(etnyPoX.dprequest)
        dproc = req[0]
        cpu = req[1]
        memory = req[2]
        storage = req[3]
        bandwidth = req[4]
        duration = req[5]
        price = req[6]
        status = req[7]
        uuid = metadata[1]

        
        orderID = etnyPoX.findOrderByDPReq()

        if orderID != None:
           order = etnyPoX.etny.caller()._getOrder(orderID)
           if order[4] == 2:
               logging.info("DP request %s completed successfully!" % etnyPoX.dprequest)
           if order[4] == 1:
               logging.info("DP request never finished, processing order %s" % orderID)
               etnyPoX.processOrder(orderID)
           if order[4] == 0:
               logging.info("Order was never approved, skipping")
           return

        logging.info("Processing NEW DP request %s" % etnyPoX.dprequest)

        while True:
            found = 0
            count = etnyPoX.etny.caller()._getDORequestsCount()
            for i in range(count-1, -1, -1):
                doReq = etnyPoX.etny.caller()._getDORequest(i)
                if doReq[7] == 0:
                    found = 1
                    logging.info("Processing DO request: %s" % i)
                    if doReq[1] <= cpu and doReq[2] <= memory and doReq[3] <= storage and doReq[4] <= bandwidth:
                        logging.info("Found DO request: %s " % i)
                        logging.info("Placing order...")
                        etnyPoX.placeOrder(i)
                        logging.info("Waiting for order %s approval..." % etnyPoX.order)
                        if etnyPoX.waitForOrderApproval() == False:
                            logging.info("Order was not approved in the latest ~10 blocks, skipping to next request")
                            break
                        etnyPoX.processOrder(etnyPoX.order)
                        logging.info("Order %s, with DO request %s and DP request %s processed successfully" % (etnyPoX.order, i, etnyPoX.dprequest))
                        break
            if found == 1:
                break


    def addProcessorToOrder(order):
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        unicorn_txn = etnyPoX.etny.functions._addProcessorToOrder(order, etnyPoX.resultaddress).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1", "wei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))


        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            logging.info("Added the enclave processor to the order!")
            logging.info("TX Hash: %s" % hash)


    def downloadIPFS(hash):
        ipfsnode = socket.gethostbyname('ipfs.ethernity.cloud')
        client = ipfshttpclient.connect('/ip4/127.0.0.1/tcp/5001/http')
        client.bootstrap.add('/ip4/%s/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5' % ipfsnode)
        #client.swarm.connect('/ip4/%s/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5' % ipfsnode) # bug tracked under https://github.com/ipfs-shipyard/py-ipfs-http-client/issues/246

        client.get(hash)

        return None

    def waitForOrderApproval():
        for o in range (0, 10):
            order = etnyPoX.etny.caller()._getOrder(etnyPoX.order)
            if order[4] > 0:
                return True
            else:
                time.sleep(5)
        return False



    def findOrder(doReq):
        logging.info("Finding order match for %s and %s" % (doReq, etnyPoX.dprequest))
        count=etnyPoX.etny.functions._getOrdersCount().call()
        for i in range(count-1, -1, -1):
            order = etnyPoX.etny.caller()._getOrder(i)
            logging.debug("Checking order %s " % i)
            if order[2] == doReq and order[3] == etnyPoX.dprequest and order[4] == 0:
                return i
        return None

    def findOrderByDPReq():
        logging.info("Finding order with DP request %s " % etnyPoX.dprequest)
        count=etnyPoX.etny.functions._getOrdersCount().call()
        for i in range(count-1, 0, -1):
            order = etnyPoX.etny.caller()._getOrder(i)
            logging.debug("Checking order %s " % i)
            if order[3] == etnyPoX.dprequest:
                return i
        return None



    def placeOrder(doReq):
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        unicorn_txn = etnyPoX.etny.functions._placeOrder(
                int(doReq), int(etnyPoX.dprequest),
        ).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1", "wei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))


        try:
            receipt = etnyPoX.w3.eth.waitForTransactionReceipt(hash)
            processed_logs = etnyPoX.etny.events._placeOrderEV().processReceipt(receipt)
            etnyPoX.order = processed_logs[0].args._orderNumber
        except:
            raise
        else:
            logging.info("Order placed successfully!")
            logging.info("TX Hash: %s" % hash)

    def resumeProcessing():
        while True:
            etnyPoX.addDPRequest()
            etnyPoX.processDPRequest()

if __name__ == '__main__':
    app = etnyPoX()

    dots="."
    logging.info("Cleaning up previous DP requests...")
    while True:
        try:
            etnyPoX.cleanupDPRequests()
        except:
            raise
        else:
            break
    logging.info("[DONE]")



    while True:
        try:
            etnyPoX.resumeProcessing()
        except:
            raise
            break
        else:
            continue




