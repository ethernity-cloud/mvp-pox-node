"""utils.enclave_compose_env: the environment of a docker-compose run on an
enclave compose is the process's own with ETNY_SCONE_QUEUES at the SCONE
queue count of the network's PoX generation (utils.scone_queues); os.environ
itself is left as it is, a count it carries included."""
import os

import pytest

from utils import enclave_compose_env


def changed_names(env, environment):
    """The names whose value differs between the mappings `env` and
    `environment`, a name one of them lacks included. Names alone: a failed
    assertion on them prints no variable's value."""
    return sorted(name for name in set(env) | set(environment) if env.get(name) != environment.get(name))


@pytest.mark.parametrize("legacy, queues", [(True, "8"), (False, "16")])
def test_the_environment_is_the_process_own_with_the_generations_count(monkeypatch, legacy, queues):
    monkeypatch.delenv("ETNY_SCONE_QUEUES", raising=False)
    monkeypatch.setenv("ETNY_TEST_KEPT", "kept")
    before = dict(os.environ)
    env = enclave_compose_env(8, legacy)
    assert env["ETNY_SCONE_QUEUES"] == queues
    assert env["ETNY_TEST_KEPT"] == "kept"
    added = changed_names(env, before)
    assert added == ["ETNY_SCONE_QUEUES"]
    left = changed_names(os.environ, before)
    assert left == []


@pytest.mark.parametrize("cpus, legacy, queues", [(1, True, "4"), (64, True, "8"), (1, False, "4"), (64, False, "64")])
def test_the_count_is_scone_queues_as_text(cpus, legacy, queues):
    assert enclave_compose_env(cpus, legacy)["ETNY_SCONE_QUEUES"] == queues


def test_a_count_in_the_process_environment_is_replaced_for_the_run_alone(monkeypatch):
    monkeypatch.setenv("ETNY_SCONE_QUEUES", "64")
    assert enclave_compose_env(8, True)["ETNY_SCONE_QUEUES"] == "8"
    assert os.environ["ETNY_SCONE_QUEUES"] == "64"
