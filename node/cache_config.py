from pathlib import Path
from os.path import expanduser
import os

class CacheConfig:
    def __init__(self, base_dir):
        base_path = Path(__file__).parent / base_dir
        base_path.mkdir(parents=True, exist_ok=True)
        
        # Define all file paths
        self.base_path = base_path
        self.auto_update_file_path = base_path / 'auto_update.etny'
        self.heart_beat_log_file_path = base_path / 'heartbeat.etny'
        self.network_cache_filepath = Path(__file__).parent / 'network_cache.txt'
        self.ipfs_version_filepath = Path(__file__).parent / 'ipfs_version.txt'
        self.orders_cache_filepath = base_path / 'orders_cache.txt'
        self.ipfs_cache_filepath = base_path / 'ipfs_cache.txt'
        self.dpreq_filepath = base_path / 'dpreq_cache.txt'
        self.doreq_filepath = base_path / 'doreq_cache.txt'
        self.merged_orders_cache = base_path / 'merged_orders_cache.json'
        self.process_orders_cache_filepath = base_path / 'process_order_data.json'
        
        # Define all cache limits
        self.network_cache_limit = 1
        self.ipfs_version_cache_limit = 10_000
        self.orders_cache_limit = 10_000_000
        self.ipfs_cache_limit = 10_000_000
        self.dpreq_cache_limit = 10_000_000
        self.doreq_cache_limit = 10_000_000
        self.merged_orders_cache_limit = 10_000_000
