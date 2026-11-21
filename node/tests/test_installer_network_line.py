"""The NETWORK line of a node's config file: etny-node-installer.sh
check_config_file writes it, ubuntu/etny-node-start.sh sources the file under
`set -e` and passes the value to the agent as `-n ${NETWORK}`, one argument
per network. The installer writes the value in double quotes, so the menu's
choice of several networks (a space-separated list of shortnames) and AUTO
are both read back whole."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
INSTALLER = REPOSITORY / "etny-node-installer.sh"
START = REPOSITORY / "ubuntu" / "etny-node-start.sh"

# Writes the config file with the installer's line, sources it as the start
# script does and prints the words of the agent's -n argument, one a line.
PROGRAM = """set -e
nodefolder=.
configfile=config
NETWORK="$1"
printf 'TASK_EXECUTION_PRICE=3\\n' > "$nodefolder/$configfile"
{line}
unset NETWORK
set -e
. ./config
for word in ${{NETWORK}}; do echo "$word"; done
"""


def installer_network_line() -> str:
    lines = [line for line in INSTALLER.read_text(encoding="utf-8").splitlines()
             if line.strip().startswith('echo "NETWORK=')]
    assert len(lines) == 1, lines
    return lines[0].strip()


@pytest.fixture(scope="module")
def bash() -> str:
    path = shutil.which("bash")
    if path is None:
        pytest.skip("no bash on this platform")
    probe = subprocess.run([path, "-c", "echo ok"], capture_output=True, text=True, timeout=60)
    if probe.returncode != 0 or probe.stdout.strip() != "ok":
        pytest.skip("the bash on this platform does not run")
    return path


def test_the_start_script_sources_the_config_and_splits_network_into_arguments():
    start = START.read_text(encoding="utf-8").splitlines()
    assert start[1] == "set -e"
    assert ". /home/vagrant/etny/node/config" in start
    assert any("-n ${NETWORK} " in line for line in start)


def test_the_installer_writes_the_value_after_removing_the_lines_before_it():
    lines = [line.strip() for line in INSTALLER.read_text(encoding="utf-8").splitlines()]
    at = lines.index(installer_network_line())
    assert lines[at - 1] == 'sed -i "/^NETWORK=/d" "$nodefolder/$configfile"'
    assert lines[at] == 'echo "NETWORK=\\"$NETWORK\\"" >> "$nodefolder/$configfile"'


@pytest.mark.parametrize("value", [
    "AUTO",
    "BLOXBERG_TESTNET",
    "BLOXBERG_TESTNET POLYGON_AMOY",
    "BLOXBERG_MAINNET POLYGON_MAINNET BLOXBERG_TESTNET_POX10 POLYGON_AMOY_POX10",
])
def test_the_written_line_is_read_back_as_one_argument_per_network(bash, tmp_path, value):
    program = PROGRAM.format(line=installer_network_line()).encode("utf-8")
    done = subprocess.run([bash, "-s", "--", value], input=program, cwd=tmp_path, capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert done.stdout.decode("utf-8").split() == value.split()
    assert (tmp_path / "config").read_text(encoding="utf-8").splitlines() == ["TASK_EXECUTION_PRICE=3",
                                                                              f'NETWORK="{value}"']
