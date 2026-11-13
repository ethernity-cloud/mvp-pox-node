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
    assert top == "3.8.0"


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
