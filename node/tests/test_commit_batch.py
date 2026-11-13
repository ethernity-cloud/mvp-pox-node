"""gas_ledger.batch_plan, batch_fallback and batch_failure_index: two or
more relayable commits of one enclave at a tick go as one commitForBatch in
their relay order, one item goes alone, two enclaves give two batches, and
a batch revert naming an index falls back to singles for that enclave with
the failing key's later files skipped."""
from eth_utils import keccak
from web3.exceptions import ContractCustomError, ContractLogicError

from constants import RELAY_BATCH_MIN
from gas_ledger import batch_failure_index, batch_fallback, batch_plan, commit_file_key

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
K1_0, K1_1, K2_0 = "esr.commit.1111.0.json", "esr.commit.1111.1.json", "esr.commit.2222.0.json"


def key_of(name):
    return commit_file_key(name)[0]


def test_two_keys_of_one_enclave_keep_their_relay_order_in_one_batch():
    plan = batch_plan([K1_0, K2_0, K1_1], {K1_0: A, K2_0: A, K1_1: A}.get)
    assert plan == [(A, [K1_0, K2_0, K1_1], True)]
    assert RELAY_BATCH_MIN == 2


def test_two_enclaves_give_two_batches_in_order_of_first_appearance():
    plan = batch_plan([K1_0, "esr.commit.3333.0.json", K1_1, "esr.commit.3333.1.json"],
                      {K1_0: A, K1_1: A, "esr.commit.3333.0.json": B, "esr.commit.3333.1.json": B}.get)
    assert plan == [(A, [K1_0, K1_1], True), (B, ["esr.commit.3333.0.json", "esr.commit.3333.1.json"], True)]


def test_one_item_goes_alone():
    assert batch_plan([K1_0], {K1_0: A}.get) == [(A, [K1_0], False)]
    plan = batch_plan([K1_0, K1_1, K2_0], {K1_0: A, K1_1: A, K2_0: B}.get)
    assert plan == [(A, [K1_0, K1_1], True), (B, [K2_0], False)]


def test_nothing_plans_nothing():
    assert batch_plan([], lambda name: A) == []


def test_a_failure_at_an_index_skips_that_name_and_its_keys_later_files():
    singles, skipped = batch_fallback([K1_0, K2_0, K1_1, "esr.commit.2222.1.json"], 0, key_of)
    assert singles == [K2_0, "esr.commit.2222.1.json"]
    assert skipped == [K1_0, K1_1]
    singles, skipped = batch_fallback([K1_0, K2_0, K1_1], 2, key_of)
    assert (singles, skipped) == ([K1_0, K2_0], [K1_1])


def test_a_failure_without_an_index_sends_every_name_alone():
    assert batch_fallback([K1_0, K2_0], None, key_of) == ([K1_0, K2_0], [])
    assert batch_fallback([K1_0, K2_0], 5, key_of) == ([K1_0, K2_0], [])


def encoded(selector_text, index):
    return "0x" + keccak(text=selector_text)[:4].hex() + index.to_bytes(32, "big").hex()


def test_the_index_is_read_from_both_batch_errors():
    failed = ContractCustomError("execution reverted", data=encoded("BatchEntryFailed(uint256,bytes)", 2) + "00" * 64)
    assert batch_failure_index(failed) == 2
    out_of_gas = ContractCustomError("execution reverted", data=encoded("BatchEntryOutOfGas(uint256)", 1))
    assert batch_failure_index(out_of_gas) == 1
    nested = ContractCustomError("execution reverted", data={"data": encoded("BatchEntryOutOfGas(uint256)", 3)})
    assert batch_failure_index(nested) == 3


def test_another_error_names_no_index():
    assert batch_failure_index(ContractLogicError("execution reverted: VersionMismatch")) is None
    assert batch_failure_index(ContractCustomError("x", data="0x" + keccak(text="Other(uint256)")[:4].hex() + "00" * 32)) is None
    assert batch_failure_index(ContractCustomError("x", data="0x1234")) is None
    assert batch_failure_index(RuntimeError("no data")) is None
