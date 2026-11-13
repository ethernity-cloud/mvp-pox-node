"""The bundled ABI files (config.abi_filepath, the image registry's and the
five Phase E files) carry every function, event and error the agent calls or
scans, at the signature the agent uses (PHASE-E-PLAN.md E6, E12-E36); the reader's
_getOrderTerms answers its twenty outputs in IPoxReader.sol's order; the
resolver's two selectors XOR to broker-interface-id.json's id; and
utils.parse_transaction_bytes_ut decodes a signed _addResultToOrder under
the 1.2 union, the seven result-v5.json examples' fields by position."""
import json
from pathlib import Path

import pytest
from eth_account import Account
from eth_utils import keccak
from eth_utils.abi import collapse_if_tuple
from web3 import Web3

import config
from utils import parse_transaction_bytes_ut

SPEC = Path(__file__).parent / "spec"

# {ABI file: [signature the agent calls or scans]}
CALLS = {
    config.abi_filepath: [
        "pricingVersion()",
        "readerAddress()",
        "pricingAddress()",
        "reservationBrokerAddress()",
        "NETWORK_FEE_PERCENT()",
        "VALIDATOR_FEE_PERCENT()",
        "publisherFees(string)",
        "dpTerms(uint256)",
        "refreshDPRequest(uint256,uint256)",
        "_getDPRequest(uint256)",
        "_cancelDPRequest(uint256)",
        "_addDPRequest(uint8,uint8,uint8,uint8,uint16,uint8,string,string,string,string)",
        "addDPRequestV2(uint8,uint8,uint8,uint8,uint16,uint32,uint256,string,string,string,string)",
        "_addMetadataToDPRequest(uint256,string,string)",
        "_addMetadataToDPRequestBatch(uint256,string[],string[])",
        "orderPlacedAt(uint256)",
        "orderTerms(uint256)",
        "orderReport(uint256)",
        "_addResultToOrder(uint256,string)",
        "_addResultToOrderV2(uint256,string,uint16,uint256)",
        "orderOfDPRequest(uint256)",
        "orderResultAt(uint256)",
        "_approveOrder(uint256)",
        "_getOrder(uint256)",
        "_placeOrder(uint256,uint256)",
        "setAcceptsReserved(bool)",
        "setPayoutAddress(address)",
        "payoutAddressOf(address)",
        "_getMyDOOrders()",
        "_getMyDPRequests()",
        "_getOrdersCount()",
        "_getResultFromOrder(uint256)",
        "_getDORequestMetadata(uint256)",
        "_getDORequestsCount()",
        "_getDORequest(uint256)",
        "_getDPRequestMetadata(uint256)",
        "_getDPRequestsCount()",
        "_getMetadataCountForRequest(uint256)",
        "_getMetadataValueForRequest(uint256,uint256)",
        "_getMetadataCountForDPRequest(uint256)",
        "_getMetadataValueForDPRequest(uint256,uint256)",
    ],
    config.image_registry_abi_filepath: [
        "getLatestTrustedZoneImageCertPublicKey(string,string)",
        "trustedZoneVersions(string,string,uint256)",
        "getTrustedZoneImageCertPublicKey(string)",
    ],
    config.esr_abi_filepath: [
        "commitFor(address,bytes32,string,uint256,uint256,uint256,bytes)",
        "commitForBatch(address,(bytes32,string,uint256,uint256,uint256,bytes)[])",
        "commitDigest(address,bytes32,string,uint256,uint256,uint256)",
        "getEntriesFrom(uint256,uint256)",
        "getState(address,bytes32)",
        "entryCount()",
        "relayNonce(address,bytes32)",
    ],
    config.reader_abi_filepath: [
        "_getDORequestV2(uint256)",
        "_getDPRequestV2(uint256)",
        "_getOrderTerms(uint256)",
        "quote()",
        "publisherFees(string)",
        "settlementOf(uint256)",
    ],
    config.pricing_abi_filepath: [
        "params()",
        "avgGasPriceWei()",
    ],
    config.broker_abi_filepath: [
        "token()",
        "params()",
        "commitReserved(uint64)",
        "commitmentOf(address)",
        "requestHandover(uint256,uint64,uint64,address)",
        "handoverOf(uint256)",
        "holderOf(uint256)",
        "acceptHandover(uint256)",
        "farmOf(address)",
        "farmRewardOf(address)",
        "joinFarm(address,uint64,bytes)",
        "supervise(uint256)",
        "superviseWith(uint256,uint256)",
        "nextDue(uint256)",
        "orderEndSlot(uint256)",
        "orderReservation(uint256)",
        "reservationOf(uint256)",
        "templateOf(uint256)",
        "supportsInterface(bytes4)",
    ],
    config.resolver_abi_filepath: [
        "handoverOf(uint256)",
        "holderOf(uint256)",
    ],
    config.erc20_abi_filepath: [
        "approve(address,uint256)",
        "balanceOf(address)",
        "allowance(address,address)",
    ],
}

# {ABI file: [event signature the agent scans]}
EVENTS = {
    config.abi_filepath: [
        "DPRequestCancelled(uint256,address,uint8)",
        "ChainFeeIncreased(uint256,uint256,uint256,uint256,uint256,uint256,uint256)",
        "OutputRowAdded(uint256,uint256)",
        "_placeOrderEV(address,uint256)",
        "_addDPRequestEV(address,uint256)",
    ],
    config.esr_abi_filepath: ["StateCommitted(address,bytes32,string,uint256,uint256,uint256)"],
    config.broker_abi_filepath: [
        "HandoverRequested(uint256,uint256,address,uint64,uint64,address,address)",
        "Unmatched(uint256,uint64)",
        "StateChanged(uint256,uint8)",
        "OrderPlaced(uint256,uint256,address,uint256,uint256,uint32,uint16,uint64,uint256)",
    ],
}

# {ABI file: [error signature a batch relay decodes]}
ERRORS = {
    config.esr_abi_filepath: ["BatchEntryFailed(uint256,bytes)", "BatchEntryOutOfGas(uint256)"],
}

ORDER_TERMS_OUTPUTS = [
    "pricePerCuHourWei", "cu", "chainFeeWei", "chainSpentWei", "gasPriceCapWei", "gasAllocated",
    "tokenPerPol", "trustedZoneFee", "secureLockFee", "placedAt", "approvedAt", "startedAt",
    "lastOutputAt", "terminatedAt", "resultAt", "successorStartedAt", "esrCommits", "esrGasWei",
    "reserved", "recorded",
]


def load(path):
    return json.loads(Path(path).read_text())


def signature(entry):
    return f"{entry['name']}({','.join(collapse_if_tuple(i) for i in entry.get('inputs', []))})"


def selectors(path, kind):
    """{four-byte selector (events: the 32-byte topic): signature} of the
    entries of `kind` in the ABI file, each from keccak over its signature."""
    width = 32 if kind == "event" else 4
    return {keccak(text=signature(e))[:width]: signature(e)
            for e in load(path) if e.get("type") == kind}


def cases(table, kind):
    return [pytest.param(path, sig, kind, id=f"{Path(path).name}:{sig}")
            for path, sigs in table.items() for sig in sigs]


@pytest.mark.parametrize("path,sig,kind",
                         cases(CALLS, "function") + cases(EVENTS, "event") + cases(ERRORS, "error"))
def test_the_bundled_abi_carries_the_signature(path, sig, kind):
    width = 32 if kind == "event" else 4
    assert keccak(text=sig)[:width] in selectors(path, kind), f"{Path(path).name} lacks {sig}"


def test_the_order_terms_outputs_are_in_the_readers_order():
    entry = next(e for e in load(config.reader_abi_filepath) if e.get("name") == "_getOrderTerms")
    assert [o["name"] for o in entry["outputs"]] == ORDER_TERMS_OUTPUTS


def test_the_resolver_selectors_make_the_fixtures_interface_id():
    fixture = json.loads((SPEC / "broker-interface-id.json").read_text())
    found = {sig.partition("(")[0]: sel for sel, sig in selectors(config.resolver_abi_filepath, "function").items()}
    assert {name: "0x" + sel.hex() for name, sel in found.items()} == fixture["resolver"]["selectors"]
    xor = 0
    for sel in found.values():
        xor ^= int.from_bytes(sel, "big")
    assert f"0x{xor:08x}" == fixture["resolver"]["interfaceId"]


def test_the_union_names_the_legacy_entries_of_the_replaced_file():
    names = {e["name"] for e in load(config.abi_filepath) if e.get("type") == "function"}
    assert {"_addDPRequest", "_placeOrder", "_addResultToOrder", "_getOrder", "_getDORequest"} <= names


def signed_result_transaction(abi, order_id, result):
    """The raw legacy-RLP transaction of _addResultToOrder(order_id, result)
    signed by a test key, as parse_transaction_bytes_ut takes it."""
    account = Account.from_key("0x" + "ab" * 32)
    contract = Web3().eth.contract(abi=abi)
    data = contract.encode_abi("_addResultToOrder", args=[order_id, result])
    signed = account.sign_transaction({
        "nonce": 7, "gasPrice": 10**9, "gas": 400000, "value": 0, "chainId": 8995,
        "to": "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7", "data": data,
    })
    return account.address, signed.raw_transaction


@pytest.mark.parametrize("example", ["plain", "session", "esr", "probed", "transferred", "forced", "replayed"])
def test_parse_transaction_bytes_ut_yields_the_result_fields_by_position(example):
    fixture = json.loads((SPEC / "result-v5.json").read_text())
    case = fixture["examples"][example]
    abi = Path(config.abi_filepath).read_text()
    sender, raw = signed_result_transaction(abi, fixture["constants"]["orderId"], case["result"])
    decoded = parse_transaction_bytes_ut(abi, raw)
    assert decoded["from"] == sender
    assert decoded["function_name"] == "_addResultToOrder"
    assert decoded["result"] == case["result"]
    fields = decoded["result"].split(fixture["separator"])
    assert len(fields) == fixture["fieldCount"]
    expected = {f["index"]: f["value"] for f in case["fields"]}
    for index in (1, 4, 6, 12, 14):
        assert fields[index] == expected[index]
    assert fields[12].startswith("etny-esr-commits=")
    assert fields[14].startswith("etny-esr-gas=")
