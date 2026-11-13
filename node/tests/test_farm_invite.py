"""broker (PHASE-E-PLAN.md E30, E31, F.3): the farm invitation of
tests/spec/farm-invite.json parsed from its configuration string and its
signer recovered with eth_account under the fixture's EIP-712 domain (the
agent never does this live: the domain is the broker's); the acceptance of
an announced handover; the refusal record."""
import json
from pathlib import Path

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak

import broker

FIXTURE = json.loads((Path(__file__).parent / "spec" / "farm-invite.json").read_text())
EXAMPLE = FIXTURE["example"]
NODE = "0x" + "11" * 20
FARM = EXAMPLE["farm"]
OTHER_FARM = "0x" + "22" * 20
ANNOUNCER = "0x" + "33" * 20
APPOINTEE = "0x" + "44" * 20
NOW = 1_800_000_000


def test_the_configuration_string_is_taken_apart():
    assert broker.parse_farm_invite(EXAMPLE["configString"]) == (FARM, EXAMPLE["expiresAt"], EXAMPLE["signature"])
    assert broker.parse_farm_invite(f" {EXAMPLE['configString']}\n") == (FARM, EXAMPLE["expiresAt"], EXAMPLE["signature"])


def test_malformed_strings_are_none():
    farm, expires, sig = EXAMPLE["configString"].split(":")
    for value in ("", None, f"{farm}:{expires}", f"{farm}:{expires}:{sig}:x", f"{farm[:-1]}:{expires}:{sig}",
                  f"{farm}:soon:{sig}", f"{farm}:-1:{sig}", f"{farm}:01:{sig}", f"{farm}:{expires}:{sig[:-2]}",
                  f"{farm}:{expires}:{sig[2:]}", f"{farm}:{expires}:0x{'zz' * 65}"):
        assert broker.parse_farm_invite(value) is None, value


def test_the_signer_recovered_under_the_fixture_domain_is_the_farm_owner():
    farm, expires_at, signature = broker.parse_farm_invite(EXAMPLE["configString"])
    signable = encode_typed_data(
        domain_data=FIXTURE["domain"], message_types=FIXTURE["types"],
        message_data={"farm": farm, "node": EXAMPLE["node"], "expiresAt": expires_at})
    assert keccak(b"\x19" + signable.version + signable.header + signable.body).hex() == EXAMPLE["digest"][2:]
    recovered = Account.recover_message(signable, signature=signature)
    assert recovered.lower() == EXAMPLE["recoveredOwner"].lower() == farm
    # The owner key of the fixture signs the same digest.
    assert Account.from_key(FIXTURE["keys"]["ownerPrivateKey"]).address.lower() == farm
    # Another node's invitation does not recover to the farm.
    other = encode_typed_data(
        domain_data=FIXTURE["domain"], message_types=FIXTURE["types"],
        message_data={"farm": farm, "node": NODE, "expiresAt": expires_at})
    assert Account.recover_message(other, signature=signature).lower() != farm


def test_the_refusal_record_is_keyed_by_invitation_and_broker(tmp_path):
    path = broker.farm_refusal_path(tmp_path)
    invite = EXAMPLE["configString"]
    assert broker.farm_join_refused(path, invite, "0xB0A0") is False
    broker.record_farm_join_refused(path, invite, "0xb0a0", "invitation used")
    assert broker.farm_join_refused(path, invite, "0xB0A0") is True
    assert broker.farm_join_refused(path, invite, "0xB0A1") is False
    assert broker.farm_join_refused(path, invite + "0", "0xB0A0") is False


def params():
    values = [0] * 30
    values[14] = 3600  # farmClaimSeconds
    values[15] = 600   # appointeeClaimSeconds
    return broker.params_of(tuple(values))


def event(dproc=ANNOUNCER, farm=FARM, appointed=broker.ZERO_ADDRESS, announced_at=NOW):
    return broker.HandoverRequested(7, 4101, dproc, 100, 1000, farm, appointed, announced_at)


def commitment(until=NOW + 86400, suspended_until=0):
    return broker.Commitment(until, 0, 0, 0, 0, suspended_until)


def test_the_appointee_accepts_at_once():
    assert broker.acceptance_due(event(farm=OTHER_FARM, appointed=NODE), NOW, NODE, None, params(), commitment()) == ('now', None)
    # Even with farm_accept off: the appointment names this node.
    assert broker.acceptance_due(event(farm=broker.ZERO_ADDRESS, appointed=NODE), NOW, NODE, None, params(),
                                 commitment(), farm_accept=False) == ('now', None)


def test_a_member_waits_out_the_appointee_window_and_accepts_at_once_without_an_appointee():
    assert broker.acceptance_due(event(appointed=APPOINTEE), NOW, NODE, FARM, params(), commitment()) == ('after', NOW + 600)
    assert broker.acceptance_due(event(appointed=APPOINTEE), NOW + 600, NODE, FARM, params(), commitment()) == ('now', None)
    assert broker.acceptance_due(event(), NOW, NODE, FARM, params(), commitment()) == ('now', None)
    # The farm compares case-insensitively.
    assert broker.acceptance_due(event(), NOW, NODE, FARM.upper().replace("0X", "0x"), params(), commitment()) == ('now', None)


def test_another_farm_the_announcer_a_closed_claim_a_suspended_or_uncommitted_node_never():
    due = broker.acceptance_due
    assert due(event(), NOW, NODE, OTHER_FARM, params(), commitment())[0] == 'never'
    assert due(event(farm=broker.ZERO_ADDRESS), NOW, NODE, None, params(), commitment())[0] == 'never'
    assert due(event(dproc=NODE), NOW, NODE, FARM, params(), commitment())[0] == 'never'
    assert due(event(), NOW + 3601, NODE, FARM, params(), commitment())[0] == 'never'
    assert due(event(), NOW + 3600, NODE, FARM, params(), commitment()) == ('now', None)
    assert due(event(), NOW, NODE, FARM, params(), commitment(suspended_until=NOW + 1))[0] == 'never'
    assert due(event(), NOW, NODE, FARM, params(), commitment(until=NOW))[0] == 'never'
    assert due(event(), NOW, NODE, FARM, params(), commitment(), farm_accept=False)[0] == 'never'
    # The appointee's claim is refused to a suspended appointee too.
    assert due(event(appointed=NODE), NOW, NODE, None, params(), commitment(suspended_until=NOW + 1))[0] == 'never'


def test_the_event_is_read_from_the_log_with_its_block_time():
    log = {'args': {'id': 7, 'orderId': 4101, 'dproc': ANNOUNCER, 'fromBlock': 100, 'toBlock': 1000, 'farm': FARM,
                    'appointed': broker.ZERO_ADDRESS}}
    assert broker.handover_requested(log, NOW) == event()
