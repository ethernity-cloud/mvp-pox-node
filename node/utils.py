import json
import os
import socket
import subprocess
import time
import uuid
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
    logger.info(" ".join(args))
    logger.info('-' * 10)
    for item in [stdout, stderr]:
        if item:
            logger.info(item.decode())
            logger.debug(item.decode())
            


def retry(func, *func_args, attempts, delay=0, callback = None):
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


class Storage:
    def __init__(self, ipfs_host, client_connect_url, client_bootstrap_url, cache, logger):
        ipfs_node = socket.gethostbyname(ipfs_host)
        self.bootstrap_client = ipfshttpclient.connect(client_connect_url)
        self.bootstrap_client.bootstrap.add(client_bootstrap_url % ipfs_node)
        self.infura_client = ipfshttpclient.connect('/dns/ipfs.infura.io/tcp/5001/https')
        self.logger = logger
        self.cache = cache

    def download(self, data, from_bootstrap=False):
        if self.cache.contains(data):
            return
        try:
            if from_bootstrap:
                self.bootstrap_client.get(data, compress=True, opts={"compression-level": 9}, timeout=120)
            else:
                self.infura_client.get(data, compress=True, opts={"compression-level": 9}, timeout=120)
            self.cache.add(data, 1)
        except Exception as e:
            self.logger.error(e)
            raise

    def download_many(self, lst, from_bootstrap=False):
        for data in lst:
            if retry(self.download, data, from_bootstrap, attempts=1, delay=0)[0] is False:
                return False
        return True

    def add(self, data):
        return self.infura_client.add_str(data)


class Cache:
    def __init__(self, items_limit, filepath):
        self.items_limit = items_limit
        self.filepath = filepath
        if not os.path.exists(filepath):
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            self.mem = OrderedDict({})
            self._update_file()
            return
        with open(filepath, 'r') as f:
            self.mem = OrderedDict(json.load(f))

    def _update_file(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.mem, f)

    def add(self, key, value):
        self.mem[key] = value
        if len(self.mem) == self.items_limit + 1:
            self.mem.popitem(last=False)
        self._update_file()

    def contains(self, key):
        return key in self.mem

    def get(self, key):
        if not self.contains(key):
            return None
        return self.mem[key]

    def get_values(self):
        return self.mem.values()


class HardwareInfoProvider:
    @staticmethod
    def get_number_of_cpus():
        return psutil.cpu_count()

    @staticmethod
    def get_free_memory():
        return psutil.virtual_memory()[1] // (2**30)  # in GB

    @staticmethod
    def get_free_storage():
        return psutil.disk_usage("/")[2] // (2**30)  # in GB
