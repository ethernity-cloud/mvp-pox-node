"""utils.Storage._parse_multiaddr, the Kubo HTTP API base taken from
IPFS_CONNECT_URL: an /ip4/ address and a /dns4/ or /dns/ name, each followed by
/tcp/<port>/http, give http://<host>:<port> (a container on a docker network
reaches another by its name, which outlives the address docker assigns at
start); anything else is refused."""
import pytest

from utils import Storage


def parse(ma):
    return Storage._parse_multiaddr(None, ma)


def test_an_ip4_address_gives_the_http_base():
    assert parse("/ip4/127.0.0.1/tcp/5001/http") == "http://127.0.0.1:5001"
    assert parse("/ip4/172.18.0.6/tcp/5001/http") == "http://172.18.0.6:5001"


def test_a_dns_name_gives_the_http_base():
    assert parse("/dns4/ipfs/tcp/5001/http") == "http://ipfs:5001"
    assert parse("/dns/ipfs.internal/tcp/5001/http") == "http://ipfs.internal:5001"


def test_anything_else_is_refused():
    for ma in ("", "/ip6/::1/tcp/5001/http", "/dns4/ipfs/tcp/5001",
               "/ip4/127.0.0.1/udp/5001/http", "http://127.0.0.1:5001",
               "/dns4/ipfs:5001/tcp/5001/http"):
        with pytest.raises(ValueError):
            parse(ma)
