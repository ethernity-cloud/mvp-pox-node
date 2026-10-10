"""utils.providers_in: the peer ids a `routing/findprovs` response names as
providers. Only lines of the Provider type count; the other query events,
blank lines and lines that are not JSON are skipped."""
import json

from utils import FINDPROVS_PROVIDER, providers_in

RSA = "QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5"
ED25519 = "12D3KooWB8qpxeHqcdb6xTNu3FXjrWE4zTFPverw8XMpms2Qm4pJ"


def line(event_type, responses=None):
    return json.dumps({"Extra": "", "ID": "", "Responses": responses, "Type": event_type}).encode()


def test_the_providers_of_provider_lines_are_returned():
    lines = [
        line(1, [{"Addrs": None, "ID": "12D3KooWPeerRoutingOnly"}]),
        line(FINDPROVS_PROVIDER, [{"Addrs": ["/ip4/80.255.2.15/tcp/4001"], "ID": RSA}]),
        line(FINDPROVS_PROVIDER, [{"Addrs": [], "ID": ED25519}, {"Addrs": [], "ID": RSA}]),
    ]
    assert providers_in(lines) == {RSA, ED25519}


def test_other_events_blank_and_non_json_lines_are_skipped():
    lines = [b"", line(0, None), line(2), b"not json", line(FINDPROVS_PROVIDER, None), b"[1, 2]"]
    assert providers_in(lines) == set()


def test_a_response_without_an_id_is_skipped():
    lines = [line(FINDPROVS_PROVIDER, [{"Addrs": []}, "text", {"ID": ""}, {"ID": RSA}])]
    assert providers_in(lines) == {RSA}
