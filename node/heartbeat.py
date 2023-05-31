import asyncio
import time
import schedule
import threading

import config

from web3 import Web3
from web3.middleware import geth_poa_middleware
from eth_account import Account

logger = config.logger


class HeartBeat:

    def __init__(self, heartbeat_interval, benchmark_results, **w3_data):
        with open(config.heartbeat_abi_filepath) as f_in:
            self.contract_heartbeat = f_in.read()
        self.w3 = w3_data['w3']
        self.heartbeat = self.w3.eth.contract(
            address=self.w3.toChecksumAddress(config.heartbeat_contract_address),
            abi=self.contract_heartbeat
        )
        self.heartbeat_interval = heartbeat_interval
        self.benchmark_results = benchmark_results
        self.nonce = w3_data['nonce']
        self.address = w3_data['address']
        self.account = w3_data['account']
        self.loop = None

    def heartbeat_start(self):
        self.loop = asyncio.get_event_loop()
        self.schedule_heartbeat_smart_contract_call()
        self.run_heartbeat_scheduler()

    def schedule_heartbeat_smart_contract_call(self):
        schedule.every(self.heartbeat_interval).minutes.do(self.call_heartbeat_smart_contract)

    def run_heartbeat_scheduler(self):
        def heartbeat_scheduler_thread():
            while True:
                schedule.run_pending()
                time.sleep(1)

        threading.Thread(target=heartbeat_scheduler_thread, daemon=True).start()
        self.loop.run_forever()

    def call_heartbeat_smart_contract(self):
        current_time = int(time.time())
        last_call_time = self._get_last_call_time()

        # Check if enough time has passed since the last call.
        if current_time - last_call_time >= self.heartbeat_interval:
            logger.info("Calling heartbeat smart contract...")
            unicorn_txn = self.heartbeat.functions.logCall(self.benchmark_results).buildTransaction(
                self._get_transaction_build())

            try:
                _hash = self._send_transaction(unicorn_txn)
                self.w3.eth.wait_for_transaction_receipt(_hash)
            except Exception as exp:
                logger.error(f"Error while sending heartbeat call:{exp}")
                raise

            logger.info(f"Added result to the order!")
            logger.info(f"TX Hash: {_hash}")

            self._update_last_call_time(current_time)
        else:
            logger.info("Skipping smart contract call. Not enough time has passed.")

    def _get_last_call_time(self):
        try:
            with open(config.heartbeat_timestamp_filepath, "r") as file:
                last_call_time = int(file.read())
        except (FileNotFoundError, ValueError) as exp:
            last_call_time = 0
            logger.exception(exp)

        return last_call_time

    def _update_last_call_time(self, timestamp):
        with open(config.heartbeat_timestamp_filepath, "w") as file:
            file.write(str(timestamp))


    def _get_transaction_build(self, existing_nonce=None):
        self.nonce = existing_nonce if existing_nonce else self.w3.eth.getTransactionCount(self.address)

        return {
            'chainId': config.chain_id,
            'gas': config.gas_limit,
            'nonce': self.nonce,
            'gasPrice': self.w3.toWei(config.gas_price_value, config.gas_price_measure),
        }

    def _send_transaction(self, unicorn_txn):
        try:
            signed_txn = self.w3.eth.account.sign_transaction(unicorn_txn, private_key=self.account.key)
            self.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
            _hash = self.w3.toHex(self.w3.sha3(signed_txn.rawTransaction))
            return _hash
        except Exception as ex:
            logger.error(f"Error sending Transaction, Error Message: {ex}")
            raise


if __name__ == '__main__':
    w3 = Web3(Web3.HTTPProvider(config.http_provider, request_kwargs={'timeout': 120}))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    acct = Account.privateKeyToAccount("AE6AE8E5CCBFB04590405997EE2D52D2B330726137B875053C36D94E974D162F")
    nonce = w3.eth.getTransactionCount("0xf17f52151EbEF6C7334FAD080c5704D77216b732")
    address = "0xf17f52151EbEF6C7334FAD080c5704D77216b732"
    heartbeat_w3_data = {
        'w3': w3,
        'nonce': nonce,
        'address': address,
        'account': acct
    }
    heartbeat_interval = 720
    heartbeat_benchmark = ""
    heartbeat = HeartBeat(heartbeat_interval, heartbeat_benchmark, **heartbeat_w3_data)
    print("Starting heartbeat ... ")
    heartbeat.heartbeat_start()
