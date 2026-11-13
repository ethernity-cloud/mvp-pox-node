"""The node's gas profile: the gas its own transactions use, which its tip
prices into a 1.2 DP request (utils.tip_per_cu_hour_wei).

Three kinds are kept, dpRequest, placement and startedRow, each an
exponential moving average of the gasUsed of this node's status-1 receipts
with weight GAS_PROFILE_ALPHA. The profile is persisted per network in
node/<network>/gas_profile.json as {kind: {gas, samples}}; a network's seed
(gas_profile_seed.json, {network: {kind: gas}}) is the first sample of a kind
the file does not hold, a zero or absent seed an empty kind. Prices are never
stored: the tip reads the pricing's avgGasPriceWei live."""
import json
import os

from constants import GAS_PROFILE_ALPHA

KINDS = ('dpRequest', 'placement', 'startedRow')


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
    def __init__(self, path, entries):
        self.path = path
        self.entries = entries

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
        entries = {}
        for kind in KINDS:
            held = stored.get(kind) if isinstance(stored, dict) else None
            if isinstance(held, dict) and int(held.get('samples') or 0) > 0:
                entries[kind] = {'gas': int(held['gas']), 'samples': int(held['samples'])}
                continue
            gas = int((seed or {}).get(kind) or 0)
            entries[kind] = {'gas': gas, 'samples': 1 if gas > 0 else 0}
        return cls(path, entries)

    def gas(self, kind):
        """The kind's average gas, 0 for an empty kind."""
        return self.entries[kind]['gas']

    def figures(self):
        """{kind: gas} of the three kinds."""
        return {kind: self.entries[kind]['gas'] for kind in KINDS}

    def record(self, kind, receipt):
        """Folds a status-1 receipt's gasUsed into the kind's average
        (gas + GAS_PROFILE_ALPHA x (gasUsed - gas), truncated; the first sample
        is taken as it is) and writes the file. A receipt of another status is
        ignored. Returns whether the receipt was recorded."""
        if kind not in KINDS or receipt is None or int(receipt['status']) != 1:
            return False
        used = int(receipt['gasUsed'])
        entry = self.entries[kind]
        if entry['samples'] == 0:
            entry['gas'] = used
        else:
            entry['gas'] = int(entry['gas'] + GAS_PROFILE_ALPHA * (used - entry['gas']))
        entry['samples'] += 1
        self._write()
        return True

    def _write(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        temporary = f"{self.path}.tmp"
        with open(temporary, 'w') as f:
            json.dump(self.entries, f)
        os.replace(temporary, self.path)
