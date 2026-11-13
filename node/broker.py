"""The node agent's side of the reservation broker (PHASE-E-PLAN.md WS-F,
E27-E33; RESERVATION-DESIGN.md sections 2 and 3): the broker's structs read
by position, the commitment window's extension, the handover window's
arithmetic and the announcement's refusals, the --handover marker file the
running agent answers, the acceptance of an announced handover, the farm
invitation, the offer's disposition on a broker-placed order and the
supervise decisions. Every function is pure over the values the agent reads
from the chain; the agent sends what they decide."""
import argparse
import json
import os
import re
import time
from collections import namedtuple

from constants import BROKER_TX_PER_TICK, COMMIT_WINDOW_FACTOR, HANDOVER_REQUEST_TIMEOUT_SECONDS
from models import OrderStatus, RequestStatus
from utils import looks_like_cid

DAY = 86400
# How often the --handover command reads the marker file for the answer.
HANDOVER_ANSWER_POLL_SECONDS = 1.0
ZERO_ADDRESS = '0x' + '00' * 20
# uint64 max: nextDue's answer for a reservation with nothing scheduled.
NEVER = 2**64 - 1

# IReservationBroker.Commitment, as commitmentOf(dproc) answers it.
Commitment = namedtuple('Commitment', 'until breached_at recommit_after last_handover_at handover_allowed_after '
                                      'suspended_until')
COMMITMENT_FIELDS = ('until', 'breachedAt', 'recommitAfter', 'lastHandoverAt', 'handoverAllowedAfter',
                     'suspendedUntil')
# The fields of IReservationBroker.ReservationParams the agent reads, with
# their positions in params()' struct.
BrokerParams = namedtuple('BrokerParams', 'handover_seconds handover_notice_seconds handover_window_blocks '
                                          'handover_window_min_blocks handover_prep_seconds block_millis '
                                          'farm_claim_seconds appointee_claim_seconds handover_cooldown_seconds '
                                          'handover_gas_deposit_wei open_order_timeout_seconds commit_min_seconds '
                                          'recommit_delay_seconds broker_grace_seconds')
PARAMS_FIELDS = (('handoverSeconds', 7), ('handoverNoticeSeconds', 9), ('handoverWindowBlocks', 10),
                 ('handoverWindowMinBlocks', 11), ('handoverPrepSeconds', 12), ('blockMillis', 13),
                 ('farmClaimSeconds', 14), ('appointeeClaimSeconds', 15), ('handoverCooldownSeconds', 16),
                 ('handoverGasDepositWei', 18), ('openOrderTimeoutSeconds', 20), ('commitMinSeconds', 23),
                 ('recommitDelaySeconds', 24), ('brokerGraceSeconds', 28))
# handoverOf(orderId)'s answer.
HandoverOf = namedtuple('HandoverOf', 'found predecessor successor_placed successor from_block to_block')
HANDOVER_FIELDS = ('found', 'predecessor', 'successorPlaced', 'successor', 'fromBlock', 'toBlock')
# The window the agent announces: the blocks and the slot it is centred on.
HandoverWindow = namedtuple('HandoverWindow', 'from_block to_block slot')
# A HandoverRequested log's fields.
HandoverRequested = namedtuple('HandoverRequested', 'id order_id dproc from_block to_block farm appointed '
                                                    'announced_at')
# The position of handoverTimeOfDay in IReservationBroker.Template, and of
# end in IReservationBroker.Reservation.
TEMPLATE_HANDOVER_TIME_OF_DAY = 6
RESERVATION_END = 3
# The positions of metadata1, metadata2 and metadata3 in
# IReservationBroker.Template: the strings naming the artefacts (the enclave
# images and compose, the payload, the challenge, the session request) of
# every order the broker places for the reservation.
TEMPLATE_METADATA = (('metadata1', 16), ('metadata2', 17), ('metadata3', 18))

ROLE_PREDECESSOR = 'predecessor'
ROLE_SUCCESSOR = 'successor'

ADDRESS_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')


def _field(raw, name, index):
    if isinstance(raw, dict):
        return raw[name]
    if hasattr(raw, name):
        return getattr(raw, name)
    return raw[index]


def commitment_of(raw):
    """Commitment from commitmentOf(dproc) (the struct as a sequence, or a
    mapping by name)."""
    return Commitment(*(int(_field(raw, name, index)) for index, name in enumerate(COMMITMENT_FIELDS)))


def params_of(raw):
    """BrokerParams from the broker's params() struct."""
    return BrokerParams(*(int(_field(raw, name, index)) for name, index in PARAMS_FIELDS))


def handover_of(raw):
    """HandoverOf from handoverOf(orderId)."""
    values = [_field(raw, name, index) for index, name in enumerate(HANDOVER_FIELDS)]
    return HandoverOf(bool(values[0]), int(values[1]), bool(values[2]), int(values[3]), int(values[4]),
                      int(values[5]))


def template_handover_time_of_day(raw):
    """handoverTimeOfDay of a templateOf(id) struct."""
    return int(_field(raw, 'handoverTimeOfDay', TEMPLATE_HANDOVER_TIME_OF_DAY))


def reservation_end(raw):
    """end of a reservationOf(id) struct."""
    return int(_field(raw, 'end', RESERVATION_END))


def template_cids(raw):
    """The CIDs a templateOf(id) struct names: every colon-separated token of
    its metadata1, metadata2 and metadata3 shaped like a CID, in order, each
    once."""
    cids = []
    for name, index in TEMPLATE_METADATA:
        for token in str(_field(raw, name, index) or '').split(':'):
            cid = token.strip()
            if looks_like_cid(cid) and cid not in cids:
                cids.append(cid)
    return cids


def template_pins_due(ends, now, read_end):
    """What the mirror's round does with the reservations it holds, {id: end}
    (reservationOf(id).end, the term's last second): (the ids whose term runs
    at `now`, whose template artefacts are pinned this round, in id order;
    the ids whose term has ended, dropped from the record). A reservation's
    end moves on by its lost seconds (IReservationBroker.Reservation.end), so
    an id whose recorded end has passed is read again through `read_end(id)`
    before it is judged, and `ends` takes the fresh value. An ended
    reservation's artefacts leave the pin set through the retention sweep."""
    live, ended = [], []
    for rid in sorted(ends):
        end = int(ends[rid])
        if end <= now:
            end = int(read_end(rid))
            ends[rid] = end
        (live if end > now else ended).append(rid)
    return live, ended


def window_role(window, own_order_id):
    """The role a handoverOf answer gives order `own_order_id`:
    ROLE_PREDECESSOR when the window is recorded (found, fromBlock != 0) on
    this order, ROLE_SUCCESSOR when on another, None for no recorded window.
    Ids start at 0, so found and fromBlock decide, never a zero id."""
    if window is None or not window.found or window.from_block == 0:
        return None
    return ROLE_PREDECESSOR if window.predecessor == int(own_order_id) else ROLE_SUCCESSOR


# ------------------------------------------------------------ the commitment

def commitment_extension(commitment, now, commit_min):
    """The `untilTs` of the commitReserved the agent sends now, or None: none
    while the window still reaches commit_min ahead (until - now >=
    commit_min) and none before recommitAfter; otherwise now +
    COMMIT_WINDOW_FACTOR x commit_min, which the contract admits (at least
    now + commit_min and not below the standing until)."""
    if int(commitment.until) - int(now) >= int(commit_min):
        return None
    if int(now) < int(commitment.recommit_after):
        return None
    return int(now) + COMMIT_WINDOW_FACTOR * int(commit_min)


# -------------------------------------------------------- the handover window

def project_time(block, head_timestamp, head_number, block_millis):
    """ReservationSlots.projectTime: the time `block` is projected to from
    the head."""
    block, head_timestamp, head_number, block_millis = int(block), int(head_timestamp), int(head_number), int(block_millis)
    if block >= head_number:
        return head_timestamp + (block - head_number) * block_millis // 1000
    back = (head_number - block) * block_millis // 1000
    return 0 if back >= head_timestamp else head_timestamp - back


def project_block(t, head_timestamp, head_number, block_millis):
    """ReservationSlots.projectBlock: the block time `t` is projected to."""
    t, head_timestamp, head_number, block_millis = int(t), int(head_timestamp), int(head_number), int(block_millis)
    if t < head_timestamp:
        return head_number
    return head_number + (t - head_timestamp) * 1000 // block_millis


def next_slot(earliest, handover_time_of_day):
    """The first daily slot of `handover_time_of_day` (seconds since
    midnight UTC) at or after `earliest`."""
    earliest = int(earliest)
    slot = earliest - earliest % DAY + int(handover_time_of_day)
    if slot < earliest:
        slot += DAY
    return slot


def handover_window(now, head_number, head_timestamp, params, handover_time_of_day, order_end_slot,
                    reservation_end, placed_at, duration_hours):
    """(HandoverWindow, None) the agent announces for the order it runs, or
    (None, reason): the window of handoverWindowBlocks blocks centred on the
    first daily slot at least handoverNoticeSeconds plus half a window ahead
    of `now`, projected from the head at the broker's blockMillis
    (RESERVATION-DESIGN.md section 2). None when that slot is not below the
    order's end slot (the reservation's end for the term's last order, end
    slot 0): the order serves to its end window; and none when t(toBlock) +
    handoverSeconds passes the order's PoX end, placed_at + duration_hours x
    3600."""
    window_seconds = int(params.handover_window_blocks) * int(params.block_millis) // 1000
    slot = next_slot(int(now) + int(params.handover_notice_seconds) + window_seconds // 2, handover_time_of_day)
    bound = int(order_end_slot) or int(reservation_end)
    if slot >= bound:
        return None, 'no earlier slot: the next handover slot is not before the order\'s end slot'
    centre = project_block(slot, head_timestamp, head_number, params.block_millis)
    from_block = centre - int(params.handover_window_blocks) // 2
    to_block = from_block + int(params.handover_window_blocks)
    to_time = project_time(to_block, head_timestamp, head_number, params.block_millis)
    if to_time + int(params.handover_seconds) > int(placed_at) + int(duration_hours) * 3600:
        return None, 'window past order end'
    return HandoverWindow(from_block, to_block, slot), None


def handover_refusal(reserved, window, commitment, now, token_balance, deposit_wei):
    """Why the agent does not announce a handover for the order it runs, or
    None: the order is not reserved, a window is recorded for it already
    (handoverOf found with fromBlock != 0), the node's handoverAllowedAfter
    has not passed, or its token balance does not cover the gas deposit."""
    if not reserved:
        return 'the order is not reserved'
    if window is not None and window.found and window.from_block != 0:
        return 'window recorded'
    if int(commitment.handover_allowed_after) > int(now):
        return f'handover cooldown until {int(commitment.handover_allowed_after)}'
    if int(token_balance) < int(deposit_wei):
        return f'token balance {int(token_balance)} below the gas deposit of {int(deposit_wei)} wei'
    return None


def appointee(value):
    """The appointed successor of a --handover request: the zero address for
    none, the address as given; None for anything else."""
    if value in (None, ''):
        return ZERO_ADDRESS
    if ADDRESS_RE.match(str(value)):
        return str(value)
    return None


# ------------------------------------------------------- the handover marker

def handover_request_path(base_path):
    """node/<network>/handover.json: the --handover command's request and
    the running agent's answer."""
    return os.path.join(str(base_path), 'handover.json')


def write_handover_request(path, appoint, requested_at):
    """Writes a --handover request, replacing any earlier file."""
    temporary = f"{path}.tmp"
    with open(temporary, 'w') as f:
        json.dump({'request': {'appoint': appoint or ''}, 'requested_at': int(requested_at)}, f)
    os.replace(temporary, path)


def read_handover_request(path):
    """The request standing in the marker file, {'appoint', 'requested_at'},
    or None when there is none, it was answered already, or the file cannot
    be read."""
    try:
        with open(path) as f:
            record = json.load(f)
        if 'answer' in record:
            return None
        return {'appoint': str(record['request'].get('appoint') or ''), 'requested_at': int(record['requested_at'])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def write_handover_answer(path, answer):
    """Writes `answer` (a mapping) under the request standing in the marker
    file; a file without a request is written with the answer alone."""
    try:
        with open(path) as f:
            record = json.load(f)
    except (OSError, ValueError):
        record = {}
    if not isinstance(record, dict):
        record = {}
    record['answer'] = dict(answer)
    temporary = f"{path}.tmp"
    with open(temporary, 'w') as f:
        json.dump(record, f)
    os.replace(temporary, path)


def read_handover_answer(path):
    """The answer in the marker file, or None while none is written."""
    try:
        with open(path) as f:
            record = json.load(f)
        answer = record.get('answer')
        return dict(answer) if isinstance(answer, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def await_handover_answer(path, sleep=time.sleep, now=time.monotonic):
    """Polls the marker file every HANDOVER_ANSWER_POLL_SECONDS for the
    running agent's answer, for HANDOVER_REQUEST_TIMEOUT_SECONDS on the
    clock `now`; the answer, or None when none came."""
    deadline = now() + HANDOVER_REQUEST_TIMEOUT_SECONDS
    while True:
        answer = read_handover_answer(path)
        if answer is not None:
            return answer
        if now() >= deadline:
            return None
        sleep(HANDOVER_ANSWER_POLL_SECONDS)


def handover_command_arguments(argv):
    """The arguments of `etny-node.py --handover --network <name> [--appoint
    <node>]`: an argparse Namespace with network and appoint; raises
    SystemExit on a malformed command line, as argparse does."""
    parser = argparse.ArgumentParser(prog='etny-node.py --handover',
                                     description='Asks the running agent to announce a handover of the reserved '
                                                 'order it runs on a network')
    parser.add_argument('--handover', action='store_true', required=True)
    parser.add_argument('-n', '--network', required=True, help='the network whose running order is handed over')
    parser.add_argument('--appoint', default='', help='the committed node appointed as the successor '
                                                      '(needed without a farm)')
    arguments = parser.parse_args(argv)
    if appointee(arguments.appoint) is None:
        parser.error(f"--appoint {arguments.appoint!r} is not an address")
    return arguments


def signal_action(reserved_order_running, announcement_refused):
    """What SIGHUP does: 'drain' (announce the handover, serve the order to
    the window, post the result, exit) while a reserved order runs and its
    announcement is not refused; 'exit' otherwise, today's effect of the
    signal."""
    if reserved_order_running and not announcement_refused:
        return 'drain'
    return 'exit'


# ------------------------------------------------------------- the acceptance

def handover_requested(log, announced_at):
    """HandoverRequested from a log of the broker's event and the timestamp
    of its block, the announcement's time the claim runs from."""
    args = log['args']
    return HandoverRequested(int(args['id']), int(args['orderId']), str(args['dproc']), int(args['fromBlock']),
                             int(args['toBlock']), str(args['farm']), str(args['appointed']), int(announced_at))


def acceptances_record(pending):
    """The HandoverRequested events whose acceptance is due later ({order
    id: HandoverRequested}) as the list broker_cursor.json keeps across
    process starts, in order id order."""
    return [list(pending[order_id]) for order_id in sorted(pending)]


def acceptances_from_record(record):
    """{order id: HandoverRequested} from acceptances_record's list; {} for
    none or a record of another shape."""
    pending = {}
    try:
        for fields in record or []:
            event = HandoverRequested(int(fields[0]), int(fields[1]), str(fields[2]), int(fields[3]), int(fields[4]),
                                      str(fields[5]), str(fields[6]), int(fields[7]))
            pending[event.order_id] = event
    except (TypeError, ValueError, IndexError):
        return {}
    return pending


def started_reservations_record(started):
    """The started reservations without an order since ({id: block}) as
    broker_cursor.json keeps them: {str(id): block}."""
    return {str(int(reservation)): int(block) for reservation, block in sorted(started.items())}


def started_reservations_from_record(record):
    """{id: block} from started_reservations_record's mapping; {} for none
    or a record of another shape."""
    try:
        return {int(reservation): int(block) for reservation, block in (record or {}).items()}
    except (TypeError, ValueError, AttributeError):
        return {}


def acceptance_due(event, now, self_address, my_farm, params, commitment, farm_accept=True):
    """When this node sends acceptHandover for `event` (HandoverRequested):
    ('now', None), ('after', t) for a farm member while the appointee alone
    may claim, or ('never', reason). Due as the appointee at once; as a
    member of the farm recorded on the announcement (not the announcer)
    at once, or from announcedAt + appointeeClaimSeconds when an appointee
    is named; both only while now <= announcedAt + farmClaimSeconds, the
    node committed (until > now), not suspended (suspendedUntil <= now) and,
    for a member, with farm_accept set."""
    me = str(self_address).lower()
    if str(event.dproc).lower() == me:
        return 'never', 'the announcer cannot accept'
    claim_end = int(event.announced_at) + int(params.farm_claim_seconds)
    if int(now) > claim_end:
        return 'never', 'the claim closed'
    if int(commitment.until) <= int(now):
        return 'never', 'the node is not committed'
    if int(commitment.suspended_until) > int(now):
        return 'never', f'the node is suspended until {int(commitment.suspended_until)}'
    appointed = str(event.appointed).lower()
    if appointed == me:
        return 'now', None
    farm = str(event.farm).lower()
    if farm == ZERO_ADDRESS or farm != str(my_farm or ZERO_ADDRESS).lower():
        return 'never', 'the handover is proposed to another farm, or to none'
    if not farm_accept:
        return 'never', 'farm handovers are not accepted by the configuration'
    if appointed != ZERO_ADDRESS:
        after = int(event.announced_at) + int(params.appointee_claim_seconds)
        if int(now) < after:
            return 'after', after
    return 'now', None


def acceptance_standing(accepted, window):
    """Whether the acceptance recorded in accepted.json ({'orderId',
    'predecessor_cu'}) still holds the node's offer: until the window's
    successor is placed or the window is gone (handoverOf found false or
    fromBlock 0)."""
    if not accepted:
        return False
    if window is None or not window.found or window.from_block == 0:
        return False
    return not window.successor_placed


def bounded_cu(record_cu, accepted):
    """The cu argument of this node's DP request while an acceptance stands:
    min(record cu, the predecessor's cu); the record's otherwise."""
    if not accepted:
        return int(record_cu)
    predecessor_cu = int(accepted.get('predecessor_cu') or 0)
    if predecessor_cu <= 0:
        return int(record_cu)
    return min(int(record_cu), predecessor_cu)


# ------------------------------------------------------------------ the farm

def parse_farm_invite(value):
    """(farm, expires_at, signature) of a <NETWORK>_FARM_INVITE value
    '<farm>:<expiresAt>:<signature>' (farm-invite.json configString: the
    farm 0x hex, expiresAt decimal unix seconds, the signature 65 bytes 0x
    hex); None for any other shape."""
    parts = str(value or '').strip().split(':')
    if len(parts) != 3:
        return None
    farm, expires_at, signature = parts
    if not ADDRESS_RE.match(farm) or not re.match(r'^0x[0-9a-fA-F]{130}$', signature):
        return None
    try:
        expires_at = int(expires_at)
    except ValueError:
        return None
    if str(expires_at) != parts[1] or expires_at < 0:
        return None
    return farm.lower(), expires_at, signature


def farm_refusal_path(base_path):
    """node/<network>/farm.json: the invitation a broker refused, never
    sent again to that broker."""
    return os.path.join(str(base_path), 'farm.json')


def farm_join_refused(path, invite, broker_address):
    """Whether farm.json records `invite` refused by `broker_address`."""
    try:
        with open(path) as f:
            record = json.load(f)
        return (str(record.get('invite')) == str(invite)
                and str(record.get('broker', '')).lower() == str(broker_address).lower())
    except (OSError, ValueError, AttributeError):
        return False


def record_farm_join_refused(path, invite, broker_address, refused):
    temporary = f"{path}.tmp"
    with open(temporary, 'w') as f:
        json.dump({'invite': str(invite), 'broker': str(broker_address), 'refused': str(refused)}, f)
    os.replace(temporary, path)


# --------------------------------------------------------- the offer's order

def offer_disposition(found, order_status, reserved, dp_status):
    """What the node does with the order orderOfDPRequest names for its
    standing request: 'approve' (found, OPEN, reserved: _approveOrder at
    once), 'run' (found, PROCESSING: the node's order to run), 'offer'
    (found, CANCELLED, the request AVAILABLE: the broker rejected an order
    the node did not approve in time and the offer waits for the next
    placement; the orders-cache entry is dropped), 'closed' (found, CLOSED),
    'none' (not found, or an OPEN order of a spot request, kept for the
    client's approval as today). The statuses are models.OrderStatus and
    models.RequestStatus."""
    if not found:
        return 'none'
    if int(order_status) == OrderStatus.PROCESSING:
        return 'run'
    if int(order_status) == OrderStatus.OPEN:
        return 'approve' if reserved else 'none'
    if int(order_status) == OrderStatus.CANCELLED:
        return 'offer' if int(dp_status) == RequestStatus.AVAILABLE else 'none'
    if int(order_status) == OrderStatus.CLOSED:
        return 'closed'
    return 'none'


# ------------------------------------------------------------- the supervise

def supervise_decision(kind, next_due, grace, now, order_placed, acted):
    """Whether the agent sends the broker call for a reservation it watches:
    'send', 'wait' (not yet due) or 'drop'. `kind` 'unmatched' (an Unmatched
    log: superviseWith with the own offer at once, the reservation's nextDue
    being the far future), 'started' (a StateChanged to a started
    reservation with no OrderPlaced since: superviseWith once next_due +
    grace < now) or 'window' (the own successor order's window past toBlock
    without the predecessor's result: supervise once next_due + grace <
    now). An order placed since drops the hint; `acted` False (the
    pre-flight call answers it would change nothing) drops the send: for
    'unmatched' and 'started' until the next log, for 'window' until the
    next poll of the window."""
    if order_placed:
        return 'drop'
    if kind != 'unmatched' and not (int(next_due) + int(grace) < int(now)):
        return 'wait'
    if acted is False:
        return 'drop'
    return 'send'


def transactions_admitted(sent_this_tick):
    """Whether another broker transaction goes out this idle tick (at most
    BROKER_TX_PER_TICK)."""
    return int(sent_this_tick) < BROKER_TX_PER_TICK
