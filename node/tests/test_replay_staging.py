"""replay (PHASE-E-PLAN.md E34, F.6; PRICING-PLAN.md section 6): the switch
mirrors replay.js switchDecision; the replay set is the predecessor's input
rows without a signed output row landed by its bound (a row unsigned or
signed by another wallet answers nothing, an etny-sw row verifying under
none of the certificates leaves no wallet and every input replayed, a row
logged after the bound answers nothing, the set in seq order), with vectors
built here (eth_account for the rows, cryptography for the P-384
attestation) and the fixture's replayed example; the certificate walk."""
import json
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from eth_account import Account
from eth_account.messages import encode_defunct

import broker
import capability
import replay
from constants import RPC_REPLICA_LAG_BLOCKS

RESULT = json.loads((Path(__file__).parent / "spec" / "result-v5.json").read_text())
FENCE = json.loads((Path(__file__).parent / "spec" / "session-fence.json").read_text())
RECORD = json.loads((Path(__file__).parent / "spec" / "capability-record.json").read_text())
PREDECESSOR = 4101
WINDOW = broker.HandoverOf(True, PREDECESSOR, True, 4102, 52_646_900, 52_647_800)

TASK_KEY = Account.from_key("0x" + "5a" * 32)
OTHER_KEY = Account.from_key("0x" + "6b" * 32)
WALLET = TASK_KEY.address.lower()


def input_row(seq, order_id=PREDECESSOR):
    return f"v1:{seq}:{order_id}:bafkreicid{seq}:{'%064x' % seq}"


def output_row(seq, ack, signer=TASK_KEY, order_id=PREDECESSOR, status='ok', code=0, sig=None):
    cid, sha = f"bafkreiout{seq}", '%064x' % (1000 + seq)
    if sig is None:
        message = f"etny-so|{order_id}|{seq}|{ack}|{status}|{code}|{cid}|{sha}"
        sig = signer.sign_message(encode_defunct(text=message)).signature.hex()
        if not sig.startswith('0x'):
            sig = '0x' + sig
    return f"v1:{seq}:{order_id}:{ack}:{status}:{code}:{cid}:{sha}:{sig}"


def p384_key():
    return ec.generate_private_key(ec.SECP384R1(), default_backend())


def pem_of(private):
    return private.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def wallet_row(private, order_id=PREDECESSOR, wallet=WALLET):
    message = f"etny-sw|{order_id}|{wallet}".encode()
    return f"v1:{order_id}:{wallet}:{private.sign(message, ec.ECDSA(hashes.SHA256())).hex()}"


# ------------------------------------------------------------------ the switch

def test_no_window_is_kind_none_with_no_replay():
    assert replay.switch_bound(None, 52_647_000, (0, 0), 1) == replay.Switch('none', 0, None)


def test_a_result_inside_the_window_is_result_block_for_an_inheritor_and_none_for_the_placed_successor():
    result_at = (1_800_000_000, 52_647_000)
    assert replay.switch_bound(WINDOW, 52_647_005, result_at, 1_800_000_000) == replay.Switch('resultBlock', 52_647_000, 52_647_000)
    assert replay.switch_bound(WINDOW, 52_647_005, result_at, 1_800_000_500) == replay.Switch('resultBlock', 52_647_000, 52_647_000)
    assert replay.switch_bound(WINDOW, 52_647_005, result_at, 1_799_999_999) == replay.Switch('none', 0, 52_647_000)


def test_to_block_passed_without_a_result_inside_is_kind_to_block():
    assert replay.switch_bound(WINDOW, WINDOW.to_block + 1, (0, 0), 1) == replay.Switch('toBlock', WINDOW.to_block, WINDOW.to_block)
    # A result past toBlock is a miss: the bound stays toBlock.
    assert replay.switch_bound(WINDOW, WINDOW.to_block + 5, (1, WINDOW.to_block + 2), 1) == replay.Switch('toBlock', WINDOW.to_block, WINDOW.to_block)
    # A result before fromBlock is not inside the window either.
    assert replay.switch_bound(WINDOW, WINDOW.to_block + 1, (1, WINDOW.from_block - 1), 1).kind == 'toBlock'


def test_the_window_still_open_without_a_result_is_undecided():
    assert replay.switch_bound(WINDOW, WINDOW.to_block, (0, 0), 1) is None
    assert replay.switch_bound(WINDOW, WINDOW.from_block + 1, (0, 0), 1) is None


def test_the_reads_are_pinned_eight_blocks_below_the_head():
    assert replay.pinned_block(100) == 100 - RPC_REPLICA_LAG_BLOCKS == 92


# --------------------------------------------------------------- the row parse

def test_the_rows_are_parsed_by_shape():
    assert replay.parse_input_row(input_row(3)) == replay.InputRow(3, PREDECESSOR, 'bafkreicid3', '%064x' % 3)
    assert replay.parse_input_row("v1:close:4101") is None
    assert replay.parse_input_row("v2:0:4101:cid:sha") is None
    row = replay.parse_output_row(output_row(2, 1))
    assert (row.seq, row.order_id, row.ack, row.status, row.code) == (2, PREDECESSOR, 1, 'ok', 0)
    assert replay.parse_output_row("v1:0:4101:0:ok:0:cid:sha") is None
    assert replay.parse_wallet_row(f"v1:4101:{WALLET}:abcd") == replay.WalletRow(4101, WALLET, 'abcd')
    assert replay.parse_wallet_row(f"v1:x:{WALLET}:abcd") is None
    assert replay.trustedzone_name("v3:bafyimage:etny-nodenithy-amoy:bafycompose:ch:pk") == "etny-nodenithy-amoy"
    assert replay.trustedzone_name("v1:x") == ""


def test_the_output_row_signer_is_recovered_over_its_fields():
    assert replay.output_row_signer(replay.parse_output_row(output_row(0, 0))) == WALLET
    assert replay.output_row_signer(replay.parse_output_row(output_row(0, 0, signer=OTHER_KEY))) == OTHER_KEY.address.lower()
    assert replay.output_row_signer(replay.parse_output_row(output_row(0, 0, sig='0x' + 'ff' * 65))) is None
    assert replay.output_row_signer(replay.parse_output_row(output_row(0, 0, sig='zz'))) is None


# --------------------------------------------------------- the attestation

def test_the_task_wallet_is_attested_under_a_registry_certificate():
    private, other = p384_key(), p384_key()
    row = replay.parse_wallet_row(wallet_row(private))
    assert replay.wallet_attested_by(row, PREDECESSOR, [pem_of(other), pem_of(private)]) == WALLET
    assert replay.wallet_attested_by(row, PREDECESSOR, [pem_of(other)]) is None
    assert replay.wallet_attested_by(row, PREDECESSOR, []) is None
    assert replay.wallet_attested_by(row, 4102, [pem_of(private)]) is None
    assert replay.wallet_attested_by(None, PREDECESSOR, [pem_of(private)]) is None
    # The DER of the key as hex is a certificate text too.
    der_hex = private.public_key().public_bytes(serialization.Encoding.DER,
                                                serialization.PublicFormat.SubjectPublicKeyInfo).hex()
    assert replay.attested_wallet([wallet_row(other), wallet_row(private)], PREDECESSOR, ['0x' + der_hex]) == WALLET


def test_verify_p384_checks_the_fixture_fence_signatures_under_the_trustedzone_key():
    pem = RECORD["trustedzoneKey"]["publicKeyPem"]
    for example in FENCE["examples"].values():
        assert capability.verify_p384(pem, example["signedText"], example["signatureDerHex"])
        assert not capability.verify_p384(pem, example["signedText"] + " ", example["signatureDerHex"])
    assert capability.verify_p384("not a key", "x", "00") is False
    assert capability.verify_p384(pem, "x", "zz") is False
    assert capability.p384_public_key("") is None
    # A secp256k1 key is not a P-384 key.
    k1 = ec.generate_private_key(ec.SECP256K1(), default_backend())
    assert capability.p384_public_key(pem_of(k1)) is None


# ------------------------------------------------------------ the replay set

INPUTS = [input_row(0), input_row(1), input_row(2), input_row(3), "v1:close:4101", input_row(0, order_id=4100)]


def seqs(rows):
    return [row.seq for row in rows]


def test_signed_rows_answer_and_unsigned_or_foreign_rows_do_not():
    outputs = [(5, output_row(0, 0)), (6, output_row(1, 2, signer=OTHER_KEY)), (7, output_row(2, 1, sig='0x' + 'ff' * 65)),
               (8, output_row(3, 3, order_id=4102))]
    assert seqs(replay.replay_set(PREDECESSOR, INPUTS, outputs, WALLET, None)) == [1, 2, 3]


def test_no_wallet_replays_every_input():
    outputs = [(5, output_row(0, 0)), (6, output_row(1, 1)), (7, output_row(2, 2)), (8, output_row(3, 3))]
    assert seqs(replay.replay_set(PREDECESSOR, INPUTS, outputs, None, None)) == [0, 1, 2, 3]
    assert seqs(replay.replay_set(PREDECESSOR, INPUTS, outputs, WALLET, None)) == []


def test_a_row_logged_after_the_bound_answers_nothing():
    outputs = [(5, output_row(0, 0)), (6, output_row(1, 1)), (7, output_row(2, 2))]
    assert seqs(replay.replay_set(PREDECESSOR, INPUTS, outputs, WALLET, 6)) == [1, 2, 3]
    assert replay.first_late_row([{'args': {'rowNumber': 7}}, {'args': {'rowNumber': 6}}]) == 6
    assert replay.first_late_row([]) is None


def test_the_set_comes_in_seq_order_and_a_notice_row_answers_too():
    inputs = [input_row(3), input_row(1), input_row(2), input_row(0)]
    outputs = [(9, output_row(0, 2, status='error', code=54))]
    assert seqs(replay.replay_set(PREDECESSOR, inputs, outputs, WALLET, None)) == [0, 1, 3]


def test_the_fixture_forced_example_leaves_its_second_input_and_the_successor_answers_it_first():
    forced = RESULT["examples"]["forced"]["inputs"]
    replayed = RESULT["examples"]["replayed"]["inputs"]
    wallet = forced["taskWallet"].lower()
    outputs = list(enumerate(forced["outputValues"]))
    assert replay.output_row_signer(replay.parse_output_row(forced["outputValues"][0])) == wallet
    rows = replay.replay_set(PREDECESSOR, forced["inputRows"], outputs, wallet, None)
    assert seqs(rows) == replayed["replayedSeqs"] == [1]
    assert [f"v1:{r.seq}:{r.order_id}:{r.cid}:{r.sha256}" for r in rows] == replayed["replayedInputRows"]
    first = replay.parse_output_row(replayed["outputValues"][0])
    assert (first.order_id, first.ack) == (replayed["orderId"], 1)
    assert replay.output_row_signer(first) == replayed["taskWallet"].lower()
    assert Account.from_key(replayed["taskWalletPrivateKey"]).address.lower() == replayed["taskWallet"].lower()


# ------------------------------------------------------- the certificate walk

def test_the_certificates_are_walked_until_the_enumerator_reverts(monkeypatch):
    records = {0: ('0xowner', 'bafyA', 'v3', True, 'CERT-A', 'bafyCA', 'tz'),
               1: ('0xowner', 'bafyB', 'v3', True, '', 'bafyCB', 'tz'),
               2: ('0xowner', 'bafyC', 'v3', False, '', 'bafyCC', 'tz'),
               3: ('0xowner', 'bafyD', 'v3', True, 'CERT-A', 'bafyCD', 'tz')}

    def versions(name, version, i):
        assert (name, version) == ('tz', 'v3')
        return records[i]

    def cert_of(ipfs_hash):
        return {'bafyB': 'CERT-B', 'bafyC': ''}[ipfs_hash]

    assert replay.registry_certificates(versions, cert_of, 'tz') == ['CERT-A', 'CERT-B']
    monkeypatch.setattr(replay, 'MAX_TRUSTEDZONE_VERSIONS', 1)
    assert replay.registry_certificates(versions, cert_of, 'tz') == ['CERT-A']
    assert replay.registry_certificates(lambda *a: (_ for _ in ()).throw(ValueError()), cert_of, 'tz') == []


def test_the_spool_path_and_the_digest_check(tmp_path):
    assert replay.spooled_input_path(str(tmp_path), 4102, 7).endswith(str(Path('4102') / 'replay' / '7'))
    assert replay.digest_matches(b'abc', 'BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD')
    assert not replay.digest_matches(b'abd', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
