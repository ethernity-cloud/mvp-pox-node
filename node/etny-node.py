#!/usr/bin/python3

import os, time, json
import shutil

from eth_account import Account
from web3 import Web3
from web3 import exceptions
from web3.middleware import geth_poa_middleware

import config
from utils import get_or_generate_uuid, run_subprocess, retry, Storage, Cache, ListCache, MergedOrdersCache, subprocess
from models import *
from error_messages import errorMessages
from swift_stream_service import SwiftStreamService
import io

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
    __endpoint = None
    __access_key = None
    __secret_key = None
    __price = 3

    def __init__(self):
        self.parse_arguments(config.arguments, config.parser)

        if config.network == None:
            config.network = 'OPENBETA';

        if config.network == 'TESTNET':
            config.contract_address = config.testnet_contract_address;
            config.heart_beat_address = config.testnet_heartbeat_address;
            config.gas_price_measure = config.testnet_gas_price_measure;
        else:
            config.contract_address = config.openbeta_contract_address;
            config.heart_beat_address = config.openbeta_heartbeat_address;
            config.gas_price_measure = config.openbeta_gas_price_measure;

        if config.contract_address == None:
            config.contract_address = '0x549A6E06BB2084100148D50F51CF77a3436C3Ae7';

        if config.heart_beat_address == None:
            config.heart_beat_address = '0x5c190f7253930C473822AcDED40B2eF1936B4075';

        if config.gas_price_measure == None:
            config.gas_price_measure = 'mwei';


        with open(config.abi_filepath) as f:
            self.__contract_abi = f.read()
        self.__w3 = Web3(Web3.HTTPProvider(config.http_provider, request_kwargs={'timeout': 120}))
        self.__w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.__acct = Account.privateKeyToAccount(self.__privatekey)
        self.__etny = self.__w3.eth.contract(
            address=self.__w3.toChecksumAddress(config.contract_address),
            abi=self.__contract_abi
        )

        with open(config.image_registry_abi_filepath) as f:
            self.__image_registry_abi = f.read()

        with open(config.heart_beat_abi_filepath) as f:
            self.__heart_beat_abi = f.read()

        self.__image_registry = self.__w3.eth.contract(
            address=self.__w3.toChecksumAddress(config.image_registry_address),
            abi=self.__image_registry_abi)
        self.__heart_beat = self.__w3.eth.contract(
            address=self.__w3.toChecksumAddress(config.heart_beat_address),
            abi=self.__heart_beat_abi)
        self.__nonce = self.__w3.eth.getTransactionCount(self.__address)
        self.__dprequest = 0
        self.__order_id = 0
        self.can_run_under_sgx = False
        self.__uuid = get_or_generate_uuid(config.uuid_filepath)
        self.orders_cache = Cache(config.orders_cache_limit, config.orders_cache_filepath)
        self.dpreq_cache = ListCache(config.dpreq_cache_limit, config.dpreq_filepath)
        self.doreq_cache = ListCache(config.doreq_cache_limit, config.doreq_filepath)
        self.ipfs_cache = ListCache(config.ipfs_cache_limit, config.ipfs_cache_filepath)
        self.storage = Storage(config.ipfs_host, config.client_connect_url, config.client_bootstrap_url,
                               self.ipfs_cache, config.logger)
        self.merged_orders_cache = MergedOrdersCache(config.merged_orders_cache_limit, config.merged_orders_cache)
        self.swift_stream_service = SwiftStreamService(self.__endpoint,
                                                       self.__access_key,
                                                       self.__secret_key)
        self.process_order_data = {}
        self.generate_process_order_data()
        self.__run_integration_test()

    def generate_process_order_data(self):
        if not os.path.exists(config.process_orders_cache_filepath):
            self.process_order_data = {"process_order_retry_counter": 0,
                                       "order_id": self.__order_id,
                                       "uuid": self.__uuid}

            json_object = json.dumps(self.process_order_data, indent=4)

            with open(config.process_orders_cache_filepath, "w") as outfile:
                outfile.write(json_object)

        else:
            with open(config.process_orders_cache_filepath, 'r') as openfile:
                self.process_order_data = json.load(openfile)

    def parse_arguments(self, arguments, parser):
        parser = parser.parse_args()
        for args_type, args in arguments.items():
            for arg in args:
                setattr(self, "_" + self.__class__.__name__ + "__" + arg, args_type(getattr(parser, arg)))

    def cleanup_dp_requests(self):
        my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
        cached_ids = self.dpreq_cache.get_values
        for req_id in set(my_dp_requests) - set(cached_ids):
            req_uuid = self.__etny.caller()._getDPRequestMetadata(req_id)[1]
            if req_uuid != self.__uuid:
                logger.info(f"Skipping DP request {req_id}, not mine")
                self.dpreq_cache.add(req_id)
                continue
            req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
            if req.status == RequestStatus.BOOKED:
                logger.info(f"Request {req_id} already assigned to order")
                self.__dprequest = req_id
                self.process_dp_request()
            if req.status == RequestStatus.AVAILABLE:
                self.cancel_dp_request(req_id)
            self.dpreq_cache.add(req_id)

    def _limited_arg(self, item, allowed_max=255):
        return allowed_max if item > allowed_max else item

    def add_dp_request(self, waiting_period_on_error=15, beginning_of_recursion=None):
        if self.__price is None:
            self.__price = 0

        params = [
            self._limited_arg(self.__cpu),
            self._limited_arg(self.__memory),
            self._limited_arg(self.__storage),
            self._limited_arg(self.__bandwidth),
            self.__duration,
            self.__price,
            self.__uuid,
            "v3",
            "",
            ""
        ]

        logger.info('params: {}'.format(params))

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

            receipt = self.__w3.eth.waitForTransactionReceipt(transaction_hash=_hash, timeout=waiting_seconds)

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

        try:
            unicorn_txn = self.__etny.functions._cancelDPRequest(req).buildTransaction(self.get_transaction_build())
            _hash = self.send_transaction(unicorn_txn)

            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(f"Error while canceling DP request - {req}: Error Message: {ex}")
            raise

        logger.info(f"DP request {req} cancelled successfully!")
        logger.info(f"TX Hash: {_hash}")
        time.sleep(5)

    def ipfs_timeout_cancel(self, order_id):
        result = 'Error: cannot download files from IPFS'
        self.add_result_to_order(order_id, result)

    def process_order(self, order_id, metadata=None):
        with open(config.process_orders_cache_filepath, 'r') as openfile:
            self.process_order_data = json.load(openfile)

        if self.process_order_data["order_id"] != order_id:
            self.process_order_data["order_id"] = order_id
            self.process_order_data["process_order_retry_counter"] = 0

        # this line should be checked later
        if not metadata:
            order = Order(self.__etny.caller()._getOrder(order_id))
            metadata = self.__etny.caller()._getDORequestMetadata(order.do_req)

        if self.process_order_data['process_order_retry_counter'] > 10:
            if metadata[1].startswith('v1:') == 1:
                logger.info('Building result ')
                result = self.build_result_format_v1("[Warn]",
                                                     f'Too many retries for the current order_id: {order_id}')
                logger.info(f'Result is: {result}')
                logger.info('Adding result to order')
                self.add_result_to_order(order_id, result)
                return

            else:
                logger.info('Building result ')
                result_msg = f'Too many retries for the current order_id: {order_id}'
                logger.warn(result_msg)
                logger.info('Adding result to order')
                self.add_result_to_order(order_id, result_msg)
                return

        self.process_order_data['process_order_retry_counter'] += 1
        json_object = json.dumps(self.process_order_data, indent=4)
        with open(config.process_orders_cache_filepath, "w") as outfile:
            outfile.write(json_object)

        self.add_processor_to_order(order_id)
        version = 0
        if metadata[1].startswith('v1:'):
            version = 1
            [v1, enclave_image_hash, enclave_image_name, docker_compose_hash, challenge_hash] = metadata[1].split(':')

        if metadata[1].startswith('v2:'):
            version = 2
            [v2, enclave_image_hash, enclave_image_name, docker_compose_hash, challenge_hash, public_cert] = metadata[
                1].split(':')

        if metadata[1].startswith('v3:'):
            version = 3
            [v3, enclave_image_hash, enclave_image_name, docker_compose_hash, challenge_hash, public_cert] = metadata[
                1].split(':')

        logger.info(f'Running version v{version}')
        if version == 0:
            # before
            [enclave_image_hash, *etny_pinithy] = metadata[1].split(':')
            try:
                logger.info(f"Downloading IPFS Image: {enclave_image_hash}")
                logger.info(f"Downloading IPFS Payload Hash: {metadata[2]}")
                logger.info(f"Downloading IPFS FileSet Hash: {metadata[3]}")
            except Exception as e:
                logger.info(str(e))
            self.storage.download_many([enclave_image_hash])
            if not self.storage.download_many([enclave_image_hash, metadata[2], metadata[3]]):
                logger.info("Cannot download data from IPFS, cancelling processing")
                self.ipfs_timeout_cancel(order_id)
                return

            logger.info("Stopping previous docker registry")

            run_subprocess(['docker', 'stop', 'registry'], logger)
            logger.info("Cleaning up docker registry")
            run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
            logger.info("Running new docker registry")
            logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry')
            run_subprocess([
                'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
                os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry',
                'registry:2'
            ], logger)

            logger.info("Cleaning up docker container")
            run_subprocess(['docker', 'rm', '-f', 'etny-pynithy-' + str(order_id)], logger)

            logger.info("Running docker-compose")

            yaml_file = '-initial-image' if enclave_image_hash in [
                'QmeQiSC1dLMKv4BvpvjWt1Zeak9zj6TWgWhN7LLiRznJqC'] else ''

            run_subprocess([
                'docker-compose', '-f', f'docker/docker-compose-etny-pynithy{yaml_file}.yml', 'run', '--rm', '-d',
                '--name',
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

        if version == 1:
            try:
                logger.info(f"Downloading IPFS Image: {enclave_image_hash}")
                logger.info(f"Downloading IPFS docker yml file: {docker_compose_hash}")
                logger.info(f"Downloading IPFS Payload Hash: {metadata[2]}")
                logger.info(f"Downloading IPFS FileSet Hash: {metadata[3]}")
                logger.info(f"Downloading IPFS Challenge Hash: {challenge_hash}")
            except Exception as e:
                logger.info(str(e))

            payload_hash = metadata[2].split(':')[1]
            input_hash = metadata[3].split(':')[1]
            list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash, challenge_hash, payload_hash]
            if input_hash is not None and len(input_hash) > 0:
                list_of_ipfs_hashes.append(input_hash)

            self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3)
            if not self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3):
                logger.info("Cannot download data from IPFS, cancelling processing")
                self.ipfs_timeout_cancel(order_id)
                return

            payload_file = f'{os.path.dirname(os.path.realpath(__file__))}/{payload_hash}'
            if input_hash is not None and len(input_hash) > 0:
                input_file = f'{os.path.dirname(os.path.realpath(__file__))}/{input_hash}'
                logger.info(f'input hash is not none: {input_file}')
            else:
                input_file = None

            docker_compose_file = f'{os.path.dirname(os.path.realpath(__file__))}/{docker_compose_hash}'
            challenge_file = f'{os.path.dirname(os.path.realpath(__file__))}/{challenge_hash}'
            challenge_content = self.read_file(challenge_file)
            self.build_prerequisites_v1(order_id, payload_file, input_file, docker_compose_file, challenge_content)

            logger.info("Stopping previous docker registry")
            run_subprocess(['docker', 'stop', 'registry'], logger)
            logger.info("Cleaning up docker registry")
            run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
            logger.info("Running new docker registry")
            logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry')

            logger.info("Stopping previous docker las")
            run_subprocess(['docker', 'stop', 'las'], logger)
            logger.info("Removing previous docker las")
            run_subprocess(['docker', 'rm', 'las'], logger)
            run_subprocess([
                'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
                os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry',
                'registry:2'
            ], logger)

            logger.info("Cleaning up docker container")
            run_subprocess(['docker', 'rm', '-f', 'etny-pynithy-' + str(order_id)], logger)

            logger.info("Running docker-compose")
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'run',
                '--rm', '-d',
                '--name',
                'etny-pynithy-' + str(order_id), 'etny-pynithy'
            ], logger)

            logger.info('waiting for result')
            self.wait_for_enclave(120)
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)
            logger.info('Uploading result to ipfs')
            result_hash = self.upload_result_to_ipfs(f'{self.order_folder}/result.txt')
            logger.info(f'Result ipfs hash is {result_hash}')
            logger.info('Reading transaction from file')
            transaction_hex = self.read_file(f'{self.order_folder}/transaction.txt')
            logger.info('Transaction content is: ', transaction_hex)

            logger.info('Building result ')
            result = self.build_result_format_v1(result_hash, transaction_hex)
            logger.info(f'Result is: {result}')
            logger.info('Adding result to order')
            self.add_result_to_order(order_id, result)

        if version == 2:
            try:
                logger.info(f"Downloading IPFS Image: {enclave_image_hash}")
                logger.info(f"Downloading IPFS docker yml file: {docker_compose_hash}")
                logger.info(f"Downloading IPFS Payload Hash: {metadata[2]}")
                logger.info(f"Downloading IPFS FileSet Hash: {metadata[3]}")
                logger.info(f"Downloading IPFS Challenge Hash: {challenge_hash}")
            except Exception as e:
                logger.info(str(e))

            payload_hash = metadata[2].split(':')[1]
            input_hash = metadata[3].split(':')[1]
            list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash, challenge_hash, payload_hash]
            if input_hash is not None and len(input_hash) > 0:
                list_of_ipfs_hashes.append(input_hash)

            if self.process_order_data['process_order_retry_counter'] < 2:
                logger.info("Downloading data from IPFS")
                self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3)
                if not self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3):
                    logger.info("Cannot download data from IPFS, cancelling processing")
                    self.ipfs_timeout_cancel(order_id)
                    return

            payload_file = f'{os.path.dirname(os.path.realpath(__file__))}/{payload_hash}'
            if input_hash is not None and len(input_hash) > 0:
                input_file = f'{os.path.dirname(os.path.realpath(__file__))}/{input_hash}'
                logger.info('input hash is not none: ', input_file)
            else:
                input_file = None

            logger.info("Running docker swift-stream")
            run_subprocess(
                ['docker-compose', '-f', f'docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
                logger)

            docker_compose_file = f'{os.path.dirname(os.path.realpath(__file__))}/{docker_compose_hash}'
            challenge_file = f'{os.path.dirname(os.path.realpath(__file__))}/{challenge_hash}'
            challenge_content = self.read_file(challenge_file)
            bucket_name = f'{enclave_image_name}-{v2}'
            logger.info('Preparing prerequisites for v2')
            self.build_prerequisites_v2(bucket_name, order_id, payload_file, input_file,
                                        docker_compose_file, challenge_content)

            logger.info("Stopping previous docker registry")
            run_subprocess(['docker', 'stop', 'registry'], logger)
            logger.info("Cleaning up docker registry")
            run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
            logger.info("Running new docker registry")
            logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry')

            logger.info("Stopping previous docker las")
            run_subprocess(['docker', 'stop', 'las'], logger)
            logger.info("Removing previous docker las")
            run_subprocess(['docker', 'rm', 'las'], logger)
            run_subprocess([
                'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
                os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry',
                'registry:2'
            ], logger)

            logger.info("Cleaning up docker container")
            run_subprocess(['docker', 'rm', '-f', f'{enclave_image_name}-' + str(order_id)], logger)

            logger.info("Running docker-compose")
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'run',
                '--rm', '-d',
                '--name',
                f'{enclave_image_name}-' + str(order_id), enclave_image_name
            ], logger)

            logger.info('Waiting for execution of v2')
            self.wait_for_enclave_v2(bucket_name, 'result.txt', 120)
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)
            logger.info(f'Uploading result to {enclave_image_name}-{v2} bucket')
            status, result_data = self.swift_stream_service.get_file_content(bucket_name, "result.txt")
            if not status:
                logger.info(result_data)

            with open(f'{self.order_folder}/result.txt', 'w') as f:
                f.write(result_data)
            logger.info(f'[2] Result file successfully downloaded to {self.order_folder}/result.txt')
            result_hash = self.upload_result_to_ipfs(f'{self.order_folder}/result.txt')
            logger.info(f'[v2] Result file successfully uploaded to IPFS with hash: {result_hash}')
            logger.info(f'Result file successfully uploaded to {enclave_image_name}-{v2} bucket')
            logger.info('Reading transaction from file')
            status, transaction_data = self.swift_stream_service.get_file_content(bucket_name, "transaction.txt")
            if not status:
                logger.info(transaction_data)
            logger.info('Building result for v2')
            result = self.build_result_format_v2(result_hash, transaction_data)
            logger.info(f'Result is: {result}')
            logger.info('Adding result to order')
            self.add_result_to_order(order_id, result)

            logger.info('Cleaning up swift-stream docker container.')
            run_subprocess([
                'docker-compose', '-f', f'docker/docker-compose-swift-stream.yml', 'down', 'swift-stream'
            ], logger)

        if version == 3:
            try:
                logger.info(f"Downloading IPFS Image: {enclave_image_hash}")
                logger.info(f"Downloading IPFS docker yml file: {docker_compose_hash}")
                logger.info(f"Downloading IPFS Payload Hash: {metadata[2]}")
                logger.info(f"Downloading IPFS FileSet Hash: {metadata[3]}")
                logger.info(f"Downloading IPFS Challenge Hash: {challenge_hash}")
            except Exception as e:
                logger.info(str(e))

            payload_hash = metadata[2].split(':')[1]
            input_hash = metadata[3].split(':')[1]
            list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash, challenge_hash, payload_hash]
            if input_hash is not None and len(input_hash) > 0:
                list_of_ipfs_hashes.append(input_hash)

            if self.process_order_data['process_order_retry_counter'] < 2:
                logger.info("Downloading data from IPFS")
                # self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3)
                if not self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3):
                    logger.info("Cannot download data from IPFS, cancelling processing")
                    self.ipfs_timeout_cancel(order_id)
                    return

            payload_file = f'{os.path.dirname(os.path.realpath(__file__))}/{payload_hash}'
            if input_hash is not None and len(input_hash) > 0:
                input_file = f'{os.path.dirname(os.path.realpath(__file__))}/{input_hash}'
                logger.info('input hash is not none: ', input_file)
            else:
                input_file = None

            logger.info("Running docker swift-stream")
            run_subprocess(
                ['docker-compose', '-f', f'docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
                logger)

            docker_compose_file = f'{os.path.dirname(os.path.realpath(__file__))}/{docker_compose_hash}'
            challenge_file = f'{os.path.dirname(os.path.realpath(__file__))}/{challenge_hash}'
            challenge_content = self.read_file(challenge_file)
            bucket_name = f'{enclave_image_name}-{v3}'
            logger.info(f'Preparing prerequisites for {v3}')
            self.build_prerequisites_v3(bucket_name, order_id, payload_file, input_file,
                                        docker_compose_file, challenge_content)

            logger.info("Stopping previous docker registry")
            run_subprocess(['docker', 'stop', 'registry'], logger)
            logger.info("Cleaning up docker registry")
            run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
            logger.info("Running new docker registry")
            logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry')

            logger.info("Stopping previous docker las")
            run_subprocess(['docker', 'stop', 'las'], logger)
            logger.info("Removing previous docker las")
            run_subprocess(['docker', 'rm', 'las'], logger)
            run_subprocess([
                'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
                os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry',
                'registry:2'
            ], logger)

            logger.info("Cleaning up docker container")
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down', '-d'
            ], logger)

            logger.info("Started enclaves by running ETNY docker-compose")
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'up', '-d'
            ], logger)

            logger.info('Waiting for execution of v3 enclave')
            self.wait_for_enclave_v2(bucket_name, 'result.txt', 120)
            logger.info(f'Uploading result to {enclave_image_name}-{v3} bucket')
            status, result_data = self.swift_stream_service.get_file_content(bucket_name, "result.txt")
            if not status:
                logger.info(result_data)

            with open(f'{self.order_folder}/result.txt', 'w') as f:
                f.write(result_data)
            logger.info(f'[v3] Result file successfully downloaded to {self.order_folder}/result.txt')
            result_hash = self.upload_result_to_ipfs(f'{self.order_folder}/result.txt')
            logger.info(f'[v3] Result file successfully uploaded to IPFS with hash: {result_hash}')
            logger.info(f'Result file successfully uploaded to {enclave_image_name}-{v3} bucket')
            logger.info('Reading transaction from file')
            status, transaction_data = self.swift_stream_service.get_file_content(bucket_name, "transaction.txt")
            if not status:
                logger.info(transaction_data)
            logger.info('Building result for v3')
            result = self.build_result_format_v3(result_hash, transaction_data)
            logger.info(f'Result is: {result}')
            logger.info('Adding result to order')
            self.add_result_to_order(order_id, result)

            logger.info('Cleaning up SecureLock and TrustedZone containers.')
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)
            logger.info('Cleaning up swift-stream docker container.')
            run_subprocess([
                'docker-compose', '-f', f'docker/docker-compose-swift-stream.yml', 'down', 'swift-stream'
            ], logger)

    def wait_for_enclave(self, timeout=120):
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
        i = 0
        logger.info(f'Checking if object {object_name} exists in bucket {bucket_name}')
        while True:
            time.sleep(1)
            i = i + 1
            if i > timeout:
                break
            (status, result) = self.swift_stream_service.is_object_in_bucket(bucket_name, object_name)
            if status:
                break

        logger.info('enclave finished the execution')

    def build_result_format_v1(self, result_hash, transaction_hex):
        return f'v1:{transaction_hex}:{result_hash}'

    def build_result_format_v2(self, result_hash, transaction_hex):
        return f'v2:{transaction_hex}:{result_hash}'

    def build_result_format_v3(self, result_hash, transaction_hex):
        return f'v3:{transaction_hex}:{result_hash}'

    def add_result_to_order(self, order_id, result):
        logger.info('Adding result to order', order_id, result)
        _nonce = self.__w3.eth.getTransactionCount(self.__address)
        unicorn_txn = self.__etny.functions._addResultToOrder(
            order_id, result
        ).buildTransaction({
            'chainId': config.chain_id,
            'gas': config.gas_limit,
            'nonce': _nonce,
            'gasPrice': self.__w3.toWei(config.gas_price_value, config.gas_price_measure),
        })

        try:
            _hash = self.send_transaction(unicorn_txn)
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(f"Error while adding result to Order, Error Message:{ex}")
            raise

        logger.info(f"Added result to the order!")
        logger.info(f"TX Hash: {_hash}")

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
        try:
            open(file_path, 'w').close()
        except OSError:
            logger.error('Failed creating the file')
            return False

        logger.info('File created')
        return True

    def build_prerequisites_v1(self, order_id, payload_file, input_file, docker_compose_file, challenge):
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
        logger.info('Cleaning up swift-stream bucket.')
        self.swift_stream_service.delete_bucket(bucket_name)
        logger.info('Creating new bucket.')
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
        logger.info('Cleaning up swift-stream bucket.')
        self.swift_stream_service.delete_bucket(bucket_name)
        logger.info('Creating new bucket.')
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
        if os.path.isfile(source):
            shutil.copy(source, dest)
        else:
            logger.info('The copied path is not a file')

    def generate_enclave_env_file(self, env_file, env_dictionary):
        with open(env_file, 'w') as f:
            for key, value in env_dictionary.items():
                f.write(f'{key}={value}\n')
        f.close()

    def get_enclave_env_dictionary(self, order_id, challenge):
        env_vars = {
            "ETNY_CHAIN_ID": config.chain_id,
            "ETNY_SMART_CONTRACT_ADDRESS": config.contract_address,
            "ETNY_WEB3_PROVIDER": config.http_provider,
            "ETNY_CLIENT_CHALLENGE": challenge,
            "ETNY_ORDER_ID": order_id
        }
        return env_vars

    def update_enclave_docker_compose(self, docker_compose_file, order):
        with open(docker_compose_file, 'r') as f:
            contents = f.read()

        contents = contents.replace('[ETNY_ORDER_ID]', str(order))
        with open(docker_compose_file, 'w') as f:
            f.write(contents)

    def _getOrder(self):
        order_id = self.find_order_by_dp_req()
        if order_id is not None:
            order = Order(self.__etny.caller()._getOrder(order_id))
            return [order_id, order]
        return None

    def __can_place_order(self, dp_req_id: int, do_req_id: int) -> bool:
        if config.network == 'TESTNET':
            dispersion_factor = 1
        else:
            dispersion_factor = 40
        if dp_req_id % dispersion_factor != do_req_id % dispersion_factor:
            return False
        return True

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
        timeout_in_seconds = 10
        while seconds < config.contract_call_frequency:
            try:
                count = self.__etny.caller()._getDORequestsCount()
            except Exception as e:
                logger.error(f"Error while trying to get DORequestCount, errorMessage: {e}")
                time.sleep(timeout_in_seconds)
                seconds += timeout_in_seconds
                continue

            found = False
            cached_do_requests = self.doreq_cache.get_values
            for i in reversed(list(set(range(checked, count)) - set(cached_do_requests))):
                _doreq = self.__etny.caller()._getDORequest(i)
                doreq = DORequest(_doreq)
                self.doreq_cache.add(i)

                if not self.can_run_under_sgx:
                    logger.error('SGX is not enabled or correctly configured, skipping DO request')
                    continue

                if doreq.status != RequestStatus.AVAILABLE:
                    logger.debug(
                        f'''Skipping Order, DORequestId = {_doreq}, DPRequestId = {i}, Order has different status: '{RequestStatus._status_as_string(doreq.status)}' ''')
                    continue

                logger.info(f"Checking DO request: {i}")
                if not (doreq.cpu <= req.cpu and doreq.memory <= req.memory and
                        doreq.storage <= req.storage and doreq.bandwidth <= req.bandwidth and doreq.price >= req.price):
                    logger.info("Order doesn't meet requirements, skipping to next request")
                    continue

                metadata = self.__etny.caller()._getDORequestMetadata(i)

                if metadata[4] != '' and metadata[4] != self.__address:
                    logger.info(f'Skipping DO Request: {i}. Request is delegated to a different Node.')
                    continue

                if metadata[4] == '':
                    status = self.__can_place_order(self.__dprequest, i)
                    if not status:
                        continue

                if self._check_installed_drivers():
                    logger.error('SGX configuration error. Both isgx drivers are installed. Skipping order placing ...')
                    continue

                logger.info("Placing order...")
                try:
                    self.place_order(i)

                    # store merged log
                    self.merged_orders_cache.add(do_req_id=i, dp_req_id=self.__dprequest, order_id=self.__order_id)

                except (exceptions.SolidityError, IndexError) as error:
                    logger.info(f"Order already created, skipping to next DO request")
                    continue
                found = True
                logger.info(f"Waiting for order {self.__order_id} approval...")
                if retry(self.wait_for_order_approval, attempts=20, delay=2)[0] is False:
                    logger.info("Order was not approved in the last ~20 blocks, skipping to next request")
                    break
                # performance improvement, to avoid duplication
                # self.process_order(self.__order_id, metadata=metadata)
                self.process_order(self.__order_id)
                logger.info(
                    f"Order {self.__order_id}, with DO request {i} and DP request {self.__dprequest} processed successfully")
                break
            if found:
                logger.info(f"Finished processing order {self.__order_id}")
                return
            checked = count - 1
            time.sleep(timeout_in_seconds)

            seconds += timeout_in_seconds
            if seconds >= config.contract_call_frequency:
                logger.info("DP request timed out!")
                self.cancel_dp_request(self.__dprequest)
                break

        logger.info("DP request timed out!")

        # self.cancel_dp_request(self.__dprequest)

    def add_processor_to_order(self, order_id):
        unicorn_txn = self.__etny.functions._addProcessorToOrder(order_id, self.__resultaddress).buildTransaction(
            self.get_transaction_build())

        try:
            _hash = self.send_transaction(unicorn_txn)
            self.__w3.eth.waitForTransactionReceipt(_hash)
        except Exception as ex:
            logger.error(f"Error while adding Processor to Order, Error Message:{ex}")
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
        cached_order_ids = self.orders_cache.get_values
        for _order_id in reversed(list(set(my_orders) - set(cached_order_ids))):
            _order = self.__etny.caller()._getOrder(_order_id)
            order = Order(_order)
            self.orders_cache.add(order.dp_req, _order_id)
            if order.dp_req == self.__dprequest:
                return _order_id
        logger.info(f"Couldn't find order with DP request {self.__dprequest}")
        return None

    def place_order(self, doreq):
        unicorn_txn = self.__etny.functions._placeOrder(
            int(doreq),
            int(self.__dprequest),
        ).buildTransaction(self.get_transaction_build())
        order_id = 0
        try:
            _hash = self.send_transaction(unicorn_txn)
            receipt = self.__w3.eth.waitForTransactionReceipt(_hash)
            processed_logs = self.__etny.events._placeOrderEV().processReceipt(receipt)
            self.__order_id = processed_logs[0].args._orderNumber
            order_id = self.__order_id
        except Exception as e:
            errorMessage = 'Already Taken by other Node' if type(e) == IndexError else str(e)
            logger.error(
                f'''Failed to place Order: {order_id}, DORequest_id: {doreq}, DPRequest_id: {self.__dprequest}, Error 
                Message: {errorMessage}''')
            raise

        logger.info("Order placed successfully!")
        logger.info(f"TX Hash: {_hash}")

    def get_transaction_build(self, existing_nonce=None):
        self.__nonce = existing_nonce if existing_nonce else self.__w3.eth.getTransactionCount(self.__address)

        return {
            'chainId': config.chain_id,
            'gas': config.gas_limit,
            'nonce': self.__nonce,
            'gasPrice': self.__w3.toWei(config.gas_price_value, config.gas_price_measure),
        }

    def send_transaction(self, unicorn_txn):
        try:
            signed_txn = self.__w3.eth.account.sign_transaction(unicorn_txn, private_key=self.__acct.key)
            self.__w3.eth.sendRawTransaction(signed_txn.rawTransaction)
            _hash = self.__w3.toHex(self.__w3.sha3(signed_txn.rawTransaction))
            return _hash
        except Exception as e:
            logger.error(f"Error sending Transaction, Error Message: {e}")
            raise

    def resume_processing(self):
        while True:
            self.__enforce_update()
            self.__call_heart_beat()
            self.add_dp_request()
            self.process_dp_request()

    def _check_installed_drivers(self):
        driver_list = os.listdir('/dev')
        return 'isgx' in driver_list and 'sgx_enclave' in driver_list

    def get_env_for_integration_test(self):
        env_vars = {
            "ETNY_CHAIN_ID": config.chain_id,
            "ETNY_SMART_CONTRACT_ADDRESS": config.contract_address,
            "ETNY_WEB3_PROVIDER": config.http_provider,
            "ETNY_RUN_INTEGRATION_TEST": 1,
            "ETNY_ORDER_ID": 0
        }
        return env_vars

    def build_prerequisites_integration_test(self, bucket_name, order_id, docker_compose_file):
        logger.info('Cleaning up swift-stream bucket.')
        self.swift_stream_service.delete_bucket(bucket_name)
        logger.info('Creating new bucket.')
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

    def __clean_up_integration_test(self):
        logger.info('Cleaning up containers after integration test.')
        run_subprocess([
            'docker-compose', '-f', self.order_docker_compose_file, 'down'
        ], logger)
        logger.info('Cleaning up swift-stream docker container.')
        run_subprocess([
            'docker-compose', '-f', f'docker/docker-compose-swift-stream.yml', 'down', 'swift-stream'
        ], logger)
        logger.info('Cleaning up swift-stream integration bucket.')
        self.swift_stream_service.delete_bucket(self.integration_bucket_name)

    def __run_integration_test(self):
        logger.info('Running integration test.')
        [enclave_image_hash, _,
         docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey('etny-pynithy',
                                                                                                      'v3')
        self.integration_bucket_name = 'etny-bucket-integration'
        order_id = 'integration_test'
        integration_test_file = 'context_test.etny'

        try:
            logger.info(f"Downloading IPFS Image: {enclave_image_hash}")
            logger.info(f"Downloading IPFS docker yml file: {docker_compose_hash}")
        except Exception as e:
            logger.info(str(e))

        list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash]
        if not self.storage.download_many(list_of_ipfs_hashes, attempts=10, delay=3):
            logger.info("Cannot download data from IPFS, stopping test")
            return

        logger.info("Running docker swift-stream")
        run_subprocess(
            ['docker-compose', '-f', f'docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
            logger)

        docker_compose_file = f'{os.path.dirname(os.path.realpath(__file__))}/{docker_compose_hash}'
        logger.info(f'Preparing prerequisites for integration test')
        self.build_prerequisites_integration_test(self.integration_bucket_name, order_id, docker_compose_file)

        logger.info("Stopping previous docker registry and containets")
        run_subprocess(['docker', 'stop', 'registry'], logger)
        run_subprocess(['docker', 'stop', 'etny-securelock'], logger)
        run_subprocess(['docker', 'stop', 'etny-trustzone'], logger)
        run_subprocess(['docker', 'stop', 'las'], logger)
        logger.info("Cleaning up docker registry")
        run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
        logger.info("Running new docker registry")
        logger.debug(os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry')

        logger.info("Stopping previous docker las")
        run_subprocess(['docker', 'stop', 'las'], logger)
        logger.info("Removing previous docker las")
        run_subprocess(['docker', 'rm', 'las'], logger)
        run_subprocess([
            'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
            os.path.dirname(os.path.realpath(__file__)) + '/' + enclave_image_hash + ':/var/lib/registry',
            'registry:2'
        ], logger)

        logger.info("Started enclaves by running ETNY docker-compose")
        run_subprocess([
            'docker-compose', '-f', self.order_docker_compose_file, 'up', '-d'
        ], logger)

        logger.info('Waiting for execution of integration test enclave')
        self.wait_for_enclave_v2(self.integration_bucket_name, integration_test_file, 120)
        status, result_data = self.swift_stream_service.get_file_content(self.integration_bucket_name,
                                                                         integration_test_file)
        if not status:
            logger.info('could not download the integration test result file')
            logger.error('The node is not properly running under SGX. Please check the configuration.')
            self.can_run_under_sgx = False
            self.__clean_up_integration_test()
            return

        self.can_run_under_sgx = True
        logger.info('Integration test result file successfully downloaded', result_data)
        logger.info('Node is properly configured to run confidential tasks using SGX')
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
                with open(file_path, 'w') as file:
                    file.write(str(current_timestamp))
                return True
            else:
                return False
        else:
            with open(file_path, 'w') as file:
                file.write(str(current_timestamp))

            return True

    def __enforce_update(self):
        logger.info('Checking if the auto update can be performed...')
        if self.__can_run_auto_update(config.auto_update_file_path, 24 * 60 * 60):
            logger.info('Exiting the agent. Performing auto update...')
            exit(1)

    def __call_heart_beat(self):
        logger.info('Checking if heart call is necessary...')

        if config.network == 'TESTNET':
            heartbeat_frequency = 1 * 60 * 60 - 60;
        else
            heartbeat_frequency = 12 * 60 * 60 - 60;

        if self.__can_run_auto_update(config.heart_beat_log_file_path, heartbeat_frequency):
            logger.info('Heart beat can be called...')
            params = [
                "v3"
            ]
            unicorn_txn = self.__heart_beat.functions.logCall(*params).buildTransaction(self.get_transaction_build())
            _hash = ''

            try:
                _hash = self.send_transaction(unicorn_txn)
                receipt = self.__w3.eth.waitForTransactionReceipt(_hash)
                if receipt.status == 1:
                    logger.info('Heart beat successfully called...')
            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')
                raise

        logger.info('Heart beat called already within last %s seconds...', heartbeat_frequency)

class SGXDriver:
    def __init__(self):
        try:
            subprocess.call(['bash','../ubuntu/etny-node-isgx-removal-tool.sh'])
        except Exception as e:
            pass

if __name__ == '__main__':
    try:
        sgx = SGXDriver()
        app = EtnyPoXNode()
        logger.info("Cleaning up previous DP requests...")
        app.cleanup_dp_requests()
        logger.info("[DONE]")
        app.resume_processing()
    except Exception as e:
        logger.error(e)
        raise
