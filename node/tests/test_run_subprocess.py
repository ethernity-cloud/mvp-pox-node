"""utils.run_subprocess_status runs a command to completion and returns its
exit code with its stdout and stderr merged; run_subprocess runs it the same
way and returns nothing. Both run the command in the environment given as
`env`, and in the process's own without one."""
import logging
import os
import sys

from utils import run_subprocess, run_subprocess_status

PROGRAM = "import sys; print('out'); print('err: oops', file=sys.stderr); sys.exit(3)"
# Prints the two variables the environment tests set, None for one that is absent.
PRINT_VARIABLES = "import os; print(os.environ.get('ETNY_SCONE_QUEUES'), os.environ.get('ETNY_TEST_KEPT'))"
# Writes the same line into the file named by its argument.
WRITE_VARIABLES = ("import os, sys; open(sys.argv[1], 'w').write("
                   "f\"{os.environ.get('ETNY_SCONE_QUEUES')} {os.environ.get('ETNY_TEST_KEPT')}\")")


def test_the_status_run_returns_the_exit_code_and_the_merged_output():
    code, output = run_subprocess_status([sys.executable, '-c', PROGRAM], logging.getLogger('test'))
    assert code == 3
    assert 'out' in output and 'err: oops' in output


def test_a_clean_run_returns_zero_and_run_subprocess_returns_nothing():
    code, output = run_subprocess_status([sys.executable, '-c', "print('ok')"], logging.getLogger('test'))
    assert (code, output.strip()) == (0, 'ok')
    assert run_subprocess([sys.executable, '-c', 'pass'], logging.getLogger('test')) is None


def test_the_status_run_hands_the_command_the_given_environment_and_the_process_own_without_one(monkeypatch):
    monkeypatch.delenv('ETNY_SCONE_QUEUES', raising=False)
    monkeypatch.setenv('ETNY_TEST_KEPT', 'kept')
    command = [sys.executable, '-c', PRINT_VARIABLES]
    code, output = run_subprocess_status(command, logging.getLogger('test'), env={**os.environ, 'ETNY_SCONE_QUEUES': '8'})
    assert (code, output.strip()) == (0, '8 kept')
    code, output = run_subprocess_status(command, logging.getLogger('test'))
    assert (code, output.strip()) == (0, 'None kept')
    code, output = run_subprocess_status(command, logging.getLogger('test'), env=None)
    assert (code, output.strip()) == (0, 'None kept')


def test_run_subprocess_hands_the_command_the_given_environment_and_the_process_own_without_one(monkeypatch, tmp_path):
    monkeypatch.delenv('ETNY_SCONE_QUEUES', raising=False)
    monkeypatch.setenv('ETNY_TEST_KEPT', 'kept')
    written = tmp_path / 'variables.txt'
    command = [sys.executable, '-c', WRITE_VARIABLES, str(written)]
    assert run_subprocess(command, logging.getLogger('test'), env={**os.environ, 'ETNY_SCONE_QUEUES': '16'}) is None
    assert written.read_text() == '16 kept'
    assert run_subprocess(command, logging.getLogger('test')) is None
    assert written.read_text() == 'None kept'
