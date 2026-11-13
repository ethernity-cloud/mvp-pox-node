"""ipfs_intake: a payload lodged before the request names it is taken on the
request owner's EIP-191 signature of owner_signature_message, counted per
request in the ledger (bloxberg testnet rig, 2026-10-26: two clients stopped
between committing an input's row and uploading its bytes, and the row
without bytes held their sessions until the deadline). The owner is the
request's downer, or the holder a downer answering ERC-165 for
IReservationResolver names through holderOf (PHASE-E-PLAN.md F.8, E36)."""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipfs_intake import (IntakeLedger, NO_HOLDER, OWNER_SIGNED_PAYLOADS_PER_REQUEST, PayloadIntake,
                         RESOLVER_INTERFACE_ID, owner_signature_message, signed_by)

MESSAGE = owner_signature_message("bloxberg_testnet", 4935)
SPEC = Path(__file__).parent / "spec" / "broker-interface-id.json"
CID = "Qm" + "a" * 44
NETWORK = "polygon_amoy"
REQUEST = 77
BROKER = "0x" + "bb" * 20
EOA = "0x" + "ee" * 20


def sign(account, message=MESSAGE):
    return account.sign_message(encode_defunct(text=message)).signature.hex()


class FakeStorage:
    connected = True

    def add_bytes_raw(self, body, name):
        return CID

    def provide(self, cid):
        pass


def fake_backend(downer, code=b"", supports=None, holder=None):
    """A backend whose request's downer is `downer`, with `code` at it; the
    contract there answers `supports` to supportsInterface (an exception
    raises) and `holder` ((found, address)) to holderOf."""
    calls = []

    class Resolver:
        def __init__(self, address, abi):
            self.address = address
            self.abi = abi

        def caller(self):
            resolver = self

            class Caller:
                def supportsInterface(self, interface_id):
                    calls.append(("supportsInterface", interface_id))
                    if isinstance(supports, Exception):
                        raise supports
                    return supports

                def holderOf(self, do_req):
                    calls.append(("holderOf", do_req))
                    return holder
            return Caller()

    eth = SimpleNamespace(get_code=lambda address: code, contract=Resolver)
    w3 = SimpleNamespace(eth=eth, to_checksum_address=Web3.to_checksum_address)

    class Contract:
        def __init__(self):
            self.w3 = w3

        def caller(self):
            return SimpleNamespace(_getDORequest=lambda do_req: (downer, 1, 1, 1, 1, 1, 1, 0))

    return Contract(), FakeStorage(), calls


def intake(tmp_path, contract, storage):
    one = PayloadIntake(ledger_path=str(tmp_path / "ledger.json"), bind="127.0.0.1:0",
                        logger=logging.getLogger("test-intake"), chain_wait_seconds=0)
    one.register_network(NETWORK, contract, storage, logging.getLogger("test-intake"))
    return one


def lodge(one, account):
    signature = sign(account, owner_signature_message(NETWORK, REQUEST))
    return one.accept(NETWORK, REQUEST, CID, b"payload", signature)


def test_the_resolver_interface_id_is_the_specs():
    spec = json.loads(SPEC.read_text())
    assert "0x" + RESOLVER_INTERFACE_ID.hex() == spec["resolver"]["interfaceId"]


def test_an_eoa_downer_signs_for_its_request(tmp_path):
    owner, other = Account.create(), Account.create()
    contract, storage, calls = fake_backend(owner.address)
    one = intake(tmp_path, contract, storage)
    assert lodge(one, other) == (403, {"error": "the signature is not the request owner's"})
    assert lodge(one, owner) == (200, {"status": "pinned", "cid": CID})
    assert calls == []


def test_a_resolver_downers_request_is_lodged_by_the_holder(tmp_path):
    holder, downer_key = Account.create(), Account.create()
    contract, storage, calls = fake_backend(BROKER, code=b"\x60\x80", supports=True,
                                            holder=(True, holder.address))
    one = intake(tmp_path, contract, storage)
    assert lodge(one, downer_key) == (403, {"error": "the signature is not the request owner's"})
    assert lodge(one, holder) == (200, {"status": "pinned", "cid": CID})
    assert ("supportsInterface", RESOLVER_INTERFACE_ID) in calls
    assert ("holderOf", REQUEST) in calls


def test_a_resolver_that_finds_no_holder_lodges_nothing(tmp_path):
    anyone = Account.create()
    contract, storage, calls = fake_backend(BROKER, code=b"\x60\x80", supports=True,
                                            holder=(False, "0x" + "00" * 20))
    one = intake(tmp_path, contract, storage)
    assert lodge(one, anyone) == (403, {"error": "the request's owner resolves no holder"})
    assert PayloadIntake._request_owner(one.backends[NETWORK], REQUEST) == NO_HOLDER


def test_a_contract_downer_that_is_no_resolver_signs_itself(tmp_path):
    for supports in (False, ContractLogicError("execution reverted")):
        contract, storage, calls = fake_backend(EOA, code=b"\x60\x80", supports=supports)
        assert PayloadIntake._request_owner(one_backend(tmp_path, contract, storage), REQUEST) == EOA
        assert all(name == "supportsInterface" for name, _ in calls)


def one_backend(tmp_path, contract, storage):
    return intake(tmp_path, contract, storage).backends[NETWORK]


def test_the_message_names_the_network_and_the_request():
    assert MESSAGE == "etny-intake|bloxberg_testnet|4935"
    assert owner_signature_message("ethereum_sepolia", "12") == "etny-intake|ethereum_sepolia|12"


def test_the_owners_signature_is_recognised_with_or_without_the_prefix_whatever_the_case_of_the_address():
    owner = Account.create()
    signature = sign(owner)
    bare = signature[2:] if signature.startswith("0x") else signature
    assert signed_by(MESSAGE, bare, owner.address)
    assert signed_by(MESSAGE, "0x" + bare, owner.address.lower())


def test_another_keys_signature_a_signature_of_another_message_and_garbage_are_not():
    owner, other = Account.create(), Account.create()
    assert not signed_by(MESSAGE, sign(other), owner.address)
    assert not signed_by(MESSAGE, sign(owner, owner_signature_message("bloxberg_testnet", 4936)), owner.address)
    assert not signed_by(MESSAGE, "0xnot-a-signature", owner.address)
    assert not signed_by(MESSAGE, "", owner.address)


def test_the_ledger_counts_a_requests_uploads(tmp_path):
    ledger = IntakeLedger(str(tmp_path / "ledger.json"))
    for cid in ("bafkreia", "bafkreib", "bafkreic"):
        ledger.add("bloxberg_testnet", 7, cid)
    ledger.add("bloxberg_testnet", 8, "bafkreia")
    ledger.add("ethereum_sepolia", 7, "bafkreid")
    assert ledger.count("bloxberg_testnet", 7) == 3
    assert ledger.count("bloxberg_testnet", "8") == 1
    assert ledger.count("bloxberg_testnet", 9) == 0
    assert OWNER_SIGNED_PAYLOADS_PER_REQUEST >= 256 + 4
