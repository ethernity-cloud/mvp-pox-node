"""cas_resolver.recheck_cas: a kept CAS selection is reused only while its
endpoint answers and still attests as the validator it was selected for.
No network: the identity fetch is a stand-in."""
import logging

import cas_resolver

ADDRESS = '0x033a89C24abe0fF0b3Bd5Cf212ec2bE0277f11C4'
OTHER = '0xd0b2e913d06bcf63962a5bdaa9de0aca22ad4a56'
MRENCLAVE = '22' * 32
CERT = 'ab' * 32
SELECTION = {'address': ADDRESS, 'host': 'cas.example', 'port': 19775,
             'scone_cas_addr': 'cas.example:19775', 'mrenclave': MRENCLAVE, 'cert_hash': CERT}


def _quote(address, mrenclave):
    """A DCAP-shaped quote whose report body carries `mrenclave` and binds
    `address` in its report_data, as the CAS serves it."""
    body = bytearray(cas_resolver._REPORT_DATA[1])
    body[cas_resolver._MRENCLAVE[0]:cas_resolver._MRENCLAVE[1]] = bytes.fromhex(mrenclave)
    body[cas_resolver._REPORT_DATA[0]:cas_resolver._REPORT_DATA[0] + 20] = bytes.fromhex(address[2:])
    return body.hex()


def _identity(address=ADDRESS, mrenclave=MRENCLAVE, cert=CERT):
    return {'address': address, 'quote': _quote(address, mrenclave), 'cert_hash': cert}


def test_a_selection_that_still_attests_is_kept(monkeypatch):
    seen = []

    def fetch(host, timeout, rest_port):
        seen.append((host, timeout, rest_port))
        return _identity()
    monkeypatch.setattr(cas_resolver, '_fetch_identity', fetch)
    assert cas_resolver.recheck_cas(SELECTION, 7, logging.getLogger('test')) is True
    assert seen == [('cas.example', 7, cas_resolver.rest_port_for(19775))]


def test_an_endpoint_now_speaking_for_another_validator_is_dropped(monkeypatch):
    monkeypatch.setattr(cas_resolver, '_fetch_identity', lambda host, timeout, rest_port: _identity(address=OTHER))
    assert cas_resolver.recheck_cas(SELECTION, 7, logging.getLogger('test')) is False


def test_a_rotated_measurement_is_dropped(monkeypatch):
    monkeypatch.setattr(cas_resolver, '_fetch_identity', lambda host, timeout, rest_port: _identity(mrenclave='33' * 32))
    assert cas_resolver.recheck_cas(SELECTION, 7, logging.getLogger('test')) is False


def test_an_endpoint_that_does_not_answer_is_dropped(monkeypatch):
    def refused(host, timeout, rest_port):
        raise ConnectionRefusedError(111, 'Connection refused')
    monkeypatch.setattr(cas_resolver, '_fetch_identity', refused)
    assert cas_resolver.recheck_cas(SELECTION, 7, logging.getLogger('test')) is False
