from asyncio.log import logger
import json
import os
import shutil
import socket
import subprocess
import uuid
import math
import urllib.request
import time
import re
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

from collections import OrderedDict
from collections import deque

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
    def __init__(self, ipfs_host, ipfs_port, ipfs_id, ipfs_timeout, client_connect_url, gateway_url, cache, logger, target):
        self.client_bootstrap_url = '/dns4/' + ipfs_host + '/tcp/' + str(ipfs_port) + '/ipfs/' + ipfs_id
        self.ipfs_host = ipfs_host
        self.ipfs_port = ipfs_port
        self.ipfs_id = ipfs_id
        self.ipfs_timeout = ipfs_timeout
        self.target = target
        self.client_connect_url = client_connect_url
        self.logger = logger
        self.cache = cache
        self.gateway = gateway_url.rstrip('/')
        self.session = requests.Session()
        max_workers = 10
        adapter = HTTPAdapter(pool_connections=max_workers, pool_maxsize=max_workers)
        self.session.mount("https://", adapter)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        logger.info("Initializing ipfs connection");
        self.connect()
        if "127.0.0.1" in self.client_connect_url:
            logger.info("Setting up local IPFS")
            self.bootstrap_client.config.set("Datastore.StorageMax", "3GB")
            args = ("Swarm.ConnMgr.LowWater", 25)
            opts = {'json': 'true'}
            self.bootstrap_client._client.request('/config', args, opts=opts, decoder='json')

    def connect(self):
        attempt = 0
        while True:
            if attempt == 10:
                break
            try:
                self.bootstrap_client = ipfshttpclient.connect(self.client_connect_url)
                self.bootstrap_client.bootstrap.add(self.client_bootstrap_url)
                address = self.client_bootstrap_url
                args = (address, address)
                opts = {'json': 'true'}
                self.bootstrap_client._client.request('/swarm/connect', args, opts=opts, decoder='json')
                return True
            except Exception as e:
                self.logger.warning(f"Error communicating to ipfs: {e}")
                if "127.0.0.1" in self.client_connect_url:
                    self.logger.warning("Restarting IPFS service")
                    self.restart_ipfs_service()
                else:
                    self.logger.warning("Please make sure your IPFS host is working properly")
            attempt = attempt + 1
        raise Exception(f"{e}")

    def _http_request_with_retry(self,
                                 method: str,
                                 url: str,
                                 max_retries: int = 5,
                                 backoff_factor: float = 1.0,
                                 retry_on_status: tuple = (429, 500, 502, 503, 504),
                                 **kwargs) -> requests.Response:
        """
        Do a session.request(method, url, **kwargs), retrying on specified status codes
        and on connection-related exceptions.
        """
        import http.client
        from requests.exceptions import ConnectionError, Timeout

        delay = backoff_factor
        last_exc = None

        for attempt in range(1, max_retries + 1):
            try:
                resp = getattr(self.session, method)(url, **kwargs)
                # If status is OK, return immediately
                if resp.status_code not in retry_on_status:
                    resp.raise_for_status()
                    return resp

            except (ConnectionError, Timeout, http.client.RemoteDisconnected) as e:
                # Caught a retryable exception
                last_exc = e

            else:
                # Received a retryable status code
                last_exc = None

            # If this was our last attempt, raise appropriately
            if attempt == max_retries:
                if last_exc:
                    raise
                else:
                    resp.raise_for_status()

            # Otherwise, log and back off
            reason = f"exception {last_exc!r}" if last_exc else f"status {resp.status_code}"
            self.logger.warning(
                "[%s/%s] %s %s failed with %s; retrying in %.1fs …",
                attempt, max_retries, method.upper(), url,
                reason, delay
            )
            time.sleep(delay)
            delay *= 2

        # Should not get here
        raise RuntimeError("Exceeded max retries in _http_request_with_retry")


    def is_ipfs_folder(self, path: str) -> bool:
        """
        Return True if accessing /ipfs/<path>/ on the gateway yields an HTML
        directory listing (i.e. it’s a folder), False otherwise.
        Retries the request up to 10 times if status is not 200.
        """
        url = f"{self.gateway}/ipfs/{path}/"

        for attempt in range(10):
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    if  '<a href="/ipfs/' in resp.text:
                        self.logger.info(f"IPFS path is a folder")
                    return '<a href="/ipfs/' in resp.text
            except requests.RequestException:
                pass
            time.sleep(1)  # brief pause before retry

        raise Exception(f"Unable to determine if {path} is file or folder")

    def download_file(self, path: str, out_path: str) -> None:
        """
        Fast, streaming copy of a single file via gateway.
        """
        try:
            url = f"{self.gateway}/ipfs/{path}"
            resp = self._http_request_with_retry("get", url, stream=True, timeout=60)
            resp.raw.decode_content = True

            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "wb") as f:
                shutil.copyfileobj(resp.raw, f)
        except Exception as e:
            raise Exception(f"{e}")

        self.logger.debug(f"Downloaded file {out_path}")

    def download_folder(self, cid: str, dest_dir: str) -> bool:
        """
        BFS-crawl the HTML indexes to build a flat list of all file tasks,
        then download them in parallel. Retry failed downloads up to 10 times.
        Returns True if all files downloaded successfully, False otherwise.
        """
        os.makedirs(dest_dir, exist_ok=True)
        queue = deque([(cid, dest_dir)])
        file_tasks: list[tuple[str, str]] = []

        # 1) Crawl directory structure via HTML
        while queue:
            prefix, local_path = queue.popleft()
            index_url = f"{self.gateway}/ipfs/{prefix}/"
            resp = self._http_request_with_retry("get", index_url, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")

            for link in soup.find_all("a", href=True):
                href = link["href"]
                if not href.startswith(f"/ipfs/{prefix}/"):
                    continue
                name = href.rstrip("/").split("/")[-1]
                if name in ("..", ""):
                    continue

                child_prefix = f"{prefix}/{name}"
                child_path = os.path.join(local_path, name)

                # HEAD to detect folder vs file
                head = self.session.head(
                    f"{self.gateway}/ipfs/{child_prefix}/",
                    allow_redirects=True, timeout=5
                )
                if head.ok and "text/html" in head.headers.get("Content-Type", ""):
                    os.makedirs(child_path, exist_ok=True)
                    queue.append((child_prefix, child_path))
                else:
                    file_tasks.append((child_prefix, child_path))

        # 2) Download all files in parallel with retries
        def _download_with_retries(prefix: str, out_path: str) -> bool:
            for attempt in range(1, 11):
                try:
                    self.download_file(prefix, out_path)
                    return True
                except Exception as e:
                    self.logger.warning(
                        "Attempt %d/10 failed for %s → %s: %s",
                        attempt, prefix, out_path, e
                    )
            # All retries failed
            self.logger.error(
                "All 10 retries failed for %s → %s", prefix, out_path
            )
            return False

        futures = {
            self.executor.submit(_download_with_retries, p, o): (p, o)
            for p, o in file_tasks
        }
        all_success = True
        for fut in as_completed(futures):
            prefix, out_path = futures[fut]
            success = fut.result()
            if not success:
                all_success = False

        return all_success

    def fetch_ipfs_content(self, cid: str, output: str = None) -> None:
        """
        Detect if CID is a file or folder and download accordingly.
        """
        if output is None:
            output = cid
        if self.is_ipfs_folder(cid):
            self.logger.info(f"{cid} is a folder; downloading into ./{output}/")
            os.makedirs(output, exist_ok=True)
            self.download_folder(cid, output)
        else:
            self.logger.info(f"{cid} is a file; downloading as {output}")
            self.download_file(cid, output)



    def download(self, data):
        if self.cache.contains(data):
            self.logger.info(f"{data} found in local cache, skipping download")
            return

        if not self.is_pinned(data) and self.gateway is not None:
            try:
                self.logger.info(f"{data} is not pinned locally, downloading from IPFS gateway")
                self.fetch_ipfs_content(data)
                self.bootstrap_client.add(data, recursive=True, timeout=self.ipfs_timeout)
                self.pin_add(data)
                self.cache.add(data)
                return
            except Exception as e_remote:
                self.logger.warning(
                    f"Remote IPFS fetch failed for {data}: {e_remote}, falling back…"
                )

        try:
            address = self.client_bootstrap_url
            if not self.is_pinned(data):
                self.logger.info(f"{data} downloading from IPFS swarm")
                args = (address, address)
                opts = {'json': 'true'}
                self.bootstrap_client._client.request('/swarm/connect', args, opts=opts, decoder='json')
                self.bootstrap_client.bootstrap.add(self.client_bootstrap_url)
                self.pin_add(data)
                self.bootstrap_client.get(data, target=self.target, compress=True, opts={"compression-level": 9}, timeout=self.ipfs_timeout)
            else:
                self.logger.info(f"{data} is pinned locally, from local IPFS")
                self.bootstrap_client.get(data, target=self.target, compress=True, opts={"compression-level": 9}, timeout=self.ipfs_timeout)

            self.cache.add(data)
        except Exception as e:
            self.logger.warning(f"Error while downloading file {data}: {e}")
            if "127.0.0.1" in self.client_connect_url:
                self.logger.warning("Restarting IPFS service")
                self.restart_ipfs_service()
            else:
                self.logger.warning("Please make sure your IPFS host is working properly")
            raise

    def restart_ipfs_service(self):
        try:
            # Execute the systemctl command to restart the ipfs service
            result = subprocess.run(
                ['systemctl', 'restart', 'ipfs'],
                check=True,        # Raises CalledProcessError if the command exits with a non-zero status
                stdout=subprocess.PIPE,  # Capture standard output
                stderr=subprocess.PIPE   # Capture standard error
            )
            self.logger.info("IPFS service restarted successfully.")
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
        attempt = 0
        while True:
            if attempt == 10:
                break
            try:
                address = self.client_bootstrap_url
                args = (address, address)
                opts = {'json': 'true'}

                self.bootstrap_client._client.request('/swarm/connect', args, opts=opts, decoder='json')
                self.bootstrap_client.bootstrap.add(self.client_bootstrap_url)
                response = self.bootstrap_client.add(data, timeout=self.ipfs_timeout)
                self.cache.add(response['Hash'])
                return response['Hash']
            except Exception as e:
                self.logger.warn(f"Error while uploading: {e}")
                if "127.0.0.1" in self.client_connect_url:
                    self.logger.warning("Restarting IPFS service")
                    self.restart_ipfs_service()
            attempt += 1
        raise Exception(f"Error while uploading: {e}")

    def add(self, hash):
        pass

    def pin_add(self, hash):
        try:
            self.bootstrap_client.pin.add(hash)
        except Exception as e:
            error_message = str(e).lower()
            if 'not pinned' in error_message or 'pinned indirectly' in error_message:
                return
            self.logger.info(f'error while adding pin')
            self.logger.error(e)
            raise

        pass

    def pin_rm(self, hash):
        try:
            self.bootstrap_client.pin.rm(hash)
        except Exception as e:
            error_message = str(e).lower()
            if 'not pinned' in error_message or 'pinned indirectly' in error_message:
                return
            self.logger.info(f'error while removing pin')
            self.logger.error(e)
            raise

    def is_pinned(self, cid: str) -> bool:
        """
        Check whether a given CID is pinned on this IPFS node.

        Returns:
          True   – if pinned (directly or indirectly)
          False  – if not pinned at all

        Raises:
          Any unexpected exception (e.g. network issues) after logging it.
        """
        try:
            # Try to list the pin status for just this CID
            self.bootstrap_client.pin.ls(cid)
            # No exception → it's pinned
            return True

        except Exception as e:
            err = str(e).lower()

            # Known “not pinned” error
            if 'not pinned' in err:
                return False

            # “pinned indirectly” is still “pinned”
            if 'pinned indirectly' in err:
                return True

            # Anything else is unexpected
            self.logger.info(f'Unexpected error while checking pin status for {cid}')

            if "127.0.0.1" in self.client_connect_url:
                self.logger.warning("Restarting IPFS service")
                self.restart_ipfs_service()

            self.logger.error(e)
            raise

    def mig(self, hash, base_path):
        prefix = "Qm"

        legacy_path = hash
        target_path = base_path / hash
        # Validate the target_directory

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
        """
        Delete all files and directories within the specified directory whose names start with 'Qm'.

        Args:
            hash (str): The path to the file or directory to clean up.

        Raises:
            ValueError: If the target_directory does not exist or is not a directory.
            Exception: Re-raises any unexpected exceptions encountered during deletion.
        """

        # Define the prefix to look for
        prefix = "Qm"

        legacy_path = "../" + hash

        # Validate the target_directory
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

        # Iterate over all items in the target directory
        if os.path.isdir(target_directory):
          for item_name in os.listdir(target_directory):
            if item_name.startswith(prefix):
                item_path = os.path.join(target_directory, item_name)

                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        # If it's a file or a symbolic link, remove it
                        os.remove(item_path)

                    elif os.path.isdir(item_path):
                        # If it's a directory, remove it and all its contents
                        shutil.rmtree(item_path)

                except Exception as e:
                    # Log any other exceptions and re-raise
                    self.logger.error(f"Error while removing '{item_path}': {e}")
                    raise

        #Deleting legacy cache file
        target_directory = "../" + hash

        # Iterate over all items in the target directory
        if os.path.isdir(target_directory):
          for item_name in os.listdir(target_directory):
            if item_name.startswith(prefix):
                item_path = os.path.join(target_directory, item_name)

                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        # If it's a file or a symbolic link, remove it
                        os.remove(item_path)

                    elif os.path.isdir(item_path):
                        # If it's a directory, remove it and all its contents
                        shutil.rmtree(item_path)

                except Exception as e:
                    # Log any other exceptions and re-raise
                    self.logger.error(f"Error while removing '{item_path}': {e}")
                    raise

        self.cache.rem(hash)


    def repo_gc(self):
        try:
            self.bootstrap_client.repo.gc()
        except Exception as e:
            self.logger.info(f'error while removing pin')
            self.logger.error(e)
            raise



class Cache:
    def __init__(self, items_limit, filepath, store_type=OrderedDict):
        self.items_limit = items_limit
        self.filepath = filepath
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

    def add(self, key, value):
        self.mem[key] = value
        if len(self.mem) == self.items_limit + 1:
            self.mem.popitem(last=False)
        self._update_file()

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
        return math.floor(psutil.virtual_memory()[1] / (2 ** 30))  # in GB

    @staticmethod
    def get_free_storage():
        return psutil.disk_usage("/")[2] // (2 ** 30)  # in GB



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

