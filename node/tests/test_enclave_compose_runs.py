"""How the agent runs an order's and an integration test's enclave compose
(etny-node.py __start_enclave_environment, __enclave_environment_down,
__run_integration_test), driven on an EtnyPoXNode built without __init__
whose commands are recorded and not run: no docker, no chain. On a node with
8 CPUs every docker-compose run on the compose (pull, down, up) gets the
process's environment with ETNY_SCONE_QUEUES at 8 on a legacy PoX and at 16
on PoX 1.2 (utils.enclave_compose_env); the swift-stream compose and the
docker commands run in the process's own environment, which the agent leaves
as it is."""
import importlib.util
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

NODE = Path(__file__).resolve().parents[1]
IMAGE = "etny-pynithy"
TREE = "QmaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
TREE_COMPOSE = "QmbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
SWIFT_STREAM_UP = ["docker-compose", "-f", "../docker/docker-compose-swift-stream.yml", "up", "-d", "swift-stream"]
# The docker-compose runs on an enclave compose whose images docker does not
# hold, in order: the pull, the removal of the containers left, the services
# but the securelock, the securelock, and the removal once the task is over.
ENCLAVE_COMPOSE_RUNS = [["pull"], ["down"], ["up", "-d", "las", "etny-trustedzone"], ["up", "-d", "etny-securelock"],
                        ["down"]]

# A dApp's compose as the SDKs publish it since sdk-py 0.10.3 and sdk-js
# 1.8.3: both enclaves take SCONE_QUEUES from ETNY_SCONE_QUEUES.
COMPOSE = """services:
  las:
    container_name: las
    image: localhost:5000/etny-las
    entrypoint: "/las_entrypoint.sh"
    command: "/usr/local/bin/las -a 0.0.0.0"
    restart: on-failure
  etny-securelock:
    container_name: etny-securelock
    image: localhost:5000/etny-securelock
    environment:
      - SCONE_QUEUES=${ETNY_SCONE_QUEUES:-4}
    restart: on-failure
    depends_on:
      - las
  etny-trustedzone:
    container_name: etny-trustedzone
    image: localhost:5000/etny-trustedzone
    environment:
      - SCONE_QUEUES=${ETNY_SCONE_QUEUES:-4}
    restart: on-failure
    depends_on:
      - las
"""


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compose_of(order_id):
    """The compose of order `order_id` as the agent names it, relative to
    its working directory."""
    return f"./orders/{order_id}/docker-compose.yml"


class Environment(dict):
    """A copy of an environment whose repr is its variables' names. pytest
    prints the operands of a failed assertion and the arguments of the
    function it fails in; of this mapping it prints no variable's value."""

    def __repr__(self):
        return f"Environment({sorted(self)})"


def recorded_node(agent, monkeypatch, tmp_path, pox_is_12, order_id):
    """(node, calls): an EtnyPoXNode of a node with 8 CPUs on a network of
    the given PoX generation, working in `tmp_path`, whose commands are
    appended to `calls` as (args, env) and not run, env the Environment the
    command is given or None. COMPOSE is the compose of order `order_id`
    there, and docker holds none of its images, so they are pulled."""
    calls = []

    def run_subprocess(args, logger, env=None):
        calls.append((list(args), None if env is None else Environment(env)))

    def run_subprocess_status(args, logger, env=None):
        calls.append((list(args), None if env is None else Environment(env)))
        return 0, ""

    monkeypatch.setattr(agent, "run_subprocess", run_subprocess)
    monkeypatch.setattr(agent, "run_subprocess_status", run_subprocess_status)
    monkeypatch.setattr(agent, "await_enclave_runtime", lambda name, timeout: (True, 0.0))
    monkeypatch.setattr(agent.config, "enclave_start_stagger_seconds", 90.0)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ETNY_SCONE_QUEUES", raising=False)
    monkeypatch.setenv("ETNY_TEST_KEPT", "kept")
    compose = tmp_path / compose_of(order_id)
    compose.parent.mkdir(parents=True)
    compose.write_text(COMPOSE)
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node.cache_config = SimpleNamespace(base_path=tmp_path,
                                        tested_las_filepath=lambda name: tmp_path / f"las-tested-{name}.yml")
    node._EtnyPoXNode__number_of_cpus = 8
    node._EtnyPoXNode__pox_is_12 = pox_is_12
    node._EtnyPoXNode__enclave_up = False
    node._EtnyPoXNode__images_held = lambda tags: False
    return node, calls


def changed_names(env, environment):
    """The names whose value differs between the mappings `env` and
    `environment`, a name one of them lacks included. Names alone: a failed
    assertion on them prints no variable's value."""
    return sorted(name for name in set(env) | set(environment) if env.get(name) != environment.get(name))


def assert_the_enclave_runs_alone_carry(calls, compose_file, queues, environment):
    """The docker-compose runs on `compose_file` among `calls` are
    ENCLAVE_COMPOSE_RUNS, each in `environment` with ETNY_SCONE_QUEUES at
    `queues` added; every other command of `calls` is given no environment;
    os.environ is `environment` still."""
    runs = [(args, env) for args, env in calls if args[:3] == ["docker-compose", "-f", compose_file]]
    assert [args[3:] for args, _ in runs] == ENCLAVE_COMPOSE_RUNS
    for args, env in runs:
        assert env is not None, args
        assert env.get("ETNY_SCONE_QUEUES") == queues, args
        assert env.get("ETNY_TEST_KEPT") == "kept", args
        added = changed_names(env, environment)
        assert added == ["ETNY_SCONE_QUEUES"], args
    others = [env for args, env in calls if args[:3] != ["docker-compose", "-f", compose_file]]
    assert others and all(env is None for env in others)
    left = changed_names(os.environ, environment)
    assert left == []


@pytest.mark.parametrize("pox_is_12, queues", [(False, "8"), (True, "16")])
def test_an_orders_compose_runs_carry_the_generations_count(agent, monkeypatch, tmp_path, pox_is_12, queues):
    node, calls = recorded_node(agent, monkeypatch, tmp_path, pox_is_12, "74")
    node.order_docker_compose_file = compose_of("74")
    environment = Environment(os.environ)
    node._EtnyPoXNode__start_enclave_environment(TREE)
    node._EtnyPoXNode__enclave_environment_down()
    assert_the_enclave_runs_alone_carry(calls, compose_of("74"), queues, environment)


@pytest.mark.parametrize("pox_is_12, queues", [(False, "8"), (True, "16")])
def test_an_integration_tests_compose_runs_carry_the_generations_count(agent, monkeypatch, tmp_path, pox_is_12, queues):
    node, calls = recorded_node(agent, monkeypatch, tmp_path, pox_is_12, "integration_test")

    # The test's prerequisites are in place (its compose in the order's
    # folder) and its enclave has written the objects the agent reads.
    def prerequisites(bucket_name, order_id, docker_compose_file):
        node.order_docker_compose_file = compose_of(order_id)

    node.build_prerequisites_integration_test = prerequisites
    node.wait_for_enclave_v2 = lambda bucket_name, object_name, timeout: True
    node.storage = SimpleNamespace(download_many=lambda hashes, attempts, delay: True)
    node.swift_stream_service = SimpleNamespace(get_file_content=lambda bucket_name, object_name: (True, "written"),
                                                delete_bucket=lambda bucket_name: None)
    node._EtnyPoXNode__network = "bloxberg_testnet" if pox_is_12 else "bloxberg_mainnet"
    node._EtnyPoXNode__trustedzones = {IMAGE: "dcap"}
    node._EtnyPoXNode__latest_trustedzone_image = lambda image: (TREE, TREE_COMPOSE)
    environment = Environment(os.environ)
    assert node._EtnyPoXNode__run_integration_test(IMAGE) == (TREE, "written", "written")
    assert [call for call in calls if "swift-stream" in call[0]] == [(SWIFT_STREAM_UP, None)]
    assert_the_enclave_runs_alone_carry(calls, compose_of("integration_test"), queues, environment)
