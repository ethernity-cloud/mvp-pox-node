"""utils.environment_failure: the reason an order's enclave environment did
not come up, from the failing docker command's output; the last line that
reports an error is kept (its end, bounded), the last line when none does,
and the text carries no ':' (the order result is colon-separated)."""
from utils import ENCLAVE_FAILURE_TEXT_MAX, environment_failure

REGISTRY_PULL_REFUSED = (
    "Unable to find image 'registry:2' locally\n"
    'docker: Error response from daemon: unknown: failed to resolve reference "docker.io/library/registry:2": '
    'unexpected status from HEAD request to https://registry-1.docker.io/v2/library/registry/manifests/2: '
    '429 Too Many Requests.\n'
    "See 'docker run --help'.\n")
COMPOSE_PULL_REFUSED = (
    ' etny-trustedzone Pulling \n'
    ' etny-trustedzone Error Get "https://localhost:5000/v2/": dial tcp 127.0.0.1:5000: connect: connection refused\n'
    'Error response from daemon: Get "https://localhost:5000/v2/": dial tcp 127.0.0.1:5000: connect: connection refused\n')


def test_the_last_error_line_is_the_detail_and_the_text_has_no_colon():
    reason = environment_failure('the docker registry did not start', REGISTRY_PULL_REFUSED)
    assert reason.startswith('the docker registry did not start - ')
    assert reason.endswith('429 Too Many Requests.')
    assert "See 'docker run --help'" not in reason
    assert ':' not in reason
    reason = environment_failure('the enclave containers did not start', COMPOSE_PULL_REFUSED)
    assert reason.endswith('connection refused')
    assert ':' not in reason


def test_without_an_error_line_the_last_line_counts_and_no_output_is_said_so():
    step = 'the docker registry did not start'
    assert environment_failure(step, 'first\nlast line\n') == f'{step} - last line'
    assert environment_failure(step, '') == f'{step} - no output'
    assert environment_failure(step, None) == f'{step} - no output'


def test_the_detail_keeps_the_end_of_a_long_error_line():
    reason = environment_failure('step', 'Error: ' + 'x' * 400 + ' tail')
    assert reason.endswith(' tail')
    assert len(reason) == len('step - ') + ENCLAVE_FAILURE_TEXT_MAX
