"""The changelog's first header names the version node/.env's VERSION gives:
config.version, which the agent logs at start ("Initializing Ethernity CLOUD
Agent v...") and sends in its heartbeat."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_changelog_header_is_the_version_the_agent_reports():
    header = (ROOT / 'changelog').read_text(encoding='utf-8').splitlines()[0].strip()
    match = re.fullmatch(r'v(\d+\.\d+\.\d+)', header)
    assert match, header
    env_versions = [line.split('=', 1)[1].strip()
                    for line in (ROOT / 'node' / '.env').read_text(encoding='utf-8').splitlines()
                    if line.startswith('VERSION=')]
    assert env_versions == [match.group(1)]
