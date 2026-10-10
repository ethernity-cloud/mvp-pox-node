"""utils.PEER_MULTIADDR against the peers a registrant writes into an image
registry entry: a transport address ending in a canonical peer id passes, the
bare `/p2p/<id>` a publisher behind NAT registers passes, and anything else
does not."""
from utils import PEER_MULTIADDR

RSA = "QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5"
ED25519 = "12D3KooWB8qpxeHqcdb6xTNu3FXjrWE4zTFPverw8XMpms2Qm4pJ"


def accepted(peer):
    return PEER_MULTIADDR.fullmatch(peer) is not None


def test_a_transport_address_ending_in_a_peer_id_passes():
    assert accepted(f"/ip4/80.255.2.15/tcp/4001/p2p/{RSA}")
    assert accepted(f"/dns4/cas.ethernity.cloud/udp/14001/quic-v1/p2p/{ED25519}")


def test_the_bare_peer_id_a_publisher_behind_nat_registers_passes():
    assert accepted(f"/p2p/{RSA}")
    assert accepted(f"/p2p/{ED25519}")


def test_anything_else_is_refused():
    assert not accepted("")
    assert not accepted(RSA)
    assert not accepted("/p2p/")
    assert not accepted(f"/p2p/{RSA[:-1]}")
    assert not accepted(f"/p2p/{RSA}/tcp/4001")
    assert not accepted(f"/ip4/80.255.2.15/tcp/4001/p2p/{RSA} ")
    assert not accepted(f"p2p/{RSA}")
