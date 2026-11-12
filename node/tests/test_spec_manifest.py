"""node/tests/spec/ holds byte-identical copies of the shared fixtures of
pox-smart-contract-litvm/spec/ (PHASE-E-PLAN.md section 11): each copy's
sha256 equals its MANIFEST.sha256 entry, and the source bytes when the
litvm checkout sits beside this one."""
import hashlib
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "spec"
SOURCE = Path(__file__).resolve().parents[3] / "pox-smart-contract-litvm" / "spec"
COPIES = [
    "capability-record.json",
    "metadata-keys.md",
    "session-commits.json",
    "session-fence.json",
    "result-v5.json",
    "farm-invite.json",
    "broker-interface-id.json",
]


def manifest():
    entries = {}
    for line in (SPEC / "MANIFEST.sha256").read_text().splitlines():
        digest, _, name = line.partition("  ")
        entries[name.strip()] = digest.strip()
    return entries


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", COPIES)
def test_the_copy_matches_the_manifest(name):
    assert sha256(SPEC / name) == manifest()[name]


@pytest.mark.parametrize("name", COPIES + ["MANIFEST.sha256"])
def test_the_copy_matches_the_source_checkout(name):
    if not SOURCE.is_dir():
        pytest.skip(f"{SOURCE} is not checked out beside this repository")
    assert (SPEC / name).read_bytes() == (SOURCE / name).read_bytes()
