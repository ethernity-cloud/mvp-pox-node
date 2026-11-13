"""An order's gas ledger, the gas wait and the relay batches (PHASE-E-PLAN.md
E19, E21, E22).

The ledger is one counter of gas-token wei over the order's etny-sw and
etny-so rows, its ESR commits and its result, judged as the trustedzones
judge it (nodenithy chain_fee.js chainCost, pynithy __esr_cost_within_terms):
on a recorded 1.2 order against the chainFeeWei of orderTerms(orderId), the
sum converted at the order's tokenPerPol, with a row at the pricing's
gasPerRow and the cap, a commit at its estimated gas and the live price
capped at gasPriceCapWei (the receipt's gasUsed x effectiveGasPrice once it
is in) and the result reserved at gasPerResult and the cap; on an order
without terms (a legacy PoX, a 1.1-placed order) against the network's relay
budget, rows and commits at their estimated gas and the live price, no
reserve. Under a cap of 0 every recorded cost is 0 (capped_price). The
etny-st row is never counted. The ledger is persisted in
orders/<id>/relay.json with the names relayed, the rows landed and the
session.commit names each landed row unlocks, and reloaded by a resumed
order.

Every amount is an int of wei."""
import json
import os
from collections import namedtuple

from eth_utils import keccak

from constants import ESR_LEDGER_CAP, RELAY_BATCH_MIN

WEI_PER_TOKEN = 10**18
# The gas a commit is priced at when its estimate fails (a later relay nonce
# of a key whose earlier commit has not landed), the trustedzones' figure.
COMMIT_GAS_FALLBACK = 120000
# The error selectors of a reverted commitForBatch, each naming the index of
# the entry that failed.
BATCH_ERROR_SELECTORS = {
    keccak(text='BatchEntryFailed(uint256,bytes)')[:4].hex(),
    keccak(text='BatchEntryOutOfGas(uint256)')[:4].hex(),
}

OrderTerms = namedtuple('OrderTerms', 'price_per_cu_hour_wei cu chain_fee_wei gas_price_cap_wei gas_allocated token_per_pol')
PricingParams = namedtuple('PricingParams', 'gas_per_row gas_per_commit gas_per_result priority_fee_wei')

TERMS_FIELDS = ('pricePerCuHourWei', 'cu', 'chainFeeWei', 'gasPriceCapWei', 'gasAllocated', 'tokenPerPol')
# The positions of the four fields in IPoxPricing.params()' struct.
PARAMS_FIELDS = (('gasPerRow', 13), ('gasPerCommit', 14), ('gasPerResult', 15), ('priorityFeeWei', 17))


def _field(raw, name, index):
    if isinstance(raw, dict):
        return raw[name]
    if hasattr(raw, name):
        return getattr(raw, name)
    return raw[index]


def order_terms(raw):
    """OrderTerms from orderTerms(orderId) (a sequence in the ABI's order, or
    a mapping by name); None when every field is zero: an order the PoX did
    not record (a 1.1 placement on a 1.2 PoX) is judged as legacy."""
    values = [int(_field(raw, name, index)) for index, name in enumerate(TERMS_FIELDS)]
    if not any(values):
        return None
    return OrderTerms(*values)


def pricing_params(raw):
    """PricingParams from IPoxPricing.params() (the struct as a sequence, or
    a mapping by name)."""
    return PricingParams(*(int(_field(raw, name, index)) for name, index in PARAMS_FIELDS))


def capped_price(live_price_wei, cap_wei):
    """The price a relay is accrued at: the live price, at most the cap
    (chain_fee.js cappedGasPrice). Under a cap of 0 (a 1.2 order on a chain
    without a base fee) it is 0, as the PoX accrues the rows at
    min(tx.gasprice, cap): the counter stays at the reserve alone and no
    budget refuses a relay there."""
    return min(int(live_price_wei), int(cap_wei))


def gas_wait_active(base_fee_wei, priority_fee_wei, cap_wei, now, order_end, in_window=False):
    """The trustedzones' gas wait (chain_fee.gasWaitActive bounded as
    trustedzone.js.tmpl gasWaitActive bounds it): True while the chain's
    base fee plus the pricing's priority fee is above the order's cap, so no
    row, commit or result is sent. False without a base fee (bloxberg), under
    a cap of 0, inside the own handover window, and once the order's end
    (`order_end`, None for unknown) has passed."""
    if base_fee_wei is None or not cap_wei or in_window:
        return False
    if order_end is not None and now >= order_end:
        return False
    return int(base_fee_wei) + int(priority_fee_wei) > int(cap_wei)


def capped_fee_fields(cap_wei, priority_fee_wei, ceiling_wei):
    """(price per gas, EIP-1559 fee fields) of a row, commit or result of a
    recorded 1.2 order outside a gas wait: maxFeePerGas the cap, at most the
    operator's ceiling, and maxPriorityFeePerGas the pricing's priority fee,
    at most maxFeePerGas."""
    max_fee = min(int(cap_wei), int(ceiling_wei))
    priority = min(int(priority_fee_wei), max_fee)
    return max_fee, {'type': 2, 'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': priority}


def commit_file_key(name):
    """(key16, relay nonce) of a staged authorization's name, which orders the
    relay within a key: session.commit.<n>.<key16>.<rn>.json and
    esr.commit.<key16>.<rn>.json; esr.commit.<rn>.json gives ('', rn); a name
    of another shape sorts last."""
    parts = name.split('.')
    try:
        if len(parts) == 6:
            return (parts[3], int(parts[4]))
        if len(parts) == 5:
            return (parts[2], int(parts[3]))
        return ('', int(parts[2]))
    except (ValueError, IndexError):
        return ('~', 0)


def relay_candidates(names, commits_by_row, rows_landed, relayed):
    """The authorizations due for relay, in order, from the bucket listing
    `names`: every esr.commit.*.json not relayed, in (key16, relay nonce)
    order, then the session.commit.*.json names of the rows landed
    (`rows_landed`, the seqs whose etny-so receipt has status 1), in row
    order and each list's order (`commits_by_row`: {seq: [names]}, the
    row's session.output.<seq>.commits). A session.commit named by no landed
    row is left out and one named by no list is never listed; a name listed
    before its file is staged waits."""
    present = set(names)
    plan = [name for name in sorted(
        (n for n in present if n.startswith('esr.commit.') and n.endswith('.json') and n not in relayed),
        key=commit_file_key)]
    for seq in sorted(int(s) for s in rows_landed):
        for name in commits_by_row.get(seq, commits_by_row.get(str(seq), [])):
            if name.startswith('session.commit.') and name in present and name not in relayed and name not in plan:
                plan.append(name)
    return plan


def relay_room(relayed):
    """How many more names the order may relay: ESR_LEDGER_CAP less the
    names relayed, both prefixes counted."""
    return max(0, ESR_LEDGER_CAP - len(relayed))


def relay_plan(names, commits_by_row, rows_landed, relayed):
    """The authorizations to relay now (relay_candidates), at most the
    order's room under ESR_LEDGER_CAP."""
    return relay_candidates(names, commits_by_row, rows_landed, relayed)[:relay_room(relayed)]


def parse_authorization(raw):
    """A staged authorization's fields for commitFor: (enclave, key hash
    bytes, cid, expected version, relay nonce, idempotency nonce, signature
    bytes). Raises on a malformed record."""
    a = json.loads(raw)
    return (a['enclave'], bytes.fromhex(a['keyHash'][2:]), str(a['cid']), int(a['expectedVersion']),
            int(a['relayNonce']), int(a['nonce']), bytes.fromhex(a['signature'][2:]))


def batch_plan(relayable, enclave_of):
    """The transactions that relay `relayable` (names in relay order): the
    names of one enclave go as one commitForBatch when they are
    RELAY_BATCH_MIN or more, alone otherwise; enclaves in the order of their
    first name, each enclave's names in their relay order. [(enclave,
    [names], batched)]."""
    groups = []
    for name in relayable:
        enclave = enclave_of(name)
        for group in groups:
            if group[0] == enclave:
                group[1].append(name)
                break
        else:
            groups.append((enclave, [name]))
    plan = []
    for enclave, names in groups:
        if len(names) >= RELAY_BATCH_MIN:
            plan.append((enclave, names, True))
        else:
            plan.extend((enclave, [name], False) for name in names)
    return plan


def batch_fallback(names, failed_index, key_of):
    """After a batch of `names` reverted: (the names sent alone, the names
    skipped). An index named by the revert skips that name and the later
    names of its key, whose relay nonces are gapped; no index skips
    nothing."""
    if failed_index is None or not 0 <= failed_index < len(names):
        return list(names), []
    failed_key = key_of(names[failed_index])
    singles, skipped = [], []
    for position, name in enumerate(names):
        if position == failed_index or (position > failed_index and key_of(name) == failed_key):
            skipped.append(name)
        else:
            singles.append(name)
    return singles, skipped


def batch_failure_index(error):
    """The entry index a reverted commitForBatch names (BatchEntryFailed,
    BatchEntryOutOfGas) from the error's revert data, or None when the error
    carries none."""
    data = getattr(error, 'data', None)
    if isinstance(data, dict):
        data = data.get('data')
    if not isinstance(data, str):
        return None
    data = data[2:] if data.startswith('0x') else data
    if len(data) < 72 or data[:8].lower() not in BATCH_ERROR_SELECTORS:
        return None
    try:
        return int(data[8:72], 16)
    except ValueError:
        return None


class OrderGasLedger:
    """The one counter of an order (the module docstring). `terms` is the
    order's OrderTerms or None, `params` the PricingParams or None, and
    `legacy_budget_wei` the network's relay budget for an order without
    terms; `path` the persisted file, None for none; `order_end` the order's
    end in unix seconds (None for unknown), which bounds its gas waits."""

    def __init__(self, order_id, terms, params, legacy_budget_wei, path=None, order_end=None):
        self.order_id = int(order_id)
        self.terms = terms
        self.params = params
        self.legacy_budget_wei = int(legacy_budget_wei)
        self.path = path
        self.order_end = order_end
        # Whether the current gas wait has been logged.
        self.gas_wait_logged = False
        # When the terms were last re-read from the chain (set_terms), unix
        # seconds; 0 until a re-read.
        self.terms_read_at = 0
        # How many due names the last plan() left beyond ESR_LEDGER_CAP, and
        # whether that was logged for this order.
        self.beyond_cap = 0
        self.cap_logged = False
        self.spent = 0
        self.relayed = []
        self.rows_landed = []
        self.commits_by_row = {}
        # The commits whose estimate stands in the counter until their
        # receipt replaces it: {name: estimated cost}.
        self.estimates = {}
        if path is not None:
            self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                held = json.load(f)
        except (OSError, ValueError):
            return
        self.spent = int(held.get('spent') or 0)
        self.relayed = [str(n) for n in held.get('relayed') or []]
        self.rows_landed = [int(s) for s in held.get('rows_landed') or []]
        self.commits_by_row = {int(k): [str(n) for n in v] for k, v in (held.get('commits_by_row') or {}).items()}
        self.estimates = {str(k): int(v) for k, v in (held.get('estimates') or {}).items()}

    def _write(self):
        if self.path is None:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        temporary = f"{self.path}.tmp"
        with open(temporary, 'w') as f:
            json.dump({'spent': self.spent, 'relayed': self.relayed, 'rows_landed': self.rows_landed,
                       'commits_by_row': {str(k): v for k, v in self.commits_by_row.items()},
                       'estimates': self.estimates}, f)
        os.replace(temporary, self.path)

    @property
    def recorded(self):
        """Whether the order is judged against its own terms."""
        return self.terms is not None and self.params is not None

    @property
    def cap_wei(self):
        return self.terms.gas_price_cap_wei if self.terms is not None else 0

    def set_terms(self, terms, now):
        """The order's terms re-read at `now` (unix seconds): a
        ChainFeeIncreased raise lands here."""
        self.terms = terms
        self.terms_read_at = int(now)

    def terms_stale(self, now, period):
        """Whether the terms were read `period` seconds or more before `now`:
        a gas wait re-reads them at that cadence, as the trustedzone re-reads
        orderTerms at its idle points."""
        return int(now) - self.terms_read_at >= int(period)

    def rows_cost(self, rows, gas_units, live_price_wei):
        """What `rows` rows sent in one transaction of `gas_units` cost the
        counter: rows x gasPerRow x the cap on a recorded order, the
        transaction's gas at the live price otherwise."""
        if self.recorded:
            return rows * self.params.gas_per_row * self.cap_wei
        return gas_units * live_price_wei

    def commit_cost(self, gas_units, live_price_wei):
        """What one commit of `gas_units` costs the counter: its gas at the
        live price capped on a recorded order, at the live price otherwise."""
        if self.recorded:
            return gas_units * capped_price(live_price_wei, self.cap_wei)
        return gas_units * live_price_wei

    def reserve(self):
        """The result's reserve: gasPerResult x the cap on a recorded order."""
        return self.params.gas_per_result * self.cap_wei if self.recorded else 0

    def token_wei(self, gas_token_wei):
        """`gas_token_wei` in the order's token at its tokenPerPol."""
        return gas_token_wei * self.terms.token_per_pol // WEI_PER_TOKEN

    def budget_wei(self):
        """The bound the counter is judged against: chainFeeWei (token wei)
        on a recorded order, the network budget (gas-token wei) otherwise."""
        return self.terms.chain_fee_wei if self.recorded else self.legacy_budget_wei

    def total(self, cost=0):
        """The counter with `cost` and the reserve added, in the budget's
        unit."""
        gas_token_wei = self.spent + cost + self.reserve()
        return self.token_wei(gas_token_wei) if self.recorded else gas_token_wei

    def admits(self, cost):
        """Whether a relay costing `cost` keeps the counter, with the result's
        reserve, within the budget."""
        return self.total(cost) <= self.budget_wei()

    def charge(self, cost):
        """Adds a row transaction's cost."""
        self.spent += int(cost)
        self._write()

    def charge_commits(self, names, costs):
        """Adds the commits' estimated costs and marks them relayed; their
        receipt settles them (settle)."""
        for name, cost in zip(names, costs):
            self.estimates[name] = int(cost)
            self.spent += int(cost)
            if name not in self.relayed:
                self.relayed.append(name)
        self._write()

    def settle(self, names, actual_cost):
        """Replaces the estimates of `names` (one transaction's commits) with
        the receipt's cost, gasUsed x effectiveGasPrice."""
        estimated = sum(self.estimates.pop(name, 0) for name in names)
        self.spent += int(actual_cost) - estimated
        self._write()

    def skip(self, names):
        """Marks `names` relayed without a charge: a failed relay the order
        does not try again."""
        for name in names:
            if name not in self.relayed:
                self.relayed.append(name)
        self._write()

    def row_landed(self, seq, names):
        """Records the etny-so row `seq` landed with the session.commit names
        its .commits object carries, which the relay plan then lists."""
        seq = int(seq)
        if seq not in self.rows_landed:
            self.rows_landed.append(seq)
        self.commits_by_row[seq] = list(names)
        self._write()

    def plan(self, bucket_names):
        """relay_plan over the bucket listing with this ledger's state; the
        due names it leaves beyond ESR_LEDGER_CAP are counted in
        beyond_cap."""
        relayed = set(self.relayed)
        candidates = relay_candidates(bucket_names, self.commits_by_row, self.rows_landed, relayed)
        plan = candidates[:relay_room(relayed)]
        self.beyond_cap = len(candidates) - len(plan)
        return plan

    def budget_line(self):
        """The log line of the order's budget."""
        if self.recorded:
            return (f"order {self.order_id}: chain fee {self.terms.chain_fee_wei} wei at cap "
                    f"{self.terms.gas_price_cap_wei} wei/gas (rows {self.params.gas_per_row}, result "
                    f"{self.params.gas_per_result} gas, priority {self.params.priority_fee_wei} wei)")
        return f"order {self.order_id}: legacy relay budget {self.legacy_budget_wei} wei of gas"
