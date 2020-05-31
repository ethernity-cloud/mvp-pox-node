#!/usr/bin/python3

import time
import argparse
import uuid
import errno
import os
import sys
import ipfshttpclient
from os.path import expanduser
import subprocess

from web3 import Web3
from eth_account import Account
from web3.middleware import geth_poa_middleware
from web3.exceptions import (
    BlockNotFound,
    TimeExhausted,
    TransactionNotFound,
)



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

        etnyPoX.dprequest = etnyPoX.etny.functions._getDPRequestsCount().call() - 3


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
            print(e.errno)
        finally:
            etnyPoX.uuid=f.read()
            f.close()

    def cleanupDPRequests():
        count=etnyPoX.etny.functions._getDPRequestsCount().call()
        for i in range(count-3, count):
            req = etnyPoX.etny.caller()._getDPRequest(i)
            metadata = etnyPoX.etny.caller()._getDPRequestMetadata(i)
            if metadata[1] == etnyPoX.uuid and req[0] == etnyPoX.address:
                if req[7] == 1:
                    raise DPInProcessing
                if req[7] == 2:
                    continue
                etnyPoX.cancelDPRequest(i)


    def addDPRequest():
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)
        unicorn_txn = etnyPoX.etny.functions._addDPRequest(
            etnyPoX.cpu, etnyPoX.memory, etnyPoX.storage, etnyPoX.bandwidth, etnyPoX.duration, 0, etnyPoX.uuid, "", "", ""
        ).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1000", "gwei"),
        })


        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))

        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            print("DP request created successfully!")
            print("TX Hash: %s" % hash)


    def cancelDPRequest(req):
        print("Cancelling DP request %s" % req)
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        unicorn_txn = etnyPoX.etny.functions._cancelDPRequest(req).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1000", "gwei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))


        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            print("DP request %s cancelled successfully!" % req)
            print("TX Hash: %s" % hash)


    def getNextDPRequests():
        count = etnyPoX.etny.functions._getDPRequestsCount().call()
        if etnyPoX.dprequest >= count:
            etnyPoX.dprequest = count - 10

        print("Fetching DP request: %s" % etnyPoX.dprequest)
        if etnyPoX.dprequest == count - 1:
            time.sleep(5)

        dpReq = etnyPoX.etny.caller()._getDPRequest(etnyPoX.dprequest)
        metadata = etnyPoX.etny.caller()._getDPRequestMetadata(etnyPoX.dprequest)
        if dpReq[0] == etnyPoX.address and dpReq [7] < 2 and  metadata[1] == etnyPoX.uuid:
            etnyPoX.dprequest += 1
            return  etnyPoX.dprequest-1
        etnyPoX.dprequest += 1

    def processDPRequest(dpReq):
        req = etnyPoX.etny.caller()._getDPRequest(dpReq)
        metadata = etnyPoX.etny.caller()._getDPRequestMetadata(dpReq)
        dproc = req[0]
        cpu = req[1]
        memory = req[2]
        storage = req[3]
        bandwidth = req[4]
        duration = req[5]
        price = req[6]
        status = req[7]
        uuid = metadata[1]

        
        count = etnyPoX.etny.caller()._getDORequestsCount()

        for i in range(count-3, count):
            doReq = etnyPoX.etny.caller()._getDORequest(i)
            print("Processing DO request: %s of %s" % (i, count))
            if doReq[1] <= cpu and doReq[2] <= memory and doReq[3] <= storage and doReq[4] <= bandwidth:
                order = None
                order = etnyPoX.findOrder(i, dpReq)
                if order is None:
                    if status < 1 and doReq[7] < 1:
                        print("Found DO request: %s " % i)
                        print("Placing order...")
                        etnyPoX.placeOrder(i, dpReq)
                        order = etnyPoX.findOrder(i, dpReq)
                    else:
                        continue
                etnyPoX.waitForOrderApproval(order)
                etnyPoX.addProcessorToOrder(order)
                print ("Downloading IPFS content...")
                metadata =  etnyPoX.etny.caller()._getDORequestMetadata(i)
                template = metadata[1].split(':')
                etnyPoX.downloadIPFS(template[0])
                etnyPoX.downloadIPFS(metadata[2])
                etnyPoX.downloadIPFS(metadata[3])
                print ("Stopping previous docker registry")
                out = subprocess.Popen(['docker', 'stop', 'registry'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT)
                stdout,stderr = out.communicate()
                print (stdout)
                print (stderr)
                print ("Cleaning up docker registry")
                out = subprocess.Popen(['docker', 'system', 'prune', '-a', '-f'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT)
                stdout,stderr = out.communicate()
                print (stdout)
                print (stderr)
                print ("Running new docker registry")
                print (os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry')
                out = subprocess.Popen(['docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v', os.path.dirname(os.path.realpath(__file__)) + '/' + template[0] + ':/var/lib/registry', 'registry:2'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT)
                stdout,stderr = out.communicate()
                print (stdout)
                print (stderr)
                out = subprocess.Popen(['docker-compose', '-f', 'docker/docker-compose-etny-pynithy.yml', 'run', 'etny-pynithy', str(order), metadata[2], metadata[3], etnyPoX.resultaddress, etnyPoX.resultprivatekey],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT)
                stdout,stderr = out.communicate()
                print (stdout)
                print (stderr)
                etnyPoX.addDPRequest()

    def addProcessorToOrder(order):
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        unicorn_txn = etnyPoX.etny.functions._addProcessorToOrder(order, etnyPoX.resultaddress).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1000", "gwei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))


        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            print("Added the enclave processor to the order!")
            print("TX Hash: %s" % hash)


    def downloadIPFS(hash):
        print(hash)
        client = ipfshttpclient.connect('/ip4/127.0.0.1/tcp/5001/http')
        client.get(hash)

        return None

    def waitForOrderApproval(orderID):
        print("Waiting for order %s approval..." % orderID)
        while True:
            order = etnyPoX.etny.caller()._getOrder(orderID)
            if order[4] < 1:
                time.sleep(5)
            else:
                break


    def findOrder(doReq, dpReq):
        print("Finding order match for %s and %s" % (doReq, dpReq))
        count=etnyPoX.etny.functions._getOrdersCount().call()
        for i in range(count-3, count):
            order = etnyPoX.etny.caller()._getOrder(i)
            if order[2] == doReq and order[3] == dpReq and order[4] == 0:
                return i 
        return None


    def placeOrder(doReq, dpReq):
        etnyPoX.nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.address)

        unicorn_txn = etnyPoX.etny.functions._placeOrder(
                int(doReq), int(dpReq),
        ).buildTransaction({
            'chainId': 8995,
            'gas': 1000000,
            'nonce': etnyPoX.nonce,
            'gasPrice':  etnyPoX.w3.toWei("1000", "gwei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))


        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            print("Order placed successfully!")
            print("TX Hash: %s" % hash)
            time.sleep(5)

    def reusumeProcessing():
         dpReq = etnyPoX.getNextDPRequests()
         if dpReq:
            etnyPoX.processDPRequest(dpReq)
            #etnyPoX.addDPRequest()

        

if __name__ == '__main__':
    app = etnyPoX()

    dots="."
    print("Cleaning up previous DP requests...")
    while True:
        try:
            etnyPoX.cleanupDPRequests()
        except DPInProcessing:
            print("[DONE]")
            print("Resuming DP task...")
            etnyPoX.reusumeProcessing()
            break 
        except:
            raise
        else:
            break
    print ("[DONE]")



    etnyPoX.addDPRequest()
   
    while True:
        try:
            etnyPoX.reusumeProcessing()
        except:
            raise
            break
        else:
            continue




