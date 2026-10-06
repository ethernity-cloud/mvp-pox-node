"""
Utils.py - Utility functions for the Ethernity CLOUD Agent.

This module provides essential utilities for IPFS interactions, caching mechanisms, hardware information retrieval, and Ethereum transaction parsing. It is d
esigned to support the EtnyPoXNode class in managing IPFS connections, downloading and uploading content, handling cache for performance and persistence, and
 ensuring thread-safe operations for multi-network setups.

Key Components:
- Storage Class: Manages IPFS operations, including connection, version checking/upgrading, downloading (with file/directory handling), uploading, pinning, a
nd garbage collection. It ensures robust handling of files and directories, with special logic for tar archives and gzip compression during downloads.
- Cache Classes: Provide in-memory and file-backed caching with limits, including list-based and timestamped variants for efficient storage and retrieval of
hashes or values.
- HardwareInfoProvider: Retrieves system hardware details like CPU count, free memory, and storage.
- Transaction Parsing: Decodes Ethereum transaction bytes for contract interactions.
- Thread Safety: Uses locks for IPFS upgrades and version cache accesses to handle concurrent network threads safely.

This code hhandles IPFS upgrades with cache wipes across networks, download errors (e.g., directories as tars, gzip detection), and cleanup for conflicting p
aths. It ensures plain files are downloaded without compression where possible, extracts tars for directories or wrapped files, skips PaxHeaders, and wipes c
aches/files during upgrades without unpinning (as upgrade handles it).
"""

from asyncio.log import logger
import base64
import hashlib
import json
import os
import io
import shutil
import socket
import subprocess
import uuid
import math
import urllib.request
import time
import re
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from collections import OrderedDict
from collections import deque
import psutil
import tarfile
import gzip

import threading
upgrade_lock = threading.Lock()
global_version_lock = threading.Lock()
# Serializes `systemctl restart ipfs` across network threads. The list holds
# the last restart time so it can be mutated under the lock without a global
# declaration at each use.
_ipfs_restart_lock = threading.Lock()
_ipfs_last_restart = [0.0]
IPFS_RESTART_COOLDOWN = 60
# Bookkeeping of the local daemon's self-heal, shared by every Storage instance
# in the process (one daemon): the last redial and the current cooldown per
# swarm peer. A peer is redialed at most once per its cooldown, which doubles
# on each failed redial up to IPFS_HEAL_MAX_COOLDOWN and resets on success.
# The lock is held only while these stamps are read and written, never across
# an API call.
_ipfs_heal_lock = threading.Lock()
_ipfs_last_redial = {}
_ipfs_redial_cooldown = {}
IPFS_HEAL_COOLDOWN = 120
IPFS_HEAL_MAX_COOLDOWN = 1800
# Serializes the read-compare-write of Datastore.StorageMax across the Storage
# instances of the process; held across the two bounded API calls it covers.
_ipfs_storage_lock = threading.Lock()

def looks_like_cid(value):
    """True for the text forms of an IPFS CID: CIDv0 is 46 characters starting
    "Qm"; CIDv1 is 'b' followed by at least 58 characters of the lowercase
    base32 alphabet (a-z and 2-7). An empty value, a 0x hex digest or the repr
    of a bytes value is not a CID."""
    cid = value.strip() if isinstance(value, str) else ""
    if cid.startswith("Qm") and len(cid) == 46:
        return True
    return (cid.startswith("b") and len(cid) >= 59
            and set(cid[1:]) <= set("abcdefghijklmnopqrstuvwxyz234567"))


_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _varint(raw, i):
    """The unsigned varint at raw[i] and the index after it, read as go-varint
    reads one: at most 9 bytes, and minimally encoded."""
    value = shift = 0
    for n in range(9):
        if i >= len(raw):
            break
        byte = raw[i]
        i += 1
        if byte < 0x80:
            if byte == 0 and shift > 0:
                raise ValueError("not a minimal varint")
            return value | byte << shift, i
        if n == 8:
            break
        value |= (byte & 0x7F) << shift
        shift += 7
    raise ValueError("not a varint")


def canonical_cid(value):
    """True when `value` is a CID in the one text form Kubo prints for it: a
    CIDv0 whose base58btc text decodes to a 34-byte sha2-256 multihash, or a
    CIDv1 ('b' base32) that is well formed and re-encodes to the same text, so
    its padding bits are zero. pin/ls and files/stat name CIDs in that form.
    A 46-character base58 text starting "Qm" decodes to less than 2**269 and
    has no leading zero digit, so it is the only text of its 34 bytes."""
    if not isinstance(value, str):
        return False
    if value.startswith("Qm") and len(value) == 46:
        number = 0
        for c in value:
            digit = _BASE58.find(c)
            if digit < 0:
                return False
            number = number * 58 + digit
        return number.to_bytes(34, "big")[:2] == b"\x12\x20"
    if not looks_like_cid(value) or value != value.strip():
        return False
    body = value[1:]
    try:
        raw = base64.b32decode(body.upper() + "=" * (-len(body) % 8))
        version, i = _varint(raw, 0)
        _, i = _varint(raw, i)
        _, i = _varint(raw, i)
        length, i = _varint(raw, i)
    except ValueError:
        return False
    return (version == 1 and i + length == len(raw)
            and base64.b32encode(raw).decode().rstrip("=").lower() == body)


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

def retry(func, *func_args, attempts, delay=1, callback=None):
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

class PinAborted(Exception):
    """A pin/add its watcher ended, with the watcher's reason."""


class _ResponseStream:
    """A streamed Kubo API response read as a file: read(n) and close()."""

    def __init__(self, resp):
        self._resp = resp

    def read(self, size):
        return self._resp.raw.read(size)

    def close(self):
        self._resp.close()


class Storage:
    def __init__(self, ipfs_swarm, ipfs_timeout, client_connect_url, gateway_url, cache, ipfs_version_cache, logger, target, kubo_url, kubo_version, network_name):
        self.ipfs_swarm = ipfs_swarm
        self.ipfs_timeout = ipfs_timeout
        self.target = target
        self.client_connect_url = client_connect_url
        self.logger = logger
        self.cache = cache
        self.gateway = gateway_url.rstrip('/')
        self.kubo_url = kubo_url
        self.kubo_version = kubo_version
        self.ipfs_version_cache = ipfs_version_cache
        self.network_name = network_name
        # Validators' IPFS peers taken from chain: {peer id: [multiaddrs]}.
        # Kept apart from IPFS_SWARM so a validator leaving the set is removed
        # from the peering list without touching the configured entries.
        self._chain_peers = {}
        # Blocks this instance pinned recently, re-announced to the DHT by
        # reprovide_recent: {cid: {'pinned', 'announces', 'last'}}, under
        # _recent_lock.
        self._recent_pins = {}
        self._recent_lock = threading.Lock()

        self._setup_session_and_executor()
        logger.info("Initializing ipfs connection")
        self.api_base = self._parse_multiaddr(self.client_connect_url)

        self._check_and_upgrade_ipfs_version()
        self._detect_and_handle_version_change()
        self._connect_and_configure()

    def _setup_session_and_executor(self):
        """Set up the requests session and thread executor."""
        self.session = requests.Session()
        max_workers = 10
        adapter = HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
        self.session.mount("https://", adapter)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        # DHT announces run one at a time on their own worker: each is a full
        # DHT walk, and sharing the pin executor would let a slow DHT hold
        # result pins in the queue.
        self.announce_executor = ThreadPoolExecutor(max_workers=1)

    def _check_and_upgrade_ipfs_version(self):
        """Check IPFS version and upgrade if necessary."""
        with upgrade_lock:
            self.logger.info("Checking IPFS version")
            version = self._get_ipfs_version_with_retries()
            if version is None:
                self.logger.error("Failed to get IPFS version after 10 attempts.")
                self.connected = False
                version = "0.0.0"

            if self._is_version_outdated(version):
                self.logger.info(f"IPFS version {version} is less than {self.kubo_version}, switching to local IPFS setup...")
                self.client_connect_url = "/ip4/127.0.0.1/tcp/5001/http"
                run_subprocess(['systemctl', 'start', 'ipfs'], self.logger)
                self.api_base = self._parse_multiaddr(self.client_connect_url)

            # Second check after potential service start
            version = self._get_ipfs_version_with_retries()
            if version is None:
                self.logger.error("Failed to get IPFS version after 10 attempts. Proceeding with limited functionality.")
                self.connected = False
                version = "0.0.0"

            if self._is_version_outdated(version):
                self.logger.info(f"IPFS version {version} is less than {self.kubo_version}, upgrading kubo locally...")
                if "127.0.0.1" in self.client_connect_url:
                    self.logger.info("Setting up local IPFS with upgrade procedure")
                    try:
                        self._perform_ipfs_upgrade()
                        version = self._get_ipfs_version_with_retries()  # Re-query after upgrade
                        with global_version_lock:
                            self.ipfs_version_cache.add("GLOBAL_IPFS_VERSION", version)
                            self.ipfs_version_cache.add("UPDATED_NETWORKS", json.dumps([]))
                    except Exception as e:
                        self.logger.error(f"Failed to upgrade IPFS: {e}")
                        self.connected = False
                        return
                else:
                    self.logger.info(f"IPFS version {version} is compatible, proceeding with current configuration.")

    def _get_ipfs_version_with_retries(self, attempts=10, delay=1):
        """Retrieve IPFS version with retries."""
        version = None
        for _ in range(attempts):
            version = self.get_version()
            if version is not None:
                break
            time.sleep(delay)
        return version

    def _is_version_outdated(self, version):
        """Check if the current IPFS version is outdated compared to required."""
        v_parts = [int(x) for x in version.split('.')]
        required_parts = [int(x) for x in str(self.kubo_version).split('.')]
        max_len = max(len(v_parts), len(required_parts))
        v_parts.extend([0] * (max_len - len(v_parts)))
        required_parts.extend([0] * (max_len - len(required_parts)))
        v_tuple = tuple(v_parts)
        required_tuple = tuple(required_parts)
        return v_tuple < required_tuple

    def _perform_ipfs_upgrade(self):
        """Perform the actual IPFS upgrade steps."""
        run_subprocess(['systemctl', 'stop', 'ipfs'], self.logger)
        shutil.rmtree('/home/vagrant/etny/node/go-ipfs', ignore_errors=True)
        os.makedirs('/home/vagrant/etny/node/go-ipfs', exist_ok=True)
        shutil.rmtree(os.path.expanduser('~/.ipfs'), ignore_errors=True)

        for item in os.listdir(Path(__file__).parent):
            if item.startswith('Qm'):
                item_path = os.path.join(Path(__file__).parent, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path, ignore_errors=True)
                else:
                    os.remove(item_path)
                self.logger.debug(f"Deleted legacy local cache item: {item_path}")

        # Delete all local IPFS cache files (no age check, no unpinning)
        for item in os.listdir(self.target):
            if item.startswith('Qm'):
                item_path = os.path.join(self.target, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path, ignore_errors=True)
                else:
                    os.remove(item_path)
                self.logger.debug(f"Deleted local cache item: {item_path}")

        self.cache.wipe()  # Wipe for the upgrading instance
        tar_url = self.kubo_url
        tar_file = os.path.join('/tmp', os.path.basename(tar_url))
        urllib.request.urlretrieve(tar_url, tar_file)
        # Extract the tar file to a temporary directory
        temp_extract_path = '/tmp/kubo_extract'
        shutil.rmtree(temp_extract_path, ignore_errors=True)  # Clean up any existing temp directory
        os.makedirs(temp_extract_path, exist_ok=True)
        with tarfile.open(tar_file, 'r:gz') as tar:
            tar.extractall(path=temp_extract_path)
        # Move contents of the kubo directory to the target directory
        kubo_dir = os.path.join(temp_extract_path, 'kubo')
        if os.path.exists(kubo_dir):
            for item in os.listdir(kubo_dir):
                source = os.path.join(kubo_dir, item)
                destination = os.path.join('/home/vagrant/etny/node/go-ipfs', item)
                if os.path.isdir(source):
                    shutil.move(source, destination)
                else:
                    shutil.move(source, destination)
        # Clean up temporary files and directories
        shutil.rmtree(temp_extract_path, ignore_errors=True)
        os.remove(tar_file)
        run_subprocess(['systemctl', 'start', 'ipfs'], self.logger)
        time.sleep(10)  # Wait for the service to start
        self.logger.info("IPFS upgraded and started.")

    def _detect_and_handle_version_change(self):
        """Detect IPFS version changes and handle cache wipes."""
        version = self._get_ipfs_version_with_retries()
        if version is not None:
            with global_version_lock:
                self.ipfs_version_cache._reload_cache()  # Reload from disk for latest shared state
                global_version = self.ipfs_version_cache.get("GLOBAL_IPFS_VERSION")
                updated_nets_str = self.ipfs_version_cache.get("UPDATED_NETWORKS")
                updated_nets = json.loads(updated_nets_str) if updated_nets_str else []
                stored_version = self.ipfs_version_cache.get(f"IPFS_VERSION_{self.network_name}")

                self.logger.debug(f"[{self.network_name}] Version check: current={version}, global={global_version}, stored={stored_version}, in_updated_nets={self.network_name in updated_nets}")

                if global_version is None:
                    self.ipfs_version_cache.add("GLOBAL_IPFS_VERSION", version)
                    self.ipfs_version_cache.add("UPDATED_NETWORKS", json.dumps([self.network_name]))
                    self.ipfs_version_cache.add(f"IPFS_VERSION_{self.network_name}", version)
                else:
                    if version != global_version:
                        self.ipfs_version_cache.add("GLOBAL_IPFS_VERSION", version)
                        self.ipfs_version_cache.add("UPDATED_NETWORKS", json.dumps([self.network_name]))
                        self.logger.info(f"Detected IPFS version change from {global_version} to {version}, deleting local files and wiping cache for {self.network_name}")
                        for item in os.listdir(self.target):
                            if item.startswith('Qm'):
                                item_path = os.path.join(self.target, item)
                                if os.path.isdir(item_path):
                                    shutil.rmtree(item_path, ignore_errors=True)
                                else:
                                    os.remove(item_path)
                                self.logger.debug(f"Deleted local cache item: {item_path}")
                        self.cache.wipe()
                        self.ipfs_version_cache.add(f"IPFS_VERSION_{self.network_name}", version)
                    else:
                        if stored_version != version or self.network_name not in updated_nets:
                            self.logger.info(f"Detected IPFS version change from {stored_version} to {version}, deleting local files and wiping cache for {self.network_name}")
                            for item in os.listdir(self.target):
                                if item.startswith('Qm'):
                                    item_path = os.path.join(self.target, item)
                                    if os.path.isdir(item_path):
                                        shutil.rmtree(item_path, ignore_errors=True)
                                    else:
                                        os.remove(item_path)
                                    self.logger.debug(f"Deleted local cache item: {item_path}")
                            self.cache.wipe()
                            updated_nets = list(set(updated_nets + [self.network_name]))
                            self.ipfs_version_cache.add("UPDATED_NETWORKS", json.dumps(updated_nets))
                            self.ipfs_version_cache.add(f"IPFS_VERSION_{self.network_name}", version)

    def _connect_and_configure(self):
        """Connect to IPFS and apply configurations if local."""
        self.connected = self.connect()
        if not self.connected:
            self.logger.error("Failed to connect to IPFS after 10 attempts. Proceeding with limited functionality (gateway downloads only).")
        if self.connected and "127.0.0.1" in self.client_connect_url:
            try:
                self._api_call('config', params={'arg': ['Swarm.ConnMgr.LowWater', '25'], 'json': 'true'})
                self.logger.info("Successfully set Swarm.ConnMgr.LowWater to 25")
            except Exception as config_error:
                self.logger.error(f"Failed to set IPFS config: {config_error}")
            # IPFS_STORAGE_MAX is the floor for Datastore.StorageMax; the value
            # is only ever raised to it, never lowered.
            self._ensure_storage_max()

    def _parse_multiaddr(self, ma):
        match = re.match(r'/ip4/([\d.]+)/tcp/(\d+)/http', ma)
        if match:
            host, port = match.groups()
            return f'http://{host}:{port}'
        raise ValueError(f"Invalid multiaddr: {ma}")

    def get_version(self):
        try:
            resp = self.session.post(f"{self.api_base}/api/v0/version", timeout=10)
            resp.raise_for_status()
            return resp.json()['Version']
        except Exception as e:
            self.logger.warning(f"Failed to get version: {e}")
            return None

    def peer_id(self):
        """The peer id of the Kubo node this agent uses."""
        return self._api_call('id', timeout=10)['ID']

    def connect(self, attempts=3):
        attempt = 0
        while attempt < attempts:
            try:
                # Verify IPFS node is responsive
                self._api_call('id')
                # peering/ls reports transport addresses without the /p2p/<id>
                # suffix the add call takes; the comparison puts it back.
                peering_list = self._api_call('swarm/peering/ls')
                present = {f"{addr}/p2p/{peer.get('ID')}"
                           for peer in peering_list.get('Peers', []) for addr in peer.get('Addrs', [])}
                try:
                    swarm_list = self._swarm_addrs()
                except ValueError as e:
                    self.logger.error(str(e))
                    return False

                for url in swarm_list:
                    if not url.startswith('/') or not re.search(r'/p2p/[^/]+$', url):
                        self.logger.error(f"Invalid multiaddr format: {url} "
                                          f"(must start with '/' and end in /p2p/<peer id>)")
                # One add per peer carrying all of its addresses: the peering
                # service replaces a peer's address list on every add, so an
                # add per address would leave only the last one.
                for pid, addrs in self._group_by_peer(u for u in swarm_list if u.startswith('/')).items():
                    if all(a in present for a in addrs):
                        self.logger.debug(f"Peer already configured: {addrs}")
                        continue
                    try:
                        self._api_call('swarm/peering/add', params={'arg': addrs})
                        self.logger.debug(f"Peering with {pid} at {addrs}")
                    except Exception as peer_error:
                        self.logger.error(f"Failed to add peer {addrs}: {peer_error}")
                return True
            except Exception as e:
                self.logger.warning(f"IPFS communication error (attempt {attempt + 1}/{attempts}): {e}")
                if "127.0.0.1" in self.client_connect_url:
                    self.logger.warning("Restarting IPFS service")
                    try:
                        self.restart_ipfs_service()
                    except Exception as restart_error:
                        self.logger.error(f"Failed to restart IPFS service: {restart_error}")
                else:
                    self.logger.warning("Please verify your IPFS host is operational")
                attempt += 1
                # Add small delay between retries
                if attempt < attempts:
                    import time
                    time.sleep(1)
        self.logger.error("Failed to connect to IPFS swarm after all attempts")
        return False

    def _swarm_addrs(self):
        """The configured swarm multiaddrs as a list. IPFS_SWARM is one string
        of newline-separated entries, each split on whitespace when it contains
        a space and on commas otherwise, or already a list."""
        if isinstance(self.ipfs_swarm, list):
            return list(self.ipfs_swarm)
        if not isinstance(self.ipfs_swarm, str):
            raise ValueError("Invalid ipfs_swarm format: must be a string or list of multiaddrs")
        addrs = []
        for line in self.ipfs_swarm.split('\n'):
            parts = line.split() if ' ' in line else line.split(',')
            addrs.extend(p.strip() for p in parts if p.strip())
        return addrs

    def _swarm_peers(self):
        """{peer id: [multiaddrs]} for the swarm entries that end in /p2p/<id>,
        in configured order, followed by the validators' peers from chain."""
        peers = {}
        for addr in self._swarm_addrs():
            m = re.search(r'/p2p/([^/]+)$', addr)
            if m:
                peers.setdefault(m.group(1), []).append(addr)
        for pid, addrs in self._chain_peers.items():
            known = peers.setdefault(pid, [])
            known.extend(a for a in addrs if a not in known)
        return peers

    @staticmethod
    def _group_by_peer(multiaddrs):
        """{peer id: [multiaddrs]} for the entries that end in /p2p/<id>."""
        peers = {}
        for addr in multiaddrs:
            m = re.search(r'/p2p/([^/]+)$', addr)
            if m:
                peers.setdefault(m.group(1), []).append(addr)
        return peers

    def sync_chain_peers(self, multiaddrs):
        """Make Kubo's peering list carry exactly the validators' IPFS peers in
        `multiaddrs`, beside the configured IPFS_SWARM entries: a peer whose
        addresses are not all listed is (re)added with one swarm/peering/add
        carrying all of them, since the peering service replaces a peer's
        address list on every add; peers this method added before and that
        are no longer listed are removed with swarm/peering/rm. Configured
        entries are never removed. Never raises."""
        wanted = self._group_by_peer(multiaddrs)
        configured = set(self._group_by_peer(self._swarm_addrs()))
        try:
            listed = self._api_call('swarm/peering/ls', timeout=10)
            # peering/ls reports transport addresses without the /p2p/<id>
            # suffix the add call takes; the comparison puts it back.
            present = {f"{addr}/p2p/{p.get('ID')}"
                       for p in listed.get('Peers', []) for addr in p.get('Addrs', [])}
        except Exception as e:
            self.logger.warning(f"ipfs-peers: swarm/peering/ls failed: {e}")
            return
        for pid, addrs in wanted.items():
            if all(addr in present for addr in addrs):
                continue
            try:
                self._api_call('swarm/peering/add', params={'arg': addrs}, timeout=10)
                self.logger.info(f"ipfs-peers: peering with validator IPFS {pid} at {addrs}")
            except Exception as e:
                self.logger.warning(f"ipfs-peers: swarm/peering/add {addrs} failed: {e}")
        for pid in list(self._chain_peers):
            if pid in wanted or pid in configured:
                continue
            try:
                self._api_call('swarm/peering/rm', params={'arg': pid}, timeout=10)
                self.logger.info(f"ipfs-peers: validator IPFS {pid} left the set; peering removed")
            except Exception as e:
                self.logger.warning(f"ipfs-peers: swarm/peering/rm {pid} failed: {e}")
        self._chain_peers = wanted

    def connect_peer(self, multiaddr, timeout=15):
        """One swarm/connect to `multiaddr` (a full /…/p2p/<id> address), so
        the next fetch has that node as a provider. Raises when the dial
        fails."""
        self._api_call('swarm/connect', params={'arg': multiaddr, 'timeout': f'{timeout}s'}, timeout=timeout + 5)

    def peering_add(self, multiaddr):
        """Keep Kubo reconnecting to `multiaddr`'s peer until peering_rm; its
        peer id is returned. Peers added here are not touched by
        sync_chain_peers, which manages only the validators' peers."""
        m = re.search(r'/p2p/([^/]+)$', multiaddr)
        if not m:
            raise ValueError(f"{multiaddr!r} does not end in /p2p/<peer id>")
        self._api_call('swarm/peering/add', params={'arg': [multiaddr]}, timeout=10)
        return m.group(1)

    def peering_rm(self, peer_id):
        """Stop keeping the connection to `peer_id`. Never raises."""
        try:
            self._api_call('swarm/peering/rm', params={'arg': peer_id}, timeout=10)
        except Exception as e:
            self.logger.debug(f"swarm/peering/rm {peer_id}: {e}")

    # A pinned block is announced when pinned and again at the next
    # REPROVIDE_EVERY-second sync passes until REPROVIDE_TIMES announces are
    # done or it is older than REPROVIDE_WINDOW; Kubo's own reprovider carries
    # it from there.
    REPROVIDE_WINDOW = 1800
    REPROVIDE_EVERY = 240
    REPROVIDE_TIMES = 3

    def provide(self, cid):
        """Announce `cid` to the DHT (routing/provide) on the announce worker,
        so a peer that is not connected to this node can still find it, and
        remember it for reprovide_recent. Returns at once; never raises."""
        with self._recent_lock:
            self._recent_pins[cid] = {'pinned': time.time(), 'announces': 1, 'last': time.time()}
        self.announce_executor.submit(self._announce, cid)

    def _announce(self, cid):
        """One routing/provide, read to the end of its event stream so the
        call returns when the DHT walk is over. A refusal (HTTP error) or a
        transport failure is logged at WARNING: a node whose announces all
        fail must be visible in the log."""
        try:
            resp = self._api_call('routing/provide', params={'arg': cid}, stream=True, timeout=120)
            try:
                for _ in resp.iter_lines():
                    pass
            finally:
                resp.close()
            self.logger.debug(f"ipfs-provide: announced {cid}")
        except Exception as e:
            self.logger.warning(f"ipfs-provide: {cid}: {e}")

    def reprovide_recent(self):
        """Schedule the next announce of every recently pinned CID that is due
        (REPROVIDE_EVERY since its last one, fewer than REPROVIDE_TIMES
        announces, younger than REPROVIDE_WINDOW) and forget the rest."""
        now = time.time()
        due = []
        with self._recent_lock:
            for cid, entry in list(self._recent_pins.items()):
                if now - entry['pinned'] > self.REPROVIDE_WINDOW or entry['announces'] >= self.REPROVIDE_TIMES:
                    del self._recent_pins[cid]
                elif now - entry['last'] >= self.REPROVIDE_EVERY:
                    entry['announces'] += 1
                    entry['last'] = now
                    due.append(cid)
        for cid in due:
            self.announce_executor.submit(self._announce, cid)

    def _api_call(self, command, params=None, files=None, data=None, stream=False, timeout=None):
        if params is None:
            params = {}
        url = f"{self.api_base}/api/v0/{command}"
        kwargs = {'params': params, 'timeout': self.ipfs_timeout if timeout is None else timeout}
        if files:
            kwargs['files'] = files
        if data:
            kwargs['data'] = data
        if stream:
            kwargs['stream'] = True
        resp = self.session.post(url, **kwargs)
        if not resp.ok:
            raise Exception(f"IPFS API error: {url} {resp.status_code} - {resp.text}")
        if stream:
            return resp
        try:
            return resp.json()
        except:
            return resp.text

    def _http_request_with_retry(self,
                                 method: str,
                                 url: str,
                                 max_retries: int = 5,
                                 backoff_factor: float = 1.0,
                                 retry_on_status: tuple = (429, 500, 502, 503, 504),
                                 **kwargs) -> requests.Response:
        import http.client
        from requests.exceptions import ConnectionError, Timeout
        delay = backoff_factor
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = getattr(self.session, method)(url, **kwargs)
                if resp.status_code not in retry_on_status:
                    resp.raise_for_status()
                    return resp
            except (ConnectionError, Timeout, http.client.RemoteDisconnected) as e:
                last_exc = e
            else:
                last_exc = None
            if attempt == max_retries:
                if last_exc:
                    raise
                else:
                    resp.raise_for_status()
            reason = f"exception {last_exc!r}" if last_exc else f"status {resp.status_code}"
            self.logger.warning(
                "[%s/%s] %s %s failed with %s; retrying in %.1fs …",
                attempt, max_retries, method.upper(), url,
                reason, delay
            )
            time.sleep(delay)
            delay *= 2
        raise RuntimeError("Exceeded max retries in _http_request_with_retry")


    def download(self, data):
        """
        Main download function: Checks cache, attempts gateway if not local, then local IPFS/swarm.
        Handles files (with gzip check) and directories (via tar).
        """
        out_path = os.path.join(self.target, data)

        # The cache flag only says "we fetched this once" -- it does NOT
        # guarantee the bytes are still on disk. Periodic GC / eviction or a
        # cleared cache dir can remove the file while the flag lingers, so
        # trusting the flag alone made download() a no-op that returned with no
        # content (the integration-test image / compose then 'missing', failing
        # the SGX check). Only skip the fetch when the flag is set AND the file
        # actually exists on disk; otherwise fall through and REFETCH.
        if self.cache.contains(data) and os.path.exists(out_path):
            self.logger.info(f"{data} found in local cache, skipping download")
            return
        if self.cache.contains(data):
            self.logger.info(f"{data} cache flag set but file missing on disk; refetching")

        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        if self._try_download_from_gateway(data, out_path):
            self._post_download_processing(data, out_path)
            return

        self._ensure_ipfs_connection()

        self._prepare_local_download(data)

        try:
            self._download_from_local_ipfs(data, out_path)
        except Exception as e:
            self._handle_download_error(e, data, out_path)

        self.cache.add(data)

    def _try_download_from_gateway(self, data, out_path):
        """
        Attempt to download from the IPFS gateway if conditions are met.
        Returns True if successful, False otherwise.
        """
        if self.gateway is None or (self.connected and self.is_pinned(data)):
            return False


        if self.is_pinned(data):
            self.logger.info(f"{data} is pinned locally, downloading from local IPFS")
            try:
                self._download_from_local_ipfs(data, out_path)
            except Exception as e:
                self._handle_download_error(e, data, out_path)
        else:
            try:
                self.logger.info(f"{data} is not pinned locally, downloading from IPFS gateway")
                self.fetch_ipfs_content(data, output=out_path)
                return True
            except Exception as e_remote:
                self.logger.warning(f"Fetch from IPFS gateway failed for {data}: {e_remote}")
                return False


    def _post_download_processing(self, data, out_path):
        """
        Perform post-download actions like adding to IPFS and pinning if connected.
        """
        self.cache.add(data)
        if self.connected:
            try:
                self.add_path(out_path)
                self.pin_add(data)
            except Exception as e:
                self.logger.warning(f"Failed to add/pin after gateway download for {data}: {e}")

    def _ensure_ipfs_connection(self):
        """
        Ensure connection to IPFS; reconnect if necessary.
        """
        if not self.connected:
            if not self.connect():
                raise Exception("No IPFS connection and gateway failed")
            self.connected = True

    def _prepare_local_download(self, data):
        """
        Prepare for local download by pinning if not already pinned; the swarm
        fetch is preceded by the provider connection check.
        """
        if not self.is_pinned(data):
            self.logger.info(f"{data} is not pinned locally, downloading from IPFS swarm")
            self._ensure_provider_path()
            self.pin_add(data)
        else:
            self.logger.info(f"{data} is pinned locally, downloading from local IPFS")

    def _download_from_local_ipfs(self, data, out_path):
        """
        Download content from local IPFS, handling file or directory cases.
        """
        # Clean up any existing conflicting path to avoid OS errors
        if os.path.exists(out_path):
            if os.path.isdir(out_path):
                shutil.rmtree(out_path, ignore_errors=True)
                self.logger.debug(f"Removed existing directory {out_path} for clean download")
            else:
                os.remove(out_path)
                self.logger.debug(f"Removed existing file {out_path} for clean download")
        try:
            # Try as plain file without compression
            params = {'arg': data}
            resp = self._api_call('get', params=params, stream=True)
            resp.raw.decode_content = False  # Ensure raw bytes
            with open(out_path, 'wb') as f:
                shutil.copyfileobj(resp.raw, f)

            # Check if the downloaded file is a tar and extract if so (for wrapped single files)
            if tarfile.is_tarfile(out_path):
                temp_dir = out_path + '.temp'
                os.makedirs(temp_dir, exist_ok=True)
                with tarfile.open(out_path, 'r:*') as tar:
                    members = [m for m in tar.getmembers() if not m.name.startswith('PaxHeaders.0')]
                    tar.extractall(path=temp_dir, members=members)
                os.remove(out_path)  # Remove the tar after extraction
                contents = os.listdir(temp_dir)
                if len(contents) == 1:
                    item_path = os.path.join(temp_dir, contents[0])
                    if os.path.isfile(item_path):
                        shutil.move(item_path, out_path)
                        self.logger.debug(f"Extracted single file to {out_path}")
                    else:
                        # Single dir, move its contents
                        sub_dir = item_path
                        os.makedirs(out_path, exist_ok=True)
                        for sub_item in os.listdir(sub_dir):
                            shutil.move(os.path.join(sub_dir, sub_item), out_path)
                        self.logger.debug(f"Extracted directory contents to {out_path}")
                else:
                    # Multiple items, move all to out_path dir
                    os.makedirs(out_path, exist_ok=True)
                    for item in contents:
                        shutil.move(os.path.join(temp_dir, item), out_path)
                    self.logger.debug(f"Extracted multiple items to {out_path}")
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception as e:
            error_msg = str(e).lower()
            if "file is not regular" in error_msg or "this dag node is a directory" in error_msg:
                self._download_directory_as_tar(data, out_path)
            else:
                raise

    def _download_directory_as_tar(self, data, out_path):
        """
        Download directory as tar archive and extract it.
        """
        self.logger.info(f"Detected directory CID {data}; downloading as archive and extracting")
        params = {'arg': data, 'archive': 'true', 'compress': 'true', 'compression-level': '9'}
        resp = self._api_call('get', params=params, stream=True)
        temp_tar = os.path.join(self.target, f"{data}.tar.gz")
        try:
            with open(temp_tar, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            os.makedirs(out_path, exist_ok=True)
            with tarfile.open(temp_tar, 'r:gz') as tar:
                members = [m for m in tar.getmembers() if not m.name.startswith('PaxHeaders.0')]
                for member in members:
                    parts = member.name.split('/')
                    if parts and parts[0] == data:
                        member.name = '/'.join(parts[1:])
                    if member.name:
                        tar.extract(member, path=out_path)
                    else:
                        self.logger.debug(f"Skipping empty name member for {data}")
        finally:
            if os.path.exists(temp_tar):
                os.remove(temp_tar)


    def _handle_download_error(self, e, data, out_path):
        """
        Handle errors during download, including service restarts.
        """
        self.logger.warning(f"Error while downloading file {data}: {e}")
        if "127.0.0.1" in self.client_connect_url:
            self.logger.warning("Restarting IPFS service")
            self.restart_ipfs_service()
        else:
            self.logger.warning("Please make sure your IPFS host is working properly")
        raise e

    def fetch_ipfs_content(self, cid: str, output: str = None) -> None:
        """
        Fetch content from IPFS gateway, determining if it's a file or folder.
        """
        if output is None:
            output = cid

        try:
            is_folder = self.is_ipfs_folder(cid)
            url = f"{self.gateway}/ipfs/{cid}?format=tar" if is_folder else f"{self.gateway}/ipfs/{cid}"
            resp = self._http_request_with_retry("get", url, stream=True, timeout=60)
            resp.raise_for_status()
            resp.raw.decode_content = True

            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

            if is_folder:
                temp_tar = os.path.join(os.path.dirname(output), f"temp_{cid}.tar")
                try:
                    with open(temp_tar, "wb") as f:
                        shutil.copyfileobj(resp.raw, f)
                    os.makedirs(output, exist_ok=True)
                    with tarfile.open(temp_tar, "r") as tar:
                        for member in tar.getmembers():
                            parts = member.name.split('/')
                            if parts and parts[0] == cid:
                                member.name = '/'.join(parts[1:])
                            if member.name:
                                tar.extract(member, path=output)
                            else:
                                self.logger.debug(f"Skipping empty name member for {cid}")
                    self.logger.debug(f"Downloaded and extracted folder {output}")
                finally:
                    if os.path.exists(temp_tar):
                        os.remove(temp_tar)
            else:
                with open(output, "wb") as f:
                    shutil.copyfileobj(resp.raw, f)
                self.logger.debug(f"Downloaded file {output}")
        except Exception as e:
            raise

    def is_ipfs_folder(self, path: str) -> bool:
        """
        Check if the IPFS path is a folder by attempting to fetch the directory listing.
        """
        url = f"{self.gateway}/ipfs/{path}/"
        for attempt in range(10):
            try:
                resp = self.session.get(url, timeout=10)
                if resp.status_code == 200 and '<a href="/ipfs/' in resp.text:
                    self.logger.info(f"IPFS path {path} is a folder")
                    return True
                elif resp.status_code == 200:
                    self.logger.info(f"IPFS path {path} is a file")
                    return False
            except requests.RequestException as e:
                self.logger.debug(f"Attempt {attempt+1} failed to check if folder: {e}")
            time.sleep(1)
        raise Exception(f"Unable to determine if {path} is file or folder after 10 attempts")

    @staticmethod
    def cidv1_raw(content_bytes):
        """CIDv1/raw/sha2-256 for `content_bytes`, computed locally.

        Layout: 'b' + base32( 0x01 0x55 0x12 0x20 || sha256(content) )
                        CIDv1  raw  sha256  32 bytes

        Matches what `ipfs add --cid-version=1 --raw-leaves` returns, so the CID
        can be known WITHOUT talking to IPFS at all. That is what lets the node
        record a result and move on while pinning happens in the background.
        """
        digest = hashlib.sha256(content_bytes).digest()
        raw = bytes([0x01, 0x55, 0x12, 0x20]) + digest
        return 'b' + base64.b32encode(raw).decode('ascii').lower().rstrip('=')

    def pin_bytes_deferred(self, content_bytes, name='blob', attempts=10, delay=30):
        """Compute the CID now, return it, and pin in the BACKGROUND.

        `upload()` blocks for up to 10 attempts with an IPFS service restart
        between failures, and raises if they all fail. On the result path that
        meant a slow or unhealthy IPFS could stall -- or lose -- an on-chain
        result submission for work the enclave had already completed correctly,
        and the stall grows with the size of the result.

        Since the CID is just a hash of the content, it can be computed locally.
        The caller gets it immediately and proceeds; the actual add/pin is queued
        on the existing executor and retries on its own without holding anything
        up.

        Returns the CID (never None, never raises).
        """
        cid = self.cidv1_raw(content_bytes)
        # Spool the content before returning. In-memory retries die with the
        # process, and the result is then unrecoverable: the CID is on chain
        # but no node holds the bytes. Written first so a crash between here
        # and the first attempt still leaves the queue able to finish the pin.
        self._spool_pending_pin(cid, content_bytes, name)

        def _work():
            for attempt in range(attempts):
                try:
                    # add_bytes_raw raises "Not connected" off self.connected
                    # without calling the API, so a stale flag fails every
                    # attempt identically no matter how healthy the daemon is.
                    # Re-establish it here the way upload() does.
                    if not self.connected:
                        if self.connect():
                            self.connected = True
                        else:
                            raise Exception("Failed to connect to IPFS for background pin")
                    stored = self.add_bytes_raw(content_bytes, name=name)
                    if stored == cid:
                        self.cache.add(cid)
                        self._unspool_pending_pin(cid)
                        self.logger.info(f"Pinned {name} -> {cid} (background)")
                        self.provide(cid)
                        return
                    # Keep the spool: the bytes are still unpinned under the CID
                    # the caller published, so this needs looking at rather than
                    # discarding.
                    self.logger.warning(
                        f"Background pin of {name}: IPFS stored {stored}, expected {cid}")
                    return
                except Exception as e:
                    self.logger.warning(
                        f"Background pin attempt {attempt + 1}/{attempts} for {name} failed: {e}")
                    # Drop the flag so the next attempt reconnects instead of
                    # replaying the same dead state.
                    self.connected = False
                    time.sleep(delay)
            # The spool stays on disk: drain_pending_pins picks it up on the
            # next cycle or after a restart.
            self.logger.error(
                f"Background pin of {name} ({cid}) failed after {attempts} attempts; "
                f"queued for retry")

        try:
            self.executor.submit(_work)
        except Exception as e:
            # Even losing the worker must not fail the caller: the CID is valid
            # regardless, and other nodes replicate from the registry/chain.
            self.logger.warning(f"Could not queue background pin for {name}: {e}")
        return cid

    def _pending_pin_dir(self):
        d = os.path.join(self.target, 'pending_pins')
        os.makedirs(d, exist_ok=True)
        return d

    def _spool_pending_pin(self, cid, content_bytes, name):
        """Persist content that still needs pinning, keyed by CID.

        Written via a temp file + rename so a crash mid-write cannot leave a
        truncated blob that would later be re-added under the wrong CID.
        """
        try:
            path = os.path.join(self._pending_pin_dir(), cid)
            if os.path.exists(path):
                return
            tmp = f"{path}.tmp"
            with open(tmp, 'wb') as f:
                f.write(content_bytes)
            os.replace(tmp, path)
            meta = os.path.join(self._pending_pin_dir(), f"{cid}.name")
            with open(meta, 'w') as f:
                f.write(name)
        except Exception as e:
            # Never fail the caller: the in-memory retry still runs, this only
            # costs durability across a restart.
            self.logger.warning(f"Could not spool pending pin for {name} ({cid}): {e}")

    def _unspool_pending_pin(self, cid):
        for suffix in ('', '.name', '.tmp'):
            try:
                os.remove(os.path.join(self._pending_pin_dir(), f"{cid}{suffix}"))
            except FileNotFoundError:
                pass
            except Exception as e:
                self.logger.warning(f"Could not clear spooled pin {cid}{suffix}: {e}")

    def drain_pending_pins(self, limit=25):
        """Re-attempt every spooled pin. Safe to call on any cycle.

        This is what makes a failed pin survive an IPFS outage or a node
        restart: the content is on disk, so the pin is retried until it lands
        instead of being lost with the process that queued it.

        Verifies the bytes still hash to the CID they are filed under -- a
        corrupted spool must not be published under a CID the chain already
        references.
        """
        try:
            d = self._pending_pin_dir()
            names = [n for n in os.listdir(d)
                     if not n.endswith('.name') and not n.endswith('.tmp')]
        except Exception as e:
            self.logger.warning(f"Could not list pending pins: {e}")
            return 0
        if not names:
            return 0
        if not self.connected:
            if self.connect():
                self.connected = True
            else:
                self.logger.info(
                    f"{len(names)} pin(s) still queued; IPFS unreachable, will retry")
                return 0
        done = 0
        for cid in names[:limit]:
            path = os.path.join(d, cid)
            try:
                with open(path, 'rb') as f:
                    content = f.read()
            except Exception as e:
                self.logger.warning(f"Could not read spooled pin {cid}: {e}")
                continue
            if self.cidv1_raw(content) != cid:
                self.logger.error(
                    f"Spooled content for {cid} does not hash to its CID; discarding")
                self._unspool_pending_pin(cid)
                continue
            try:
                label = cid
                meta = os.path.join(d, f"{cid}.name")
                if os.path.exists(meta):
                    with open(meta) as f:
                        label = f.read().strip() or cid
                stored = self.add_bytes_raw(content, name=label)
                if stored == cid:
                    self.cache.add(cid)
                    self._unspool_pending_pin(cid)
                    self.logger.info(f"Pinned queued {label} -> {cid}")
                    self.provide(cid)
                    done += 1
                else:
                    self.logger.warning(
                        f"Queued pin {label}: IPFS stored {stored}, expected {cid}")
            except Exception as e:
                self.logger.warning(f"Queued pin {cid} failed: {e}")
                self.connected = False
                break
        return done

    def add_bytes_raw(self, content_bytes, name='blob'):
        """Add bytes as CIDv1 with raw leaves, returning the CID.

        The default `add` produces a CIDv0 dag-pb node (content wrapped in UnixFS
        framing), whose CID an enclave cannot derive without reimplementing that
        framing. With cid-version=1 + raw-leaves the CID is simply
        base32(0x01 0x55 0x12 0x20 || sha256(content)) -- so the enclave can
        compute it from the bytes it authored and commit THAT to the chain,
        meaning a hostile node cannot substitute different content for what was
        committed.

        Returns the CID, or None on failure.
        """
        if not self.connected:
            raise Exception("Not connected")
        files = {'file': (name, content_bytes)}
        params = {'cid-version': '1', 'raw-leaves': 'true', 'pin': 'true'}
        resp = self._api_call('add', params=params, files=files)
        try:
            if isinstance(resp, str):
                resp = json.loads(resp.strip().split('\n')[-1])
            return resp.get('Hash')
        except Exception as e:
            self.logger.warning(f"Could not parse add response for {name}: {e}")
            return None

    def add_path(self, path):
        if not self.connected:
            raise Exception("Not connected")
        params = {}
        files = None
        if os.path.isfile(path):
            files = {'file': (os.path.basename(path), open(path, 'rb'))}
        elif os.path.isdir(path):
            files = []
            base_dir = os.path.basename(path)
            for root, _, fnames in os.walk(path):
                for fname in fnames:
                    fullp = os.path.join(root, fname)
                    relp = os.path.relpath(fullp, path)
                    api_path = f"{base_dir}/{relp}"
                    files.append(('file', (api_path, open(fullp, 'rb'))))
        else:
            raise ValueError(f"Path {path} is neither file nor directory")
        resp = self._api_call('add', params=params, files=files)

        # Parse if newline-separated JSON string
        if isinstance(resp, str):
            lines = resp.strip().split('\n')
            parsed = []
            for line in lines:
                if line.strip():
                    try:
                        parsed.append(json.loads(line))
                    except json.JSONDecodeError:
                        self.logger.warning(f"Failed to parse JSON line: {line}")
            resp = parsed if len(parsed) > 1 else parsed[0] if parsed else {}

        if isinstance(resp, list):
            return resp[-1]['Hash']
        else:
            return resp['Hash']

    def _prepare_files_for_add(self, path):

        """
        Prepare files tuple for IPFS add API, handling both files and directories.
        """
        files = None
        if os.path.isfile(path):
            files = {'file': (os.path.basename(path), open(path, 'rb'))}
        elif os.path.isdir(path):
            files = []
            base_dir = os.path.basename(path)
            for root, _, fnames in os.walk(path):
                for fname in fnames:
                    fullp = os.path.join(root, fname)
                    relp = os.path.relpath(fullp, path)
                    api_path = f"{base_dir}/{relp}"
                    files.append(('file', (api_path, open(fullp, 'rb'))))
        else:
            raise ValueError(f"Path {path} is neither file nor directory")
        return files

    # Self-heal of the local daemon, run before a swarm fetch and after a failed
    # pin. The configured swarm peers are kept on a TCP or QUIC connection: a
    # connection relayed through a circuit is limited and bitswap opens no
    # stream over it, and a connection over a browser transport (webrtc-direct,
    # webtransport) is replaced by a TCP or QUIC one. `swarm connect` reports
    # success as soon as any connection to the peer exists and Kubo's peering
    # service keeps whichever connection it has, so the heal reads swarm/peers,
    # closes indirect connections beside a direct one, redials a peer whose
    # connections are all indirect and, after a failed pin, one whose direct
    # connection neither receives nor answers a ping. A peer with no connection
    # at all is left to the peering service, which redials it on its own, until
    # a pin fails. Redials are rate-limited per peer; detection never is.

    @staticmethod
    def _is_direct_addr(addr):
        """True for a TCP or QUIC connection: not relayed through a circuit and
        not over a browser transport (webrtc-direct, webtransport). swarm/peers
        reports resolved ip4/ip6 addresses; a relayed one ends in
        /p2p/<relay>/p2p-circuit."""
        return not any(t in addr for t in ('/p2p-circuit', '/webrtc-direct', '/webtransport'))

    def _peer_conns(self, peer_ids):
        """{peer id: [Addr, ...]} of the open connections to the given peers,
        one entry per connection as swarm/peers lists them; Addr is the remote
        multiaddr without the /p2p/<id> suffix."""
        conns = {}
        for p in self._api_call('swarm/peers', timeout=10).get('Peers') or []:
            if p.get('Peer') in peer_ids:
                conns.setdefault(p['Peer'], []).append(p.get('Addr', ''))
        return conns

    def _close_conns(self, pid, addrs):
        """Close the listed connections to the peer one at a time. swarm/disconnect
        with a full multiaddr closes the connection whose remote multiaddr
        equals it, limited or not; a per-address failure is reported inside
        Strings with HTTP 200."""
        for addr in addrs:
            try:
                reply = self._api_call('swarm/disconnect', params={'arg': f'{addr}/p2p/{pid}'}, timeout=10)
                strings = reply.get('Strings', []) if isinstance(reply, dict) else [str(reply)]
                if any('success' in s for s in strings):
                    self.logger.info(f"ipfs-heal[transport] {pid}: closed {addr}")
                else:
                    self.logger.debug(f"ipfs-heal[transport] {pid}: swarm/disconnect {addr}: {strings}")
            except Exception as e:
                self.logger.debug(f"ipfs-heal[transport] {pid}: swarm/disconnect {addr}: {e}")

    def _peer_is_dead(self, pid):
        """True when nothing has been received from the peer lately and a ping
        gets no pong. stats/bw RateIn is the decaying average of the bytes
        received from the peer over every protocol, so a non-zero value means
        bytes arrived within roughly the last half minute and the peer is kept
        without a ping. A pong is a ping line with Success true and Time > 0;
        the leading 'PING <id>.' line also carries Success true, with Time 0.
        An unparseable ping reply counts as alive."""
        try:
            if float(self._api_call('stats/bw', params={'peer': pid}, timeout=10).get('RateIn', 0)) > 0:
                return False
        except Exception as e:
            self.logger.debug(f"ipfs-heal[transport] {pid}: stats/bw: {e}")
        try:
            reply = self._api_call('ping', params={'arg': pid, 'count': '1'}, timeout=15)
        except Exception as e:
            self.logger.debug(f"ipfs-heal[transport] {pid}: ping: {e}")
            return True
        try:
            lines = [reply] if isinstance(reply, dict) else [json.loads(l) for l in str(reply).splitlines() if l.strip()]
        except ValueError:
            self.logger.warning(f"ipfs-heal[transport] {pid}: unparseable ping reply: {reply!r}")
            return False
        return not any(l.get('Success') and int(l.get('Time') or 0) > 0 for l in lines)

    @staticmethod
    def _redial_candidates(pid, addrs):
        """The configured multiaddrs of the peer plus the udp/<port>/quic-v1 twin
        of each tcp address. All are passed to one swarm/connect, which dials
        them together with the peerstore's addresses: quic-v1 at once, tcp
        250 ms later, relay 500 ms later (go-libp2p's default dial ranker)."""
        candidates = list(addrs)
        for addr in addrs:
            m = re.match(r'^(.*)/tcp/(\d+)/p2p/[^/]+$', addr)
            if m:
                twin = f'{m.group(1)}/udp/{m.group(2)}/quic-v1/p2p/{pid}'
                if twin not in candidates:
                    candidates.append(twin)
        return candidates

    @staticmethod
    def _redial_recent(pid):
        """True while the last redial of the peer is within its cooldown."""
        return time.time() - _ipfs_last_redial.get(pid, 0.0) < _ipfs_redial_cooldown.get(pid, IPFS_HEAL_COOLDOWN)

    @staticmethod
    def _claim_redial(pid):
        """Stamp a redial of the peer now; False when one is within its cooldown."""
        with _ipfs_heal_lock:
            if Storage._redial_recent(pid):
                return False
            _ipfs_last_redial[pid] = time.time()
            return True

    @staticmethod
    def _settle_redial(pid, ok):
        """Reset the peer's cooldown to IPFS_HEAL_COOLDOWN on success; double it
        up to IPFS_HEAL_MAX_COOLDOWN on failure. Returns the new cooldown."""
        with _ipfs_heal_lock:
            current = _ipfs_redial_cooldown.get(pid, IPFS_HEAL_COOLDOWN)
            _ipfs_redial_cooldown[pid] = IPFS_HEAL_COOLDOWN if ok else min(2 * current, IPFS_HEAL_MAX_COOLDOWN)
            return _ipfs_redial_cooldown[pid]

    def _redial_provider(self, pid, candidates):
        """Close every connection to the peer and dial the candidates. host.Connect
        returns without dialing while any non-limited connection exists, and the
        swarm reuses an existing limited connection, so the peer must be fully
        disconnected first. 'connect ... success' only means some connection
        exists; swarm/peers is re-read and a direct address is required. Indirect
        connections that completed alongside the direct one are closed.
        Returns True when the peer ends with a direct connection."""
        current = self._peer_conns([pid]).get(pid, [])
        self._close_conns(pid, current)
        if current:
            try:
                self._api_call('swarm/disconnect', params={'arg': f'/p2p/{pid}'}, timeout=10)
            except Exception as e:
                self.logger.debug(f"ipfs-heal[transport] {pid}: swarm/disconnect /p2p/{pid}: {e}")
        try:
            self._api_call('swarm/connect', params={'arg': candidates, 'timeout': '10s'}, timeout=15)
        except Exception as e:
            self.logger.warning(f"ipfs-heal[transport] {pid}: swarm/connect {candidates} failed: {e}")
            return False
        now = self._peer_conns([pid]).get(pid, [])
        direct = [a for a in now if self._is_direct_addr(a)]
        if not direct:
            self.logger.warning(f"ipfs-heal[transport] {pid}: no direct connection after redial ({now or 'not connected'})")
            return False
        self._close_conns(pid, [a for a in now if a not in direct])
        self.logger.info(f"ipfs-heal[transport] {pid}: connected via {direct}")
        return True

    def _check_provider(self, pid, addrs, have, suspect):
        """One peer: keep a direct connection and close the indirect ones beside
        it; redial when every connection is indirect, when the direct one is
        dead (suspect=True), or when there is none and a pin just failed
        (suspect=True). One redial per peer per cooldown."""
        direct = [a for a in have if self._is_direct_addr(a)]
        if direct:
            self._close_conns(pid, [a for a in have if a not in direct])
            if not suspect or self._redial_recent(pid) or not self._peer_is_dead(pid):
                return
            reason = f"direct connection {direct} is dead"
        elif have:
            reason = f"no direct connection ({have})"
        elif suspect:
            reason = "not connected"
        else:
            return
        if not self._claim_redial(pid):
            self.logger.debug(f"ipfs-heal[transport] {pid}: {reason}; redialed within cooldown, skipping")
            return
        candidates = self._redial_candidates(pid, addrs)
        self.logger.warning(f"ipfs-heal[transport] {pid}: {reason}; redialing {candidates}")
        ok = False
        try:
            ok = self._redial_provider(pid, candidates)
        except Exception as e:
            self.logger.warning(f"ipfs-heal[transport] {pid}: redial failed: {e}")
        cooldown = self._settle_redial(pid, ok)
        if cooldown != IPFS_HEAL_COOLDOWN:
            self.logger.info(f"ipfs-heal[transport] {pid}: next redial no sooner than {cooldown}s")

    def _ensure_provider_path(self, suspect=False):
        """Keep every configured swarm peer on a direct connection: indirect
        connections beside a direct one are closed and a peer whose connections
        are all indirect is redialed. With suspect=True (a pin just failed) a
        direct connection is kept only while _peer_is_dead says it answers, and
        a peer with no connection is redialed too; otherwise that peer is left
        to the peering service. Skipped for a remote daemon and within
        IPFS_RESTART_COOLDOWN of a daemon restart, while the peering service is
        redialing on its own. Never raises."""
        if "127.0.0.1" not in self.client_connect_url:
            return
        if time.time() - _ipfs_last_restart[0] < IPFS_RESTART_COOLDOWN:
            return
        try:
            peers = self._swarm_peers()
            conns = self._peer_conns(peers)
        except Exception as e:
            self.logger.warning(f"ipfs-heal[transport] swarm/peers failed: {e}")
            return
        # The suspect redial of an unconnected peer applies to the configured
        # providers only: a validator's IPFS peer from chain is dialed by the
        # peering service, and one that is unreachable must not cost a redial
        # attempt on every failed pin.
        configured = set(self._group_by_peer(self._swarm_addrs()))
        for pid, addrs in peers.items():
            try:
                self._check_provider(pid, addrs, conns.get(pid, []), suspect and pid in configured)
            except Exception as e:
                self.logger.warning(f"ipfs-heal[transport] {pid}: check failed: {e}")

    @staticmethod
    def _parse_byte_size(text):
        """Bytes for a Kubo size string such as '10GB', '512MiB' or '12345':
        decimal multipliers for K/M/G/T/P, binary for the Ki/Mi/Gi/Ti/Pi forms,
        as Kubo parses Datastore.StorageMax."""
        m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(i?)B?\s*', str(text), re.IGNORECASE)
        if not m:
            raise ValueError(f"Invalid size: {text!r}")
        number, prefix, binary = m.groups()
        exponent = 'KMGTP'.index(prefix.upper()) + 1 if prefix else 0
        return int(float(number) * (1024 if binary else 1000) ** exponent)

    def _ensure_storage_max(self):
        """Raise Datastore.StorageMax to the IPFS_STORAGE_MAX floor (default
        10GB) when it is below it; a higher value is kept. Kubo reads the cap
        only when started with --enable-gc, as the input of its periodic
        garbage collector; repo/stat reports it either way. The read, compare
        and write run under _ipfs_storage_lock so two Storage instances cannot
        lower each other's value. Never raises."""
        if "127.0.0.1" not in self.client_connect_url:
            return
        with _ipfs_storage_lock:
            try:
                stat = self._api_call('repo/stat', params={'size-only': 'true'}, timeout=10)
                cap = int(stat['StorageMax'])
                floor_text = os.environ.get('IPFS_STORAGE_MAX', '10GB')
                floor = self._parse_byte_size(floor_text)
                if cap >= floor:
                    self.logger.debug(f"ipfs-heal[storage] Datastore.StorageMax {cap} kept (floor {floor})")
                    return
                self._api_call('config', params={'arg': ['Datastore.StorageMax', floor_text]}, timeout=10)
                self.logger.info(f"ipfs-heal[storage] Datastore.StorageMax raised {cap} -> {floor_text}")
            except Exception as e:
                self.logger.warning(f"ipfs-heal[storage] Datastore.StorageMax check failed: {e}")

    def restart_ipfs_service(self):
        # One daemon is shared by every network thread, and each decides to
        # restart it on its own errors. Unsynchronized, that is a storm: the
        # first restart makes every other thread's call fail with connection
        # refused, and each of those failures triggers another restart.
        # Observed at 21:20:09 -- four networks restarting ipfs within 6s.
        #
        # The lock serializes them; the cooldown makes the followers skip
        # entirely, since a restart that just happened is what broke their
        # call in the first place.
        with _ipfs_restart_lock:
            since = time.time() - _ipfs_last_restart[0]
            if since < IPFS_RESTART_COOLDOWN:
                self.logger.info(
                    f"IPFS restarted {since:.1f}s ago; reconnecting instead of restarting again.")
                self.connected = self.connect()
                return
            _ipfs_last_restart[0] = time.time()
        try:
            result = subprocess.run(
                ['systemctl', 'restart', 'ipfs'],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.logger.info("IPFS service restarted successfully.")
            self.connected = self.connect()
            # Drain before repo_gc, not after: gc reclaims unpinned data, and
            # queued results are by definition unpinned. Pinning them first is
            # what stops the collector from taking them.
            self.drain_pending_pins()
            self.repo_gc()
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"Failed to restart local IPFS service. Error: {e.stderr.decode().strip()}")

    def download_many(self, lst, attempts=1, delay=0):
        for data in lst:
            self.logger.debug(f'Downloading {data}')
            if retry(self.download, data, attempts=attempts, delay=delay)[0] is False:
                return False
        return True

    def upload(self, data, timeout=600):
        if not self.connected:
            if self.connect():
                self.connected = True
            else:
                raise Exception("Failed to connect to IPFS for upload")
        attempt = 0
        while attempt < 10:
            try:
                hash_val = self.add_path(data)
                self.cache.add(hash_val)
                return hash_val
            except Exception as e:
                self.logger.warning(f"Error while uploading: {e}")
                if "127.0.0.1" in self.client_connect_url:
                    self.logger.warning("Restarting IPFS service")
                    self.restart_ipfs_service()
            attempt += 1
        raise Exception("Failed to upload after 10 attempts")

    def add(self, hash):
        pass

    def pin_add(self, hash, timeout=None):
        if not self.connected:
            return
        try:
            self._api_call('pin/add', params={'arg': hash}, timeout=timeout)
            self._pin_fail_streak = 0
        except Exception as e:
            error_message = str(e).lower()
            if 'not pinned' in error_message or 'pinned indirectly' in error_message:
                self._pin_fail_streak = 0
                return
            self.logger.info(f'error while adding pin')
            self.logger.error(e)
            # A single pin timeout is NORMAL (replication fail-fast on CIDs
            # that have not propagated yet), so it must never restart the
            # daemon by itself. But a long CONSECUTIVE streak across
            # different CIDs is the signature of a daemon that still accepts
            # connections yet answers nothing -- the one wedge state none of
            # the other restart triggers catch. Escalate: past the streak
            # threshold, run a cheap health probe; if even that times out,
            # restart the local daemon (rate-limited).
            self._pin_fail_streak = getattr(self, '_pin_fail_streak', 0) + 1
            if (self._pin_fail_streak >= 10
                    and "127.0.0.1" in self.client_connect_url):
                import time as _time
                last = getattr(self, '_pin_wedge_restart_at', 0)
                if _time.time() - last >= 600:  # at most once per 10 min
                    healthy = False
                    try:
                        self._api_call('version', timeout=10)
                        healthy = True
                    except Exception:
                        pass
                    if not healthy:
                        self.logger.warning(
                            f"IPFS daemon unresponsive ({self._pin_fail_streak} "
                            f"consecutive pin failures and health probe timed "
                            f"out) -- restarting IPFS service")
                        self._pin_wedge_restart_at = _time.time()
                        self._pin_fail_streak = 0
                        try:
                            self.restart_ipfs_service()
                        except Exception as restart_error:
                            self.logger.error(f"Failed to restart IPFS service: {restart_error}")
                    else:
                        # Daemon answers cheap calls: not wedged, just busy
                        # or the CIDs are unavailable. Reset the streak so we
                        # only escalate on a fresh unbroken run of failures.
                        self._pin_fail_streak = 0
            self._ensure_provider_path(suspect=True)
            raise

    def pin_rm(self, hash):
        if not self.connected:
            return
        try:
            self._api_call('pin/rm', params={'arg': hash})
        except Exception as e:
            error_message = str(e).lower()
            if 'not pinned' in error_message or 'pinned indirectly' in error_message:
                return
            self.logger.info(f'error while removing pin')
            self.logger.error(e)
            raise

    def open_ipfs_path(self, path, seconds):
        """The bytes at `path` (/ipfs/<cid>/...) as a stream to read(n) and
        close(), which Kubo ends after `seconds`. Raises FileNotFoundError when
        the tree has no such path."""
        try:
            resp = self._api_call('cat', params={'arg': path, 'timeout': f'{seconds}s'}, stream=True,
                                  timeout=seconds + 30)
        except Exception as e:
            if 'no link named' in str(e):
                raise FileNotFoundError(path) from e
            raise
        return _ResponseStream(resp)

    def ipfs_cumulative_size(self, cid, timeout=60):
        """The size in bytes the root block of `cid` declares for the whole
        tree (files/stat CumulativeSize); only that block is fetched. The
        publisher writes that number, so it bounds nothing by itself."""
        stat = self._api_call('files/stat', params={'arg': f'/ipfs/{cid}', 'timeout': f'{timeout}s'},
                              timeout=timeout + 30)
        return int(stat['CumulativeSize'])

    def recursive_pins(self):
        """The CIDs Kubo holds pinned recursively."""
        return set((self._api_call('pin/ls', params={'type': 'recursive'}, timeout=120).get('Keys') or {}).keys())

    def peering_ids(self):
        """The peer ids in Kubo's peering list."""
        peers = self._api_call('swarm/peering/ls', timeout=10).get('Peers') or []
        return {p.get('ID') for p in peers}

    def repo_size(self):
        """The bytes Kubo's repository holds (repo/stat RepoSize)."""
        return int(self._api_call('repo/stat', params={'size-only': 'true'}, timeout=60)['RepoSize'])

    def unpin(self, cid):
        """pin/rm `cid`, whatever the connection state recorded; done when Kubo
        no longer holds it pinned recursively, which is checked. Raises
        otherwise."""
        try:
            self._api_call('pin/rm', params={'arg': cid}, timeout=120)
        except Exception as e:
            if 'not pinned' not in str(e).lower():
                raise
        if cid in self.recursive_pins():
            raise Exception(f"pin/rm {cid} left it pinned")

    def repo_gc_streamed(self):
        """repo/gc, read to its end: the collector waits for every pin in
        flight and then runs for as long as the repository needs, so no read
        timeout applies."""
        resp = self._api_call('repo/gc', stream=True, timeout=(30, None))
        try:
            for _ in resp.iter_lines():
                pass
        finally:
            resp.close()

    def pin_add_watched(self, cid, timeout, watch, interval=10):
        """pin/add `cid` while `watch()` is asked every `interval` seconds
        whether to go on. When it returns a reason, or raises three times in a
        row, the request is closed, which ends the pin in Kubo, and
        PinAborted(reason) is raised; the blocks fetched so far stay unpinned
        for the collector."""
        resp = self._api_call('pin/add', params={'arg': cid, 'progress': 'true', 'timeout': f'{timeout}s'},
                              stream=True, timeout=interval + 120)
        reason = []
        done = threading.Event()

        def watcher():
            failures = 0
            while not done.wait(interval):
                try:
                    why = watch()
                    failures = 0
                except Exception as e:
                    failures += 1
                    self.logger.warning(f"pin/add {cid}: its watch failed ({e})")
                    why = f"its watch failed {failures} times in a row ({e})" if failures >= 3 else None
                if why:
                    reason.append(why)
                    resp.close()
                    return

        threading.Thread(target=watcher, name=f"pin-watch-{cid[:12]}", daemon=True).start()
        pinned = False
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                message = json.loads(line)
                if message.get('Type') == 'error':
                    raise Exception(f"pin/add {cid}: {message.get('Message')}")
                if message.get('Pins'):
                    pinned = True
        except Exception:
            if reason:
                raise PinAborted(reason[0])
            raise
        finally:
            done.set()
            resp.close()
        if reason:
            raise PinAborted(reason[0])
        # Kubo reports a failure after the body has started in a trailer, and the body then simply ends.
        if not pinned:
            raise Exception(f"pin/add {cid} ended without pinning it")

    def is_pinned(self, cid: str) -> bool:
        if not self.connected:
            return False
        try:
            self._api_call('pin/ls', params={'arg': cid})
            return True
        except Exception as e:
            err = str(e).lower()
            if 'not pinned' in err:
                return False
            if 'pinned indirectly' in err:
                return True
            # A failed pin/ls for one CID says nothing about the connection,
            # so do not latch the client off here. Clearing self.connected
            # makes every later add/pin raise "Not connected" without calling
            # the API, and nothing sets it back: observed at 21:20:09, one
            # error disabled result pinning node-wide for 6h.
            self.logger.warning(
                f'Pin status check failed for {cid!r}: {e}')
            return False
            self.logger.info(f'Unexpected error while checking pin status for {cid}')
            if "127.0.0.1" in self.client_connect_url:
                self.logger.warning("Restarting IPFS service")
                self.restart_ipfs_service()
            self.logger.error(e)
            return False

    def mig(self, hash, base_path):
        prefix = "Qm"
        legacy_path = hash
        target_path = base_path / hash
        if not os.path.exists(legacy_path) and not os.path.exists(base_path):
            self.cache.rem(hash)
            raise ValueError(f"The paths '{hash}' or '{legacy_path}' do not exist.")
        try:
            if os.path.exists(legacy_path):
                shutil.move(legacy_path, target_path)
        except Exception as e:
            self.cache.rem(hash)
            raise Exception("Unable to migrate '{hash}', deleting from cache.")

    def rm(self, hash):
        prefix = "Qm"
        legacy_path = "../" + hash
        if not os.path.exists(hash) and not os.path.exists(legacy_path):
            self.cache.rem(hash)
            logger.warning(f"The paths '{hash}' or '{legacy_path}' do not exist.")
        if os.path.exists(hash):
          if not os.path.isdir(hash):
            os.remove(hash) if hash.startswith(prefix) else None
            return
        if os.path.exists(legacy_path):
          if not os.path.isdir(legacy_path):
            os.remove(legacy_path) if hash.startswith(prefix) else None
            return
        target_directory = hash
        if os.path.isdir(target_directory):
          for item_name in os.listdir(target_directory):
            if item_name.startswith(prefix):
                item_path = os.path.join(target_directory, item_name)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    self.logger.error(f"Error while removing '{item_path}': {e}")
                    raise
        target_directory = "../" + hash
        if os.path.isdir(target_directory):
          for item_name in os.listdir(target_directory):
            if item_name.startswith(prefix):
                item_path = os.path.join(target_directory, item_name)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    self.logger.error(f"Error while removing '{item_path}': {e}")
                    raise
        self.cache.rem(hash)

    def repo_gc(self):
        if not self.connected:
            return
        try:
            self._api_call('repo/gc')
        except Exception as e:
            self.logger.info(f'error while performing garbage collect')
            self.logger.error(e)
            raise

_shared_caches = {}
_shared_caches_lock = threading.Lock()


class Cache:
    def __init__(self, items_limit, filepath, store_type=OrderedDict):
        self.items_limit = items_limit
        self.filepath = filepath
        self.store_type = store_type
        self._lock = threading.RLock()
        try:
            if not os.path.exists(filepath):
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                raise
            with open(filepath, 'r') as f:
                self.mem = store_type(json.load(f))
        except Exception as e:
            self.mem = store_type({})
            self._update_file()

    @classmethod
    def shared(cls, items_limit, filepath):
        """The one Cache of `filepath` in this process, for every handle that
        keeps state in that file. A Cache writes its whole content on each
        change."""
        key = os.path.abspath(filepath)
        with _shared_caches_lock:
            cache = _shared_caches.get(key)
            if cache is None:
                cache = _shared_caches[key] = cls(items_limit, filepath)
            return cache

    def _update_file(self):
        """Write the content as one json.dumps snapshot -- the C encoder, which
        no other thread interleaves with -- to a temporary file that then
        replaces the file, so the file is always a whole snapshot."""
        with self._lock:
            try:
                text = json.dumps(self.mem)
            except TypeError:
                text = json.dumps(list(self.mem))
            temporary = f"{self.filepath}.tmp"
            with open(temporary, 'w') as f:
                f.write(text)
            os.replace(temporary, self.filepath)

    def _reload_cache(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, 'r') as f:
                self.mem = self.store_type(json.load(f))
        else:
            self.mem = self.store_type({})

    def add(self, key, value):
        with self._lock:
            self.mem[key] = value
            if len(self.mem) == self.items_limit + 1:
                self.mem.popitem(last=False)
            self._update_file()
    def rem(self, key):
        with self._lock:
            if key in self.mem:
                removed_value = self.mem.pop(key)
                self._update_file()
                return removed_value
        return None
    def get(self, key):
        return self.mem.get(key)
    def get_key(self, value):
        for key, val in self.mem.items():
            if val == value:
                return key
        return None
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
    def rem(self, value):
        if value in self.mem:
            self.mem.remove(value)
            self._update_file()
    def contains(self, value):
        return value in self.mem
    @property
    def get_values(self):
        """
        Return a list of cached values.
        Converts string representations of integers to actual integers.
        Non-convertible strings remain as strings.
        """
        converted_values = []
        for item in self.mem:
            if isinstance(item, int):
                converted_values.append(item)
            elif isinstance(item, str):
                try:
                    converted_item = int(item)
                    converted_values.append(converted_item)
                except ValueError:
                    converted_values.append(item)
            else:
                # This should not happen due to type checks in add method
                converted_values.append(item)
        return converted_values
    def __iter__(self):
        """Make the object iterable."""
        return iter(self.mem)
    def __len__(self):
        """Return the number of items in the cache."""
        return len(self.mem)
    def __contains__(self, item):
        """Check if an item exists in the cache."""
        return item in self.mem
class ListCacheWithTimestamp:
    """
    A cache that stores unique items with associated timestamps using OrderedDict.
    It can migrate existing cache files from a JSON list format to a JSON dict format with timestamps.
    Attributes:
        items_limit (int): The maximum number of items the cache can hold.
        filepath (str): The path to the JSON file used for persisting the cache.
        mem (OrderedDict): In-memory storage of cache items with timestamps.
    """
    def __init__(self, items_limit, filepath):
        """
        Initialize the ListCacheWithTimestamp instance.
        Args:
            items_limit (int): The maximum number of items the cache can hold.
            filepath (str): The path to the JSON file used for persisting the cache.
        """
        self.items_limit = items_limit
        self.filepath = filepath
        self.mem = self._load_cache()
    def _load_cache(self):
        """
        Load the cache from the JSON file. If the file contains a list, migrate it to include timestamps.
        If the file does not exist or is corrupted, initialize an empty cache.
        Returns:
            OrderedDict: The in-memory cache with items and their timestamps.
        """
        if not os.path.exists(self.filepath):
            initial_mem = OrderedDict()
            self._update_file(initial_mem)
            return initial_mem
        try:
            with open(self.filepath, 'r', encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Migrate list to OrderedDict with timestamps
                    current_time = time.time()
                    entries = OrderedDict()
                    for item in data:
                        if looks_like_cid(item):
                            entries[item] = {'timestamp': current_time}
                    self._update_file(entries)
                    return entries
                elif isinstance(data, dict):
                    # Ensure all entries have a 'timestamp'. The cache names
                    # pins, so a key that is not a CID is left out.
                    entries = OrderedDict()
                    updated = False
                    for key, value in data.items():
                        if not looks_like_cid(key):
                            updated = True
                            continue
                        if not isinstance(value, dict) or 'timestamp' not in value:
                            value = {'timestamp': time.time()}
                            updated = True
                        entries[key] = value
                    if updated:
                        self._update_file(entries)
                    return entries
                else:
                    initial_mem = OrderedDict()
                    self._update_file(initial_mem)
                    return initial_mem
        except (json.JSONDecodeError, TypeError) as e:
            initial_mem = OrderedDict()
            self._update_file(initial_mem)
            return initial_mem
    def _update_file(self, mem=None):
        """
        Update the cache file with the current in-memory data.
        Args:
            mem (OrderedDict, optional): The in-memory cache to be saved. Defaults to self.mem.
        """
        mem = mem if mem is not None else self.mem
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                # Serialize the cache as a dictionary with items and their timestamps
                json.dump(mem, f, indent=4)
        except IOError as e:
            return
    def add(self, value):
        """
        Add a unique value to the cache with the current timestamp. If the cache exceeds the items_limit,
        the oldest item is evicted.
        Args:
            value (str): The value to add to the cache.
        """
        # The cache names pins; a value that is not a CID is not recorded.
        if not looks_like_cid(value):
            return
        current_time = time.time()
        if value in self.mem:
            # Update the timestamp for existing value
            self.mem[value]['timestamp'] = current_time
            # Optionally, move the item to the end to represent recent use
            self.mem.move_to_end(value)
        else:
            self.mem[value] = {'timestamp': current_time}
            if len(self.mem) > self.items_limit:
                popped_item, _ = self.mem.popitem(last=False)
        self._update_file()
    def get(self, value):
        """
        Retrieve a value from the cache.
        Args:
            value (str): The value to retrieve.
        Returns:
            str or None: The value if it exists in the cache; otherwise, None.
        """
        if value in self.mem:
            return value
        return None
    def rem(self, value):
        """
        Remove a value from the cache.
        Args:
            value (str): The value to remove.
        """
        if value in self.mem:
            del self.mem[value]
            self._update_file()
    def contains(self, value):
        """
        Check if a value exists in the cache.
        Args:
            value (str): The value to check.
        Returns:
            bool: True if the value exists in the cache; otherwise, False.
        """
        presence = value in self.mem
        return presence
    @property
    def get_values(self):
        """
        Get all values in the cache.
        Returns:
            list: A list of all values in the cache.
        """
        return list(self.mem.keys())
    def get_timestamp(self, value):
        """
        Get the timestamp of a specific entry.
        Args:
            value (str): The value whose timestamp is to be retrieved.
        Returns:
            float or None: The timestamp if the value exists; otherwise, None.
        """
        entry = self.mem.get(value)
        timestamp = entry['timestamp'] if entry else None
        return timestamp
    def __iter__(self):
        """
        Make the object iterable over its values.
        Returns:
            iterator: An iterator over the cached values.
        """
        return iter(self.mem.keys())
    def __len__(self):
        """
        Return the number of items in the cache.
        Returns:
            int: The number of items in the cache.
        """
        length = len(self.mem)
        return length
    def __contains__(self, item):
        """
        Check if an item exists in the cache.
        Args:
            item (str): The item to check.
        Returns:
            bool: True if the item exists; otherwise, False.
        """
        presence = item in self.mem
        return presence
    def wipe(self):
        self.mem = OrderedDict()
        self._update_file()

class MergedOrdersCache(Cache):
    def __init__(self, items_limit, filepath, store_type=list):
        super().__init__(items_limit, filepath, store_type)
    def add(self, do_req_id, dp_req_id, order_id):
        self.mem.append(dict(do=do_req_id, dp=dp_req_id, order=order_id))
        self._update_file()
    def rem(self, order_id):
        initial_len = len(self.mem)
        self.mem = [entry for entry in self.mem if entry.get("order") != order_id]
        if len(self.mem) < initial_len:
            self._update_file()
            return True # Successfully removed
        return False # Not found
class HardwareInfoProvider:
    @staticmethod
    def get_number_of_cpus():
        return psutil.cpu_count()
    @staticmethod
    def get_free_memory():
        return math.floor(psutil.virtual_memory()[1] / (2 ** 30)) # in GB
    @staticmethod
    def get_free_storage():
        return psutil.disk_usage("/")[2] // (2 ** 30) # in GB
def parse_transaction_bytes_ut(contract_abi, bytes_input):
    import rlp
    from rlp.sedes import big_endian_int, Binary, binary
    from eth_utils import keccak, to_checksum_address, decode_hex
    from eth_keys import keys
    from web3 import Web3
    # Define the signed transaction class
    class SignedTransaction(rlp.Serializable):
        fields = [
            ("nonce", big_endian_int),
            ("gasPrice", big_endian_int),
            ("gas", big_endian_int),
            ("to", Binary.fixed_length(20, allow_empty=True)),
            ("value", big_endian_int),
            ("data", binary),
            ("v", big_endian_int),
            ("r", big_endian_int),
            ("s", big_endian_int),
        ]
    # Define the unsigned transaction class
    class UnsignedTransaction(rlp.Serializable):
        fields = [
            ("nonce", big_endian_int),
            ("gasPrice", big_endian_int),
            ("gas", big_endian_int),
            ("to", Binary.fixed_length(20, allow_empty=True)),
            ("value", big_endian_int),
            ("data", binary),
        ]
    # Convert hex string to bytes if necessary
    if isinstance(bytes_input, str):
        bytes_input = bytes_input.strip()
        if bytes_input.startswith("0x"):
            bytes_input = decode_hex(bytes_input)
        else:
            bytes_input = bytes.fromhex(bytes_input)
    # Decode the transaction using RLP
    try:
        tx = rlp.decode(bytes_input, SignedTransaction)
    except Exception as e:
        print(f"Error decoding transaction: {e}")
        return None
    # Create an unsigned transaction instance
    unsigned_tx = UnsignedTransaction(
        nonce=tx.nonce,
        gasPrice=tx.gasPrice,
        gas=tx.gas,
        to=tx.to,
        value=tx.value,
        data=tx.data,
    )
    # Create a Web3 instance
    w3 = Web3(Web3.HTTPProvider("https://core.bloxberg.org"))
    # Compute the transaction hash (the message hash used for signing)
    tx_hash = keccak(rlp.encode(unsigned_tx))
    # Recover the sender's public key and address
    v = tx.v
    if v >= 35:
        # EIP-155
        chain_id = (v - 35) // 2
        v_standard = v - (chain_id * 2 + 35) + 27
    else:
        chain_id = None
        v_standard = v
    try:
        # Build the signature object
        # signature = keys.Signature(vrs=(v_standard, tx.r, tx.s))
        # Recover the public key
        # public_key = signature.recover_public_key_from_msg_hash(tx_hash)
        sender_address = w3.eth.account.recover_transaction(bytes_input)
    except Exception as e:
        print(f"Error recovering sender address: {e}")
        return None
    # Decode the function input data
    try:
        contract = w3.eth.contract(abi=contract_abi)
        decoded_function = contract.decode_function_input(tx.data)
        function_name = decoded_function[0].fn_name
        params = decoded_function[1]
    except Exception as e:
        print(f"Error decoding function input: {e}")
        return None
    # Prepare the result
    result = {
        "from": sender_address,
        "to": to_checksum_address(tx.to) if tx.to else None,
        "nonce": tx.nonce,
        "gasPrice": tx.gasPrice,
        "gas": tx.gas,
        "value": tx.value,
        "function_name": function_name,
        "params": params,
        "transaction_hash": "0x" + keccak(bytes_input).hex(),
        "result": params["_result"] if "_result" in params else None,
    }
    return result
