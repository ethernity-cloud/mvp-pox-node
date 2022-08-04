#!/usr/bin/python

# update 14.06.2022

import argparse
import os

from web3 import Web3
from eth_account import Account
from web3.middleware import geth_poa_middleware
from web3.exceptions import (
    BlockNotFound,
    TimeExhausted,
    TransactionNotFound,
)

CONTRACT_ADDRESS = "0x2f5b4cdaf5A03Cb9947F9213821d94B758D1f312"

class etnyPoX:
    def __init__(self):
        parser = argparse.ArgumentParser(description = "Ethernity PoX result")
        parser.add_argument("-p", "--publickey", help = "Etherem wallet publickey (0x0123456789abcdef0123456789abcdef01234567) ", required = True)
        parser.add_argument("-k", "--privatekey", help = "Etherem wallet privatekey (0x0123456789abcDEF0123456789abcDEF0123456789abcDEF0123456789abcDEF) ", required = True)
        parser.add_argument("-o", "--order", help = "Ethernity PoX orderID", required = True, default = "")
        parser.add_argument("-r", "--result", help = "Ethernity PoX results hash", required = True, default = "")
        # parser.add_argument("-c", "--contract", help = "Contract address", required = True, default = "")
        print('after init')

        argument = parser.parse_args()

        if argument.publickey:
            etnyPoX.publickey = format(argument.publickey)
        if argument.privatekey:
            etnyPoX.privatekey = format(argument.privatekey)
        if argument.order:
            etnyPoX.order= int(format(argument.order))
        if argument.result:
            etnyPoX.result = format(argument.result)
        # if argument.contract:
        #     etnyPoX.contract_address = format(argument.contract)

        etnyPoX.args = vars(argument)

        f = open(os.path.dirname(os.path.realpath(__file__)) + '/pox.abi')
        etnyPoX.contract_abi = f.read()
        f.close()
        

        etnyPoX.w3 = Web3(Web3.HTTPProvider("https://core.bloxberg.org"))
        etnyPoX.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        etnyPoX.acct = Account.privateKeyToAccount(etnyPoX.privatekey)
        etnyPoX.etny = etnyPoX.w3.eth.contract(address=etnyPoX.w3.toChecksumAddress(CONTRACT_ADDRESS), abi=etnyPoX.contract_abi)

    def addResult():
        nonce = etnyPoX.w3.eth.getTransactionCount(etnyPoX.publickey)

        unicorn_txn = etnyPoX.etny.functions._addResultToOrder(
            etnyPoX.order, etnyPoX.result
        ).buildTransaction({
            'gas': 1000000,
            'chainId': 8995,
            'nonce': nonce,
            'gasPrice': etnyPoX.w3.toWei("1", "mwei"),
        })

        signed_txn = etnyPoX.w3.eth.account.sign_transaction(unicorn_txn, private_key=etnyPoX.acct.key)
        etnyPoX.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        hash = etnyPoX.w3.toHex(etnyPoX.w3.sha3(signed_txn.rawTransaction))

        try:
            etnyPoX.w3.eth.waitForTransactionReceipt(hash)
        except:
            raise
        else:
            print('args = ')
            print(etnyPoX.args)
            print("Result transaction was successful!")
            print("Result IPFSdddd d: %s" % etnyPoX.result)
            print("TX Hash: %s" % hash)


if __name__ == '__main__':
    app = etnyPoX()
    etnyPoX.addResult()
