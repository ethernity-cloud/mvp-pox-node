from asyncio.log import logger
import json
import os
import socket
import subprocess
import time
import uuid
import math
import urllib.request
from collections import OrderedDict

import ipfshttpclient
import psutil


def get_or_generate_uuid(filename):
    if os.path.exists(filename):
        with open(filename) as f:
            return f.read()

    _uuid = uuid.uuid4().hex
    os.makedirs(os.path.dirname(filename))
    with open(filename, "w+") as f:
        f.write(_uuid)
    return _uuid


def run_subprocess(args, logger):
    out = subprocess.Popen(args,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
    stdout, stderr = out.communicate()
    for item in [stdout, stderr]:
        if item:
            logger.debug(item.decode())


def retry(func, *func_args, attempts, delay=0, callback=None):
    for _ in range(attempts):
        try:
            if callback != None:
                callback(_)
        except Exception as e:
            print('error = ', e)

        try:
            resp = func(*func_args)
            return True, resp
        except:
            time.sleep(delay)
    return False, None

def get_node_geo():
    try:
        request = urllib.request.urlopen('https://ipinfo.io/json')
        data = json.loads(request.read().decode(request.info().get_param('charset') or 'utf-8'))
        location = data.get('loc')
        if (location):
            return location
        else:
            raise Exception('Location not found in JSON object')
    except Exception as e:
        print('error = ', e)
        return ''


class Storage:
    def __init__(self, ipfs_host, client_connect_url, client_bootstrap_url, cache, logger):
        self.client_bootstrap_url = client_bootstrap_url
        self.ipfs_host = ipfs_host
        ipfs_node = socket.gethostbyname(ipfs_host)
        self.bootstrap_client = ipfshttpclient.connect(client_connect_url)
        self.bootstrap_client.bootstrap.add(client_bootstrap_url % ipfs_node)
        self.bootstrap_client.config.set("Datastore.StorageMax", "3GB")
        args = ("Swarm.ConnMgr.LowWater", 25)
        opts = {'json': 'true'}
        self.bootstrap_client._client.request('/config', args, opts=opts, decoder='json')
        args = ("Swarm.ConnMgr.HighWater", 50)
        opts = {'json': 'true'}
        self.bootstrap_client._client.request('/config', args, opts=opts, decoder='json')
        self.logger = logger
        self.cache = cache

    def download(self, data):
        if self.cache.contains(data):
            return
        try:
            ipfs_node = socket.gethostbyname(self.ipfs_host)
            address = (self.client_bootstrap_url % ipfs_node)
            args = (address, address)
            opts = {'json': 'true'}
            self.bootstrap_client._client.request('/swarm/connect', args, opts=opts, decoder='json')
            self.bootstrap_client.bootstrap.add(self.client_bootstrap_url % ipfs_node)
            self.bootstrap_client.get(data, compress=True, opts={"compression-level": 9}, timeout=120)
            self.cache.add(data)
        except Exception as e:
            self.logger.info(f'error while downloading file {data}', e)
            self.logger.error(e)
            raise

    def download_many(self, lst, attempts=1, delay=0):
        for data in lst:
            self.logger.info(f'Downloading {data}')
            if retry(self.download, data, attempts=attempts, delay=delay)[0] is False:
                return False
        return True

    def upload(self, data):
        try:
            ipfs_node = socket.gethostbyname(self.ipfs_host)
            self.bootstrap_client.bootstrap.add(self.client_bootstrap_url % ipfs_node)
            response = self.bootstrap_client.add(data, timeout=120)
            self.logger.info(f'Uploaded response: {response}')
            self.logger.info('Hash: ', response['Hash'])
            return response['Hash']
        except Exception as e:
            self.logger.info(f'error while uploading')
            self.logger.error(e)
            raise

    def add(self, data):
        pass


class Cache:
    def __init__(self, items_limit, filepath, store_type=OrderedDict):
        self.items_limit = items_limit
        self.filepath = filepath
        try:
            if not os.path.exists(filepath):
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                raise Exception
            with open(filepath, 'r') as f:
                self.mem = store_type(json.load(f))
        except Exception as e:
            self.mem = store_type({})
            self._update_file()

    def _update_file(self):
        with open(self.filepath, 'w') as f:
            try:
                json.dump(self.mem, f)
            except TypeError:
                json.dump(list(self.mem), f)

    def add(self, key, value):
        self.mem[key] = value
        if len(self.mem) == self.items_limit + 1:
            self.mem.popitem(last=False)
        self._update_file()

    def get(self, key):
        return self.mem.get(key)

    def wipe(self):
        self.mem = None
        self._update_file()

    @property
    def get_values(self):
        return self.mem.values()


class ListCache(Cache):
    def __init__(self, items_limit, filepath, store_type=set):
        super().__init__(items_limit, filepath, store_type)

    def add(self, value):
        if value not in self.mem:
            self.mem.add(value)
            self._update_file()

    def get(self, value):
        return value if self.contains(value) else None

    def contains(self, value):
        return value in self.mem

    @property
    def get_values(self):
        return list(map(lambda x: int(x), self.mem))


class MergedOrdersCache(Cache):
    def __init__(self, items_limit, filepath, store_type=list):
        super().__init__(items_limit, filepath, store_type)

    def add(self, do_req_id, dp_req_id, order_id):
        self.mem.append(dict(do=do_req_id, dp=dp_req_id, order=order_id))
        self._update_file()


class HardwareInfoProvider:
    @staticmethod
    def get_number_of_cpus():
        return psutil.cpu_count()

    @staticmethod
    def get_free_memory():
        return math.ceil(psutil.virtual_memory()[1] / (2 ** 30))  # in GB

    @staticmethod
    def get_free_storage():
        return psutil.disk_usage("/")[2] // (2 ** 30)  # in GB
