"""The node's gas profile: the gas its own transactions use, which its tip
prices into a 2.0 DP request (utils.tip_per_cu_hour_wei).

The kinds kept are dpRequest, placement and startedRow, and on PoX 2.0
matching claim, announce, beat, pause and askCorrection (MATCHING-DESIGN.md
7 item 9; MATCHING-SYNC-PLAN.md B06, OD32 A), each an exponential moving
average of the gasUsed of this node's status-1 receipts with weight
GAS_PROFILE_ALPHA. The tip is priced from the kinds the node sends on the
network (kinds_for): on a network whose parameter set in force takes claims
the five matching kinds join dpRequest and startedRow and placement leaves
them, the claim's gas counted once per claim sent per order won (the claims
recorded over the started orders recorded, at least one). The profile is
persisted per network in node/<network>/gas_profile.json as {kind: {gas,
samples}} with the claims sent and the orders won under CLAIMS_PER_ORDER;
a network's seed (gas_profile_seed.json, {network: {kind: gas}}) is the
first sample of a kind the file does not hold, a zero or absent seed an
empty kind. Prices are never stored: the tip reads the pricing's
avgGasPriceWei live."""
import json
import os

from constants import GAS_PROFILE_ALPHA

PLACEMENT_KINDS = ('dpRequest', 'placement', 'startedRow')
MATCHING_KINDS = ('claim', 'announce', 'beat', 'pause', 'askCorrection')
KINDS = PLACEMENT_KINDS + MATCHING_KINDS
CLAIM_KINDS = ('dpRequest', 'startedRow') + MATCHING_KINDS
# The profile file's key of {sent, won}: the claims recorded and the orders
# won, counted by their started rows.
CLAIMS_PER_ORDER = 'claimsPerOrder'


def kinds_for(claims):
    """The kinds a node's tip is priced from: CLAIM_KINDS on a network whose
    parameter set in force takes claims (`claims`), PLACEMENT_KINDS
    elsewhere."""
    return CLAIM_KINDS if claims else PLACEMENT_KINDS


def load_seed(path, network):
    """{kind: gas} for `network` from the seed file at `path`; {} without a
    file or an entry."""
    try:
        with open(path) as f:
            seeds = json.load(f)
    except (OSError, ValueError):
        return {}
    entry = seeds.get(network) or {}
    return {kind: int(entry.get(kind) or 0) for kind in KINDS}


class GasProfile:
    def __init__(self, path, entries, claims=None):
        self.path = path
        self.entries = entries
        self.claims = dict(claims or {'sent': 0, 'won': 0})

    @classmethod
    def load(cls, path, seed):
        """The profile at `path` (missing or unreadable: empty), the kinds it
        lacks seeded from `seed` ({kind: gas}): a non-zero seed is one sample,
        a zero or absent one no sample."""
        try:
            with open(path) as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        entries = {}
        for kind in KINDS:
            held = stored.get(kind)
            if isinstance(held, dict) and int(held.get('samples') or 0) > 0:
                entries[kind] = {'gas': int(held['gas']), 'samples': int(held['samples'])}
                continue
            gas = int((seed or {}).get(kind) or 0)
            entries[kind] = {'gas': gas, 'samples': 1 if gas > 0 else 0}
        held = stored.get(CLAIMS_PER_ORDER)
        claims = ({'sent': int(held.get('sent') or 0), 'won': int(held.get('won') or 0)}
                  if isinstance(held, dict) else None)
        return cls(path, entries, claims)

    def gas(self, kind):
        """The kind's average gas, 0 for an empty kind."""
        return self.entries[kind]['gas']

    def claims_per_order(self):
        """The claims sent per order won: the claims recorded over the orders
        won, rounded up, at least 1."""
        sent, won = self.claims['sent'], max(1, self.claims['won'])
        return max(1, -(-sent // won))

    def figures(self, kinds=PLACEMENT_KINDS):
        """{kind: gas} of `kinds`, the claim's gas times claims_per_order."""
        return {kind: self.entries[kind]['gas'] * (self.claims_per_order() if kind == 'claim' else 1)
                for kind in kinds}

    def record(self, kind, receipt):
        """Folds a status-1 receipt's gasUsed into the kind's average
        (gas + GAS_PROFILE_ALPHA x (gasUsed - gas), truncated; the first sample
        is taken as it is) and writes the file; a claim counts one claim sent.
        A receipt of another status is ignored. Returns whether the receipt
        was recorded."""
        if kind not in KINDS or receipt is None or int(receipt['status']) != 1:
            return False
        used = int(receipt['gasUsed'])
        entry = self.entries[kind]
        if entry['samples'] == 0:
            entry['gas'] = used
        else:
            entry['gas'] = int(entry['gas'] + GAS_PROFILE_ALPHA * (used - entry['gas']))
        entry['samples'] += 1
        if kind == 'claim':
            self.claims['sent'] += 1
        self._write()
        return True

    def record_won(self):
        """Counts one order won on a claim network and writes the file."""
        self.claims['won'] += 1
        self._write()

    def _write(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        temporary = f"{self.path}.tmp"
        with open(temporary, 'w') as f:
            json.dump({**self.entries, CLAIMS_PER_ORDER: self.claims}, f)
        os.replace(temporary, self.path)
