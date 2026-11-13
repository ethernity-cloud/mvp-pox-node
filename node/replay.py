"""The successor's side of a handover window on the node agent (PHASE-E-PLAN.md
E34, E35; RESERVATION-DESIGN.md section 2): the switch decided from the window,
the predecessor's orderResultAt and the own orderPlacedAt as the trustedzones
decide it (replay.js switchDecision, replay.py fence_signal), the replay set
drawn from the predecessor's rows (its etny-si inputs without an etny-so row
signed by its attested task wallet and landed by its bound), the certificate
walk of the image registry, and the historical state CIDs the securelock's
fence reads. The agent stages what these functions name: session.replay.<seq>
for the trustedzone's replays and state.<cid>.enc for the fence."""
import hashlib
import os
import re
from collections import namedtuple

from eth_account import Account
from eth_account.messages import encode_defunct

import capability
from constants import RPC_REPLICA_LAG_BLOCKS
from utils import SESSION_INPUT_KEY, SESSION_OUTPUT_KEY, SESSION_WALLET_KEY

WIRE = 'v1'
# The fence kinds the trustedzone writes (session-fence.json).
FENCE_TO_BLOCK = 'toBlock'
FENCE_RESULT_BLOCK = 'resultBlock'
FENCE_NONE = 'none'
# The object the agent stages a replay's ciphertext as.
REPLAY_OBJECT_PREFIX = 'session.replay.'
# The protocol version the registry lists trustedzone builds under, and the
# most builds of one name walked for their certificates.
TRUSTEDZONE_PROTOCOL_VERSION = 'v3'
MAX_TRUSTEDZONE_VERSIONS = 32
# The field of a DO request's image metadata naming the trustedzone.
TRUSTEDZONE_FIELD = 2
# The securelock publishes a fenced key's re-commit as state.fence.<key16>.cid.
FENCE_OBJECT_RE = re.compile(r'^state\.fence\.[0-9a-fA-F]{16}\.cid$')

InputRow = namedtuple('InputRow', 'seq order_id cid sha256')
OutputRow = namedtuple('OutputRow', 'seq order_id ack status code cid sha256 sig')
WalletRow = namedtuple('WalletRow', 'order_id wallet sig')
Switch = namedtuple('Switch', 'kind bound replay_bound')


def pinned_block(head):
    """The block the window's reads are pinned at: the reported head less
    RPC_REPLICA_LAG_BLOCKS."""
    return int(head) - RPC_REPLICA_LAG_BLOCKS


def switch_bound(window, head, result_at, own_placed_at):
    """The switch of a reserved order's successor (replay.js switchDecision):
    `window` the HandoverOf this order is the successor of (None: no window,
    Switch none with no replay bound), `result_at` the predecessor's
    orderResultAt as (at, blockNumber), `own_placed_at` this order's
    orderPlacedAt, `head` the block of the tick. The predecessor's result
    inside the window: kind resultBlock at its block for an inheritor
    (placed at or after the result), kind none for the successor placed
    before it, the replay bound the result's block either way; toBlock
    passed without a result inside the window: kind toBlock, the replay
    bound toBlock. None while the window is open with no result inside."""
    if window is None:
        return Switch(FENCE_NONE, 0, None)
    at, result_block = int(result_at[0]), int(result_at[1])
    if result_block != 0 and window.from_block <= result_block <= window.to_block:
        if int(own_placed_at) >= at:
            return Switch(FENCE_RESULT_BLOCK, result_block, result_block)
        return Switch(FENCE_NONE, 0, result_block)
    if int(head) > window.to_block:
        return Switch(FENCE_TO_BLOCK, window.to_block, window.to_block)
    return None


def trustedzone_name(image_metadata):
    """The trustedzone name a DO request's image metadata names
    (v3:<image>:<trustedzone>:...); '' for other metadata."""
    fields = str(image_metadata or '').split(':')
    if fields[0] != 'v3' or len(fields) <= TRUSTEDZONE_FIELD:
        return ''
    return fields[TRUSTEDZONE_FIELD]


def parse_input_row(value):
    """InputRow from an etny-si value v1:<seq>:<orderId>:<cid>:<sha256hex>;
    None for any other value (a close row included)."""
    parts = str(value or '').strip().split(':')
    if len(parts) != 5 or parts[0] != WIRE:
        return None
    try:
        return InputRow(int(parts[1]), int(parts[2]), parts[3].strip(), parts[4].strip().lower())
    except ValueError:
        return None


def parse_output_row(value):
    """OutputRow from an etny-so value
    v1:<seq>:<orderId>:<ack>:<status>:<code>:<cid>:<sha256hex>:<sig>."""
    parts = str(value or '').strip().split(':')
    if len(parts) != 9 or parts[0] != WIRE:
        return None
    try:
        return OutputRow(int(parts[1]), int(parts[2]), int(parts[3]), parts[4], int(parts[5]), parts[6],
                         parts[7].lower(), parts[8])
    except ValueError:
        return None


def parse_wallet_row(value):
    """WalletRow from an etny-sw value v1:<orderId>:<taskWallet>:<sigDerHex>."""
    parts = str(value or '').strip().split(':')
    if len(parts) != 4 or parts[0] != WIRE:
        return None
    try:
        return WalletRow(int(parts[1]), parts[2], parts[3])
    except ValueError:
        return None


def output_row_message(row):
    """The text an etny-so row is signed over (the validators' and the
    runner's): etny-so|order|seq|ack|status|code|cid|sha."""
    return f"{SESSION_OUTPUT_KEY}|{row.order_id}|{row.seq}|{row.ack}|{row.status}|{row.code}|{row.cid}|{row.sha256}"


def output_row_signer(row):
    """The wallet (lower-case) an etny-so row is signed with, recovered
    (EIP-191) over its own fields; None when the signature does not
    recover."""
    try:
        signature = bytes.fromhex(row.sig[2:] if row.sig.startswith('0x') else row.sig)
        return Account.recover_message(encode_defunct(text=output_row_message(row)), signature=signature).lower()
    except Exception:
        return None


def wallet_attested_by(row, order_id, certs):
    """The task wallet (lower-case) an etny-sw row of `order_id` attests when
    its P-384 signature over etny-sw|<orderId>|<taskWallet> verifies under
    any of `certs`, the registry's certificates of the trustedzone name;
    None otherwise."""
    if row is None or row.order_id != int(order_id):
        return None
    message = f"{SESSION_WALLET_KEY}|{row.order_id}|{row.wallet}"
    for cert in certs:
        if capability.verify_p384(cert, message, row.sig):
            return row.wallet.lower()
    return None


def attested_wallet(wallet_values, order_id, certs):
    """The first wallet among the etny-sw values that verifies
    (wallet_attested_by); None when none does."""
    for value in wallet_values:
        wallet = wallet_attested_by(parse_wallet_row(value), order_id, certs)
        if wallet is not None:
            return wallet
    return None


def first_late_row(logs):
    """The lowest row number among OutputRowAdded logs read after the bound;
    None when there is none (every row landed by the bound)."""
    rows = [int(log['args']['rowNumber']) for log in logs]
    return min(rows) if rows else None


def replay_set(predecessor, input_values, output_rows, wallet, first_late):
    """The replay set (replay.js replaySet): every etny-si row of the
    predecessor's request (`input_values`) naming `predecessor` without an
    etny-so row of its DP request (`output_rows`: (row number, value)
    pairs) numbered below `first_late` (None: none landed late), naming the
    predecessor, signed by `wallet` and acknowledging its seq; in seq order.
    A None wallet answers nothing, so every input is replayed."""
    answered = set()
    if wallet is not None:
        for row_number, value in output_rows:
            if first_late is not None and int(row_number) >= int(first_late):
                continue
            output = parse_output_row(value)
            if output is None or output.order_id != int(predecessor):
                continue
            if output_row_signer(output) != str(wallet).lower():
                continue
            answered.add(output.ack)
    replays = [row for row in (parse_input_row(value) for value in input_values)
               if row is not None and row.order_id == int(predecessor) and row.seq not in answered]
    return sorted(replays, key=lambda row: row.seq)


def registry_certificates(versions, cert_of, name):
    """The certificates the image registry holds for trustedzone `name`:
    `versions(name, TRUSTEDZONE_PROTOCOL_VERSION, i)` answers the
    registered image's record (trustedZoneVersions: owner, ipfsHash,
    version, validated, certPublicKey, dockerComposeHash, name) for i from
    0 until it raises, at most MAX_TRUSTEDZONE_VERSIONS; a record with an
    empty certificate is read through `cert_of(ipfsHash)`
    (getTrustedZoneImageCertPublicKey), and one still empty contributes
    nothing."""
    certs = []
    for i in range(MAX_TRUSTEDZONE_VERSIONS):
        try:
            record = versions(name, TRUSTEDZONE_PROTOCOL_VERSION, i)
        except Exception:
            break
        cert = str(record[4] or '')
        if not cert.strip():
            try:
                cert = str(cert_of(record[1]) or '')
            except Exception:
                cert = ''
        if cert.strip() and cert not in certs:
            certs.append(cert)
    return certs


def spooled_input_path(orders_dir, order_id, seq):
    """Where the successor keeps the predecessor's input ciphertext `seq`
    before the switch: orders/<id>/replay/<seq>."""
    return os.path.join(str(orders_dir), str(order_id), 'replay', str(seq))


def digest_matches(blob, sha_hex):
    return hashlib.sha256(blob).hexdigest() == str(sha_hex).lower()


def fence_state_cids(logs_after_bound, logs_at_or_before, bound):
    """{(enclave, key): cid} the fence reads: for every (enclave, key) a
    StateCommitted log names after `bound` (`logs_after_bound`, blocks over
    (bound, head]), the CID of its last StateCommitted at or before the
    bound among `logs_at_or_before` (any order; the latest block, then the
    highest version, decides). A key not moved after the bound is absent (its
    bound CID is its current pointer, staged already); a key whose first
    commit is after the bound is absent (no state as of the bound). Two
    enclaves are kept apart. Addresses and keys compare lower-case."""
    moved = set()
    for log in logs_after_bound:
        if int(log['blockNumber']) > int(bound):
            moved.add(log_pair(log))
    last = {}
    for log in logs_at_or_before:
        if int(log['blockNumber']) > int(bound):
            continue
        pair = log_pair(log)
        if pair not in moved:
            continue
        rank = (int(log['blockNumber']), int(log['args']['version']))
        if pair not in last or rank > last[pair][0]:
            last[pair] = (rank, str(log['args']['cid']))
    return {pair: cid for pair, (_, cid) in last.items()}


def log_pair(log):
    """(enclave lower-case, key hex without 0x lower-case) of a StateCommitted
    log."""
    key = log['args']['key']
    key_hex = key.hex() if hasattr(key, 'hex') else str(key)
    if key_hex.startswith('0x'):
        key_hex = key_hex[2:]
    return str(log['args']['enclave']).lower(), key_hex.lower()


def fence_objects_present(names):
    """Whether the securelock has published a fenced key's re-commit
    (state.fence.<key16>.cid among the bucket's object names)."""
    return any(FENCE_OBJECT_RE.match(str(name)) for name in names)
