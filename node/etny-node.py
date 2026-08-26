#!/usr/bin/python3

import io, os, time, json, sys, argparse, threading
import base64, hashlib
from types import SimpleNamespace
from collections import defaultdict
import concurrent.futures
import shutil
from pathlib import Path

import logging
import config

from eth_account import Account
from web3 import Web3
from web3 import exceptions
from web3.middleware import ExtraDataToPOAMiddleware
from web3 import middleware
from web3.gas_strategies.time_based import fast_gas_price_strategy
from web3.gas_strategies.rpc import rpc_gas_price_strategy

from utils import get_or_generate_uuid, run_subprocess, retry, Storage, Cache, ListCache, ListCacheWithTimestamp, MergedOrdersCache, subprocess, get_node_geo, HardwareInfoProvider, parse_transaction_bytes_ut
from models import *
from error_messages import errorMessages
from swift_stream_service import SwiftStreamService
from cache_config import CacheConfig

logger = config.logger 
task_running_on = None
task_lock = threading.Lock()
# SGX integration-test coordination, per network_type (TESTNET / MAINNET).
#
# The test verifies the trustedzone enclave's result, which is identical across
# all networks of a type -- so it must run to success exactly ONCE per type, and
# every other same-type network must WAIT for that outcome and then skip (never
# run its own, and never re-run on later processing-loop cycles).
#
#   integration_test_lock    Reentrant. Held across the whole check-and-run so
#                            only one test runs at a time; reentrant because
#                            __run_integration_test -> set_integration_test_complete
#                            re-acquires it on the same thread.
#   integration_test_done    Per-type Event, SET once that type's test passes.
#                            Waiters block on it instead of skipping unresolved,
#                            and once set no network ever runs or re-runs the
#                            test for that type again (survives the per-network
#                            resilient_process loop re-creating EtnyPoXNode).
integration_test_lock = threading.RLock()
integration_test_complete = {'MAINNET': False, 'TESTNET': False}
integration_test_done = {'MAINNET': threading.Event(), 'TESTNET': threading.Event()}

# process_network re-creates EtnyPoXNode every processing-loop cycle, so the ESR
# replication background thread must be launched exactly ONCE per network per
# process, not once per cycle. Guard the launch with a per-network flag under a
# lock so re-created instances don't each spawn a new (leaking) thread.
_esr_replication_lock = threading.Lock()
_esr_replication_started = set()  # network names whose replication thread is running
_esr_replication_slots = [0]      # monotonically increasing stagger counter


def _esr_replication_stagger_slot(step_seconds=8):
    """Return an increasing per-launch delay so replication handles for the
    different networks do not all construct at the same instant."""
    with _esr_replication_lock:
        slot = _esr_replication_slots[0]
        _esr_replication_slots[0] += 1
    return slot * step_seconds

stop_event = threading.Event()

class NetworkLoggerAdapter(logging.LoggerAdapter):
    def __init__(self, logger, network):
        super().__init__(logger, {})
        self.network = network

    def process(self, msg, kwargs):
        """
        Prepend the network name to the log message.
        """
        return f"[{self.network}] {msg}", kwargs

class EtnyPoXNode:
    logger = None

    def __init__(self, network, replication_only=False):

        # replication_only: build just enough to mirror this network's results +
        # ESR state (contracts, storage, IPFS) and skip everything that only the
        # task-executing instance needs -- above all the SGX integration test, so
        # a replication handle never runs or waits on it.
        self.__replication_only = replication_only

        self.__address = None
        self.__privatekey = None
        self.__resultaddress = None
        self.__resultprivatekey = None
        self.__cpu = None
        self.__memory = None
        self.__storage = None
        self.__bandwidth = None
        self.__duration = None
        self.__endpoint = None
        self.__access_key = None
        self.__secret_key = None
        self.__network = None
        self.__ipfs_swarm = None
        self.__ipfs_connect_url = None
        self.__ipfs_gateway_url = None
        self.__ipfs_timeout = None
        self.__kubo_url = None
        self.__kubo_version = None
        self.__price = None
        self.__orders = defaultdict(lambda: None)
        self.__do_requests_build_pending = True


        self.parse_arguments(config.arguments, config.parser)
        self.__network = network.name
        self.logger = NetworkLoggerAdapter(config.logger, self.__network)
        logger = self.logger

        if stop_event.is_set():
            return


        logger.info(f"Initializing Ethernity CLOUD Agent v{config.version}");

        logger.info(f"Configured network is: {self.__network}")
        self.__network_config = network
        self.__price = int(network.task_execution_price);

        try:
            with open(config.abi_filepath) as f:
                self.__contract_abi = f.read()

            self.__w3 = Web3(Web3.HTTPProvider(self.__network_config.rpc_url, request_kwargs={'timeout': 120}))

            if network.middleware is not None:
                self.__w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            self.__acct = Account.from_key(self.__privatekey)
            self.__address = self.__acct.address
            self.__etny = self.__w3.eth.contract(
                address=self.__w3.to_checksum_address(self.__network_config.contract_address),
                abi=self.__contract_abi
            )

            balance = self.__w3.eth.get_balance(self.__address)

            # A replication-only handle never sends transactions, so it must not
            # block on the gas-wait loop: it should keep mirroring state even on
            # a gas-starved network (in fact, especially then).
            if not self.__replication_only and balance < int(network.minimum_gas_at_start):
               logger.error(f"Not enough gas at {self.__address} to run node agent, wating until enough balance is set")

               while not stop_event.is_set() and balance < int(network.minimum_gas_at_start):
                   balance = self.__w3.eth.get_balance(self.__address)
                   time.sleep(600)

               if stop_event.is_set():
                   return
               
        except Exception as e:
            logger.info(f"Error: {e}")
            raise Exception(e)


        self.__node_geo = get_node_geo();
        self.__number_of_cpus = int(HardwareInfoProvider.get_number_of_cpus());
        self.__free_memory = int(HardwareInfoProvider.get_free_memory());
        self.__free_storage = int(HardwareInfoProvider.get_free_storage());

        with open(config.image_registry_abi_filepath) as f:
            self.__image_registry_abi = f.read()

        with open(config.heart_beat_abi_filepath) as f:
            self.__heart_beat_abi = f.read()

        self.__image_registry = self.__w3.eth.contract(
            address=self.__w3.to_checksum_address(self.__network_config.image_registry_contract_address),
            abi=self.__image_registry_abi)
        self.__heart_beat = self.__w3.eth.contract(
            address=self.__w3.to_checksum_address(self.__network_config.heartbeat_contract_address),
            abi=self.__heart_beat_abi)

        # Enclave State Registry (optional): only wired when this network has a
        # deployment. Absent => state replication is skipped entirely, so a
        # network without an ESR behaves exactly as before.
        self.__esr = None
        self.__esr_last_block = 0
        try:
            # Network names are lowercase at runtime (e.g. "bloxberg_testnet")
            # but the config keys are uppercase -- look up case-insensitively so
            # the ESR actually resolves (a plain .get() silently returned None,
            # which is why "No ESR deployed" was logged even where one exists).
            esr_address = (config.esr_contract_addresses.get(
                (self.__network_config.name or "").upper()) or "").strip()
            if esr_address:
                with open(config.esr_abi_filepath) as f:
                    esr_abi = f.read()
                self.__esr = self.__w3.eth.contract(
                    address=self.__w3.to_checksum_address(esr_address),
                    abi=esr_abi)
                logger.info(f"Enclave State Registry: {esr_address}")
            else:
                logger.info(f"No Enclave State Registry deployed on {self.__network_config.name}; state replication disabled")
        except Exception as e:
            # Never let ESR wiring stop the node from starting -- it is an
            # add-on to replication, not part of task execution.
            self.__esr = None
            logger.warning(f"Could not initialise the Enclave State Registry: {e}")

        # ethernity-cas SessionRegistry -- same wiring pattern as the ESR
        # above: resolved per network, and never allowed to stop the node.
        self.__cas_session_registry = None
        self.__cas_sessreg_last_block = 0
        try:
            sr_address = (config.cas_session_registry_addresses.get(
                (self.__network_config.name or "").upper()) or "").strip()
            if sr_address:
                # Minimal inline ABI: the record(bytes32) auto-getter is all
                # replication needs (events are matched by topic, not ABI).
                sr_abi = ('[{"type":"function","name":"record","stateMutability":"view",'
                          '"inputs":[{"name":"sessionHash","type":"bytes32"}],'
                          '"outputs":[{"type":"bytes32"},{"type":"address"},'
                          '{"type":"string"},{"type":"uint32"},{"type":"string"},'
                          '{"type":"string"},{"type":"uint8"},{"type":"uint64"},'
                          '{"type":"bool"}]}]')
                self.__cas_session_registry = self.__w3.eth.contract(
                    address=self.__w3.to_checksum_address(sr_address), abi=sr_abi)
                logger.info(f"CAS Session Registry: {sr_address}")
            else:
                logger.info(f"No CAS Session Registry deployed on {self.__network_config.name}; session replication disabled")
        except Exception as e:
            self.__cas_session_registry = None
            logger.warning(f"Could not initialise the CAS Session Registry: {e}")

        self.__nonce = self.__w3.eth.get_transaction_count(self.__address)
        self.__dprequest = 0
        self.__order_id = 0
        self.__total_nodes_count = 0
        self.__is_first_cycle = defaultdict(lambda: True)
        self.can_run_under_sgx = False

        logger.info(f"NodeID: {self.__address}");
        logger.info(f"Network: {self.__network}");
        logger.info(f"RPC URL: {self.__network_config.rpc_url}");
        logger.info(f"ChainID: {self.__network_config.chain_id}");
        logger.info(f"Protocol Contract Address: %s", self.__network_config.contract_address);
        logger.info(f"Heartbeat Contract Address: %s", self.__network_config.heartbeat_contract_address);
        logger.info(f"Image Registry Address: %s", self.__network_config.image_registry_contract_address);
        logger.info(f"Minimum reward for order processing: %d %s / hour", self.__price, self.__network_config.token_name);
        logger.info(f"IPFS Connect URL: %s", self.__ipfs_connect_url);
        logger.info(f"IPFS Gateway URL: %s", self.__ipfs_gateway_url);
        logger.info(f"Node number of cpus: %s", self.__number_of_cpus);
        logger.info(f"Node free memory: %s", self.__free_memory);
        logger.info(f"Node free storage: %s", self.__free_storage);
        logger.info(f"Node geo: %s", self.__node_geo);


        [enclave_image_hash, _, docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey(self.__network_config.integration_test_image, 'v3')
        logger.info(f"Docker registry hash: {enclave_image_hash}")
        logger.info(f"Docker composer hash: {docker_compose_hash}")

        self.cache_config = CacheConfig(network.name)
        self.network_cache = Cache(self.cache_config.network_cache_limit, self.cache_config.network_cache_filepath)
        self.ipfs_version_cache = Cache(self.cache_config.ipfs_version_cache_limit, self.cache_config.ipfs_version_filepath)

        if self.network_cache.get("NETWORK") == "BLOXBERG" and self.__network == "bloxberg_mainnet":
           self.__migrate_cache()
           self.network_cache.add("NETWORK","MIGRATED_FROM_BLOXBERG")

        if self.network_cache.get("NETWORK") == "TESTNET" and self.__network == "bloxberg_testnet":
           self.__migrate_cache()
           self.network_cache.add("NETWORK","MIGRATED_FROM_TESTNET")

        if self.network_cache.get("NETWORK") == "POLYGON" and self.__network == "polygon_mainnet":
           self.__migrate_cache()
           self.network_cache.add("NETWORK","MIGRATED_FROM_POLYGON")

        # task_running_on is a single global mutex that serializes the ONE
        # task-executing instance. A replication-only handle executes no tasks,
        # so it must NOT wait on or take this mutex -- otherwise it blocks here
        # forever while the processing instances keep re-acquiring it.
        if not self.__replication_only:
            while get_task_running_on() is not None:
               time.sleep(1)
            set_task_running_on(self.__network)

        os.chdir(self.cache_config.base_path)


        logger.info(f"Initializing swift-stream service")

        self.swift_stream_service = SwiftStreamService(logger, self.__endpoint,
                                                       self.__access_key,
                                                       self.__secret_key)

        self.orders_cache = Cache(self.cache_config.orders_cache_limit, self.cache_config.orders_cache_filepath)
        self.dpreq_cache = ListCache(self.cache_config.dpreq_cache_limit, self.cache_config.dpreq_filepath)
        self.doreq_cache = ListCache(self.cache_config.doreq_cache_limit, self.cache_config.doreq_filepath)
        self.ipfs_cache = ListCacheWithTimestamp(self.cache_config.ipfs_cache_limit, self.cache_config.ipfs_cache_filepath)
        # Resumable replication progress (per network): last scanned block/order
        # and last pinned version per (enclave,key). Loaded from disk so a
        # restart resumes at the right height instead of re-scanning from zero.
        self.esr_progress = Cache(10_000_000, self.cache_config.esr_progress_filepath)
        self.__esr_last_block = int(self.esr_progress.get('esr_last_block') or 0)

        self.storage = Storage(self.__ipfs_swarm, self.__ipfs_timeout, self.__ipfs_connect_url, self.__ipfs_gateway_url,
                               self.ipfs_cache, self.ipfs_version_cache, logger, self.cache_config.base_path, self.__kubo_url,
                               self.__kubo_version, self.__network)

        self.merged_orders_cache = MergedOrdersCache(self.cache_config.merged_orders_cache_limit, self.cache_config.merged_orders_cache)
        self.process_order_data = {}


        self.__uuid = get_or_generate_uuid(config.uuid_filepath)

        # SGX integration test policy, per network_type (TESTNET / MAINNET):
        #
        #   * The test verifies the trustedzone enclave produces the expected
        #     result, and that result is identical across all networks of the
        #     same type -- so completion is tracked per type, not per network.
        #   * The test must be SERIALIZED: only one integration test runs at a
        #     time (running two enclave tests concurrently fights over the same
        #     docker/swift-stream integration scaffolding and SGX device).
        #   * FIRST SUCCESS WINS: as soon as one network of a type passes, every
        #     remaining same-type network skips the test and just records the
        #     capability.
        #   * ON FAILURE, TRY THE NEXT: a failing network leaves the type flag
        #     False, so the next same-type thread acquires the lock and attempts
        #     it, until one succeeds.
        #
        # integration_test_lock (held across the whole check-and-run below)
        # provides the serialization; re-checking completion INSIDE the lock is
        # what makes it first-success-wins rather than every-thread-runs.
        if self.__replication_only:
           # A replication-only handle never executes tasks, so it neither runs
           # nor waits on the SGX integration test.
           self.can_run_under_sgx = False
        elif config.skip_integration_test == True:
           logger.warning('Agent skipped SGX integration test, SGX capabilitties overwritten by configuration')
           self.can_run_under_sgx = True
        else:
           net_type = self.__network_config.network_type.upper()
           done_event = integration_test_done[net_type]

           # Fast path: this type's test already passed (this run, on any network,
           # including an earlier cycle of THIS network). Never run or re-run it.
           if done_event.is_set():
               logger.info('SGX integration test completed already')
               self.can_run_under_sgx = True
           else:
               # Serialize: only one integration test runs at a time. A network
               # that arrives while another is mid-test blocks here on the lock
               # rather than skipping unresolved.
               with integration_test_lock:
                   if done_event.is_set():
                       # The network that held the lock before us passed -- the
                       # capability is proven for the whole type, so skip.
                       logger.info('SGX integration test completed already')
                       self.can_run_under_sgx = True
                   else:
                       # We hold the lock and the type is still unproven: run it.
                       # On success __run_integration_test marks it done (which
                       # sets done_event, permanently unblocking + skipping every
                       # other same-type network). On failure it leaves it unset
                       # and the next thread to take the lock attempts it.
                       self.__run_integration_test()


        # Tracks when the pin cleanup last ran, so __maybe_clear_ipfs_cache can
        # throttle the periodic sweep. Set before the first run so the attribute
        # always exists.
        self.__last_ipfs_cleanup_at = 0
        if not self.__replication_only:
            # The processing instance runs the cleanup/replication sweep at
            # startup. A replication-only handle skips it here (its own loop does
            # the replication) so construction stays fast and never blocks on an
            # IPFS download during startup.
            self.__clear_ipfs_cache()
            reset_task_running_on()

          
    def __migrate_cache(self):
        logger = self.logger

        logger.info(f"Migrating cache from legacy cache {self.network_cache.get('NETWORK')} to network dir {self.__network}")
        self.cache_config_legacy = CacheConfig('./')
        self.ipfs_cache_legacy = ListCache(self.cache_config_legacy.ipfs_cache_limit, self.cache_config_legacy.ipfs_cache_filepath)

        self.storage_legacy = Storage(self.__ipfs_swarm, self.__ipfs_timeout, self.__ipfs_connect_url, self.__ipfs_gateway_url,
                               self.ipfs_cache_legacy, self.ipfs_version_cache, logger, self.cache_config_legacy.base_path,
                               self.__kubo_url, self.__kubo_version, self.__network)

        for hash in list(self.ipfs_cache_legacy.get_values):
            self.storage_legacy.mig(hash, self.cache_config.base_path)

        for attr in dir(self.cache_config_legacy):
            if attr.startswith('_'):
                continue

            if attr == 'base_path':
                continue

            if attr == 'network_cache_filepath':
                continue

            legacy_path = getattr(self.cache_config_legacy, attr, None)

            if isinstance(legacy_path, Path) and legacy_path.is_absolute():
                src = getattr(self.cache_config_legacy, attr)
                dest = getattr(self.cache_config, attr)

            try:
                shutil.copy2(src, dest)
                logger.debug(f"Copied '{src}' to '{dest}'")
            except FileNotFoundError:
                logger.warning(f"Source file '{src}' does not exist and was skipped.")
            except Exception as e:
                logger.error(f"Failed to copy '{src}' to '{dest}': {e}")
       

    @staticmethod
    def __looks_like_cid(value):
        """True for values that plausibly are IPFS CIDs.

        The contract accepts any non-empty string as the pointer, so a buggy
        writer can commit something that is not a CID at all -- the live registry
        currently has one entry holding a 0x… digest. That is a defect in
        whatever wrote it, NOT a format to support: such entries are skipped and
        logged, never pinned. Handing one to pin/add errors on every cleanup
        pass, and a client would retry-loop on it forever.

        CIDv0 is 46 chars starting "Qm"; CIDv1 is base32 starting "b".
        """
        cid = (value or "").strip()
        if not cid or cid.startswith("0x"):
            return False
        if cid.startswith("Qm") and len(cid) == 46:
            return True
        if cid.startswith("b") and len(cid) >= 46 and cid.islower():
            return True
        return False

    def __esr_current_state_cids(self):
        """CIDs that are the CURRENT state for some enclave/key in the registry.

        Scans StateCommitted events, then confirms each (enclave, key) against
        the contract's live getState. Confirming matters: the newest event we
        happened to see is not necessarily the newest commit, and pinning
        decisions must follow the chain, not our scan window.

        Returns a set of CIDs, empty when ESR is unavailable for any reason --
        an empty set means "protect nothing extra", never "unpin everything".
        """
        # Records for EVERY enclave/key seen in the registry -- replication is a
        # network-wide durability service, so we mirror the state of all
        # enclaves that committed to the ESR, not only enclaves whose tasks
        # happen to run on this node. Each record is
        # {enclave, key, cid, version}; enclave IS the ESR identity address
        # (the enclave signs commits with its identity key, so acct.address ==
        # the ESR address), which makes (enclave, key) the unambiguous identity
        # under which progress (last-pinned version) is tracked.
        if self.__esr is None:
            return []
        # Preferred: enumerate the registry directly (upgraded contract). One
        # batched call per page gives every enclave/key with its current
        # cid/version -- no event scanning, no block-range limits, no cursors to
        # keep. Falls back to the log scan when the deployed contract predates
        # the enumeration API.
        records = self.__esr_records_via_enumeration()
        if records is not None:
            return records
        return self.__esr_records_via_log_scan()

    def __esr_records_via_enumeration(self):
        """Full registry via entryCount()/getEntriesFrom(). None if unsupported."""
        try:
            total = int(self.__esr.caller().entryCount())
        except Exception:
            return None  # old contract: no enumeration API -> caller falls back
        records = []
        page = 200
        start = 0
        while start < total:
            try:
                time.sleep(self.__network_config.rpc_delay / 1000)
                enclaves, keys, cids, versions, updated_ats, _tot = \
                    self.__esr.caller().getEntriesFrom(start, page)
            except Exception as e:
                self.logger.warning(f"ESR getEntriesFrom({start}) failed: {e}")
                break
            got = len(enclaves)
            if got == 0:
                break
            for i in range(got):
                cid = cids[i]
                version = int(versions[i])
                if version and self.__looks_like_cid(cid):
                    key = keys[i]
                    records.append({
                        'enclave': enclaves[i],
                        'key': key.hex() if hasattr(key, 'hex') else str(key),
                        'cid': cid,
                        'version': version,
                    })
            start += got
        return records

    def __esr_records_via_log_scan(self):
        """Fallback for pre-enumeration contracts: reconstruct from events."""
        records = []
        try:
            latest_block = self.__w3.eth.block_number
            from_block = self.__esr_last_block or max(0, latest_block - config.esr_scan_lookback_blocks)
            logs = self.__esr_state_committed_logs(from_block, latest_block)

            pairs = {(l['args']['enclave'], l['args']['key']) for l in logs}
            for enclave, key in pairs:
                try:
                    time.sleep(self.__network_config.rpc_delay / 1000)
                    cid, version, _updated_at = self.__esr.caller().getState(enclave, key)
                    if version and self.__looks_like_cid(cid):
                        records.append({
                            'enclave': enclave,
                            'key': key.hex() if hasattr(key, 'hex') else str(key),
                            'cid': cid,
                            'version': int(version),
                        })
                    elif version and cid:
                        self.logger.warning(
                            f"ESR entry for {enclave}/{key.hex()[:10]}… is not a valid CID "
                            f"({cid[:24]}…); skipping.")
                except Exception as e:
                    self.logger.debug(f"ESR getState failed for {enclave}/{key.hex()}: {e}")

            self.__esr_last_block = latest_block
            try:
                self.esr_progress.add('esr_last_block', latest_block)
            except Exception:
                pass
        except Exception as e:
            self.logger.warning(f"ESR state scan failed: {e}")
            return []
        return records

    def __esr_state_committed_logs(self, from_block, to_block, chunk=9000):
        """StateCommitted logs for [from_block, to_block], scanned in chunks.

        Some RPCs (e.g. LitVM) time out or cap the block range on a single
        get_logs over a large window. Chunking keeps each call small and lets a
        big lookback complete without a -32002 timeout.
        """
        out = []
        start = max(0, int(from_block))
        end = int(to_block)
        while start <= end:
            stop = min(start + chunk, end)
            out.extend(self.__esr.events.StateCommitted().get_logs(
                from_block=start, to_block=stop))
            start = stop + 1
        return out

    def __replicate_esr_state(self):
        """Pin the current ESR state of every enclave, while disk allows.

        Any node holding a state CID keeps that dApp's state available, so a
        dApp does not depend on the single node that produced it. Bounded by
        free disk (ESR_MIN_FREE_STORAGE_GB) so replication can never crowd out
        the storage the node needs to run tasks -- the node stops pinning new
        state rather than filling up.

        Never raises: replication is best-effort and must not disturb order
        processing.
        """
        if self.__esr is None:
            return set()
        try:
            records = self.__esr_current_state_cids()
            if not records:
                return set()

            pinned_versions = self.esr_progress.get('pinned_versions') or {}
            # Per-CID pin-attempt counter, PERSISTED across cycles/restarts. An
            # ESR state blob is produced by another node and must propagate to
            # ours; it may not be fetchable on the first try. Policy:
            #   - up to PINS_PER_CYCLE (2) fetch attempts PER replication cycle
            #     (a quick double-tap for propagation delay), then
            #   - keep trying on each subsequent cycle -- the counter persists,
            #     so retries accumulate across cycle restarts -- until
            #   - MAX_PIN_ATTEMPTS (10) total, after which the CID is marked
            #     FAILED and never retried (give up after a long time rather
            #     than hammering an unfetchable CID forever).
            PINS_PER_CYCLE = int(getattr(config, 'esr_pin_attempts_per_cycle', 2))
            MAX_PIN_ATTEMPTS = int(getattr(config, 'esr_pin_max_attempts', 10))
            pin_attempts = self.esr_progress.get('pin_attempts') or {}
            pin_failed = set(self.esr_progress.get('pin_failed') or [])

            pinned = 0
            kept = set()
            for rec in records:
                cid = rec['cid']
                kept.add(cid)
                # Progress key: enclave ESR address + state key. Skip an entry
                # whose version we have already pinned -- the CID is
                # content-addressed and immutable, so an unchanged version means
                # nothing new to pin. This is the resumable "ESR height" per
                # (enclave, key).
                prog_key = f"{rec['enclave']}/{rec['key']}"
                if pinned_versions.get(prog_key) == rec['version'] and self.storage.is_pinned(cid):
                    self.ipfs_cache.add(cid)  # keep it fresh against retention
                    continue

                # Already pinned (e.g. by serve_esr_state_pins on the producing
                # node): record progress and clear any attempt bookkeeping.
                if self.storage.is_pinned(cid):
                    self.ipfs_cache.add(cid)
                    pinned_versions[prog_key] = rec['version']
                    pin_attempts.pop(cid, None)
                    continue

                # Given up on this CID already -> do not retry.
                if cid in pin_failed:
                    continue

                free_gb = HardwareInfoProvider.get_free_storage()
                if free_gb < config.esr_min_free_storage_gb:
                    self.logger.warning(
                        f"Free storage {free_gb}GB is below ESR_MIN_FREE_STORAGE_GB "
                        f"({config.esr_min_free_storage_gb}GB); stopping state replication for this round"
                    )
                    break

                # Up to PINS_PER_CYCLE quick attempts this cycle, but never past
                # the persisted MAX_PIN_ATTEMPTS total.
                got = False
                for _ in range(PINS_PER_CYCLE):
                    if pin_attempts.get(cid, 0) >= MAX_PIN_ATTEMPTS:
                        break
                    pin_attempts[cid] = pin_attempts.get(cid, 0) + 1
                    try:
                        # Short per-attempt timeout: a not-yet-fetchable CID must
                        # fail fast rather than block the shared IPFS daemon for
                        # the full 600s and starve order-processing pins.
                        self.storage.pin_add(
                            cid,
                            timeout=int(getattr(config, 'esr_pin_attempt_timeout_seconds', 30)))
                        got = True
                        break
                    except Exception as e:
                        self.logger.debug(
                            f"ESR pin attempt {pin_attempts[cid]}/{MAX_PIN_ATTEMPTS} "
                            f"for {cid} failed: {e}")
                if got:
                    pinned += 1
                    self.ipfs_cache.add(cid)
                    pinned_versions[prog_key] = rec['version']
                    pin_attempts.pop(cid, None)
                elif pin_attempts.get(cid, 0) >= MAX_PIN_ATTEMPTS:
                    # Exhausted all retries across cycles -> mark failed, stop.
                    pin_failed.add(cid)
                    pin_attempts.pop(cid, None)
                    self.logger.warning(
                        f"ESR state {cid} not fetchable after {MAX_PIN_ATTEMPTS} "
                        f"attempts across cycles; marking failed and giving up.")

            try:
                self.esr_progress.add('pinned_versions', pinned_versions)
                self.esr_progress.add('pin_attempts', pin_attempts)
                self.esr_progress.add('pin_failed', list(pin_failed))
            except Exception:
                pass
            if pinned:
                self.logger.info(f"Replicated {pinned} ESR state object(s) across {len(records)} enclave/key entries")
            return kept
        except Exception as e:
            self.logger.warning(f"ESR state replication skipped: {e}")
            return set()

    def __replicate_protocol_results(self, scan_limit=None):
        """Pin the result CIDs of recent protocol-contract orders.

        Every completed order has a result recorded on-chain by
        _addResultToOrder in the form 'v<n>:<transaction_hex>:<result_cid>' (see
        build_result_format_v3). The result blob lives on IPFS at result_cid, and
        the same durability argument that applies to ESR state applies here: if
        only the producing node pinned it, the result disappears when that node
        does. Replicating recent result CIDs keeps them available network-wide.

        Best-effort and bounded (scan_limit recent orders, free-disk gated).
        Returns the set of CIDs it kept, so the caller can protect them from the
        retention sweep. Never raises.
        """
        kept = set()
        if self.__etny is None:
            return kept
        try:
            try:
                total = self.__etny.caller()._getOrdersCount()
                total = total.toNumber() if hasattr(total, 'toNumber') else int(total)
            except Exception as e:
                self.logger.debug(f"protocol replication: _getOrdersCount failed ({e})")
                return kept

            limit = scan_limit if scan_limit is not None else int(
                getattr(config, 'esr_result_scan_orders', 200))
            start = max(0, total - limit)
            for order_id in range(total - 1, start - 1, -1):
                free_gb = HardwareInfoProvider.get_free_storage()
                if free_gb < config.esr_min_free_storage_gb:
                    self.logger.warning(
                        f"Free storage {free_gb}GB below ESR_MIN_FREE_STORAGE_GB; "
                        f"stopping protocol-result replication for this round")
                    break
                try:
                    time.sleep(self.__network_config.rpc_delay / 1000)
                    result = self.__etny.caller()._getResultFromOrder(order_id)
                except Exception:
                    continue
                if not result:
                    continue
                # 'v<n>:<tx_hex>:<result_cid>' -- the CID is the last field.
                cid = result.rsplit(':', 1)[-1].strip()
                if not self.__looks_like_cid(cid):
                    continue
                try:
                    if not self.storage.is_pinned(cid):
                        # Short per-attempt timeout so an unfetchable result CID
                        # fails fast instead of blocking the shared IPFS daemon
                        # for the full 600s (see __replicate_esr_state).
                        self.storage.pin_add(
                            cid,
                            timeout=int(getattr(config, 'esr_pin_attempt_timeout_seconds', 30)))
                    self.ipfs_cache.add(cid)
                    kept.add(cid)
                except Exception as e:
                    self.logger.debug(f"Could not pin protocol result {cid}: {e}")
            if kept:
                self.logger.info(f"Replicated {len(kept)} protocol result object(s)")
            return kept
        except Exception as e:
            self.logger.warning(f"Protocol result replication skipped: {e}")
            return kept

    def __replicate_do_request_inputs(self, scan_limit=None):
        """Pin the IPFS objects referenced by recent DO requests.

        Every DO request carries colon-separated metadata whose fields
        reference the enclave image, docker-compose, encrypted challenge,
        encrypted payload and encrypted input CIDs. Only the wallet that
        created the request is guaranteed to have pinned them; if that
        runner's IPFS endpoint disappears, the task can no longer be
        reproduced or verified. Replicating recent requests keeps their
        objects available network-wide. All content is ciphertext and its
        integrity is bound by on-chain checksums, so pinning blindly is safe.

        Best-effort and bounded (scan_limit recent requests, free-disk
        gated). Returns the set of CIDs it kept so the caller can protect
        them from the retention sweep. Never raises.
        """
        kept = set()
        if self.__etny is None:
            return kept
        try:
            try:
                total = self.__etny.caller()._getDORequestsCount()
                total = total.toNumber() if hasattr(total, 'toNumber') else int(total)
            except Exception as e:
                self.logger.debug(f"do-request replication: _getDORequestsCount failed ({e})")
                return kept

            limit = scan_limit if scan_limit is not None else int(
                getattr(config, 'do_request_scan_requests', 200))
            start = max(0, total - limit)
            for req_id in range(total - 1, start - 1, -1):
                free_gb = HardwareInfoProvider.get_free_storage()
                if free_gb < config.esr_min_free_storage_gb:
                    self.logger.warning(
                        f"Free storage {free_gb}GB below ESR_MIN_FREE_STORAGE_GB; "
                        f"stopping do-request replication for this round")
                    break
                try:
                    time.sleep(self.__network_config.rpc_delay / 1000)
                    meta = self.__etny.caller()._getDORequestMetadata(req_id)
                except Exception:
                    continue
                # (downer, metadata1..metadata4): every colon-separated token
                # that looks like a CID is an IPFS object the request needs.
                for field in meta[1:]:
                    for token in str(field or '').split(':'):
                        cid = token.strip()
                        if not self.__looks_like_cid(cid) or cid in kept:
                            continue
                        try:
                            if not self.storage.is_pinned(cid):
                                # Short per-attempt timeout: fail fast on an
                                # unfetchable CID instead of blocking the
                                # shared IPFS daemon (see __replicate_esr_state).
                                self.storage.pin_add(
                                    cid,
                                    timeout=int(getattr(config, 'esr_pin_attempt_timeout_seconds', 30)))
                            self.ipfs_cache.add(cid)
                            kept.add(cid)
                        except Exception as e:
                            self.logger.debug(f"Could not pin do-request object {cid}: {e}")
            if kept:
                self.logger.info(f"Replicated {len(kept)} DO-request object(s)")
            return kept
        except Exception as e:
            self.logger.warning(f"DO-request replication skipped: {e}")
            return kept

    def __replicate_session_rows(self, scan_limit=None):
        """Pin the IPFS objects referenced by recent interactive sessions.

        For every session-flagged order in the scan window, pin the input
        ciphertext CIDs from its DO request's etny-si rows and the output
        ciphertext CIDs from its DP request's etny-so rows, so a session
        transcript's data plane survives both the data owner's IPFS endpoint
        and the operator node that produced it. Everything is chain-anchored
        ciphertext with an on-chain digest, so pinning blindly is safe.
        Bounded: <=256 rows x 2 channels per session inside the window.
        Never raises.
        """
        kept = set()
        if self.__etny is None:
            return kept
        try:
            try:
                total = self.__etny.caller()._getOrdersCount()
                total = total.toNumber() if hasattr(total, 'toNumber') else int(total)
            except Exception as e:
                self.logger.debug(f"session replication: _getOrdersCount failed ({e})")
                return kept

            limit = scan_limit if scan_limit is not None else int(
                getattr(config, 'esr_result_scan_orders', 200))
            start = max(0, total - limit)

            def pin(cid):
                if not self.__looks_like_cid(cid) or cid in kept:
                    return
                try:
                    if not self.storage.is_pinned(cid):
                        self.storage.pin_add(
                            cid,
                            timeout=int(getattr(config, 'esr_pin_attempt_timeout_seconds', 30)))
                    self.ipfs_cache.add(cid)
                    kept.add(cid)
                except Exception as e:
                    self.logger.debug(f"Could not pin session object {cid}: {e}")

            for order_id in range(total - 1, start - 1, -1):
                free_gb = HardwareInfoProvider.get_free_storage()
                if free_gb < config.esr_min_free_storage_gb:
                    self.logger.warning(
                        f"Free storage {free_gb}GB below ESR_MIN_FREE_STORAGE_GB; "
                        f"stopping session replication for this round")
                    break
                try:
                    time.sleep(self.__network_config.rpc_delay / 1000)
                    order = Order(self.__etny.caller()._getOrder(order_id))
                    meta = self.__etny.caller()._getDORequestMetadata(order.do_req)
                except Exception:
                    continue
                if str(meta[3] or '').split(':')[0] != 'v3s':
                    continue
                # Input rows: v1:<seq>:<orderId>:<cid>:<sha256>
                try:
                    count = int(self.__etny.caller()._getMetadataCountForRequest(order.do_req))
                    for i in range(count):
                        key, value = self.__etny.caller()._getMetadataValueForRequest(order.do_req, i)
                        if key != 'etny-si':
                            continue
                        parts = str(value or '').split(':')
                        if len(parts) == 5 and parts[0] == 'v1':
                            pin(parts[3].strip())
                except Exception as e:
                    self.logger.debug(f"session replication: input rows for order {order_id} failed: {e}")
                # Output rows: v1:<seq>:<orderId>:<ack>:<status>:<code>:<cid>:<sha256>:<sig>
                # -- 'ok' and 'error' rows both carry a payload CID (reply or
                # encrypted explanation); 'late' rows have none.
                try:
                    count = int(self.__etny.caller()._getMetadataCountForDPRequest(order.dp_req))
                    for i in range(count):
                        key, value = self.__etny.caller()._getMetadataValueForDPRequest(order.dp_req, i)
                        if key != 'etny-so':
                            continue
                        parts = str(value or '').split(':')
                        if len(parts) == 9 and parts[0] == 'v1' and parts[6].strip():
                            pin(parts[6].strip())
                except Exception as e:
                    self.logger.debug(f"session replication: output rows for order {order_id} failed: {e}")
            if kept:
                self.logger.info(f"Replicated {len(kept)} session object(s)")
            return kept
        except Exception as e:
            self.logger.warning(f"Session replication skipped: {e}")
            return kept

    def __replicate_cas_session_registry(self):
        """Pin the body of every session registered in the SessionRegistry.

        CAS sessions are registered ON-CHAIN with their body on IPFS as
        CIDv1/raw/sha2-256 (the ESR blob recipe), so the CID is the content
        digest and pinning by CID is safe without refetching or rehashing --
        the same chain-anchored reasoning as the other replicators here. Any
        node holding the body keeps the session fetchable for every CAS
        validator, so session material distributes across operator nodes
        after a pipeline deployment instead of depending on the pin node.

        Incremental: scans SessionRegistered logs from where the last round
        stopped (first round reaches back cas_session_registry_scan_blocks).
        Never raises.
        """
        kept = set()
        if self.__cas_session_registry is None:
            return kept
        try:
            head = self.__w3.eth.block_number
            start = self.__cas_sessreg_last_block
            if start <= 0:
                window = int(getattr(config, 'cas_session_registry_scan_blocks', 200000))
                start = max(0, head - window)
            if start > head:
                self.__cas_sessreg_last_block = head
                return kept
            topic0 = self.__w3.keccak(
                text="SessionRegistered(bytes32,string,uint32,address,uint8)").hex()
            if not topic0.startswith('0x'):
                topic0 = '0x' + topic0
            chunk = 9000
            frm = start
            while frm <= head:
                to = min(frm + chunk - 1, head)
                try:
                    logs = self.__w3.eth.get_logs({
                        'address': self.__cas_session_registry.address,
                        'topics': [topic0],
                        'fromBlock': frm, 'toBlock': to,
                    })
                except Exception as e:
                    self.logger.debug(
                        f"cas-session-registry logs {frm}-{to} failed ({e})")
                    break
                for lg in logs:
                    try:
                        session_hash = lg['topics'][1]
                        rec = self.__cas_session_registry.functions.record(
                            session_hash).call()
                        # (hash, creator, name, version, bodyCid, imageCid,
                        #  hashAlgo, registeredAt, exists)
                        if not rec[8]:
                            continue
                        body_cid = rec[4]
                        if not body_cid:
                            continue
                        self.storage.pin_add(body_cid)
                        kept.add(body_cid)
                        self.logger.info(
                            f"[cas-session-registry] pinned {rec[2]} v{rec[3]} "
                            f"body {body_cid}")
                    except Exception as e:
                        self.logger.debug(
                            f"cas-session-registry record/pin failed ({e})")
                frm = to + 1
            self.__cas_sessreg_last_block = head + 1
        except Exception as e:
            self.logger.debug(f"cas-session-registry replication failed ({e})")
        return kept

    def run_esr_replication_loop(self):
        """Replication loop for THIS network, paired with its processing thread.

        Mirrors this network's on-chain results and ESR state hashes into the
        node's IPFS, using this network's own contracts/RPC. Runs on its own
        cadence (esr_replication_interval_seconds, default 300s) and covers BOTH
        registries: ESR StateCommitted current-version CIDs and protocol-contract
        order result CIDs, refreshing their cache timestamps so the retention
        sweep never ages out live content.

        Previously replication only ran as a side-effect of the hourly IPFS
        cache cleanup (ESR-only, and dead until the fromBlock fix), so freshly
        committed state could sit un-replicated for up to an hour.

        Best-effort: any error is logged and the loop waits for the next tick.
        Exits when stop_event is set.
        """
        interval = float(getattr(config, 'esr_replication_interval_seconds', 300))
        while not stop_event.is_set():
            try:
                if self.__esr is not None:
                    self.__replicate_esr_state()
                self.__replicate_protocol_results()
                self.__replicate_do_request_inputs()
                self.__replicate_session_rows()
                self.__replicate_cas_session_registry()
            except Exception as e:
                self.logger.warning(f"[esr-replication] round failed: {e}")
            # Sleep in short slices so stop_event is honored promptly.
            waited = 0.0
            while waited < interval and not stop_event.is_set():
                time.sleep(2)
                waited += 2

    def __maybe_clear_ipfs_cache(self):
        """Run the pin cleanup at most once per configured interval.

        Cleanup used to happen only in __init__, so a node that stayed up for
        weeks never reclaimed anything until it was restarted. This is called
        from the order-processing loop (alongside the heartbeat) and throttled by
        IPFS_CLEANUP_INTERVAL_MINUTES so it costs one timestamp comparison per
        order rather than a full sweep.

        Never raises: reclaiming disk must not be able to interrupt order
        processing.
        """
        try:
            interval = float(getattr(config, 'ipfs_cleanup_interval_minutes_default', 60)) * 60
            if interval <= 0:
                return
            last = getattr(self, '_EtnyPoXNode__last_ipfs_cleanup_at', 0) or 0
            if (time.time() - last) < interval:
                return
            self.__clear_ipfs_cache()
        except Exception as e:
            self.logger.warning(f"Periodic IPFS cleanup skipped: {e}")
            self.__last_ipfs_cleanup_at = time.time()

    def __clear_ipfs_cache(self):
        logger = self.logger

        logger.info(f"Cleaning up ipfs cache")

        # Retention is configurable (IPFS_PIN_RETENTION_DAYS, default 7 days).
        # 0 disables age-based cleanup entirely.
        retention_seconds = float(getattr(config, 'ipfs_pin_retention_days_default', 7)) * 24 * 60 * 60
        if retention_seconds <= 0:
            logger.info("IPFS pin retention is disabled (IPFS_PIN_RETENTION_DAYS=0); skipping cleanup")
            self.__last_ipfs_cleanup_at = time.time()
            return

        current_time = time.time()

        trustedzone_images = self.__network_config.trustedzone_images.split(',')

        keep_hashes = []

        for image in trustedzone_images:
            # Bounded retry: this used to loop forever on RPC failure, which was
            # survivable at startup but would wedge the order-processing loop now
            # that cleanup also runs periodically. On failure we skip this round
            # -- better to keep pins one extra cycle than to stall the node.
            for _attempt in range(5):
                try:
                    time.sleep(self.__network_config.rpc_delay/1000)
                    [enclave_image_hash, _,
                     docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey(image, 'v3')
                    keep_hashes.append(enclave_image_hash)
                    keep_hashes.append(docker_compose_hash)
                    break
                except Exception as e:
                    continue
            else:
                logger.warning(
                    f"Could not resolve the current image hashes for {image}; skipping this cleanup "
                    f"round so a transient RPC failure cannot unpin an image that is still in use."
                )
                self.__last_ipfs_cleanup_at = time.time()
                return

        # Replicate current ESR state and protect it from expiry. Only the CIDs
        # that are STILL the current version for some enclave/key are kept --
        # superseded versions age out like anything else, so a dApp never loses
        # live state while stale revisions are still reclaimed.
        try:
            keep_hashes.extend(self.__replicate_esr_state())
        except Exception as e:
            logger.warning(f"ESR replication skipped during cleanup: {e}")

        retention_hours = retention_seconds / 3600
        removed = 0
        for hash in list(self.ipfs_cache.get_values):
          if hash not in keep_hashes:
            timestamp = self.ipfs_cache.get_timestamp(hash)
            if timestamp:
                age = current_time - timestamp
                if age > retention_seconds:
                    logger.debug(f"Deleting {hash} (Age: {age / 3600:.2f} hours)")
                    try:
                        self.storage.pin_rm(hash)
                        self.storage.rm(hash)
                        removed += 1
                        logger.debug(f"Successfully deleted {hash}")
                    except Exception as e:
                        logger.debug(f"Failed to delete {hash}: {e}")
                else:
                    logger.debug(f"Hash {hash} is within the {retention_hours:.0f}h retention (Age: {age / 3600:.2f} hours). Keeping pin.")
            else:
                logger.warning(f"No timestamp found for {hash}. Unable to determine age. Skipping deletion.")
          else:
            # A hash we must keep (current trustedzone image): make sure it stays
            # pinned and refresh its cache entry so it is not aged out.
            self.storage.pin_add(hash)
            self.ipfs_cache.add(hash)

        if removed:
            logger.info(f"IPFS cleanup removed {removed} expired pin(s) (retention {retention_hours:.0f}h)")

        # Unpinning above only REMOVES pins; the underlying blocks are not
        # reclaimed until a garbage collection runs. Without a periodic GC the
        # local datastore keeps growing with unpinned/expired content until it
        # passes StorageMax and IPFS GC-thrashes -- which drops fresh
        # fire-and-forget pins (the state-blob / result 404s). Run GC here, as
        # part of the same throttled cleanup pass, so expired data is actually
        # freed on a schedule. Best-effort: a GC failure must never disturb
        # order processing. Opt out with IPFS_PERIODIC_GC=0.
        if str(os.environ.get('IPFS_PERIODIC_GC', '1')).strip() not in ('0', 'false', 'False'):
            try:
                logger.info("Running periodic IPFS garbage collection to reclaim unpinned data")
                self.storage.repo_gc()
            except Exception as gc_err:
                logger.warning(f"Periodic IPFS garbage collection skipped: {gc_err}")

        self.__last_ipfs_cleanup_at = time.time()

    def generate_process_order_data(self, write=False):

        if not os.path.exists(self.cache_config.process_orders_cache_filepath) or write == True:
            self.process_order_data = {"process_order_retry_counter": 0,
                                       "order_id": self.__order_id,
                                       "uuid": self.__uuid}

            json_object = json.dumps(self.process_order_data, indent=4)

            with open(self.cache_config.process_orders_cache_filepath, "w") as outfile:
                outfile.write(json_object)

        else:
            with open(self.cache_config.process_orders_cache_filepath, 'r') as openfile:
                self.process_order_data = json.load(openfile)

    def parse_arguments(self, arguments, parser):
        parser, unknown_args = parser.parse_known_args()
        for args_type, args in arguments.items():
            for arg in args:
                setattr(self, "_" + self.__class__.__name__ + "__" + arg, args_type(getattr(parser, arg)))

    def cache_dp_requests(self):
        logger = self.logger

        if not stop_event.is_set():
            try:
                time.sleep(self.__network_config.rpc_delay/1000)
                my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
                cached_ids = self.dpreq_cache.get_values
                req_to_process = sorted(set(my_dp_requests) - set(cached_ids))
       
                total_requests = len(req_to_process)
                threshold = 0

                for idx, req_id in enumerate(req_to_process, start=1):

                    self.__call_heart_beat()

                    if stop_event.is_set():
                        break

                    percent_complete = (idx * 100) // total_requests
                
                    if percent_complete >= threshold and total_requests > 1:
                        logger.info(f"Building DP requests cache [STAGE 1]: {percent_complete}% ({idx} / {total_requests})")
                        threshold += 10  # Increment to the next threshold
                  
                    logger.debug(f"Cleaning up DP request {req_id}")
                    time.sleep(self.__network_config.rpc_delay/1000)
                    req_uuid = self.__etny.caller()._getDPRequestMetadata(req_id)[1]
                    if req_uuid != self.__uuid:
                        logger.debug(f"Skipping DP request {req_id}, not mine")
                        self.__dprequest = req_id
                        order_details = self._getOrder()
                        self.dpreq_cache.add(req_id)
                        continue
                    time.sleep(self.__network_config.rpc_delay/1000)
                    req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
                    if req.status == RequestStatus.CANCELED:
                        self.__dprequest = req_id
                        order_details = self._getOrder()
                        self.dpreq_cache.add(req_id)
                    elif req.status == RequestStatus.BOOKED:
                        logger.debug(f"DP Request {req_id} already assigned to order")
                        self.__dprequest = req_id
                        order_details = self._getOrder()
                        [order_id, order] = order_details
                        if order.status == OrderStatus.CLOSED:
                            logger.debug(f"DP request {self.__dprequest} completed successfully!")
                            self.dpreq_cache.add(self.__dprequest)
                        if order.status == OrderStatus.OPEN:
                            logger.debug("Order was never approved, skipping")

                if total_requests > 1 and not stop_event.is_set():
                    logger.info(f"Building DP requests cache [STAGE 1]: 100%")
                    logger.info(f"Finished building DP requests cache [STAGE 1]")
                        
            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')

    def resume_pending_dp_requests(self):
        logger = self.logger

        if not stop_event.is_set():
            try:
                time.sleep(self.__network_config.rpc_delay/1000)
                my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
                cached_ids = self.dpreq_cache.get_values
                req_to_process = sorted(set(my_dp_requests) - set(cached_ids))

                total_requests = len(req_to_process)
                threshold = 0

                for idx, req_id in enumerate(req_to_process, start=1):

                    balance = self.__w3.eth.get_balance(self.__address)

                    if balance < int(self.__network_config.minimum_gas_at_start):
                        logger.error("Not enough gas to run on this network, exiting")
                        break

                    if stop_event.is_set():
                        break

                    percent_complete = (idx * 100) // total_requests

                    if percent_complete >= threshold and total_requests > 1:
                        logger.info(f"Building DP requests cache [STAGE 2]: {percent_complete}% ({idx} / {total_requests})")
                        threshold += 10  # Increment to the next threshold

                    time.sleep(self.__network_config.rpc_delay/1000)
                    req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
                    if req.status == RequestStatus.BOOKED:
                        logger.debug(f"DP Request {req_id} already assigned to order")
                        self.__dprequest = req_id
                        self.process_dp_request()

                if total_requests > 1 and not stop_event.is_set():
                    logger.info(f"Building DP requests cache [STAGE 2]: 100%")
                    logger.info(f"Finished building DP requests cache [STAGE 2]")


            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')

    def resume_available_dp_requests(self):
        logger = self.logger

        if not stop_event.is_set():
            try:
                time.sleep(self.__network_config.rpc_delay/1000)
                my_dp_requests = self.__etny.functions._getMyDPRequests().call({'from': self.__address})
                cached_ids = self.dpreq_cache.get_values
                req_to_process = sorted(set(my_dp_requests) - set(cached_ids))


                for idx, req_id in enumerate(req_to_process, start=1):
                    if stop_event.is_set():
                        break

                    time.sleep(self.__network_config.rpc_delay/1000)
                    req = DPRequest(self.__etny.caller()._getDPRequest(req_id))
                    if req.status == RequestStatus.AVAILABLE:
                        logger.info(f"DP Request {req_id} resumed. Unlocking the value of decentralization. ")
                        self.__dprequest = req_id
                        self.process_dp_request()
                    else:
                        logger.debug(f"DP Request {req_id} should be in cache already with status {req.status}")

            except Exception as e:
                logger.info(f'error = {e}, type = {type(e)}')



    def _limited_arg(self, item, allowed_max=255):
        return allowed_max if item > allowed_max else item

    def add_dp_request(self, waiting_period_on_error=15, beginning_of_recursion=None):
        logger = self.logger

        if self.__price is None:
            self.__price = 1


        # Getting available hardware resources
        self.__number_of_cpus = int(HardwareInfoProvider.get_number_of_cpus());
        self.__free_memory = int(HardwareInfoProvider.get_free_memory());
        self.__free_storage = int(HardwareInfoProvider.get_free_storage());

        params = [
            self._limited_arg(self.__number_of_cpus),
            self._limited_arg(self.__free_memory),
            self._limited_arg(self.__free_storage),
            self._limited_arg(self.__bandwidth),
            self.__duration,
            self.__price,
            self.__uuid,
            "v3",
            self.__node_geo,
            ""
        ]

        max_retries = 20
        retries = 0

        while True: 
          try:
            logger.info("Preparing transaction for new DP request")
            time.sleep(self.__network_config.rpc_delay/1000)
            unicorn_txn = self.__etny.functions._addDPRequest(*params).build_transaction(self.get_transaction_build())
            _hash = self.send_transaction(unicorn_txn)
            logger.info(f"TXID {_hash} pending... ")
            receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
            processed_logs = self.__etny.events._addDPRequestEV().process_receipt(receipt)
            self.__dprequest = processed_logs[0].args._rowNumber
            if receipt.status == 1:
                logger.info(f"TXID {_hash} confirmed!")
                break
          except Exception as ex:
            retries += 1
            logger.warning(f"Warning while adding DP request. Retry {retries}/{max_retries}. Message: {ex}")
            if retries == max_retries:
              logger.error("Maximum retries reached. Aborting.")
              raise
            time.sleep(5)

        logger.info(f"DP Request {self.__dprequest} initialized. Unlocking the value of decentralization.")


    def cancel_dp_request(self, req):
        logger = self.logger

        logger.info(f"Cancelling DP request {req}")

        while True:
            try:
                logger.info("Preparing transaction for DO request cancellation")
                time.sleep(self.__network_config.rpc_delay/1000)
                unicorn_txn = self.__etny.functions._cancelDPRequest(req).build_transaction(self.get_transaction_build())
                _hash = self.send_transaction(unicorn_txn)
                logger.info(f"TXID {_hash} pending... ")
                receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
                if receipt.status == 1:
                    logger.info(f"TXID {_hash} confirmed!")
                    break
            except Exception as ex:
                logger.warning(f"Unable to cancel  DP request - {req}: Error: {ex}")
                logger.warning(f"Retrying")

        logger.info(f"DP request {req} cancelled successfully!")
        time.sleep(5)

    def ipfs_timeout_cancel(self, order_id):
        result = 'Error: cannot download files from IPFS'
        self.add_result_to_order(order_id, result)


    def calculate_reward(self):
        logger = self.logger

        [order_id, order] = self._getOrder()

        do_req = DORequest(self.__etny.caller()._getDORequest(order.do_req))
        if self.__network_config.reward_type == 1:
            total_amount = do_req.price * do_req.duration
            network_fee = total_amount * self.__network_config.network_fee / 100
            enclave_fee = total_amount * self.__network_config.enclave_fee / 100
            operator_fee = total_amount - network_fee - enclave_fee
            reward = round(operator_fee, 2)
        elif self.__network_config.reward_type == 2:
            total_amount = do_req.price * do_req.duration
            base_amount = (total_amount * 100) / ( 100 + self.__network_config.network_fee + self.__network_config.enclave_fee )
            network_fee = base_amount * self.__network_config.network_fee / 100
            enclave_fee = base_amount * self.__network_config.enclave_fee / 100
            operator_fee = total_amount - network_fee - enclave_fee
            reward = round(operator_fee, 2)

        logger.info("***")
        logger.info(f"Reward: {reward} {self.__network_config.token_name}. You’ve earned it. ")
        logger.info("***")

        if self.__network_config.network_type == "MAINNET":
            logger.info(f"HODL your {self.__network_config.token_name} for long-term growth. Payout after validation.")

    def process_order(self, order_id, metadata=None):
        logger = self.logger


        logger.debug(f"Processing order {order_id}")

        try:
            with open(self.cache_config.process_orders_cache_filepath, 'r') as openfile:
                self.process_order_data = json.load(openfile)
        except Exception as e:
            pass
         
        if not self.process_order_data or self.process_order_data["order_id"] != order_id:
            self.process_order_data["order_id"] = order_id
            self.process_order_data["process_order_retry_counter"] = 0

        # this line should be checked later
        if not metadata:
            while True:
                try:
                    time.sleep(self.__network_config.rpc_delay/1000)
                    order = Order(self.__etny.caller()._getOrder(order_id))
                    metadata = self.__etny.caller()._getDORequestMetadata(order.do_req)
                    break
                except Exception as e:
                    logger.warning(f"Unable to get order metadata: {e}")
                    logger.warning("Retrying")
                    timeout_in_seconds = int(self.__network_config.block_time) - 1.3
                    time.sleep(timeout_in_seconds)

        if self.process_order_data['process_order_retry_counter'] > 10:
            if metadata[1].startswith('v1:') == 1:
                logger.debug('Building result ')
                result = self.build_result_format_v1("[Warn]",
                                                     f'Too many retries for the current order_id: {order_id}')
                logger.debug(f'Result is: {result}')
                self.add_result_to_order(order_id, result)
                return

            else:
                logger.debug('Building result ')
                logger.warn('Too many retries for the current order_id: %d', order_id)
                logger.info('Adding result to order')
                result_msg='[Warn] Order execution failed more than 10 times'
                self.add_result_to_order(order_id, result_msg)
                return

        self.process_order_data['process_order_retry_counter'] += 1
        json_object = json.dumps(self.process_order_data, indent=4)
        with open(self.cache_config.process_orders_cache_filepath, "w") as outfile:
            outfile.write(json_object)

        #self.add_processor_to_order(order_id)
        try:
            version = 0
            if metadata[1].startswith('v3:'):
                version = 3
                [v3, enclave_image_hash, enclave_image_name, docker_compose_hash, challenge_hash, public_cert] = metadata[
                    1].split(':')
                 
        except Exception as e:
            pass

        logger.debug(f'Running version v{version}')
        if version == 3:
            try:
                logger.debug(f"Downloading IPFS Image: {enclave_image_hash}")
                logger.debug(f"Downloading IPFS docker yml file: {docker_compose_hash}")
                logger.debug(f"Downloading IPFS Payload Hash: {metadata[2]}")
                logger.debug(f"Downloading IPFS FileSet Hash: {metadata[3]}")
                logger.debug(f"Downloading IPFS Challenge Hash: {challenge_hash}")
            except Exception as e:
                logger.info(str(e))

            payload_hash = metadata[2].split(':')[1]
            input_hash = metadata[3].split(':')[1]
            list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash, challenge_hash, payload_hash]
            if input_hash is not None and len(input_hash) > 0:
                list_of_ipfs_hashes.append(input_hash)

            if self.process_order_data['process_order_retry_counter'] <= 10:
                logger.info(f"Fetching task data for DO Request {order.do_req} from IPFS.")
                if not self.storage.download_many(list_of_ipfs_hashes, attempts=5, delay=3):
                    logger.info("Cannot download data from IPFS, cancelling processing")
                    self.ipfs_timeout_cancel(order_id)
                    self.dpreq_cache.add(order.dp_req)
                    return

            payload_file = f'{self.cache_config.base_path}/{payload_hash}'
            if input_hash is not None and len(input_hash) > 0:
                input_file = f'{self.cache_config.base_path}/{input_hash}'
                logger.info('input hash is not none: ', input_file)
            else:
                input_file = None


            os.chdir(self.cache_config.base_path)

            logger.info("Task preloaded. Preparing docker environment")
            run_subprocess(
                ['docker-compose', '-f', f'../docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
                logger)

            docker_compose_file = f'{self.cache_config.base_path}/{docker_compose_hash}'
            challenge_file = f'{self.cache_config.base_path}/{challenge_hash}'
            challenge_content = self.read_file(challenge_file)
            bucket_name = f'{enclave_image_name}-{v3}'
            logger.debug(f'Preparing prerequisites for {v3}')
            self.build_prerequisites_v3(bucket_name, order_id, payload_file, input_file,
                                        docker_compose_file, challenge_content)

            logger.debug("Stopping previous docker registry")
            run_subprocess(['docker', 'stop', 'registry'], logger)
            logger.debug("Cleaning up docker registry")
            run_subprocess(['docker', 'stop', 'etny-securelock'], logger)
            run_subprocess(['docker', 'stop', 'etny-trustedzone'], logger)
            run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
            logger.debug("Running new docker registry")
            logger.debug(str(self.cache_config.base_path) + '/' + enclave_image_hash + ':/var/lib/registry')

            logger.debug("Stopping previous docker las")
            run_subprocess(['docker', 'stop', 'las'], logger)
            logger.debug("Removing previous docker las")
            run_subprocess(['docker', 'rm', 'las'], logger)
            run_subprocess([
                'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
                str(self.cache_config.base_path) + '/' + enclave_image_hash + ':/var/lib/registry',
                'registry:2'
            ], logger)

            os.chdir(self.cache_config.base_path)
            logger.debug("Cleaning up docker container")
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)

            logger.debug("Started enclave execution")

            os.chdir(self.cache_config.base_path)

            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'up', '-d'
            ], logger)

            logger.info('Docker environment ready. Execution started in SGX enclave')

            while True:
               try:
                   order = Order(self.__etny.caller()._getOrder(order_id))
                   do_req = DORequest(self.__etny.caller()._getDORequest(order.do_req))
                   break
               except Exception as e:
                   logger.warning(f"Unable to fetch order details: {e}")
                   time.sleep(1)

            # Track total wait time
            start_total_wait = time.time()

            # First wait
            start_wait = time.time()
            status_enclave = self.wait_for_enclave_v2(bucket_name, 'result.txt', do_req.duration * 3600 + 120, order_id=order_id)
            elapsed_wait1 = time.time() - start_wait

            # Second wait
            start_wait = time.time()
            # The trustedzone's landed-check waits up to 5 blocks per started
            # 64 commits (max 20 blocks for a full 256-commit run), so allow
            # generous headroom on top of the relay work itself.
            status_enclave = self.wait_for_enclave_v2(bucket_name, 'transaction.txt', 300, order_id=order_id)
            elapsed_wait2 = time.time() - start_wait

            total_elapsed_wait = elapsed_wait1 + elapsed_wait2

            logger.info('Enclave finished the execution')

            # Pin any ESR state blobs the enclave wrote at the END of execution.
            # serve_esr_state_pins runs on a 5s cadence DURING the wait loops,
            # but the enclave typically writes state.<key>.enc at the same moment
            # as result.txt -- which ends the wait loop -- so the final state
            # blob can land in the bucket AFTER the last in-loop poll and never
            # get pinned. Without this sweep the pointer is committed on-chain
            # but the blob is only in Swift, so a later task's get() 404s. Do a
            # final pass here (before the relay puts the pointer on-chain) so the
            # blob is durable.
            if self.__esr is not None:
                try:
                    self.serve_esr_state_pins(bucket_name, time.time() + 60)
                except Exception as e:
                    logger.debug(f'[esr] final state-pin sweep skipped: {e}')

            # Relay any ESR state commits the enclave signed for this order. The
            # enclave never pays gas: it stages signed commitFor authorizations
            # (esr.commit.<key16>.<relayNonce>.json) and the node submits + pays
            # them, capped per order so a malicious payload cannot drain the
            # operator. (Also runs in-loop during the enclave wait; this is the
            # final sweep.)
            self.__relay_esr_commits(bucket_name, order_id)

            # Final session sweep: deliver any raced inputs, pin remaining
            # outputs and broadcast their signed rows (late notices included)
            # BEFORE the result transaction goes out, so the notices and the
            # completion land back-to-back -- same block in practice.
            try:
                self.__serve_session(bucket_name, order_id)
            except Exception as e:
                logger.debug(f'[session] final sweep skipped: {e}')

            # Pin the signed authorization ledger to IPFS. The validator runs
            # on an ISOLATED box and fetches the ledger BY THE CID the
            # trustedzone attested into the result (content-addressed =>
            # trustless delivery); without this pin the validator cannot
            # verify the order and will invalidate it.
            #
            # Whether this order made commits at all is read from the ATTESTED
            # result inside transaction.txt (fields 5-7 of the result string:
            # merkleRoot:countByte:ledgerCid) -- not inferred from bucket
            # contents. Zero root = the trustedzone attested "no commits";
            # nothing is fetched or pinned. When commits exist, the staged
            # ledger bytes are verified against the attested CID before
            # pinning, so a corrupted staging file is never pinned under the
            # wrong identity.
            if self.__esr is not None:
                try:
                    esr_root, esr_cid = '', ''
                    try:
                        ok_tx, tx_raw = self.swift_stream_service.get_file_content(
                            bucket_name, 'transaction.txt')
                        if ok_tx and tx_raw:
                            parsed = parse_transaction_bytes_ut(self.__contract_abi, tx_raw)
                            parts = str(parsed.get('result', '')).split(':')
                            if len(parts) > 6:
                                esr_root, esr_cid = parts[4], parts[6]
                    except Exception as e:
                        logger.debug(f'[esr] could not parse attested result ({e}); '
                                     f'falling back to bucket probe')

                    if esr_root and set(esr_root) == {'0'}:
                        logger.debug(f'[esr] order {order_id}: attested zero ESR '
                                     f'commits -- nothing to pin')
                    else:
                        exists, _m = self.swift_stream_service.is_object_in_bucket(
                            bucket_name, 'esr.authorizations.json')
                        if not exists:
                            if esr_root:
                                logger.error(
                                    f'[esr] order {order_id}: result attests ESR '
                                    f'commits (ledger {esr_cid}) but no staged '
                                    f'ledger exists -- cannot pin; the validator '
                                    f'will fail this order')
                        else:
                            ok, ledger_raw = self.swift_stream_service.get_file_content(
                                bucket_name, 'esr.authorizations.json')
                            if ok and ledger_raw:
                                lb = ledger_raw.encode('utf-8') if isinstance(ledger_raw, str) else ledger_raw
                                local_cid = self.__cidv1_raw(lb)
                                if esr_cid and local_cid != esr_cid:
                                    logger.error(
                                        f'[esr] order {order_id}: staged ledger '
                                        f'({local_cid}) does not match the attested '
                                        f'CID ({esr_cid}) -- refusing to pin '
                                        f'mismatched bytes')
                                else:
                                    cidl = self.storage.pin_bytes_deferred(
                                        lb, name=f'esr-ledger-{order_id}.json')
                                    logger.info(f'[esr] pinned authorizations ledger '
                                                f'for order {order_id}: {cidl}')
                except Exception as e:
                    logger.error(f'[esr] ledger pin failed for order {order_id}: {e}')

            if status_enclave == True:
                logger.debug(f'Uploading result to {enclave_image_name}-{v3} bucket')
                status, result_data = self.swift_stream_service.get_file_content(bucket_name, "result.txt")
                if not status:
                    logger.debug(result_data)

                with open(f'{self.order_folder}/result.txt', 'w') as f:
                    f.write(result_data)
                logger.debug(f'[v3] Result file successfully downloaded to {self.order_folder}/result.txt')
                # Compute the CID locally and submit on-chain immediately; the
                # actual IPFS add/pin runs in the background and retries on its
                # own. Previously this blocked on upload() -- 10 attempts with an
                # IPFS restart between failures -- so a slow or unhealthy daemon
                # could stall the submission, and after 10 failures it RAISED,
                # losing the on-chain result for work the enclave had already
                # completed. The stall grew with the size of the result.
                result_hash = self.storage.pin_bytes_deferred(
                    result_data.encode('utf-8') if isinstance(result_data, str) else result_data,
                    name=f'result-{order_id}.txt')
                logger.debug(f'[v3] Result CID {result_hash}; pinning in background')
                logger.debug(f'Result file successfully uploaded to {enclave_image_name}-{v3} bucket')
                logger.debug('Reading transaction from file')
                status, transaction_data = self.swift_stream_service.get_file_content(bucket_name, "transaction.txt")
                if not status:
                   logger.debug(transaction_data)
                logger.debug('Building result for v3')
                result = self.build_result_format_v3(result_hash, transaction_data)
                logger.debug(f'Result is: {result}')
                self.add_result_to_order(order_id, result)
                logger.info("ZK proof added. Task integrity submitted for validation.")
                self.calculate_reward()

                task_code = 0 

                try:
                    result = parse_transaction_bytes_ut(self.__contract_abi, transaction_data)
                    arr = result["result"].split(":")
                    task_code = arr[1];
                except Exception as ex:
                    logger.info("Unable to determine the result_code (return code) from transactiond data")

                if int(task_code) == ResultStatus.EXECVE:
                    logger.info(f"Process EXECVE is still running")

                    total_duration = do_req.duration * 3600
                    remaining_sleep = total_duration - total_elapsed_wait

                    logger.info(f"Waiting till the end of execution - {remaining_sleep}")

                    if remaining_sleep > 0:
                        time.sleep(remaining_sleep)
                    else:
                        logger.info(f'No remaining sleep required. Already waited {total_elapsed_wait} seconds.')

            else:
                result = self.build_result_format_v3("[WARN]","Task execution timed out");
                self.add_result_to_order(order_id, result);

            self.dpreq_cache.add(self.__dprequest)
            logger.debug('Cleaning up environment')
            logger.debug('Cleaning up SecureLock and TrustedZone containers.')
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)

    def wait_for_enclave_v2(self, bucket_name, object_name, timeout=120, order_id=None):
        logger = self.logger
        deadline = time.time() + timeout
        logger.info(f'Checking if object {object_name} exists in bucket {bucket_name} for {timeout} seconds')
        # Deliver prior ESR state INTO this order's bucket before the enclave
        # tries to read it. The enclave's _fetch reads state.<cid>.enc from the
        # bucket (it cannot reach IPFS), but each order gets a fresh bucket -- so
        # without this, get() of state committed by a PREVIOUS order finds
        # nothing and raises "Could not read state object". Stage the current
        # (per getState) version's blob from IPFS so the read hits.
        #
        # Skip for the integration-test bucket: the SGX self-test runs a fixed
        # payload that never reads ESR state, and staging there would only make
        # the capability check depend on ESR/IPFS availability.
        if self.__esr is not None and bucket_name != getattr(self, 'integration_bucket_name', 'etny-bucket-integration'):
            try:
                self.stage_esr_state_for_read(bucket_name)
            except Exception as e:
                logger.debug(f'[esr-read] staging skipped: {e}')
        last_state_poll = 0
        last_session_poll = 0
        while time.time() < deadline:
            exists, msg = self.swift_stream_service.is_object_in_bucket(bucket_name, object_name)
            if exists:
                logger.info('Enclave execution finished!')
                return True
            # While waiting on the enclave, serve any ESR state it has dropped
            # for pinning. The enclave blocks on the CID coming back, so this
            # has to run DURING the wait, not after it. Polled every few seconds
            # rather than every pass to keep the bucket listing cheap.
            if self.__esr is not None and (time.time() - last_state_poll) >= 5:
                last_state_poll = time.time()
                self.serve_esr_state_pins(bucket_name, deadline)
                # Relay staged ESR commits AS THEY APPEAR: the trustedzone
                # verifies (up to 5 blocks) that every signed commit landed
                # on the registry before it signs the result, so the relay
                # must run DURING its wait, not after. Idempotent -- already-
                # relayed files are skipped.
                if order_id is not None:
                    try:
                        self.__relay_esr_commits(bucket_name, order_id)
                    except Exception as e:
                        logger.debug(f'[esr-relay] in-loop relay skipped: {e}')
            # Interactive-session transport runs on its own cadence and does
            # NOT depend on ESR being deployed on this network.
            if order_id is not None and (time.time() - last_session_poll) >= 5:
                last_session_poll = time.time()
                try:
                    self.__serve_session(bucket_name, order_id)
                except Exception as e:
                    logger.debug(f'[session] in-loop serve skipped: {e}')
            time.sleep(1)
        logger.info('Enclave execution timed out')
        return False

    @staticmethod
    def __cidv1_raw(content_bytes):
        """CIDv1/raw/sha2-256 for `content_bytes` -- the same CID `ipfs add
        --cid-version=1 --raw-leaves` produces.

        Layout: multibase 'b' + base32( 0x01 0x55 0x12 0x20 || sha256(content) )
                                        CIDv1 raw  sha256  32 bytes
        """
        digest = hashlib.sha256(content_bytes).digest()
        raw = bytes([0x01, 0x55, 0x12, 0x20]) + digest
        return 'b' + base64.b32encode(raw).decode('ascii').lower().rstrip('=')

    def stage_esr_state_for_read(self, bucket_name):
        """Deliver current ESR state into the bucket so the enclave can read it.

        The enclave's StateRegistry.get() reads the CID from the chain
        (getState = the last committed value + version) and then fetches the
        blob from the SwiftStream bucket as state.<cid>.enc -- it cannot reach
        IPFS itself. Each task runs in a FRESH bucket, so state committed by a
        previous order is not present, and get() raises "Could not read state
        object". This bridges that gap: for every (enclave, key) whose current
        on-chain version points at a real CID, fetch that blob from IPFS (where
        replication keeps it pinned) and write it into this order's bucket as
        state.<cid>.enc. The enclave's _fetch already looks for exactly that
        name, so no enclave-side change is needed.

        Staging by the CID getState returns means we always deliver the LAST
        committed value for the current version, never a stale one. The enclave
        still verifies the blob against the on-chain CID, so a wrong or corrupted
        stage is rejected rather than trusted.

        Best-effort and bounded by free disk; never raises.
        """
        if self.__esr is None or not bucket_name:
            return 0
        staged = 0
        try:
            records = self.__esr_current_state_cids()
            cids = {r['cid'] for r in records}
        except Exception as e:
            self.logger.debug(f"[esr-read] could not resolve current state CIDs: {e}")
            return 0
        for cid in cids:
            object_name = f"state.{cid}.enc"
            try:
                # Already delivered for this order? skip.
                present, _ = self.swift_stream_service.is_object_in_bucket(bucket_name, object_name)
                if present:
                    continue
                if HardwareInfoProvider.get_free_storage() < config.esr_min_free_storage_gb:
                    self.logger.warning("[esr-read] low disk; stopping state staging this round")
                    break
                # Only stage a blob that is already pinned LOCALLY (this node, or
                # replicated to it). is_pinned is a fast local check. If it is not
                # here, DO NOT call the blocking download()/gateway fetch: a CID
                # that no node ever pinned (e.g. a historical commit whose pin
                # failed) is unfetchable, and download() would retry for a long
                # time -- stalling the order's execution wait and, when staging
                # runs during the integration test, the whole SGX check. Skipping
                # is safe: staging is best-effort; a missing prior state just
                # means the enclave's get() returns default, not a hang.
                if not self.storage.is_pinned(cid):
                    self.logger.debug(f"[esr-read] {cid} not pinned locally; skipping stage (non-blocking)")
                    continue
                self.storage.download(cid)
                blob_path = os.path.join(self.storage.target, cid)
                if not os.path.isfile(blob_path):
                    self.logger.debug(f"[esr-read] {cid} not retrievable; skipping")
                    continue
                with open(blob_path, "rb") as fh:
                    blob = fh.read()
                # Verify the delivered bytes actually hash to the committed CID
                # before handing them to the enclave -- never stage substituted
                # content, even though the enclave re-checks too.
                if self.__cidv1_raw(blob) != cid:
                    self.logger.warning(f"[esr-read] {cid}: fetched content hashes differently; not staging")
                    continue
                self.swift_stream_service.put_file_content(
                    bucket_name, object_name, "", io.BytesIO(blob))
                staged += 1
                self.logger.info(f"[esr-read] staged {object_name} into {bucket_name}")
            except Exception as e:
                self.logger.debug(f"[esr-read] could not stage {object_name}: {e}")
        return staged

    def serve_esr_state_pins(self, bucket_name, deadline_ts):
        """Pin ESR state blobs the enclave drops in the bucket.

        The enclave cannot reach IPFS: the Kubo API binds 127.0.0.1 on the host,
        so no container can talk to it (unlike MinIO, which is a container on the
        ethernity network). Rather than exposing the admin API or adding a proxy,
        the enclave writes the encrypted blob to the bucket it ALREADY uses and
        the node pins it -- the same shape as the existing result.txt flow.

        Protocol, per key:
            enclave writes  state.<key>.enc   (encrypted state blob)
            enclave writes  state.<key>.cid   (the CID IT computed itself)
            node pins the blob and confirms the CID matches its content

        THE NODE NEVER SUPPLIES THE CID. The node is untrusted: if it returned
        the CID, a malicious operator could pin the enclave's blob but hand back
        the CID of different content, and the enclave would sign THAT onto the
        chain -- clients would then fetch attacker-chosen state believing the
        enclave authored it. Because a CID is a hash of the content, the enclave
        derives it from the bytes it just encrypted and commits that. A hostile
        node can refuse to pin, or pin something else, but it cannot change what
        was committed: substitution is impossible rather than merely detectable.

        That also means the enclave never waits on this: it commits as soon as it
        has written the blob, and pinning is fire-and-forget from its point of
        view. `deadline_ts` only bounds how long the node itself spends here.

        The node still verifies, so it fails loudly instead of pinning a blob
        whose CID does not match what the enclave published.

        Never raises: a state pin failing must not fail the task itself.
        """
        served = {}
        if not bucket_name:
            return served
        try:
            objects = self.swift_stream_service.list_object_names(bucket_name) or []
        except Exception:
            objects = []
        try:
            for name in list(objects):
                if not name or not name.startswith('state.') or not name.endswith('.enc'):
                    continue
                key = name[len('state.'):-len('.enc')]
                cid_object = f'state.{key}.cid'
                if name in served:
                    continue
                if time.time() > deadline_ts:
                    self.logger.warning('Deadline reached while serving ESR state pins')
                    break

                # The enclave publishes the CID it computed. Without it there is
                # nothing to verify against, so wait for it rather than invent one.
                has_cid, _ = self.swift_stream_service.is_object_in_bucket(bucket_name, cid_object)
                if not has_cid:
                    continue
                ok, declared_cid = self.swift_stream_service.get_file_content(bucket_name, cid_object)
                declared_cid = (declared_cid or '').strip()
                if not ok or not declared_cid:
                    continue

                ok, blob = self.swift_stream_service.get_file_content_bytes(bucket_name, name)
                if not ok or blob is None:
                    self.logger.warning(f'Could not read {name} for pinning')
                    continue

                # Confirm the enclave's CID actually describes this blob. A
                # mismatch means the blob and the committed pointer disagree --
                # pinning it would serve content that does not match the chain.
                computed = self.__cidv1_raw(blob)
                if computed != declared_cid:
                    self.logger.warning(
                        f'ESR state {name}: CID mismatch (enclave published {declared_cid}, '
                        f'content hashes to {computed}); not pinning'
                    )
                    continue

                # Queue the add/pin in the background and move on: a large state
                # blob must not hold up order processing, and a temporarily
                # unhealthy IPFS should retry rather than drop the state. The CID
                # is already known (the enclave computed it, we verified it), so
                # there is nothing to wait for.
                self.storage.pin_bytes_deferred(blob, name=name)
                self.ipfs_cache.add(declared_cid)
                served[name] = declared_cid
                self.logger.info(f'ESR state {name} -> {declared_cid} (pinning in background)')
        except Exception as e:
            self.logger.warning(f'Serving ESR state pins failed: {e}')
        return served

    def build_result_format_v1(self, result_hash, transaction_hex):
        return f'v1:{transaction_hex}:{result_hash}'

    def build_result_format_v2(self, result_hash, transaction_hex):
        return f'v2:{transaction_hex}:{result_hash}'

    def build_result_format_v3(self, result_hash, transaction_hex):
        return f'v3:{transaction_hex}:{result_hash}'

    def add_result_to_order(self, order_id, result):
        logger = self.logger

        logger.info(f'Packaging results for blockchain submission.')

        max_retries = 20
        retries = 0

        while True:
            try:
                unicorn_txn = self.__etny.functions._addResultToOrder(
                    order_id, result
                ).build_transaction(self.get_transaction_build())

                _hash = self.send_transaction(unicorn_txn)
                logger.info(f"TXID {_hash} pending... ")
                receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
                if receipt.status == 1:
                    logger.info(f"TXID {_hash} confirmed!")
                    self.dpreq_cache.add(self.__dprequest)
                    break
            except Exception as ex:
                retries += 1
                logger.warning(f"Warning while adding result to Order. Retry {retries}/{max_retries}. Warning Message: {ex}")
                if retries == max_retries:
                    logger.error("Maximum retries reached. Aborting.")
                    raise
                time.sleep(5)
        
    def upload_result_to_ipfs(self, result_file):
        """Blocking upload; kept for callers outside the v3 result path.

        The v3 path now uses Storage.pin_bytes_deferred instead: this blocks for
        up to 10 attempts with an IPFS restart between failures, and raises if
        they all fail, which could stall or lose an on-chain result submission.
        """
        response = self.storage.upload(result_file)
        return response

    def create_folder_v1(self, order_directory):
        if not os.path.exists(order_directory):
            os.makedirs(order_directory)

    def read_file(self, chanllenge_file):
        with open(chanllenge_file, "r") as file:
            contents = file.read()

        return contents

    def __create_empty_file(self, file_path: str) -> bool:
        logger = self.logger

        try:
            open(file_path, 'w').close()
        except OSError:
            logger.error('Failed creating the file')
            return False

        logger.info('File created')
        return True

    def build_prerequisites_v1(self, order_id, payload_file, input_file, docker_compose_file, challenge):
        logger = self.logger

        self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
        self.create_folder_v1(self.order_folder)
        self.copy_order_files(payload_file, f'{self.order_folder}/payload.py')
        if input_file is not None:
            self.copy_order_files(input_file, f'{self.order_folder}/input.txt')
        else:
            status = self.__create_empty_file(f'{self.order_folder}/input.txt')
            if not status:
                raise Exception("Could not create context.")

        self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'

        self.copy_order_files(docker_compose_file, self.order_docker_compose_file)
        self.set_retry_policy_on_fail_for_compose()

        self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)
        env_content = self.get_enclave_env_dictionary(order_id, challenge)
        self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

    def build_prerequisites_v3(self, bucket_name, order_id, payload_file, input_file, docker_compose_file, challenge):
        logger = self.logger

        logger.debug('Cleaning up swift-stream bucket.')
        self.swift_stream_service.delete_bucket(bucket_name)
        logger.debug('Creating new bucket.')
        self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
        self.create_folder_v1(self.order_folder)
        (status, msg) = self.swift_stream_service.create_bucket(bucket_name)
        if not status:
            logger.error(msg)

        self.payload_file_name = "payload.etny"
        (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                              self.payload_file_name,
                                                              payload_file)
        if not status:
            logger.error(msg)

        self.input_file_name = "input.txt"
        if input_file is None:
            (status, msg) = self.swift_stream_service.put_file_content(bucket_name,
                                                                       self.input_file_name,
                                                                       "",
                                                                       io.BytesIO(b""))
        else:
            (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                                  self.input_file_name,
                                                                  input_file)
        if (not status):
            logger.error(msg)

        self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'
        self.copy_order_files(docker_compose_file, self.order_docker_compose_file)

        self.set_retry_policy_on_fail_for_compose()
        self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)

        env_content = self.get_enclave_env_dictionary(order_id, challenge)
        self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

        (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                              ".env",
                                                              f'{self.order_folder}/.env')
        if not status:
            logger.error(msg)

    def set_retry_policy_on_fail_for_compose(self):
        with open(self.order_docker_compose_file, "r") as f:
            content = f.read()
        content = content.replace("restart: on-failure", "restart: on-failure:20")
        with open(self.order_docker_compose_file, "w") as f:
            f.write(content)

    def copy_order_files(self, source, dest):
        logger = self.logger

        if os.path.isfile(source):
            shutil.copy(source, dest)
        else:
            logger.debug('The copied path is not a file')

    def generate_enclave_env_file(self, env_file, env_dictionary):
        with open(env_file, 'w') as f:
            for key, value in env_dictionary.items():
                f.write(f'{key}={value}\n')
        f.close()

    def get_enclave_env_dictionary(self, order_id, challenge):
        env_vars = {
            "ETNY_CHAIN_ID": self.__network_config.chain_id,
            "ETNY_SMART_CONTRACT_ADDRESS": self.__network_config.contract_address,
            "ETNY_WEB3_PROVIDER": self.__network_config.rpc_url,
            "ETNY_CLIENT_CHALLENGE": challenge,
            "ETNY_ORDER_ID": order_id,
            "ETNY_NGROK_AUTHTOKEN": "DEFAULT"
        }
        return env_vars

    def update_enclave_docker_compose(self, docker_compose_file, order):
        with open(docker_compose_file, 'r') as f:
            contents = f.read()

        contents = contents.replace('[ETNY_ORDER_ID]', str(order))
        with open(docker_compose_file, 'w') as f:
            f.write(contents)

    def _getOrder(self):
        logger = self.logger

        order_id = self.find_order_by_dp_req()
        if order_id is not None:
            order = Order(self.__etny.caller()._getOrder(order_id))
            return [order_id, order]
        return None

    def __can_place_order(self, dp_req_id: int, do_req_id: int) -> bool:
        logger = self.logger

        """
        Determines if we can place an order at the current block,
        with special handling if we're still in the 'first cycle'.

        :param dp_req_id: Data Processesor request id
        :param do_req_id: Data Owner request id
        :return: True if this node can place an order now; otherwise False.
        """

        current_block_number = self.__w3.eth.block_number

        # Decide how many nodes can place orders per block
        # Use max(1, ...) to avoid zero if total_nodes_count < 25
        if self.__network == 'TESTNET':
            dispersion_factor = 1
        else:
            dispersion_factor = max(1, self.__total_nodes_count // 25)
            logger.debug(
                f"Dispersion factor set to {dispersion_factor} for "
                f"{self.__total_nodes_count} registered nodes"
            )

        # Compute an integer offset from current block + dp_req_id
        offset = current_block_number + dp_req_id

        # Compare offset's position in the cycle to do_req_id's position
        offset_mod = offset % dispersion_factor
        do_req_id_mod = do_req_id % dispersion_factor

        # difference_raw tells us how far 'offset_mod' is from 'do_req_id_mod':
        #   == 0 => aligned now
        #   >  0 => we haven't reached do_req_id_mod yet in this cycle
        #   <  0 => we've already passed do_req_id_mod in this cycle
        difference_raw = do_req_id_mod - offset_mod

        # CASE 1: Perfect alignment this block
        if difference_raw == 0:
            return True

        # CASE 2: difference_raw > 0 => we are still "early"
        if difference_raw > 0:
            next_block = current_block_number + difference_raw
            logger.debug(
                f"Offset={offset_mod}, required={do_req_id_mod}; "
                f"waiting {difference_raw} more block(s). Next block: {next_block}."
            )
            logger.info(f"DO Request {do_req_id} will be processed after block #{next_block}, current block is #{current_block_number}")
            # Mark __is_first_cycle as False once we pass do_req_id_mod
            self.__is_first_cycle[do_req_id] = False
            return False

        # CASE 3: difference_raw < 0 => we've missed our slot in the current cycle
        if self.__is_first_cycle[do_req_id]:
            # If it's the first cycle, we choose NOT to skip it, and wait for the next slot
            # We wait for the next cycle.
            difference_next_cycle = difference_raw % dispersion_factor
            next_block = current_block_number + difference_next_cycle
            logger.debug(
                f"Offset={offset_mod}, required={do_req_id_mod}; "
                f"we missed our slot in the FIRST cycle (diff={difference_raw}). "
                f"Next block: {next_block}."
            )
            logger.info(f"Request will be processed after block #{next_block}, current block is #{current_block_number}")
            return False
        else:
            # On subsequent cycles, if we missed our slot, skip waiting.
            logger.debug(
                f"Offset={offset_mod}, required={do_req_id_mod}; "
                f"we missed our slot (diff={difference_raw}), "
                f"but it's NOT the first cycle, so place the order now."
            )
            self.__is_first_cycle[do_req_id] = False
            return True

    def process_dp_request(self):
        logger = self.logger
       
        order_details = self._getOrder()
        timeout_in_seconds = int(self.__network_config.block_time) - 1.3

        if order_details is not None:
            [order_id, order] = order_details
            if order.status == OrderStatus.PROCESSING:
                logger.debug(f"DP request never finished, processing order {order_id}")

                while not stop_event.is_set():
                    time.sleep(timeout_in_seconds)

                    if stop_event.is_set():
                        return

                    if get_task_running_on():
                        continue

                    break

                self.process_order(order_id)
            if order.status == OrderStatus.CLOSED:
                logger.debug(f"DP request {self.__dprequest} completed successfully!")
                self.dpreq_cache.add(self.__dprequest)
            if order.status == OrderStatus.OPEN:
                logger.debug("Order was never approved, skipping")
            return

        logger.debug(f"Processing DP request {self.__dprequest}")
        time.sleep(self.__network_config.rpc_delay/1000)
        resp, req_id = retry(self.__etny.caller()._getDPRequest, self.__dprequest, attempts=10, delay=3)
        if resp is False:
            logger.info(f"DP {self.__dprequest} wasn't found")
            return

        req = DPRequest(req_id)


        if req.status != RequestStatus.AVAILABLE:
            logger.debug(
                f'''Skipping Order, DORequestId = {_doreq[i]}, DPRequestId = {i}, Order has different status: '{RequestStatus._status_as_string(doreq[i].status)}' ''')
            return

        checked = 0
        seconds = 0

        self.__total_nodes_count = self.__heart_beat.caller().getNodesCount()

        _doreq = {}
        doreq = {}
        metadata = {}

        if not self.can_run_under_sgx:
            logger.error('SGX is not enabled or correctly configured. Agent will not perform requests on this network')
        else:
            logger.info(f"System ready for the next DO request")

        next_dp_request = False

        while not stop_event.is_set():

            time.sleep(timeout_in_seconds)

            try:
                self.__call_heart_beat()

                # Reclaim expired pins. Throttled internally to at most once per
                # IPFS_CLEANUP_INTERVAL_MINUTES, so this is a cheap timestamp
                # check on most passes. Previously cleanup only ran at startup,
                # so a long-lived node never reclaimed anything.
                self.__maybe_clear_ipfs_cache()

                self.storage.connect(1)

                if get_task_running_on():
                     continue

                if stop_event.is_set():
                     break

                time.sleep(self.__network_config.rpc_delay/1000)
                count = self.__etny.caller()._getDORequestsCount()
                checked = 0
            except Exception as e:
                logger.warning(f"Warning while trying to get DORequestCount, Message: {e}")
                continue

            if count == 0:
                continue;

            cached_do_requests = self.doreq_cache.get_values

            req_to_process = list(set(range(checked, count)) - set(cached_do_requests))

            total_requests = len(req_to_process)
            threshold = 0

            for idx, i in enumerate(reversed(req_to_process), start=1):

                self.__call_heart_beat()

                if stop_event.is_set():
                    break

                percent_complete = (idx * 100) // total_requests

                if percent_complete >= threshold and self.__do_requests_build_pending:
                    logger.info(f"Building DO Requests cache: {percent_complete}% ({idx} / {total_requests})")
                    threshold += 1   # Increment to the next threshold

                if i not in metadata:
                    metadata[i] = [None, None, None, None, None]

                if metadata[i][4] is None:
                    logger.debug(f"Fetching DO request and metadata for index {i}")

                    def _fetch_once() -> bool:
                        raw = self.__etny.caller()._getDORequest(i)
                        doreq[i] = DORequest(raw)
                        meta = self.__etny.caller()._getDORequestMetadata(i)
                        if meta[4] is None:
                            raise ValueError("metadata field[4] is still None")
                        metadata[i] = meta
                        return True

                    attempts = 20
                    delay_s = self.__network_config.rpc_delay / 1000.0

                    success, _ = retry(_fetch_once, attempts=attempts, delay=delay_s)

                    if not success:
                        logger.info(
                            f"Failed to fetch DORequest/metadata after {attempts} attempts; skipping to next DO request"
                        )
                        next_dp_request = True
                        reset_task_running_on()
                        break

                    logger.debug("Fetch succeeded; proceeding with DO request processing.")

                if not (doreq[i].cpu <= req.cpu and doreq[i].memory <= req.memory and
                        doreq[i].storage <= req.storage and doreq[i].bandwidth <= req.bandwidth and doreq[i].price >= req.price):
                    self.doreq_cache.add(i)
                    logger.debug("Not enough resources to process this DO request, skipping to next request")
                    logger.debug(f"resource:requested/available| cpu:{doreq[i].cpu}/{req.cpu} memory:{doreq[i].memory}/{req.memory} storage:{doreq[i].storage}/{req.storage} bandwidth:{doreq[i].bandwidth}/{req.bandwidth} price:{doreq[i].price}/{req.price}");
                    continue


                if metadata[i][4] != '' and metadata[i][4] != self.__address:
                    logger.debug(f'Skipping DO Request: {i}. Request is delegated to a different Node.')
                    self.doreq_cache.add(i)
                    continue

                if metadata[i][4] == '':
                    status = self.__can_place_order(self.__dprequest, i)
                    if not status:
                        continue

                while True:
                    try:
                        time.sleep(self.__network_config.rpc_delay/1000)
                        _doreq[i] = self.__etny.caller()._getDORequest(i)
                        doreq[i] = DORequest(_doreq[i])
                        break
                    except Exception as e:
                        logger.warning(f"Failed to read DO request metadata")

                if doreq[i].status != RequestStatus.AVAILABLE:
                    logger.debug(
                        f'''Skipping Order, DORequestId = {_doreq[i]}, DPRequestId = {i}, Order has different status: '{RequestStatus._status_as_string(doreq[i].status)}' ''')

                    logger.info(f"DO request {i} is matched with another operator, skipping processing")
                    self.doreq_cache.add(i)
                    continue

                if self._check_installed_drivers():
                    logger.error('SGX configuration error. Both isgx drivers are installed. Skipping order placing ...')
                    self.doreq_cache.add(i)
                    continue

                if not self.can_run_under_sgx:
                    logger.info(f"Ignoring DO Request {i} on {self.__network}")
                    self.doreq_cache.add(i)
                    continue

                set_task_running_on(self.__network)

                logger.info(f"DO Request {i} detected. Starting order placement. ")
                try:
                    self.place_order(i)
                    self.doreq_cache.add(i)

                    # store merged log
                    self.merged_orders_cache.add(do_req_id=i, dp_req_id=self.__dprequest, order_id=self.__order_id)

                except (exceptions.ContractLogicError, IndexError) as e:
                    logger.warning(f"Falied placing order: {e}")
                    reset_task_running_on()
                    # If OUR DP request is the matched/consumed one, no further
                    # order can ever be placed with it -- break out so a fresh
                    # DP request is created. Continuing here made every
                    # subsequent placement fail with 'DP request already
                    # matched' and the node stopped taking orders entirely.
                    if 'DP request' in str(e) and 'already matched' in str(e):
                        logger.info(
                            f"DP request {self.__dprequest} is consumed; "
                            f"moving to a new DP request")
                        next_dp_request = True
                        break
                    continue

                if metadata[i][4] == '':
                    logger.info(f"Awaiting approval for order")
                    attempts = int(60 / self.__network_config.block_time)
                    if retry(self.wait_for_order_approval, attempts=attempts, delay=self.__network_config.block_time)[0] is False:
                        logger.info(f"Order was not approved in the last ~{attempts} blocks, skipping to next DP request")
                        next_dp_request = True
                        reset_task_running_on()
                        break

                    logger.info(f"Approval granted. Order processing continues.")

                try:
                    self.process_order(self.__order_id)
                    logger.info(
                        f"Order {self.__order_id} (DO request {i}, DP request {self.__dprequest}) completed.")
                    reset_task_running_on()
                    next_dp_request = True
                    break
                except Exception as e:
                    logger.error(f"Unable to process order {self.__order_id}: {e}")
                    self.doreq_cache.rem(i)

                    self.merged_orders_cache.rem(order_id=self.__order_id)

                    reset_task_running_on()

            if self.__do_requests_build_pending and threshold > 0:
                logger.info(f"Building DO Requests cache: 100%")
                logger.info("Finished building DO requests cache")
                logger.info("System ready for the next DO request")

            self.__do_requests_build_pending = False

            if next_dp_request == True:
                break

        self.storage.repo_gc() # Running garbage colleciton on ipfs before exiting


    def wait_for_order_approval(self):
        logger = self.logger
        
        
        _order = self.__etny.caller()._getOrder(self.__order_id)
        order = Order(_order)
        #logger.info('Waiting...')
        if order.status != OrderStatus.PROCESSING:
            raise Exception("Order has not been yet approved")

    def find_order_by_dp_req(self):
        logger = self.logger

        logger.debug(f"Checking if DP request {self.__dprequest} has an order associated")

        order_id = self.orders_cache.get(str(self.__dprequest))
        if order_id is not None:
            logger.debug(f"Found in cache, order_id = {order_id}")
            return order_id

        my_orders = self.__etny.functions._getMyDOOrders().call({'from': self.__address})
        cached_order_ids = self.orders_cache.get_values

        orders_to_process = list(set(my_orders))
        total_requests = len(orders_to_process)
        threshold = 0
        building = False

        for idx, _order_id in enumerate(reversed(orders_to_process), start=1):

            self.__call_heart_beat()

            if stop_event.is_set():
                break

            if _order_id in cached_order_ids:
                dp_req = self.orders_cache.get_key(_order_id)
                order = {'dp_req': dp_req}
                self.__orders[_order_id] = order
                order_dp_req = dp_req
            else:
              try:

                if _order_id not in self.__orders or self.__orders[_order_id] is None:
                    building = True
                    percent_complete = (idx * 100) // total_requests

                    if percent_complete >= threshold and idx > 1:
                        logger.info(f"Building orders cache: {percent_complete}% ({idx} / {total_requests})")
                        threshold += 10

                    time.sleep(self.__network_config.rpc_delay/1000)
                    self.__orders[_order_id] = self.__etny.caller()._getOrder(_order_id)

                order = Order(self.__orders[_order_id])
                self.orders_cache.add(order.dp_req, _order_id)
                order_dp_req = order.dp_req

              except Exception as e:
                logger.error(f"Unable to find order: {e}")

            if order_dp_req == self.__dprequest:
                return _order_id

        if total_requests > 1 and building == True and not stop_event.is_set():
            logger.info(f"Building orders cache: 100%")
            logger.info(f"Finished building orders cache")

        logger.debug(f"DP request {self.__dprequest} hash no order associated")
        return None

    def place_order(self, doreq_id):
        logger = self.logger

        order_id = 0
        max_retries = 20
        retries = 0

        while True:
          try:
            time.sleep(self.__network_config.rpc_delay/1000)
            # Build the transaction INSIDE the retry loop so every attempt gets a
            # fresh nonce. A reverted transaction still consumes its nonce, so
            # re-sending the same pre-built txn after a revert (or after any
            # other transaction from this account) fails every retry with
            # "Transaction nonce is too low" until max_retries aborts the
            # placement -- observed live as a 20x nonce-too-low cascade.
            unicorn_txn = self.__etny.functions._placeOrder(
                    int(doreq_id),
                    int(self.__dprequest),
            ).build_transaction(self.get_transaction_build())
            _hash = self.send_transaction(unicorn_txn)
            logger.info(f"TXID {_hash} pending... fingers crossed")
            receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)

            if receipt.status == 1:
              logger.info(f"TXID {_hash} confirmed!")
              break
            else:
              logger.info(f"TXID {_hash} is reverted")

            _doreq = self.__etny.caller()._getDORequest(doreq_id)
            _dpreq = self.__etny.caller()._getDPRequest(self.__dprequest)

            doreq = DORequest(_doreq)
            dpreq = DPRequest(_dpreq)

            # Raise ContractLogicError so the handler below PROPAGATES the skip
            # instead of swallowing it into the generic retry path (a bare
            # `raise` here has no active exception -> RuntimeError -> 20 futile
            # reverted placements against an already-matched request).
            if doreq.status != RequestStatus.AVAILABLE:
                  logger.info(f"DO request {doreq_id} is matched with another operator, skipping processing")
                  self.doreq_cache.add(doreq_id)
                  raise exceptions.ContractLogicError(f"DO request {doreq_id} already matched")

            if dpreq.status != RequestStatus.AVAILABLE:
                  logger.info(f"DP request {self.__dprequest} is matched with another order, skipping processing")
                  self.doreq_cache.add(doreq_id)
                  raise exceptions.ContractLogicError(f"DP request {self.__dprequest} already matched")

          except (exceptions.ContractLogicError, IndexError) as e:
              logger.warning(f"ContractLogicError: {e}");
              raise
          except Exception as ex:
              retries += 1
              logger.warning(f"Error while placing Order. Retry {retries}/{max_retries}. Error Message: {ex}")
              if retries == max_retries:
                  logger.error("Maximum retries reached. Aborting.")
                  raise
              time.sleep(5)
          continue

        while True:
          try:
            time.sleep(self.__network_config.rpc_delay/1000)
            processed_logs = self.__etny.events._placeOrderEV().process_receipt(receipt)
            order_id = processed_logs[0].args._orderNumber
            if order_id != None:
                self.__order_id = order_id
                break
          except Exception as e:
            logger.warn(f"Exception while parsing transaction receipt: {e}")
            logger.warn(f"{receipt}")
            continue

        logger.info(f"Order {self.__order_id} secured!")

    def get_transaction_build(self, existing_nonce=None):
        logger = self.logger

        self.__nonce = existing_nonce if existing_nonce else self.__w3.eth.get_transaction_count(self.__address)

        if self.__network_config.eip1559 == True:
            latest_block = self.__w3.eth.get_block("latest")
            max_fee_per_gas = int(latest_block.baseFeePerGas * 1.1) + self.__w3.to_wei(self.__network_config.max_priority_fee_per_gas, self.__network_config.gas_price_measure) # 10% increase in previous block gas price + priority fee

            if max_fee_per_gas > self.__w3.to_wei(self.__network_config.max_fee_per_gas, self.__network_config.gas_price_measure):
                raise Exception("Network base fee is too high!")
                
            transaction_options = {
                "type": 2,
                "nonce": self.__nonce,
                "chainId": self.__network_config.chain_id,
                "from": self.__acct.address,
                'maxFeePerGas': max_fee_per_gas,
                'maxPriorityFeePerGas': self.__w3.to_wei(self.__network_config.max_priority_fee_per_gas, self.__network_config.gas_price_measure),
            }
            
            gas_price_value = max_fee_per_gas
            
        else:
            transaction_options = {
                "nonce": self.__nonce,
                "chainId": self.__network_config.chain_id,
                "from": self.__acct.address,
                "gasPrice": self.__w3.to_wei(self.__network_config.gas_price, self.__network_config.gas_price_measure),
                "gas": self.__network_config.gas_limit,
            }
 
            gas_price_value = self.__w3.to_wei(self.__network_config.gas_price, self.__network_config.gas_price_measure)


        logger.debug(f"Sending transaction using eip1559 = {self.__network_config.eip1559}, gasPrice = {self.__w3.from_wei(gas_price_value, 'gwei')} gwei");

        return transaction_options


    def __esr_fee(self):
        """(effective_price_per_gas, tx_fee_fields) for a relayed commit.

        Uses the network's own eip1559 flag (the same one the node uses for its
        result tx) so Bloxberg gets a legacy gasPrice and LitVM/mainnets get
        type-2 fields. The effective price is what the budget is measured in, so
        it matches the trustedzone's independent valuation.
        """
        if getattr(self.__network_config, 'eip1559', False):
            base = self.__w3.eth.get_block('latest')['baseFeePerGas']
            prio = self.__w3.to_wei(
                self.__network_config.max_priority_fee_per_gas,
                self.__network_config.gas_price_measure)
            max_fee = int(base * 1.1) + prio
            return max_fee, {'type': 2, 'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': prio}
        gp = self.__w3.to_wei(self.__network_config.gas_price,
                              self.__network_config.gas_price_measure)
        return gp, {'gasPrice': gp}

    def __serve_session(self, bucket_name, order_id):
        """Transport for interactive sessions: deliver etny-si inputs from
        IPFS into the enclave bucket, and broadcast the enclave's signed
        etny-so output proofs as DP-request metadata rows.

        The node is a PURE RELAY here: inputs are authenticated by the chain
        (only the data owner's wallet can write the rows) and verified
        against their on-chain digests before delivery; outputs are signed by
        the task wallet inside the enclave, so this node can delay but never
        read, alter or inject session traffic. Idempotent, called from the
        enclave-wait loop plus once as a final sweep before the result tx so
        late notices and the completion land back-to-back.
        """
        if not hasattr(self, '_session_state') or self._session_order != order_id:
            self._session_order = order_id
            order = Order(self.__etny.caller()._getOrder(order_id))
            meta = self.__etny.caller()._getDORequestMetadata(order.do_req)
            is_session = str(meta[3] or '').split(':')[0] == 'v3s'
            self._session_state = {
                'active': is_session,
                'do_req': int(order.do_req),
                'dp_req': int(order.dp_req),
                'rows_seen': 0,
                'delivered': set(),
                'proof_next': 0,
                'spent': 0,
            }
            if is_session:
                logger.info(f"[session] order {order_id}: interactive session detected "
                            f"(request {order.do_req}, dp request {order.dp_req})")
        st = self._session_state
        if not st['active']:
            return
        self.__session_deliver_inputs(bucket_name, order_id, st)
        self.__session_relay_outputs(bucket_name, order_id, st)

    def __session_deliver_inputs(self, bucket_name, order_id, st):
        """Fetch newly committed input CIDs and drop the ciphertext into the
        bucket as session.input.<seq>. A row whose content is not yet
        fetchable is retried next tick; a row whose content contradicts its
        on-chain digest is never delivered."""
        try:
            count = int(self.__etny.caller()._getMetadataCountForRequest(st['do_req']))
        except Exception as e:
            logger.debug(f"[session] order {order_id}: metadata count read failed: {e}")
            return
        while st['rows_seen'] < count:
            i = st['rows_seen']
            try:
                key, value = self.__etny.caller()._getMetadataValueForRequest(st['do_req'], i)
            except Exception as e:
                logger.debug(f"[session] order {order_id}: row {i} read failed: {e}")
                return
            st['rows_seen'] = i + 1
            if key != 'etny-si':
                continue
            parts = str(value or '').split(':')
            if len(parts) != 5 or parts[0] != 'v1':
                continue  # close rows and foreign formats are for the enclave
            try:
                seq, row_order = int(parts[1]), int(parts[2])
                cid, sha_hex = parts[3].strip(), parts[4].strip().lower()
            except ValueError:
                continue
            if row_order != order_id or seq in st['delivered']:
                continue
            object_name = f"session.input.{seq}"
            present, _ = self.swift_stream_service.is_object_in_bucket(bucket_name, object_name)
            if present:
                st['delivered'].add(seq)
                continue
            try:
                self.storage.download(cid)
                path = os.path.join(self.storage.target, cid)
                with open(path, 'rb') as fh:
                    blob = fh.read()
            except Exception as e:
                logger.warning(f"[session] order {order_id}: input {seq} ({cid}) "
                               f"not fetchable yet: {e}")
                st['rows_seen'] = i  # retry this row next tick
                return
            if hashlib.sha256(blob).hexdigest() != sha_hex:
                logger.error(f"[session] order {order_id}: input {seq} content does not "
                             f"match its on-chain digest; refusing to deliver")
                st['delivered'].add(seq)
                continue
            self.swift_stream_service.put_file_content(
                bucket_name, object_name, '', io.BytesIO(blob))
            st['delivered'].add(seq)
            logger.info(f"[session] order {order_id}: delivered input {seq}")

    def __session_relay_outputs(self, bucket_name, order_id, st):
        """Pin each staged output ciphertext (verified against the CID the
        enclave attested inside its signed proof) and broadcast the proof as
        a DP-request metadata row from this node's wallet. Strictly in proof
        order; shares the ESR relay's per-order gas budget discipline."""
        budget = int(config.esr_relay_gas_budget_wei)
        while True:
            proof_name = f"session.output.{st['proof_next']}.proof"
            ok, proof = self.swift_stream_service.get_file_content(bucket_name, proof_name)
            if not ok or not proof:
                return
            proof = proof.strip()
            parts = proof.split(':')
            # v1:<seq>:<orderId>:<ack>:<status>:<code>:<cid>:<sha256>:<sig>
            if len(parts) != 9 or parts[0] != 'v1':
                logger.error(f"[session] order {order_id}: malformed proof {proof_name}; skipping")
                st['proof_next'] += 1
                continue
            status_field, cid = parts[4], parts[6]
            if cid:
                okb, blob = self.swift_stream_service.get_file_content_bytes(
                    bucket_name, f"session.output.{st['proof_next']}.bin")
                if not okb or blob is None:
                    return  # ciphertext not staged yet; retry next tick
                if self.__cidv1_raw(blob) != cid:
                    logger.error(f"[session] order {order_id}: output {st['proof_next']} bytes "
                                 f"do not match the attested CID; refusing to pin")
                else:
                    self.storage.pin_bytes_deferred(
                        blob, name=f"session-output-{order_id}-{st['proof_next']}")
            fn = self.__etny.functions._addMetadataToDPRequest(st['dp_req'], 'etny-so', proof)
            gas_price, fee_fields = self.__esr_fee()
            try:
                gas_units = fn.estimate_gas({'from': self.__address})
            except Exception as e:
                logger.error(f"[session] order {order_id}: proof {st['proof_next']} "
                             f"estimate_gas failed ({e}); retrying next tick")
                return
            cost = gas_units * gas_price
            if st['spent'] + cost > budget:
                logger.error(f"[session] order {order_id}: output relay would exceed the "
                             f"per-order gas budget ({st['spent'] + cost} > {budget} wei); "
                             f"stopping relay")
                return
            try:
                opts = {
                    'from': self.__address,
                    'nonce': self.__w3.eth.get_transaction_count(self.__address),
                    'chainId': self.__network_config.chain_id,
                    'gas': gas_units + 30000,
                }
                opts.update(fee_fields)
                txn = fn.build_transaction(opts)
                self.__w3.eth.wait_for_transaction_receipt(
                    self.__w3.eth.send_raw_transaction(
                        self.__w3.eth.account.sign_transaction(
                            txn, private_key=self.__acct.key).raw_transaction),
                    timeout=180)
                st['spent'] += cost
                logger.info(f"[session] order {order_id}: broadcast output row "
                            f"{st['proof_next']} ({status_field}, gas {gas_units})")
                st['proof_next'] += 1
            except Exception as e:
                logger.error(f"[session] order {order_id}: broadcasting output "
                             f"{st['proof_next']} failed ({e})")
                return

    def __relay_esr_commits(self, bucket_name, order_id):
        """Relay the ESR state commits the enclave signed for this order.

        The enclave signs each commit (commitFor) and stages it as
        esr.commit.<key16>.<relayNonce>.json; the NODE submits it and PAYS.
        Relay nonces are PER (enclave, key): each key's commits relay strictly
        in order, different keys are independent. To stop a malicious payload
        from draining the operator, the node keeps a running per-order gas
        total and REFUSES to relay a commit that would push it over
        config.esr_relay_gas_budget_wei. A refusal is not fatal here -- the
        trustedzone independently re-prices the whole ledger and terminates the
        order, so the node just protects its wallet and moves on.

        IDEMPOTENT and called REPEATEDLY: the enclave stages commits during
        execution and the trustedzone waits (up to 5 blocks) for them to land
        before signing the result, so this runs from inside the enclave-wait
        loop as files appear, plus once more as a final sweep. Already-relayed
        files are tracked per order and skipped.
        """
        if self.__esr is None:
            return  # ESR not deployed on this network
        if not hasattr(self, '_esr_relayed_files') or self._esr_relay_order != order_id:
            self._esr_relayed_files = set()
            self._esr_relay_spent = 0
            self._esr_relay_order = order_id
        try:
            names = self.swift_stream_service.list_object_names(bucket_name) or []
        except Exception as e:
            logger.debug(f"[esr-relay] could not list bucket {bucket_name}: {e}")
            return

        def _order_key(n):
            # esr.commit.<key16>.<rn>.json -> (key16, rn); legacy
            # esr.commit.<rn>.json -> ('', rn). Per-key order is what matters.
            parts = n.split('.')
            try:
                if len(parts) == 5:
                    return (parts[2], int(parts[3]))
                return ('', int(parts[2]))
            except (ValueError, IndexError):
                return ('~', 0)

        commit_files = sorted(
            (n for n in names if n.startswith('esr.commit.') and n.endswith('.json')
             and n not in self._esr_relayed_files),
            key=_order_key)
        # PER-RUN CAP: never relay more than 256 commits for one order -- the
        # trustedzone fails the task (code 38) beyond that anyway, so paying
        # for the excess would be pure waste.
        already = len(self._esr_relayed_files)
        if already + len(commit_files) > 256:
            keep = max(0, 256 - already)
            logger.error(f"[esr-relay] order {order_id}: {already + len(commit_files)} staged "
                         f"commits exceed the per-run cap of 256; relaying only {keep} more")
            commit_files = commit_files[:keep]
        if not commit_files:
            return

        budget = int(config.esr_relay_gas_budget_wei)
        gas_price, fee_fields = self.__esr_fee()
        spent = self._esr_relay_spent
        relayed = 0
        for name in commit_files:
            ok, raw = self.swift_stream_service.get_file_content(bucket_name, name)
            if not ok or not raw:
                continue
            try:
                a = json.loads(raw)
                enclave = self.__w3.to_checksum_address(a['enclave'])
                key_hash = bytes.fromhex(a['keyHash'][2:])
                cid = a['cid']
                ev = int(a['expectedVersion'])
                rn = int(a['relayNonce'])
                # PUBLIC idempotency nonce. 0 = omitted: the registry assigns
                # the next value in sequence itself; non-zero is client-pinned
                # and must be exactly stored + 1. Required field -- every
                # authorization carries it. Signature-bound: part of the
                # digest the enclave signed, so altering or stripping it here
                # would just make commitFor revert.
                idem = int(a['nonce'])
                sig = bytes.fromhex(a['signature'][2:])
            except Exception as e:
                logger.error(f"[esr-relay] {name} malformed ({e}) -- skipping")
                continue

            fn = self.__esr.functions.commitFor(enclave, key_hash, cid, ev, rn, idem, sig)
            try:
                gas_units = fn.estimate_gas({'from': self.__address})
            except Exception as e:
                logger.error(f"[esr-relay] {name} estimate_gas failed ({e}) -- skipping")
                continue
            cost = gas_units * gas_price
            if spent + cost > budget:
                logger.error(
                    f"[esr-relay] order {order_id}: commit nonce {rn} would exceed the "
                    f"per-order gas budget ({spent + cost} > {budget} wei); refusing to pay. "
                    f"The trustedzone will terminate the order (ESR_GAS_LIMIT_EXCEEDED).")
                break  # stop; nonces are ordered, later ones can't be relayed anyway

            try:
                opts = {
                    'from': self.__address,
                    'nonce': self.__w3.eth.get_transaction_count(self.__address),
                    'chainId': self.__network_config.chain_id,
                    'gas': gas_units + 30000,
                }
                opts.update(fee_fields)
                txn = fn.build_transaction(opts)
                self.__w3.eth.wait_for_transaction_receipt(
                    self.__w3.eth.send_raw_transaction(
                        self.__w3.eth.account.sign_transaction(
                            txn, private_key=self.__acct.key).raw_transaction),
                    timeout=180)
                spent += cost
                self._esr_relay_spent = spent
                relayed += 1
                self._esr_relayed_files.add(name)
                logger.info(f"[esr-relay] order {order_id}: relayed ESR commit {name} "
                            f"(gas {gas_units}, spent {spent}/{budget} wei)")
            except Exception as e:
                logger.error(f"[esr-relay] order {order_id}: relaying {name} failed ({e})")
                # A genuine failure (e.g. VersionMismatch) is not abuse; skip
                # THIS KEY's remaining commits (its relay nonces are now
                # gapped) but keep relaying other keys -- per-key nonces make
                # them independent.
                failed_key = _order_key(name)[0]
                self._esr_relayed_files.add(name)
                for later in commit_files:
                    if later != name and _order_key(later)[0] == failed_key:
                        self._esr_relayed_files.add(later)
                continue
        if relayed:
            logger.info(f"[esr-relay] order {order_id}: relayed {relayed} ESR commit(s), "
                        f"paid for {spent} wei of gas total this order")

    def send_transaction(self, unicorn_txn):
        try:
            signed_txn = self.__w3.eth.account.sign_transaction(unicorn_txn, private_key=self.__acct.key)
            self.__w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            _hash = self.__w3.to_hex(self.__w3.keccak(signed_txn.raw_transaction))
            return _hash
        except Exception as e:
            logger.error(f"Error sending Transaction, Error Message: {e}")
            raise

    def resume_processing(self):
        while True and not stop_event.is_set():

            balance = self.__w3.eth.get_balance(self.__address)

            if balance < int(self.__network_config.minimum_gas_at_start):
                logger.error("Not enough gas to run on this network, exiting")
                break

            self.add_dp_request()
            self.process_dp_request()

    def _check_installed_drivers(self):
        driver_list = os.listdir('/dev')
        return 'isgx' in driver_list and 'sgx_enclave' in driver_list

    def get_env_for_integration_test(self):
        env_vars = {
            "ETNY_CHAIN_ID": self.__network_config.chain_id,
            "ETNY_SMART_CONTRACT_ADDRESS": self.__network_config.contract_address,
            "ETNY_WEB3_PROVIDER": self.__network_config.rpc_url,
            "ETNY_RUN_INTEGRATION_TEST": 1,
            "ETNY_ORDER_ID": 0
        }
        return env_vars

    def build_prerequisites_integration_test(self, bucket_name, order_id, docker_compose_file):
        logger = self.logger

        try:
            logger.debug('Cleaning up swift-stream bucket.')
            self.swift_stream_service.delete_bucket(bucket_name)
            logger.debug('Creating new bucket.')
            self.order_folder = f'./orders/{order_id}/etny-order-{order_id}'
            self.create_folder_v1(self.order_folder)
            (status, msg) = self.swift_stream_service.create_bucket(bucket_name)
            if not status:
                logger.error(msg)

            self.order_docker_compose_file = f'./orders/{order_id}/docker-compose.yml'
            self.copy_order_files(docker_compose_file, self.order_docker_compose_file)

            self.set_retry_policy_on_fail_for_compose()
            self.update_enclave_docker_compose(self.order_docker_compose_file, order_id)

            env_content = self.get_env_for_integration_test()
            self.generate_enclave_env_file(f'{self.order_folder}/.env', env_content)

            (status, msg) = self.swift_stream_service.upload_file(bucket_name,
                                                                  ".env",
                                                                  f'{self.order_folder}/.env')
            if not status:
                logger.error(msg)
        except Exception as e:
            raise Exception(f"Unable to preapre for integration test: {e}")

    def __clean_up_integration_test(self):
        logger = self.logger
        try: 
            logger.debug('Cleaning up containers after integration test.')
            run_subprocess([
                'docker-compose', '-f', self.order_docker_compose_file, 'down'
            ], logger)
            logger.debug('Cleaning up swift-stream integration bucket.')
            self.swift_stream_service.delete_bucket(self.integration_bucket_name)
        except Exception as e:
            logger.warning(f"Unable to clean container: {e}")

    def __run_integration_test(self):
        logger = self.logger

        logger.info('Running integration test.')

        [enclave_image_hash, _,
         docker_compose_hash] = self.__image_registry.caller().getLatestTrustedZoneImageCertPublicKey(self.__network_config.integration_test_image,
                                                                                                      'v3')
        self.integration_bucket_name = 'etny-bucket-integration'
        order_id = 'integration_test'
        integration_test_file = 'context_test.etny'

        logger.debug(f"Downloading IPFS Image: {enclave_image_hash}")
        logger.debug(f"Downloading IPFS docker yml file: {docker_compose_hash}")

        list_of_ipfs_hashes = [enclave_image_hash, docker_compose_hash]
        if not self.storage.download_many(list_of_ipfs_hashes, attempts=10, delay=3):
            logger.info("Cannot download data from IPFS, stopping test")
            return

        os.chdir(self.cache_config.base_path)

        logger.debug("Running docker swift-stream")
        run_subprocess(
            ['docker-compose', '-f', f'../docker/docker-compose-swift-stream.yml', 'up', '-d', 'swift-stream'],
            logger)

        docker_compose_file = f'{self.cache_config.base_path}/{docker_compose_hash}'
        logger.debug(f'Preparing prerequisites for integration test')

        try:
            self.build_prerequisites_integration_test(self.integration_bucket_name, order_id, docker_compose_file)
        except Exception as e:
            logger.error("Unable to build prerequisites, cleaning up cache")
            self.storage.rm(enclave_image_hash)
            self.ipfs_cache.rem(enclave_image_hash)
            self.storage.rm(docker_compose_hash)
            self.ipfs_cache.rem(docker_compose_hash)
            raise Exception(f"Integration test initialization failed: {e}")

        logger.debug("Stopping previous docker registry and containers")
        run_subprocess(['docker', 'stop', 'registry'], logger)
        run_subprocess(['docker', 'stop', 'etny-securelock'], logger)
        run_subprocess(['docker', 'stop', 'etny-trustedzone'], logger)
        run_subprocess(['docker', 'stop', 'las'], logger)
        logger.debug("Cleaning up docker registry")
        run_subprocess(['docker', 'system', 'prune', '-a', '-f', '--volumes'], logger)
        logger.debug("Running new docker registry")
        logger.debug( "{self.cache_config.base_path} / {enclave_image_hash} :/var/lib/registry")

        logger.debug("Stopping previous docker las")
        run_subprocess(['docker', 'stop', 'las'], logger)
        logger.debug("Removing previous docker las")
        run_subprocess(['docker', 'rm', 'las'], logger)
        run_subprocess([
            'docker', 'run', '-d', '--restart=always', '-p', '5000:5000', '--name', 'registry', '-v',
            f'{self.cache_config.base_path}/{enclave_image_hash}' + ':/var/lib/registry',
            'registry:2'
        ], logger)

        logger.debug("Started enclaves by running ETNY docker-compose")

        os.chdir(self.cache_config.base_path)

        logger.debug("Cleaning up docker container")
        run_subprocess([
            'docker-compose', '-f', self.order_docker_compose_file, 'down'
        ], logger)


        os.chdir(self.cache_config.base_path)

        run_subprocess([
            'docker-compose', '-f', self.order_docker_compose_file, 'up', '-d'
        ], logger)

        logger.debug('Waiting for execution of integration test enclave')
        status_enclave = self.wait_for_enclave_v2(self.integration_bucket_name, integration_test_file, 300)

        if status_enclave == True:
            status, result_data = self.swift_stream_service.get_file_content(self.integration_bucket_name,
                                                                             integration_test_file)
        else:
            status = None

        if not status:
            logger.warning('The node is not properly configured to run SGX tasks in production mode. Please check the configuration.')
            self.can_run_under_sgx = False
            self.__clean_up_integration_test()
            return False

        self.can_run_under_sgx = True
        set_integration_test_complete(self.__network_config.network_type.upper(), True)
        logger.info(f"Agent SGX capabilities tested and enabled successfully for {self.__network} ({self.__network_config.network_type.upper()})")
        self.__clean_up_integration_test()
        return True

    def __can_run_auto_update(self, file_path, interval):
        current_timestamp = int(time.time())
        if os.path.exists(file_path):
            with open(file_path, 'r') as file:
                value = file.read().strip()
                if not value.isdigit():
                    saved_timestamp = 0
                else:
                    saved_timestamp = int(value)

            if current_timestamp - saved_timestamp >= interval:
                return True
            else:
                return False
        else:
            return True


    def __write_auto_update_cache(self, file_path, interval):
        current_timestamp = int(time.time())
        if os.path.exists(file_path):
            with open(file_path, 'r') as file:
                value = file.read().strip()
                if not value.isdigit():
                    saved_timestamp = 0
                else:
                    saved_timestamp = int(value)

            if current_timestamp - saved_timestamp >= interval:
                with open(file_path, 'w') as file:
                    file.write(str(current_timestamp))
                return True
            else:
                return False
        else:
            with open(file_path, 'w') as file:
                file.write(str(current_timestamp))

            return True


    def __call_heart_beat(self):
        logger = self.logger

        if self.__network == 'TESTNET':
            heartbeat_frequency = 1 * 60 * 60 - 60;
        elif self.__network == 'POLYGON':
            heartbeat_frequency = 12 * 60 * 60 - 60;
        else:
            heartbeat_frequency = 12 * 60 * 60 - 60;

        if self.__can_run_auto_update(self.cache_config.heart_beat_log_file_path, heartbeat_frequency):

            if not self.can_run_under_sgx:
                self.__write_auto_update_cache(self.cache_config.heart_beat_log_file_path, heartbeat_frequency);
                logger.info('Skipping hearbeat on inactive network...');
                return

            logger.info('Calling hearbeat...')
            params = [
                "v" + config.version
            ]

            max_retries = 20
            retries = 0


            while True:
              try:
                time.sleep(self.__network_config.rpc_delay/1000)
                unicorn_txn = self.__heart_beat.functions.logCall(*params).build_transaction(self.get_transaction_build())
                _hash = self.send_transaction(unicorn_txn)
                logger.info(f"{_hash} pending... ")
                receipt = self.__w3.eth.wait_for_transaction_receipt(_hash)
                if receipt.status == 1:
                    logger.info(f"{_hash} confirmed!")
                    logger.info('Heart beat successfully called...')
                    self.__write_auto_update_cache(self.cache_config.heart_beat_log_file_path, heartbeat_frequency);
                    break
              except Exception as e:
                retries += 1
                logger.warning(f"Warning while sending heartbeat. Retry {retries}/{max_retries}. Message: {e}")
                if retries == max_retries:
                    logger.error("Maximum retries reached. Aborting.")
                    raise
                time.sleep(5)

class SGXDriver:
    def __init__(self):
        try:
            subprocess.call(['bash','../ubuntu/etny-node-provision-sgx.sh'])
        except Exception as e:
            pass

def process_network(network):
    """
    Processes a single network configuration.
    
    Args:
        network (NetworkConfig): The network configuration to process.
    
    Raises:
        Exception: Propagates exceptions after logging.
    """

    if stop_event.is_set():
        config.logger.warning(f"[{network.name}] Stopping network processing due to interrupt.")
        return

    try:
        app = EtnyPoXNode(network)
        app.cache_dp_requests()
        app.resume_pending_dp_requests()
        app.resume_available_dp_requests()
        app.resume_processing()

        logger.info(f"[{network.name}] Exiting")
        return(f"[{network.name}] Exiting")

    except Exception as e:
        logger.error(f"[{network.name}] An error occurred: {e}")
        raise  # Re-raise the exception after logging

def set_task_running_on(name):
    """
    Sets the shared network name in a thread-safe way.
    """
    global task_running_on
    with task_lock:
        task_running_on = name

def get_task_running_on():
    """
    Gets the shared network name in a thread-safe way.
    """
    global task_running_on 
    with task_lock:
        return task_running_on 

def reset_task_running_on():
    """
    Resets the shared network name to None in a thread-safe way.
    """
    global task_running_on
    with task_lock:
        task_running_on = None

_enclave_cleanup_lock = threading.Lock()
_enclave_cleanup_done = False

def terminate_stale_enclave_containers():
    """Force-remove any enclave containers left over from a previous run.

    The node agent may be restarted at any point in an order's lifecycle (manual
    restart, crash, deploy). If it restarts while an order's enclaves are up, the
    old containers survive -- and a stale container can poison the next order: a
    leftover integration-test trustedzone (which loads the integration .env, runs
    the test and exits) satisfies the container name the order compose expects, so
    the real order's trustedzone never runs, no payload.securelock is produced,
    and securelock waits forever while the node polls result.txt until it expires.

    Runs AT MOST ONCE per process (guarded by _enclave_cleanup_done): it is a
    startup-only clean slate. Any later call is a no-op -- critically, this stops
    it from ever tearing down enclaves of an order that is legitimately running,
    and keeps it from interfering with the periodic scheduler / thread restarts.
    Must therefore be invoked once, before any network thread launches an enclave.
    las is left running only if it is the shared, always-on attestation service;
    the per-order enclave containers are always removed. Best-effort: never raises.
    """
    global _enclave_cleanup_done
    with _enclave_cleanup_lock:
        if _enclave_cleanup_done:
            return
        _enclave_cleanup_done = True

    logger = config.logger
    # Fixed container_name values from the enclave docker-compose files. 'las'
    # is the per-order Local Attestation Service the order compose starts (exact
    # name 'las'); it is distinct from the shared, always-on 'las_Qm...' service,
    # which is matched by a different name and therefore untouched here.
    names = ['etny-securelock', 'etny-trustedzone', 'etny-validator', 'las']
    try:
        for name in names:
            run_subprocess(['docker', 'rm', '-f', name], logger)
        # Sweep any other stragglers by image, in case a name ever changes: any
        # container built from a localhost:5000/etny-* enclave image.
        try:
            out = subprocess.Popen(
                ['docker', 'ps', '-aq', '--filter', 'ancestor=localhost:5000/etny-securelock',
                 '--filter', 'ancestor=localhost:5000/etny-trustedzone',
                 '--filter', 'ancestor=localhost:5000/etny-validator'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            ids, _ = out.communicate()
            ids = [i for i in ids.decode().split() if i] if ids else []
            for cid in ids:
                run_subprocess(['docker', 'rm', '-f', cid], logger)
        except Exception:
            pass
        logger.info("Startup cleanup: removed any stale enclave containers")
    except Exception as e:
        logger.warning(f"Startup enclave-container cleanup skipped: {e}")

def start_esr_replication_for_network(network):
    """Start the ESR + protocol-result replication thread for one network.

    Paired with the network's resilient_process thread: it mirrors THIS
    network's on-chain results and ESR state hashes into the node's IPFS, using
    that network's own contracts/RPC. Started exactly once per network per
    process (guarded); the worker owns a dedicated EtnyPoXNode built for
    replication so it never contends with the order-processing instance.
    """
    net_name = network.name
    with _esr_replication_lock:
        if net_name in _esr_replication_started:
            return
        _esr_replication_started.add(net_name)

    # Stagger the replication-handle construction across networks. Each handle
    # builds a small node (w3 + contracts + storage); doing all of them at once,
    # in parallel with the order-processing instances also constructing, made
    # them slow to reach their loop. A short per-network delay smooths that out.
    delay = _esr_replication_stagger_slot()

    def _worker():
        if delay:
            time.sleep(delay)
        config.logger.info(f"[esr-replication:{net_name}] building replication handle (delay {delay}s)")
        # Build a dedicated node handle for this network's replication. The SGX
        # integration test and the gas-wait loop are both skipped for a
        # replication-only handle, so construction never blocks on them.
        try:
            node = EtnyPoXNode(network, replication_only=True)
        except Exception as e:
            config.logger.warning(f"[esr-replication:{net_name}] could not start: {e}")
            with _esr_replication_lock:
                _esr_replication_started.discard(net_name)
            return

        # Do not replicate until the SGX integration test has completed for this
        # network's type. Replication fetches/pins ESR blobs and touches IPFS;
        # holding it until the node has proven it can actually run tasks keeps
        # startup focused on the integration test (which gates order processing)
        # and avoids competing for IPFS/CPU before the node is operational.
        # The test is per-type, so any same-type network passing it unblocks us.
        if not getattr(config, 'skip_integration_test', False):
            net_type = network.network_type.upper()
            done_event = integration_test_done.get(net_type)
            if done_event is not None:
                config.logger.info(
                    f"[esr-replication:{net_name}] waiting for {net_type} integration "
                    f"test before starting replication")
                while not stop_event.is_set() and not done_event.wait(timeout=5):
                    pass
                if stop_event.is_set():
                    return
                config.logger.info(
                    f"[esr-replication:{net_name}] {net_type} integration test complete; "
                    f"starting replication")

        config.logger.info(f"[esr-replication:{net_name}] paired replication thread started")
        node.run_esr_replication_loop()

    t = threading.Thread(target=_worker, name=f"esr-replication-{net_name}", daemon=True)
    t.start()

def set_integration_test_complete(network, value):
    """
    Sets the shared value for integration test in a thread-safe way.

    Setting it True also SETS the per-type Event, which permanently unblocks
    every same-type network waiting on the test and makes them skip it (now and
    on later processing-loop cycles). We only ever set the Event, never clear it:
    a passed SGX capability does not become unproven within a process lifetime.
    """
    global integration_test_complete
    with integration_test_lock:
        integration_test_complete[network.upper()] = value
        if value:
            integration_test_done[network.upper()].set()

def get_integration_test_complete(network):
    """
    Gets the shared value for integration test in a thread-safe way.
    """
    global integration_test_complete
    with integration_test_lock:
        return integration_test_complete.get(network.upper(), False)

class TaskManager:
    def __init__(self):
        self.executor = None
        self.futures = []

    def resilient_process(self, network):
        # Pair an ESR + protocol-result replication thread with THIS network's
        # processing thread: it mirrors the results and ESR state hashes for
        # this network only (its own contracts, RPC and IPFS), keeping them
        # pinned in the node's IPFS. Started once here, alongside the network
        # loop, rather than from EtnyPoXNode.__init__ (which is re-created every
        # cycle and blocks on the gas-wait / integration test before it could
        # launch anything). Daemon so it never blocks shutdown.
        start_esr_replication_for_network(network)
        while not stop_event.is_set():
            try:
                process_network(network)
            except Exception as e:
                logger.error(f"[{network.name}] Restarting due to error: {e}")
                reset_task_running_on()
                time.sleep(2)  # brief delay before retry
            else:
                reset_task_running_on()
                logger.info(f"[{network.name}] Process exited cleanly. Restarting.")
                time.sleep(2)  # restart after clean exit

    def start_threads(self, network_configs):
        logger.info("Starting new threads...")
        # Each resilient_process is an infinite per-network loop that never
        # returns, so it holds its worker for the life of the process. The pool
        # must therefore have at least one worker PER network, or the networks
        # past the cap never get a thread and are silently never processed
        # (this stranded litvm_liteforge + ethereum_sepolia behind a hardcoded
        # max_workers=5). Size to the network count so adding a network can
        # never re-introduce that starvation.
        worker_count = max(len(network_configs), 1)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
        self.futures = [
            self.executor.submit(self.resilient_process, net) for net in network_configs
        ]

def initiate_restart(network_configs, task_manager):
    """
    1) Signal the old tasks to stop.
    2) Wait for them to finish.
    3) Shutdown the old executor.
    4) Clear stop_event and start fresh tasks in the same TaskManager.
    """
    logger.info("Initiating restart...")

    # 1) Tell current tasks to stop
    stop_event.set()

    # 2) Wait until all futures are done
    while not all(f.done() for f in task_manager.futures):
        logger.info("Waiting for current tasks to finish...")
        time.sleep(2)

    logger.info("All current tasks have stopped.")

    # 3) Shut down the old executor
    task_manager.executor.shutdown(wait=True)
    logger.info("Old executor shut down.")

    # 4) Clear the stop flag
    stop_event.clear()

    reset_task_running_on()

    # 5) Start fresh threads (re-using the same TaskManager object)
    task_manager.start_threads(network_configs)
    logger.info("New threads started after restart.")

def run_scheduler(interval, network_configs, task_manager):
    """
    Runs in a background thread. Every `interval` seconds, calls initiate_restart().
    This is an infinite loop, so adjust as needed or provide a break condition.
    """
    while True:
        time.sleep(interval)
        initiate_restart(network_configs, task_manager)


if __name__ == '__main__':

    network_names = list(config.NETWORKS.keys())
    
    parser = config.parse_arguments(network_names)
    args, unknown_args = parser.parse_known_args()

    if unknown_args:
       config.logger.warning(f"Ignored unrecognized arguments: {' '.join(unknown_args)}")

    try:
        sgx = SGXDriver()
        network_configs = config.parse_networks(args, parser, network_names)

        # Clean slate: remove any enclave containers left over from a previous
        # run BEFORE any network thread can launch an order's enclaves. A restart
        # mid-order otherwise leaves stale containers (e.g. an integration-test
        # trustedzone) that poison the next order's enclave handshake.
        terminate_stale_enclave_containers()

        # Create a TaskManager to hold executor/futures
        task_manager = TaskManager()

        # Start the first batch of threads
        task_manager.start_threads(network_configs)

        # Create a background scheduler that restarts every 20 seconds
        scheduler_thread = threading.Thread(
            target=run_scheduler,
            args=(24 * 60 * 60, network_configs, task_manager),
            daemon=True  # daemon=True so it won't block process exit
        )
        scheduler_thread.start()

        # Keep main alive (or do other work)
        logger.info("Main thread running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)

    except EnvironmentError as env_err:
        logger.error(f"Environment configuration error: {env_err}")
        sys.exit(1)
    except argparse.ArgumentError as arg_err:
        logger.error(f"Argument parsing error: {arg_err}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        sys.exit(1)
