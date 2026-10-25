"""utils.run_subprocess_status runs a command to completion and returns its
exit code with its stdout and stderr merged; run_subprocess runs it the same
way and returns nothing."""
import logging
import sys

from utils import run_subprocess, run_subprocess_status

PROGRAM = "import sys; print('out'); print('err: oops', file=sys.stderr); sys.exit(3)"


def test_the_status_run_returns_the_exit_code_and_the_merged_output():
    code, output = run_subprocess_status([sys.executable, '-c', PROGRAM], logging.getLogger('test'))
    assert code == 3
    assert 'out' in output and 'err: oops' in output


def test_a_clean_run_returns_zero_and_run_subprocess_returns_nothing():
    code, output = run_subprocess_status([sys.executable, '-c', "print('ok')"], logging.getLogger('test'))
    assert (code, output.strip()) == (0, 'ok')
    assert run_subprocess([sys.executable, '-c', 'pass'], logging.getLogger('test')) is None
