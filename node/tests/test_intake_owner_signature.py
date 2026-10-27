"""ipfs_intake: a payload lodged before the request names it is taken on the
request owner's EIP-191 signature of owner_signature_message, counted per
request in the ledger (bloxberg testnet rig, 2026-10-26: two clients stopped
between committing an input's row and uploading its bytes, and the row
without bytes held their sessions until the deadline)."""
from eth_account import Account
from eth_account.messages import encode_defunct

from ipfs_intake import IntakeLedger, OWNER_SIGNED_PAYLOADS_PER_REQUEST, owner_signature_message, signed_by

MESSAGE = owner_signature_message("bloxberg_testnet", 4935)


def sign(account, message=MESSAGE):
    return account.sign_message(encode_defunct(text=message)).signature.hex()


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
