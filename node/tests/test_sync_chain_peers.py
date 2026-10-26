"""utils.Storage.sync_chain_peers keeps Kubo's peering list carrying the
configured IPFS_SWARM peers and the validators' IPFS peers: a configured peer
the list lacks is added as a validator's is, and only validators' peers that
left the set are removed (bloxberg testnet rig, 2026-10-26: the daemon ten
nodes share listed no peers of its own, so its connection to the bootnode was
an ordinary one the connection manager trimmed, and results reached clients
17 minutes after their orders completed)."""
import logging

from utils import Storage

BOOTNODE = "/dns4/ipfs.ethernity.cloud/tcp/4001/p2p/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5"
VALIDATOR = "/ip4/80.255.2.20/tcp/4001/p2p/12D3KooWValidatorAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
GONE = "12D3KooWGoneBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def listed(*multiaddrs):
    """A swarm/peering/ls answer for `multiaddrs`: transport addresses without
    the /p2p suffix, grouped by peer id."""
    peers = {}
    for addr in multiaddrs:
        transport, pid = addr.rsplit("/p2p/", 1)
        peers.setdefault(pid, []).append(transport)
    return {"Peers": [{"ID": pid, "Addrs": addrs} for pid, addrs in peers.items()]}


def storage_with(peering, chain_peers=None):
    storage = Storage.__new__(Storage)
    storage.logger = logging.getLogger("test")
    storage.ipfs_swarm = BOOTNODE
    storage._chain_peers = dict(chain_peers or {})
    calls = []

    def api(command, params=None, **kwargs):
        calls.append((command, dict(params or {})))
        if command == "swarm/peering/ls":
            return peering
        return {}

    storage._api_call = api
    return storage, calls


def test_the_configured_peer_missing_from_the_list_is_added():
    storage, calls = storage_with(listed())
    storage.sync_chain_peers([])
    assert calls == [("swarm/peering/ls", {}), ("swarm/peering/add", {"arg": [BOOTNODE]})]
    assert storage._chain_peers == {}


def test_a_listed_configured_peer_is_left_alone_and_the_validators_are_added():
    storage, calls = storage_with(listed(BOOTNODE))
    storage.sync_chain_peers([VALIDATOR])
    assert calls == [("swarm/peering/ls", {}), ("swarm/peering/add", {"arg": [VALIDATOR]})]
    assert storage._chain_peers == {VALIDATOR.rsplit("/p2p/", 1)[1]: [VALIDATOR]}


def test_a_validator_that_left_the_set_is_removed_and_the_configured_peer_never_is():
    bootnode_id = BOOTNODE.rsplit("/p2p/", 1)[1]
    storage, calls = storage_with(listed(BOOTNODE, VALIDATOR), chain_peers={GONE: ["/ip4/1.2.3.4/tcp/4001/p2p/" + GONE],
                                                                           bootnode_id: [BOOTNODE]})
    storage.sync_chain_peers([VALIDATOR])
    assert calls == [("swarm/peering/ls", {}), ("swarm/peering/rm", {"arg": GONE})]


def test_a_listing_that_fails_changes_nothing():
    storage, calls = storage_with(listed())
    storage._api_call = lambda command, params=None, **kwargs: (_ for _ in ()).throw(Exception("api down"))
    storage.sync_chain_peers([VALIDATOR])
    assert storage._chain_peers == {}
