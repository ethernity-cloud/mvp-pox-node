"""utils.await_enclave_runtime waits for an enclave container's log to show
the enclave created, gives up when the container stops or the timeout
passes, and utils.enclave_runtime_up tells the creation from what the SCONE
runtime prints before it. No docker: the state and log readers, the clock
and sleep are injected."""
from utils import ENCLAVE_RUNTIME_POLL_SECONDS, await_enclave_runtime, enclave_runtime_up

BEFORE_CREATION = (
    "[SCONE|WARN] tools/starter/main.c:588:enclave_create(): Enclave is signed to run in production mode.\n"
    "[SCONE|INFO] tools/libsgx/src/layout.c:992:scone_enclave_create_layout(): Enclave size: 1294 MiB\n"
    "; edns version: 0 dnssec_ok: false z_flags: 0 max_payload: 1232 opts: 0\n"
    "; query\n"
    ";; cas.ethernity.cloud. IN A\n"
    "\n"
    "[SCONE|DEBUG] hickory_proto::xfer::dns_handle sending message\n"
)
CREATED = BEFORE_CREATION + "[SCONE|DEBUG] tools/starter/main.c:656:enclave_create():enclave created in 7.46127 seconds\n"
ATTESTED = BEFORE_CREATION + "\tApplying process working directory from service's session configuration (/)\n"


class _Clock:
    def __init__(self):
        self.t = 100.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _container(logs, status='running'):
    """A state reader and a log reader answering `logs` in turn (the last
    entry repeatedly)."""
    reads = {'n': 0}

    def states(names):
        return {names[0]: {'Status': status}}

    def log_tail(name, lines=500):
        i = min(reads['n'], len(logs) - 1)
        reads['n'] += 1
        return logs[i]

    return states, log_tail


def test_what_precedes_the_creation_is_not_the_enclave():
    assert not enclave_runtime_up(BEFORE_CREATION)
    assert not enclave_runtime_up("")
    assert enclave_runtime_up(CREATED)
    assert enclave_runtime_up(ATTESTED)


def test_the_wait_ends_when_the_log_shows_the_enclave_created():
    clock = _Clock()
    states, log_tail = _container(['', BEFORE_CREATION, CREATED])
    up, waited = await_enclave_runtime('etny-trustedzone', 90, states, log_tail, clock.sleep, clock.now)
    assert up is True
    assert waited == 2 * ENCLAVE_RUNTIME_POLL_SECONDS


def test_a_container_that_stopped_ends_the_wait_at_once():
    clock = _Clock()
    states, log_tail = _container([BEFORE_CREATION], status='exited')
    up, waited = await_enclave_runtime('etny-trustedzone', 90, states, log_tail, clock.sleep, clock.now)
    assert (up, waited) == (False, 0.0)


def test_the_timeout_ends_the_wait():
    clock = _Clock()
    states, log_tail = _container([BEFORE_CREATION])
    up, waited = await_enclave_runtime('etny-trustedzone', 7, states, log_tail, clock.sleep, clock.now)
    assert up is False
    assert waited == 7.0
