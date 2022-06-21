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
    logger.debug(stdout)
    logger.debug(stderr)


def retry(func, *func_args, attempts, delay=0):
    for _ in range(attempts):
        try:
            resp = func(*func_args)
            return True, resp
        except:
            time.sleep(delay)
    return False, None


class Storage:
    def __init__(self, ipfs_host, client_connect_url, client_bootstrap_url, logger):
        ipfs_node = socket.gethostbyname(ipfs_host)
        self.client = ipfshttpclient.connect(client_connect_url)
        self.client.bootstrap.add(client_bootstrap_url % ipfs_node)
        self.logger = logger

    def download(self, data):
        try:
            self.client.get(data)
        except Exception as e:
            self.logger.error(e)
            return False
        return True

    def download_many(self, lst):
        for data in lst:
            self.download(data)


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

    def get(self, key):
        if key in self.mem:
            return self.mem[key]
        return None

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
