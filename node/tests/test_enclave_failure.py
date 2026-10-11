"""utils.enclave_failure, enclave_may_have_failed and container_states_in: the
reason an enclave container will not finish its task, from its docker state
and the tail of its log. A CAS refusal in the log decides whatever the state
is; the awaited container (the trustedzone) also fails on any exit or when
never started; a container that is not awaited (the securelock) fails on a
refusal alone; a running, restarting or unreadable container is still
running. The reason carries no ':' (the order result is colon-separated)."""
from utils import CAS_REFUSAL_MARKER, ENCLAVE_FAILURE_TEXT_MAX, container_states_in, enclave_failure, enclave_may_have_failed

REFUSAL = ("Note: Connecting to CAS at cas.ethernity.cloud (port 19766) using service ID x/application\n"
           "[SCONE|FATAL] tools/starter/main.c:644:enclave_create(): Could not create enclave builder: "
           "CAS sent an error: Unexpected enclave measurement (MRENCLAVE) for this service\n")


def test_a_cas_refusal_in_the_log_is_the_reason_whatever_the_state_for_either_container():
    for state in (None, {"Status": "running", "ExitCode": 0}, {"Status": "restarting", "ExitCode": 1, "RestartCount": 3}):
        for name, awaited in (("etny-trustedzone", True), ("etny-securelock", False)):
            reason = enclave_failure(name, state, REFUSAL, awaited=awaited)
            assert reason == f"{name} was refused by the CAS - Unexpected enclave measurement (MRENCLAVE) for this service"
            assert ":" not in reason


def test_the_last_refusal_line_counts_and_is_bounded():
    log = "first " + CAS_REFUSAL_MARKER + "old reason\nnoise\n" + CAS_REFUSAL_MARKER + "a: b " + "x" * 400 + "\n"
    reason = enclave_failure("etny-securelock", None, log, awaited=False)
    assert reason.startswith("etny-securelock was refused by the CAS - a  b ")
    assert len(reason) <= len("etny-securelock was refused by the CAS - ") + ENCLAVE_FAILURE_TEXT_MAX
    assert ":" not in reason


def test_any_exit_of_the_awaited_container_is_the_reason():
    assert enclave_failure("etny-trustedzone", {"Status": "exited", "ExitCode": 1, "RestartCount": 20}, "") \
        == "etny-trustedzone exited with code 1 after 20 restarts"
    assert enclave_failure("etny-trustedzone", {"Status": "dead", "ExitCode": 137}, None) \
        == "etny-trustedzone exited with code 137 after 0 restarts"
    assert enclave_failure("etny-trustedzone", {"Status": "exited", "ExitCode": 0, "RestartCount": 0}, "") \
        == "etny-trustedzone exited with code 0 after 0 restarts"


def test_the_awaited_container_never_started_is_the_reason():
    assert enclave_failure("etny-trustedzone", {"Status": "created", "ExitCode": 0}, "") == "etny-trustedzone was not started"


def test_an_exit_of_a_container_not_awaited_is_not_a_failure():
    for state in ({"Status": "exited", "ExitCode": 1, "RestartCount": 20}, {"Status": "exited", "ExitCode": 0},
                  {"Status": "dead", "ExitCode": 137}, {"Status": "created", "ExitCode": 0}):
        assert enclave_failure("etny-securelock", state, "plain output\n", awaited=False) is None


def test_running_restarting_and_unknown_are_not_failures():
    for state in (None, {}, {"Status": "running", "ExitCode": 0}, {"Status": "restarting", "ExitCode": 1}, {"Status": "paused"}):
        assert enclave_failure("etny-trustedzone", state, "plain output\n") is None


def test_the_log_is_read_for_a_container_that_is_not_running_untroubled():
    for state in ({"Status": "restarting", "ExitCode": 1, "RestartCount": 1}, {"Status": "exited", "ExitCode": 0},
                  {"Status": "running", "ExitCode": 0, "RestartCount": 2}, {"Status": "running", "ExitCode": 1},
                  {"Status": "created"}, {"Status": "paused"}):
        assert enclave_may_have_failed(state)
    for state in (None, {}, {"Status": "running", "ExitCode": 0, "RestartCount": 0}):
        assert not enclave_may_have_failed(state)


def test_container_states_are_read_from_one_inspect_output():
    text = ('/etny-trustedzone 0 {"Status":"running","Running":true,"ExitCode":0,"Error":""}\n'
            '/etny-securelock 3 {"Status":"restarting","Running":false,"ExitCode":1,"Error":""}\n'
            'error: no such object: etny-las\n'
            '\n')
    assert container_states_in(text) == {
        "etny-trustedzone": {"Status": "running", "Running": True, "ExitCode": 0, "Error": "", "RestartCount": 0},
        "etny-securelock": {"Status": "restarting", "Running": False, "ExitCode": 1, "Error": "", "RestartCount": 3},
    }
    assert container_states_in("") == {}
