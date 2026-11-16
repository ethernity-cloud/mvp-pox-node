"""ipfs_intake: a runner in a web page uploads under the browser's CORS rules
(reported 2026-11-16 on the bloxberg testnet intake: the browser blocked the
payload upload because the intake answered the OPTIONS preflight with 501 and
no reply carried Access-Control-Allow-Origin). The intake answers the
preflight with 204 and CORS_HEADERS and puts Access-Control-Allow-Origin on
every reply, refusals included, so the page reads the status and the body."""
import http.client
import json
import logging

import pytest

from ipfs_intake import CORS_HEADERS, OWNER_SIGNATURE_HEADER, PayloadIntake

PATH = "/payload/bloxberg_testnet/7/Qm" + "a" * 44


@pytest.fixture
def intake(tmp_path):
    """A running intake on an ephemeral loopback port, serving no network."""
    served = PayloadIntake(str(tmp_path / "ledger.json"), "127.0.0.1:0", logging.getLogger("test-intake"))
    served.start()
    yield served
    served._server.shutdown()
    served._server.server_close()


def request(intake, method, path, headers=None, body=None):
    host, port = intake._server.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=10)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, {k.lower(): v for k, v in response.getheaders()}, data


def test_the_preflight_is_answered_with_the_cors_headers(intake):
    status, headers, data = request(intake, "OPTIONS", PATH, {
        "Origin": "https://dapp.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-etny-owner-signature",
    })
    assert status == 204
    assert data == b""
    for name, value in CORS_HEADERS:
        assert headers[name.lower()] == value
    allowed = [h.strip().lower() for h in headers["access-control-allow-headers"].split(",")]
    assert "content-type" in allowed
    assert OWNER_SIGNATURE_HEADER.lower() in allowed
    assert "POST" in headers["access-control-allow-methods"].split(", ")


def test_a_refusal_carries_the_allow_origin_header(intake):
    status, headers, data = request(intake, "POST", PATH, {
        "Origin": "https://dapp.example",
        "Content-Type": "application/octet-stream",
        "Content-Length": "1",
    }, body=b"x")
    assert status == 404
    assert json.loads(data) == {"error": "unknown network bloxberg_testnet"}
    assert headers["access-control-allow-origin"] == "*"


def test_a_get_carries_the_allow_origin_header(intake):
    status, headers, _ = request(intake, "GET", PATH, {"Origin": "https://dapp.example"})
    assert status == 405
    assert headers["access-control-allow-origin"] == "*"


def test_the_allow_origin_header_is_the_first_cors_header():
    assert CORS_HEADERS[0] == ("Access-Control-Allow-Origin", "*")
