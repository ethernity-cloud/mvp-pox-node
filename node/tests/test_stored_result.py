"""The stored result's grammar (MATCHING-SYNC-PLAN.md A11; OD11 A and the
agent's (a); pox-smart-contract-litvm spec/matching.md section 17 "The
stored result" at 52e36e0): utils.stored_result_status reads each of the 53
stored vectors of spec/matching/result-fields.json as PoxMatching's decoder
does, and the stored strings of its accepted and refused vectors as decoded
with their codes; utils.canonical_transaction_text brings the three
spellings of the same bytes the grammar refuses (no 0x, 0X, upper-case
digits) to 0x and lower-case hex and leaves any other text as it stands;
etny-node.py stores the result in that form on a network with matching,
logs one the grammar does not decode before it is sent, and stores the
trustedzone's text as it stands elsewhere."""
import importlib.util
import json
import logging
from pathlib import Path

import pytest

from utils import canonical_transaction_text, stored_result_status

NODE = Path(__file__).resolve().parents[1]
FIELDS = json.loads((Path(__file__).parent / "spec" / "matching" / "result-fields.json").read_text())
ORDER = FIELDS["transaction"]["orderId"]
STORED = {v["name"]: v for v in FIELDS["stored"]}
CID = FIELDS["transaction"]["cid"]


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_fixture_holds_53_stored_vectors():
    assert len(FIELDS["stored"]) == FIELDS["groups"]["stored"] == 53
    assert FIELDS["storedGrammar"]["maxTransactionBytes"] == 4096


@pytest.mark.parametrize("vector", FIELDS["stored"], ids=[v["name"] for v in FIELDS["stored"]])
def test_each_stored_vector_reads_as_the_grammar_says(vector):
    read = stored_result_status(vector["stored"], ORDER)
    want = vector["expected"]
    assert read.status == want["status"]
    assert (read.status not in ("notV3", "noTransaction")) == want["transaction"]
    if want["transaction"]:
        assert (read.order_id, read.signer) == (want["orderId"], want["signer"])
    if want["status"] == "decoded":
        assert read.code == want["code"]


@pytest.mark.parametrize("vector", FIELDS["accepted"] + FIELDS["refused"],
                         ids=[v["name"] for v in FIELDS["accepted"] + FIELDS["refused"]])
def test_the_stored_strings_of_the_field_vectors_decode_with_their_codes(vector):
    read = stored_result_status(vector["stored"], ORDER)
    assert (read.status, read.code) == ("decoded", vector["code"])


def canonical_hex():
    stored = STORED["canonical"]["stored"]
    assert stored.startswith("v3:0x") and stored.endswith(":" + CID)
    return stored[len("v3:0x"):-len(":" + CID)]


@pytest.mark.parametrize("spelling", ["no-prefix", "upper-prefix", "upper-digits", "one-upper-digit",
                                      "upper-prefix-and-digits"])
def test_the_three_spellings_of_the_same_bytes_are_brought_to_the_grammar(spelling):
    run = canonical_hex()
    text = {"no-prefix": run, "upper-prefix": "0X" + run, "upper-digits": "0x" + run.upper(),
            "one-upper-digit": "0x" + run[0].upper() + run[1:], "upper-prefix-and-digits": "0X" + run.upper()}[spelling]
    assert stored_result_status(f"v3:{text}:{CID}", ORDER).status == "noTransaction"
    canonical = canonical_transaction_text(text)
    assert canonical == "0x" + run
    assert stored_result_status(f"v3:{canonical}:{CID}", ORDER).status == "decoded"


@pytest.mark.parametrize("text", ["0x" + "ab" * 3 + "a", " 0xabab", "0xabab ", "0xab ab", "0x0xabab", "", "0x",
                                  "Task execution timed out"])
def test_any_other_text_is_left_as_it_stands(text):
    assert canonical_transaction_text(text) == text


def stored_node(agent, matching):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__matching = matching
    return node


def test_a_matching_network_stores_the_result_in_the_grammars_form(agent, caplog):
    caplog.set_level(logging.INFO)
    node = stored_node(agent, object())
    run = canonical_hex()
    assert node._EtnyPoXNode__stored_v3_result(ORDER, CID, run.upper()) == f"v3:0x{run}:{CID}"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    # A text outside the grammar in another way is sent as it stands, and
    # what the grammar reads from it is logged first.
    spaced = f" 0x{run}"
    assert node._EtnyPoXNode__stored_v3_result(ORDER, CID, spaced) == f"v3:{spaced}:{CID}"
    assert any("reads noTransaction under the stored result's grammar" in m for m in caplog.messages)


def test_without_matching_the_trustedzones_text_is_stored_as_it_stands(agent):
    run = canonical_hex()
    assert stored_node(agent, None)._EtnyPoXNode__stored_v3_result(ORDER, CID, run.upper()) == \
        f"v3:{run.upper()}:{CID}"
