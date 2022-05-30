import os
import uuid
import subprocess
import socket
import ipfshttpclient
import json

from . import config

logger = config.logger


def get_or_generate_uuid(filename):
    if os.path.exists(filename):
        with open(filename) as f:
            return f.read()

    _uuid = uuid.uuid4().hex
    os.makedirs(os.path.dirname(filename))
    with open(filename, "w+") as f:
        f.write(_uuid)
    return _uuid


def run_subprocess(args):
    out = subprocess.Popen(args,
                           stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT)
    stdout, stderr = out.communicate()
    logger.debug(stdout)
    logger.debug(stderr)


def download_ipfs(hashvalue):
    ipfsnode = socket.gethostbyname(config.ipfs_host)
    client = ipfshttpclient.connect(config.client_connect_url)
    client.bootstrap.add(config.client_bootstrap_url % ipfsnode)
    # client.swarm.connect('/ip4/%s/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5' % ipfsnode)
    # bug tracked under https://github.com/ipfs-shipyard/py-ipfs-http-client/issues/246

    client.get(hashvalue)

    return None


class Cache:
    def __init__(self, filepath):
        if not os.path.exists(filepath):
            os.makedirs(os.path.dirname(filepath))
            with open(filepath, 'w+') as f:
                f.write("{}")
        self.filepath = filepath
        with open(filepath, 'r') as f:
            self.mem = json.load(f)

    def add(self, key, value):
        self.mem[key] = value
        with open(self.filepath, 'w') as f:
            json.dump(self.mem, f)

    def get(self, key):
        if key in self.mem:
            return self.mem[key]
        return None

    def get_values(self):
        return self.mem.values()
