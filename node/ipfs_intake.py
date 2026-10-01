"""Payload intake for the bootnode (ipfs.ethernity.cloud).

A runner that runs no IPFS node delivers each task artefact here after it has
placed its DO request:

    POST /payload/<network>/<doRequestId>/<cid>   body = the raw bytes

The intake accepts the bytes only when the DO request on chain names that CID
(in any of its four metadata fields or its key/value rows) and the bytes hash
to it under the raw-block recipe (CIDv1, raw leaves, sha256). It then adds and
pins them into this host's Kubo, where validators and nodes fetch them over
bitswap. Nothing a runner sends reaches a validator directly.

Limits: bodies above `max_bytes` (100 MiB) are refused with 413; one accepted
upload per (network, request, cid), a repeat answers 200 without reading the
body again; an accepted block stays pinned until the request's order has
closed or for `retention_seconds` (24 h) after acceptance, whichever comes
first. A request that is not yet visible on chain is retried for
`chain_wait_seconds` before 404, to cover RPC lag.

The server is plain `http.server` on a loopback port; haproxy routes
`/payload` of `ipfs.ethernity.cloud` to it.
"""

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from models import Order, OrderStatus
from utils import looks_like_cid

_PATH = re.compile(r'^/payload/([a-z0-9_]+)/(\d+)/([A-Za-z0-9]+)$')


class IntakeLedger:
    """Accepted uploads, persisted as JSON so retention survives a restart.

    Keys are "<network>:<doRequestId>:<cid>"; values carry the acceptance time
    and the pieces needed to release the pin."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._entries = {}
        try:
            with open(path) as f:
                self._entries = json.load(f)
        except FileNotFoundError:
            pass
        except Exception:
            self._entries = {}

    @staticmethod
    def key(network, do_req, cid):
        return f"{network}:{do_req}:{cid}"

    def has(self, network, do_req, cid):
        with self._lock:
            return self.key(network, do_req, cid) in self._entries

    def add(self, network, do_req, cid):
        with self._lock:
            self._entries[self.key(network, do_req, cid)] = {
                'network': network, 'do_req': int(do_req), 'cid': cid,
                'accepted': time.time()}
            self._write()

    def remove(self, key):
        with self._lock:
            self._entries.pop(key, None)
            self._write()

    def items(self):
        with self._lock:
            return list(self._entries.items())

    def _write(self):
        tmp = f"{self.path}.tmp"
        with open(tmp, 'w') as f:
            json.dump(self._entries, f)
        os.replace(tmp, self.path)


class NetworkBackend:
    """What the intake needs from one network: its PoX contract (web3
    contract object), its Kubo (a utils.Storage) and a logger."""

    def __init__(self, contract, storage, logger):
        self.contract = contract
        self.storage = storage
        self.logger = logger
        self.orders_scanned = 0


class PayloadIntake:
    def __init__(self, ledger_path, bind, logger, max_bytes=100 * 1024 * 1024,
                 retention_seconds=24 * 3600, chain_wait_seconds=60,
                 retention_interval=600, order_scan_batch=500):
        self.logger = logger
        self.max_bytes = max_bytes
        self.retention_seconds = retention_seconds
        self.chain_wait_seconds = chain_wait_seconds
        self.retention_interval = retention_interval
        self.order_scan_batch = order_scan_batch
        self.ledger = IntakeLedger(ledger_path)
        self.backends = {}
        self._in_flight = set()
        self._lock = threading.Lock()
        host, port = bind.rsplit(':', 1)
        self._bind = (host, int(port))
        self._server = None

    def register_network(self, network, contract, storage, logger):
        """Serve `/payload/<network>/...` from this network's contract and
        Kubo. Called once per network as its handle comes up."""
        with self._lock:
            self.backends[network.lower()] = NetworkBackend(contract, storage, logger)
        self.logger.info(f"[intake] serving network {network.lower()}")

    def start(self):
        intake = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "etny-intake"

            def log_message(self, fmt, *args):
                intake.logger.debug("[intake] " + fmt % args)

            def _reply(self, code, body):
                data = json.dumps(body).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._reply(405, {'error': 'POST the artefact bytes to /payload/<network>/<doRequestId>/<cid>'})

            def do_POST(self):
                m = _PATH.match(self.path)
                if not m:
                    return self._reply(404, {'error': 'path is /payload/<network>/<doRequestId>/<cid>'})
                network, do_req, cid = m.group(1), int(m.group(2)), m.group(3)
                length = self.headers.get('Content-Length')
                if length is None:
                    return self._reply(411, {'error': 'Content-Length required'})
                try:
                    length = int(length)
                except ValueError:
                    return self._reply(400, {'error': 'Content-Length is not a number'})
                if length > intake.max_bytes:
                    return self._reply(413, {'error': f'body above {intake.max_bytes} bytes'})
                if intake.ledger.has(network, do_req, cid):
                    return self._reply(200, {'status': 'already', 'cid': cid})
                body = self.rfile.read(length)
                code, reply = intake.accept(network, do_req, cid, body)
                self._reply(code, reply)

        self._server = ThreadingHTTPServer(self._bind, Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="ipfs-intake-http",
                         daemon=True).start()
        threading.Thread(target=self._retention_loop, name="ipfs-intake-retention",
                         daemon=True).start()
        self.logger.info(f"[intake] listening on {self._bind[0]}:{self._bind[1]}, "
                         f"cap {self.max_bytes} bytes, retention {self.retention_seconds}s")

    # ------------------------------------------------------------ accepting

    def accept(self, network, do_req, cid, body):
        """(http status, json body) for one upload. Never raises."""
        backend = self.backends.get(network)
        if backend is None:
            return 404, {'error': f'unknown network {network}'}
        if not looks_like_cid(cid):
            return 400, {'error': 'not a CID'}
        key = self.ledger.key(network, do_req, cid)
        with self._lock:
            if key in self._in_flight:
                return 409, {'error': 'this upload is already being processed'}
            self._in_flight.add(key)
        try:
            return self._accept(backend, network, do_req, cid, body)
        except Exception as e:
            backend.logger.warning(f"[intake] {network} request {do_req} {cid}: {e}")
            return 500, {'error': 'intake failure'}
        finally:
            with self._lock:
                self._in_flight.discard(key)

    def _accept(self, backend, network, do_req, cid, body):
        log = backend.logger
        named = self._request_cids_with_wait(backend, do_req)
        if named is None:
            log.info(f"[intake] {network} request {do_req}: not on chain after "
                     f"{self.chain_wait_seconds}s; refusing {cid}")
            return 404, {'error': f'DO request {do_req} not found on chain'}
        if cid not in named:
            log.info(f"[intake] {network} request {do_req} does not name {cid}; refusing")
            return 403, {'error': 'the DO request does not name this cid'}
        if not backend.storage.connected:
            return 503, {'error': 'IPFS unavailable'}
        stored = backend.storage.add_bytes_raw(body, name=f"payload-{do_req}-{cid[:16]}")
        if not stored or stored.lower() != cid.lower():
            self._discard(backend, stored)
            log.warning(f"[intake] {network} request {do_req}: bytes hash to {stored}, "
                        f"not {cid}; refused")
            return 400, {'error': 'the bytes do not hash to the cid', 'stored': stored}
        self.ledger.add(network, do_req, cid)
        backend.storage.provide(cid)
        log.info(f"[intake] {network} request {do_req}: pinned {cid} ({len(body)} bytes)")
        return 200, {'status': 'pinned', 'cid': cid}

    def _request_cids_with_wait(self, backend, do_req):
        """The CIDs the DO request names, or None when the request is not on
        chain within chain_wait_seconds."""
        deadline = time.time() + self.chain_wait_seconds
        while True:
            try:
                return self._request_cids(backend, do_req)
            except Exception as e:
                if time.time() >= deadline:
                    backend.logger.debug(f"[intake] request {do_req}: {e}")
                    return None
                time.sleep(5)

    @staticmethod
    def _request_cids(backend, do_req):
        """Every CID-shaped token in the request's four metadata fields and
        its key/value rows. Raises when the request cannot be read (not yet
        mined, or the RPC failed)."""
        caller = backend.contract.caller()
        meta = caller._getDORequestMetadata(do_req)
        texts = [str(m) for m in meta[1:5]]
        count = caller._getMetadataCountForRequest(do_req)
        for i in range(int(count)):
            key, value = caller._getMetadataValueForRequest(do_req, i)
            texts.append(str(value))
        cids = set()
        for text in texts:
            for token in text.split(':'):
                if looks_like_cid(token):
                    cids.add(token)
        return cids

    @staticmethod
    def _discard(backend, cid):
        """Drop a block that was added but is not the one requested."""
        if not cid:
            return
        for command in ('pin/rm', 'block/rm'):
            try:
                backend.storage._api_call(command, params={'arg': cid}, timeout=30)
            except Exception as e:
                backend.logger.debug(f"[intake] {command} {cid}: {e}")

    # ------------------------------------------------------------ retention

    def _retention_loop(self):
        while True:
            time.sleep(self.retention_interval)
            try:
                self._release_expired()
            except Exception as e:
                self.logger.warning(f"[intake] retention pass failed: {e}")

    def _release_expired(self):
        """Unpin what is older than retention_seconds, and what belongs to a DO
        request whose order has closed."""
        closed = {}
        for network, backend in list(self.backends.items()):
            closed[network] = self._closed_requests(backend)
        now = time.time()
        for key, entry in self.ledger.items():
            network, do_req, cid = entry['network'], entry['do_req'], entry['cid']
            backend = self.backends.get(network)
            if backend is None:
                continue
            aged = now - float(entry.get('accepted', 0)) > self.retention_seconds
            done = do_req in closed.get(network, set())
            if not aged and not done:
                continue
            reason = 'order closed' if done else f'older than {self.retention_seconds}s'
            try:
                backend.storage.pin_rm(cid)
            except Exception as e:
                backend.logger.warning(f"[intake] unpin {cid}: {e}")
                continue
            self.ledger.remove(key)
            backend.logger.info(f"[intake] {network} request {do_req}: released {cid} ({reason})")

    def _closed_requests(self, backend):
        """DO request ids whose order has CLOSED, from the orders not yet
        scanned (at most order_scan_batch per pass; the cursor persists in
        the backend). Orders are scanned from the newest ones backwards on
        the first pass, so a fresh intake does not read the whole history."""
        found = set()
        try:
            total = int(backend.contract.caller()._getOrdersCount())
        except Exception as e:
            backend.logger.debug(f"[intake] _getOrdersCount: {e}")
            return found
        if backend.orders_scanned == 0:
            backend.orders_scanned = max(0, total - self.order_scan_batch)
        end = min(total, backend.orders_scanned + self.order_scan_batch)
        # The cursor advances past the leading run of finished orders only;
        # an order still open, and every order after it, is re-read next pass.
        cursor = backend.orders_scanned
        cursor_open = False
        for order_id in range(backend.orders_scanned, end):
            try:
                order = Order(backend.contract.caller()._getOrder(order_id))
            except Exception as e:
                backend.logger.debug(f"[intake] _getOrder({order_id}): {e}")
                break
            finished = order.status in (OrderStatus.CLOSED, OrderStatus.CANCELLED)
            if finished:
                found.add(int(order.do_req))
            if not cursor_open:
                if finished:
                    cursor = order_id + 1
                else:
                    cursor_open = True
        backend.orders_scanned = cursor
        return found
