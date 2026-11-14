"""The release's version (PHASE-E-PLAN.md E39, WS-G): the changelog's top
entry is the version node/.env carries (config.version, logged at start and
posted by the heartbeat as v<version>), and the v3.8.0 entry names what an
operator configures or sees of 3.8.0."""
import re
from pathlib import Path

import config

CHANGELOG = Path(__file__).resolve().parents[2] / "changelog"
VERSION_LINE = re.compile(r"^v(\d+\.\d+\.\d+)$")


def entries():
    """[(version, text)] of the changelog, top entry first."""
    found = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        match = VERSION_LINE.match(line.strip())
        if match:
            found.append((match.group(1), []))
        elif found:
            found[-1][1].append(line)
    return [(version, "\n".join(lines)) for version, lines in found]


def test_the_changelog_opens_with_the_release():
    top, _ = entries()[0]
    assert top == "3.8.4"


def test_the_384_entry_names_the_absolute_compose_path():
    text = dict(entries())["3.8.4"]
    for item in ("absolute path", "working", "node/<network>/", "warning"):
        assert item in text, item


def test_the_383_entry_names_the_tested_las_tag():
    text = dict(entries())["3.8.3"]
    for item in ("localhost:5000/etny-las:tested-<trustedzone>", "las-tested-<trustedzone>.yml", "integration test",
                 "-a 0.0.0.0", "ipv6.disable=1", "Unable to allocate socket"):
        assert item in text, item


def test_the_382_entry_names_the_headroom_and_the_resource_refusals_file():
    text = dict(entries())["3.8.2"]
    for item in ("DP_REQUEST_GAS_HEADROOM_PERCENT", "25", "1.5 %", "doreq_resources.json", "doreq_cache.txt"):
        assert item in text, item


def test_the_381_entry_names_the_litvm_rpc_and_the_log_chunk():
    text = dict(entries())["3.8.1"]
    for item in ("https://rpc.testnet.liteforge.zeeve.net/", "_LOGS_CHUNK_BLOCKS", "3000", "litvm_liteforge_unsafe"):
        assert item in text, item


def test_the_version_file_carries_the_changelogs_top_entry():
    top, _ = entries()[0]
    assert config.version == top


def test_the_380_entry_names_what_the_operator_sees():
    text = dict(entries())["3.8.0"]
    for item in ("_TIP_PER_CU_HOUR_WEI", "_ACCEPT_RESERVED", "_FARM_INVITE", "_FARM_ACCEPT",
                 "addDPRequestV2", "_addResultToOrderV2", "commitForBatch", "TimeoutStopSec=21600",
                 "SIGHUP", "--handover", "cryptography 3.4.8", "0x3Aa1A0d7A8A9DaE9e13345D3261afe86D67326A4",
                 "INTEGRATION_TEST_SECONDS"):
        assert item in text, item
    assert "3.7.56" not in text
