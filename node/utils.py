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
    def __init__(self, ipfs_host, ipfs_port, ipfs_id, ipfs_timeout, client_connect_url, cache, logger, target):
        self.client_bootstrap_url = '/dns4/' + ipfs_host + '/tcp/' + str(ipfs_port) + '/ipfs/' + ipfs_id
        self.ipfs_host = ipfs_host
        self.ipfs_port = ipfs_port
        self.ipfs_id = ipfs_id
        self.ipfs_timeout = ipfs_timeout
        self.target = target
        self.client_connect_url = client_connect_url
        self.bootstrap_client = ipfshttpclient.connect(client_connect_url)
        self.bootstrap_client.bootstrap.add(self.client_bootstrap_url)
        self.bootstrap_client.config.set("Datastore.StorageMax", "3GB")
        args = ("Swarm.ConnMgr.LowWater", 25)        opts = {'json': 'true'}
        self.bootstrap_client._client.request('/config', args, opts=opts, decoder='json')
        self.logger = logger
        self.cache = cache

    def download(self, data):
        if self.cache.contains(data):
            return
        try:
            address = self.client_bootstrap_url
            args = (address, address)
            opts = {'json': 'true'}
            self.bootstrap_client._client.request('/swarm/connect', args, opts=opts, decoder='json')
            self.bootstrap_client.bootstrap.add(self.client_bootstrap_url)
            self.pin_add(data)
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
                self.bootstrap_client.bootstrap.add(self.client_bootstrap_url)
                response = self.bootstrap_client.add(data, timeout=self.ipfs_timeout)
                self.cache.add(data)
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
            raise ValueError(f"The paths '{hash}' or '{legacy_path}' do not exist.")

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
