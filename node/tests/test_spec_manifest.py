"""node/tests/spec/ and node/tests/spec/matching/ hold byte-identical copies of
the shared fixtures of pox-smart-contract-litvm spec/ at SOURCE_COMMIT
(spec/README.md "Pinning a copy"): each copy's sha256 equals its line of
the MANIFEST.sha256 beside it, each MANIFEST.sha256 is the source's, and,
when the contract repository is checked out beside this one, each copy and
each manifest equals `git show SOURCE_COMMIT:spec/<file>` of it, whatever
that checkout's working tree holds."""
import hashlib
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "spec"
SOURCE = Path(__file__).resolve().parents[3] / "pox-smart-contract-litvm"
SOURCE_COMMIT = "2f600f6"
# {directory of copies: the files copied there}; the spec README's list for
# mvp-pox-node.
COPIES = {
    SPEC: ["metadata-keys.md", "broker-interface-id.json", "capability-record.json", "farm-invite.json",
           "result-v5.json", "session-commits.json", "session-fence.json", "bench-record.json",
           "time-statement.json", "abi-selectors.json", "reserved-prefixes.json", "words.json"],
    SPEC / "matching": ["matching-rank.json", "ranking.json", "matching.md", "result-fields.json"],
}
CASES = [(directory, name) for directory, names in COPIES.items() for name in names]
README_CONSUMER = ("| mvp-pox-node | `matching-rank.json`, `ranking.json`, `matching.md`, `result-fields.json`, "
                   "`metadata-keys.md`, `broker-interface-id.json`, `capability-record.json`, `farm-invite.json`, "
                   "`result-v5.json`, `session-commits.json`, `session-fence.json`, `bench-record.json`, "
                   "`time-statement.json`, `abi-selectors.json`, `reserved-prefixes.json`, `words.json` |")


def manifest(directory):
    entries = {}
    for line in (directory / "MANIFEST.sha256").read_text().splitlines():
        digest, _, name = line.partition("  ")
        entries[name.strip()] = digest.strip()
    return entries


def shown(path):
    """`git show SOURCE_COMMIT:spec/<path>` of the checkout beside this
    repository; the test is skipped without the checkout or the commit."""
    if not (SOURCE / ".git").exists():
        pytest.skip(f"{SOURCE} is not checked out beside this repository")
    result = subprocess.run(["git", "-C", str(SOURCE), "show", f"{SOURCE_COMMIT}:spec/{path}"], capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"{SOURCE} holds no commit {SOURCE_COMMIT}")
    return result.stdout


@pytest.mark.parametrize("directory, name", CASES, ids=[f"{d.name}/{n}" for d, n in CASES])
def test_the_copy_matches_its_manifest_line(directory, name):
    assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == manifest(directory)[name]


@pytest.mark.parametrize("directory", list(COPIES), ids=[d.name for d in COPIES])
def test_each_directory_holds_one_manifest_of_the_same_bytes(directory):
    assert (directory / "MANIFEST.sha256").read_bytes() == (SPEC / "MANIFEST.sha256").read_bytes()


def test_every_copy_named_by_the_spec_readme_for_this_repository_is_held():
    held = sorted(name for names in COPIES.values() for name in names)
    listed = sorted(entry.strip(" `") for entry in README_CONSUMER.split("|")[2].split(","))
    assert held == listed


@pytest.mark.parametrize("directory, name", CASES, ids=[f"{d.name}/{n}" for d, n in CASES])
def test_the_copy_is_the_source_commits(directory, name):
    assert (directory / name).read_bytes() == shown(name)


def test_the_manifest_is_the_source_commits():
    assert (SPEC / "MANIFEST.sha256").read_bytes() == shown("MANIFEST.sha256")


def test_the_readme_lists_these_copies_for_this_repository():
    assert README_CONSUMER in shown("README.md").decode("utf-8").splitlines()
