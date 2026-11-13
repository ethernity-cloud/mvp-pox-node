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
        # ESR/protocol replication progress, per network: the scan resume
        # points (last scanned block, last scanned order) and the last pinned
        # version per (enclave, key), so replication resumes there on restart.
        self.esr_progress_filepath = base_path / 'esr_progress.json'
        # The passed integration tests and their capability records
        # (capability.RecordStore).
        self.capability_filepath = base_path / 'capability.json'
        # The block cursors of the idle tick's log scans (DPRequestCancelled
        # and the broker's events), {scan name: next block to scan}.
        self.broker_cursor_filepath = base_path / 'broker_cursor.json'
        # The gas this node's DP requests, placements and started rows used
        # (gas_profile.GasProfile), priced into its tip.
        self.gas_profile_filepath = base_path / 'gas_profile.json'
        # The --handover command's request and the running agent's answer
        # (broker.handover_request_path).
        self.handover_request_filepath = base_path / 'handover.json'
        # The handover this node accepted and the predecessor's cu, until the
        # successor is placed ({orderId, predecessor_cu, reservation}).
        self.accepted_handover_filepath = base_path / 'accepted.json'
        # The farm invitation a broker refused (broker.farm_refusal_path).
        self.farm_refusal_filepath = base_path / 'farm.json'

        # Define all cache limits
        self.network_cache_limit = 1
        self.ipfs_version_cache_limit = 10_000
        self.orders_cache_limit = 10_000_000
        self.ipfs_cache_limit = 10_000_000
        self.dpreq_cache_limit = 10_000_000
        self.doreq_cache_limit = 10_000_000
        self.merged_orders_cache_limit = 10_000_000
