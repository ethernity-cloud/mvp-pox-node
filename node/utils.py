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
                # Set Datastore.StorageMax to 3GB (in bytes)
                self._api_call('config', params={'arg': ['Datastore.StorageMax', '3000000000']})
                self.logger.info("Successfully set Datastore.StorageMax to 3GB")
                # Set Swarm.ConnMgr.LowWater to 25
                self._api_call('config', params={'arg': ['Swarm.ConnMgr.LowWater', '25'], 'json': 'true'})
                self.logger.info("Successfully set Swarm.ConnMgr.LowWater to 25")
            except Exception as config_error:
                self.logger.error(f"Failed to set IPFS config: {config_error}")
                # Optionally, decide whether to proceed or fail
                # For now, log the error and continue

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

    def connect(self, attempts=3):
        attempt = 0
        while attempt < attempts:
            try:
                # Verify IPFS node is responsive
                self._api_call('id')
                # Get existing peering connections
                peering_list = self._api_call('swarm/peering/ls')
                existing_addrs = {addr for peer in peering_list.get('Peers', []) for addr in peer.get('Addrs', [])}
                # Ensure ipfs_swarm is a list of multiaddrs
                if isinstance(self.ipfs_swarm, str):
                    # Split by newlines, spaces, or commas
                    swarm_list = []
                    # First, split by newlines
                    for line in self.ipfs_swarm.split('\n'):
                        # Then split by spaces or commas within each line
                        line_addrs = line.split() if ' ' in line else line.split(',')
                        swarm_list.extend(addr.strip() for addr in line_addrs if addr.strip())
                elif isinstance(self.ipfs_swarm, list):
                    swarm_list = self.ipfs_swarm
                else:
                    self.logger.error("Invalid ipfs_swarm format: must be a string or list of multiaddrs")
                    return False

                # Process each multiaddr individually
                for url in swarm_list:
                    if not url.startswith('/'):
                        self.logger.error(f"Invalid multiaddr format: {url} (must start with '/')")
                        continue
                    try:
                        if url not in existing_addrs:
                            self.logger.debug(f"Attempting to add IPFS peer: {url}")
                            self._api_call('swarm/peering/add', params={'arg': url})
                            self.logger.debug(f"Successfully added peer: {url}")
                        else:
                            self.logger.debug(f"Peer already connected: {url}")
                    except Exception as peer_error:
                        self.logger.error(f"Failed to add peer {url}: {peer_error}")
                        continue  # Continue with next peer instead of failing entirely
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

    def _api_call(self, command, params=None, files=None, data=None, stream=False):
        if params is None:
            params = {}
        url = f"{self.api_base}/api/v0/{command}"
        kwargs = {'params': params, 'timeout': self.ipfs_timeout}
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
        if self.cache.contains(data):
            self.logger.info(f"{data} found in local cache, skipping download")
            return

        out_path = os.path.join(self.target, data)
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
        Prepare for local download by pinning if not already pinned.
        """
        if not self.is_pinned(data):
            self.logger.info(f"{data} is not pinned locally, downloading from IPFS swarm")
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

    def restart_ipfs_service(self):
        try:
            result = subprocess.run(
                ['systemctl', 'restart', 'ipfs'],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.logger.info("IPFS service restarted successfully.")
            self.connect()
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

    def pin_add(self, hash):
        if not self.connected:
            return
        try:
            self._api_call('pin/add', params={'arg': hash})
        except Exception as e:
            error_message = str(e).lower()
            if 'not pinned' in error_message or 'pinned indirectly' in error_message:
                return
            self.logger.info(f'error while adding pin')
            self.logger.error(e)
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
            self.connected = False
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

class Cache:
    def __init__(self, items_limit, filepath, store_type=OrderedDict):
        self.items_limit = items_limit
        self.filepath = filepath
        self.store_type = store_type
        try:
            if not os.path.exists(filepath):
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                raise
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

    def _reload_cache(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, 'r') as f:
                self.mem = self.store_type(json.load(f))
        else:
            self.mem = self.store_type({})

    def add(self, key, value):
        self.mem[key] = value
        if len(self.mem) == self.items_limit + 1:
            self.mem.popitem(last=False)
        self._update_file()
    def rem(self, key):
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
                        entries[item] = {'timestamp': current_time}
                    self._update_file(entries)
                    return entries
                elif isinstance(data, dict):
                    # Ensure all entries have a 'timestamp'
                    updated = False
                    for key, value in data.items():
                        if not isinstance(value, dict) or 'timestamp' not in value:
                            data[key] = {'timestamp': time.time()}
                            updated = True
                    if updated:
                        self._update_file(data)
                    return OrderedDict(data)
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
