"""The start of the node's object store (swift_stream_service.SwiftStreamService,
store_access.await_live, bounded_http, list_outcome), against a MinIO kept in
memory and a docker that records its commands (store_fakes): no docker and no
store run. The first construction in a process draws or loads the root
credential, runs the store's compose with it, waits for the store, has the
root credential list the buckets (recreating, once, a store under another
root or not live, and failing with the store's data kept when the recreated
one still does not list them), sweeps and checks that the literal pair and
MinIO's built-in root are no access keys; a failed start raises RuntimeError
and is run again; a passed start is run once."""
import ast
import logging
import os
import traceback
from pathlib import Path

import pytest
import urllib3
import yaml
from minio import Minio, MinioAdmin

import store_access
import swift_stream_service
from store_fakes import ENDPOINT, FakeDocker, FakeStore, assert_no_secret, install, s3_error
from utils import STORE_ROOT_VARIABLES

NODE = Path(__file__).resolve().parents[1]
UP = ["up", "-d", "swift-stream"]
FORCED = ["up", "-d", "--force-recreate", "swift-stream"]
LIVE =("http", "GET", f"http://{ENDPOINT}/minio/health/live")
LITERAL_PAIR = ("swiftstreamadmin", "swiftstreamadmin")
BUILT_IN_PAIR = ("minioadmin", "minioadmin")
# A root credential a node holds from an earlier process.
HELD_ROOT = ("ecldroot0123456789ab", "0123456789abcdef0123456789abcdef01234567")


def store_data_dir(network_dir):
    """The directory the node's store compose file mounts on the store's
    /data, for a process whose working directory is `network_dir`: the
    compose file is at STORE_COMPOSE_FILE from there, and the host path of
    the volume is relative to the compose file's directory."""
    compose = yaml.safe_load((NODE / "docker" / "docker-compose-swift-stream.yml").read_text(encoding="utf-8"))
    volumes = compose["services"][swift_stream_service.STORE_SERVICE]["volumes"]
    sources = [volume[:-len(":/data")] for volume in volumes if volume.endswith(":/data")]
    assert len(sources) == 1
    return (network_dir / swift_stream_service.STORE_COMPOSE_FILE).resolve().parent / sources[0]


def tree(directory):
    """{path relative to `directory`: the bytes of the file, or None for a
    directory} of every entry under `directory`."""
    return {path.relative_to(directory).as_posix(): None if path.is_dir() else path.read_bytes()
            for path in sorted(directory.rglob("*"))}


class Rig:
    """A node for the store's start: `store` and `docker` (store_fakes) in
    the place of MinIO and of docker, the node's files under a scratch
    directory, the process in a network's working directory there, so that
    `data_dir` is the store's data directory as its compose mounts it, a
    process that has started no store, and a liveness wait whose seconds are
    counted in `waited` and not slept."""

    def __init__(self, monkeypatch, tmp_path):
        self.store = FakeStore()
        self.docker = FakeDocker(self.store)
        self.root_file = tmp_path / "home" / "opt" / "etny" / "store-root.json"
        self.credential_dir = tmp_path / "ecld-store"
        network_dir = tmp_path / "node" / "bloxberg_testnet"
        network_dir.mkdir(parents=True)
        monkeypatch.chdir(network_dir)
        self.data_dir = store_data_dir(network_dir)
        self.data_dir.mkdir(parents=True)
        self.waited = []
        self.restarts = []
        install(monkeypatch, self.store, [store_access, swift_stream_service])
        monkeypatch.setattr(swift_stream_service, "run_subprocess_status", self.docker)
        monkeypatch.setattr(swift_stream_service, "_start", swift_stream_service.StoreStart())
        monkeypatch.setattr(swift_stream_service.SwiftStreamService, "restart_etny_swift_stream_and_reconnect",
                            lambda service: self.restarts.append(service) or False)
        await_live = store_access.await_live

        def counted_wait(endpoint, http):
            clock = [0.0]

            def sleep(seconds):
                self.waited.append(seconds)
                clock[0] += seconds

            return await_live(endpoint, http, sleep=sleep, now=lambda: clock[0])

        monkeypatch.setattr(store_access, "await_live", counted_wait)

    def hold_root(self, pair=HELD_ROOT):
        """The node holds `pair` as its root credential from an earlier
        process."""
        self.root_file.parent.mkdir(parents=True, exist_ok=True)
        self.root_file.write_bytes(store_access.credential_bytes(*pair))
        return pair

    def construct(self):
        return swift_stream_service.SwiftStreamService(logging.getLogger("store"), ENDPOINT, self.root_file,
                                                       credential_dir=self.credential_dir)

    def trace(self, since=0):
        """The store's events from index `since`, a run of the store's
        compose reduced to its arguments after the compose file."""
        events = []
        for event in self.store.events[since:]:
            if event[0] == "run":
                args = event[1]
                events.append(("run", args[3:] if args[0] == "docker-compose" else args))
            else:
                events.append(event)
        return events


@pytest.fixture
def rig(monkeypatch, tmp_path):
    return Rig(monkeypatch, tmp_path)


def changed_names(env, environment):
    """The names whose value differs between the mappings `env` and
    `environment`, a name one of them lacks included. Names alone: a failed
    assertion on them prints no variable's value."""
    return sorted(name for name in set(env) | set(environment) if env.get(name) != environment.get(name))


def printed_with_its_chain(error):
    """The traceback of `error` as the interpreter prints it, the errors it
    was raised from or during included."""
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def started(root):
    """The events of S2 to S5 on a store that answers the root credential
    `root` at once and holds no user, followed by the construction's own
    connection."""
    return [("run", UP), LIVE, ("s3", "list_buckets", root[0]),
            ("admin", "user_list"), ("admin", "policy_list"),
            ("s3", "list_buckets", "swiftstreamadmin"), ("s3", "list_buckets", "minioadmin"),
            ("s3", "list_buckets", root[0])]


def test_the_first_construction_runs_s1_to_s5_in_order(rig, monkeypatch):
    for name in STORE_ROOT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    before = dict(os.environ)
    drawn_before_the_run = []
    rig.docker.before = lambda args: drawn_before_the_run.append(
        store_access.credential_in(rig.root_file.read_bytes()))
    service = rig.construct()
    root = store_access.credential_in(rig.root_file.read_bytes())
    assert root[0].startswith("ecldroot")
    assert drawn_before_the_run == [root]
    assert rig.trace() == started(root)
    args, env = rig.docker.runs[0]
    assert args == ["docker-compose", "-f", "../docker/docker-compose-swift-stream.yml", "up", "-d", "swift-stream"]
    assert tuple(env[name] for name in STORE_ROOT_VARIABLES) == root
    assert changed_names(env, before) == sorted(STORE_ROOT_VARIABLES)
    assert changed_names(os.environ, before) == []
    assert swift_stream_service._start.root == root
    assert (service.client.access_key, service.client.secret_key) == root
    assert rig.waited == [] and rig.restarts == []


@pytest.mark.parametrize("live_from, answer, asks, sleeps", [(1, True, 1, 0), (4, True, 4, 3), (None, False, 61, 60)])
def test_the_wait_asks_the_liveness_path_once_a_second_for_sixty_seconds(live_from, answer, asks, sleeps):
    asked, slept, clock = [], [], [0.0]

    class Http:
        """A store that refuses the connection, then answers 503, in turn,
        and 200 from its ask number `live_from` on."""

        def request(self, method, url):
            asked.append((method, url))
            if live_from is not None and len(asked) >= live_from:
                return type("Response", (), {"status": 200})()
            if len(asked) % 2:
                raise ConnectionError("refused")
            return type("Response", (), {"status": 503})()

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    assert store_access.await_live(ENDPOINT, Http(), sleep=sleep, now=lambda: clock[0]) is answer
    assert asked == [("GET", LIVE[2])] * asks and slept == [1] * sleeps


def bounds(pool):
    """(connect timeout, read timeout, retries) of the urllib3 pool `pool`;
    urllib3 2 keeps `retries=False` as a Retry whose total is False."""
    timeout, retries = pool.connection_pool_kw["timeout"], pool.connection_pool_kw["retries"]
    return timeout.connect_timeout, timeout.read_timeout, getattr(retries, "total", retries)


def test_every_call_of_the_clients_is_bounded_and_not_retried(monkeypatch):
    pool = store_access.bounded_http()
    assert isinstance(pool, urllib3.PoolManager)
    assert bounds(pool) == (10, 10, False)
    assert store_access.bounded_http() is not pool
    # minio-py's own classes take the arguments the agent builds them with.
    assert isinstance(store_access.StoreAdmin(ENDPOINT, *HELD_ROOT)._admin, MinioAdmin)
    assert isinstance(store_access.s3_client(ENDPOINT, *HELD_ROOT), Minio)
    store = FakeStore()
    monkeypatch.setattr(store_access, "MinioAdmin", store.minio_admin())
    monkeypatch.setattr(store_access, "Minio", store.minio())
    admin = store_access.StoreAdmin(ENDPOINT, *HELD_ROOT)._admin
    client = store_access.s3_client(ENDPOINT, *HELD_ROOT)
    assert [bounds(built.http_client) for built in (admin, client)] == [(10, 10, False)] * 2
    assert (admin.endpoint, admin.secure) == (ENDPOINT, False)
    assert (admin.credentials.access_key, admin.credentials.secret_key) == HELD_ROOT
    assert (client.endpoint, client.access_key, client.secret_key) == (ENDPOINT,) + HELD_ROOT


def test_a_listing_ends_as_listed_or_as_the_class_and_code_of_its_error():
    class Client:
        def __init__(self, end):
            self.end = end

        def list_buckets(self):
            if isinstance(self.end, Exception):
                raise self.end
            return self.end

    assert store_access.list_outcome(Client([])) == store_access.LISTED == "listed"
    assert store_access.list_outcome(Client(s3_error("InvalidAccessKeyId"))) == store_access.REFUSED
    assert store_access.REFUSED == "S3Error InvalidAccessKeyId"
    assert store_access.list_outcome(Client(s3_error("AccessDenied"))) == "S3Error AccessDenied"
    assert store_access.list_outcome(Client(ConnectionError("refused by 10.0.0.1"))) == "ConnectionError"


def test_the_users_and_files_of_an_earlier_process_are_swept(rig):
    root = rig.hold_root()
    rig.store.root = root
    rig.store.users = {"ecldtz0123456789abcd": "a" * 40, "swiftstreamadmin": "swiftstreamadmin", "operator": "b" * 40}
    rig.store.policies.update({"ecld-ecldtz0123456789abcd": {}, "ecld-swiftstreamadmin": {}, "kept": {}})
    rig.store.user_policy = {"ecldtz0123456789abcd": "ecld-ecldtz0123456789abcd",
                             "swiftstreamadmin": "ecld-swiftstreamadmin"}
    for service in ("etny-trustedzone", "etny-securelock"):
        (rig.credential_dir / service).mkdir(parents=True)
        (rig.credential_dir / service / "store.json").write_bytes(b"{}")
    (rig.credential_dir / "etny-securelock" / "left").mkdir()
    (rig.credential_dir / "etny-securelock" / "left" / "file").write_bytes(b"x")
    rig.construct()
    assert rig.store.users == {} and rig.store.user_policy == {}
    assert sorted(set(rig.store.policies) - {"consoleAdmin", "diagnostics", "readonly", "readwrite", "writeonly"}) == [
        "kept"]
    assert [call for call in rig.store.admin_calls()] == [
        ("user_list",), ("user_remove", "ecldtz0123456789abcd"), ("user_remove", "operator"),
        ("user_remove", "swiftstreamadmin"), ("policy_list",), ("policy_remove", "ecld-ecldtz0123456789abcd"),
        ("policy_remove", "ecld-swiftstreamadmin")]
    assert sorted(entry.name for entry in rig.credential_dir.iterdir()) == ["etny-securelock", "etny-trustedzone"]
    assert [list((rig.credential_dir / service).iterdir()) for service in ("etny-trustedzone", "etny-securelock")] == [
        [], []]
    assert store_access.credential_in(rig.root_file.read_bytes()) == root


def test_a_compose_that_recreates_the_container_needs_no_forced_recreate(rig):
    rig.store.root = LITERAL_PAIR
    (rig.data_dir / ".minio.sys").mkdir()
    rig.construct()
    root = store_access.credential_in(rig.root_file.read_bytes())
    assert rig.docker.compose_runs() == [UP]
    assert rig.trace() == started(root)
    assert not swift_stream_service._start.recreated
    assert (rig.data_dir / ".minio.sys").is_dir()


def test_a_store_under_another_root_is_recreated_by_force(rig, caplog):
    caplog.set_level(logging.DEBUG)
    rig.store.root = LITERAL_PAIR
    rig.docker.recreates_on_up = False
    (rig.data_dir / ".minio.sys").mkdir()
    rig.construct()
    root = store_access.credential_in(rig.root_file.read_bytes())
    assert rig.trace() == [("run", UP), LIVE, ("s3", "list_buckets", root[0]),
                           ("run", FORCED), LIVE, ("s3", "list_buckets", root[0])] + started(root)[3:]
    assert [tuple(env[name] for name in STORE_ROOT_VARIABLES) for _, env in rig.docker.runs] == [root, root]
    assert swift_stream_service._start.recreated
    assert (rig.data_dir / ".minio.sys").is_dir()
    assert swift_stream_service._start.root == root
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["The store does not answer the node's root credential (S3Error InvalidAccessKeyId); its "
                        "container is recreated"]


@pytest.mark.parametrize("force_code", [0, 1])
@pytest.mark.parametrize("cause, answer", [("another root", "S3Error InvalidAccessKeyId"), ("not live", "not live")])
def test_a_store_that_does_not_list_after_the_forced_recreate_fails_the_start_and_keeps_its_data(
        rig, cause, answer, force_code):
    if cause == "another root":
        rig.store.root = LITERAL_PAIR
        rig.docker.recreates_on_up = rig.docker.force_recreates = False
    else:
        rig.store.live_when = lambda: False
    rig.docker.force_code = force_code
    (rig.data_dir / ".minio.sys" / "config").mkdir(parents=True)
    (rig.data_dir / ".minio.sys" / "config" / "iam.json").write_bytes(b"users and policies")
    (rig.data_dir / "etny-pynithy-v3").mkdir()
    (rig.data_dir / "etny-pynithy-v3" / ".env").write_bytes(b"ETNY_MODE=TEST\n")
    held = tree(rig.data_dir)
    compose = ["docker-compose", "-f", swift_stream_service.STORE_COMPOSE_FILE]
    for runs in ([UP, FORCED], [UP]):
        mark = len(rig.docker.runs)
        with pytest.raises(RuntimeError) as failure:
            rig.construct()
        assert str(failure.value) == f"The store did not start: S3, the root credential is not answered ({answer})"
        assert tree(rig.data_dir) == held
        assert [args for args, _ in rig.docker.runs[mark:]] == [compose + run for run in runs]
        assert rig.store.admin_calls() == []
        assert swift_stream_service._start.recreated and swift_stream_service._start.root is None


def test_a_failed_start_is_run_again_without_a_second_forced_recreate(rig):
    rig.store.root = LITERAL_PAIR
    rig.docker.recreates_on_up = rig.docker.force_recreates = False
    rig.docker.force_code = 1
    with pytest.raises(RuntimeError):
        rig.construct()
    root = store_access.credential_in(rig.root_file.read_bytes())
    mark = len(rig.store.events)
    with pytest.raises(RuntimeError):
        rig.construct()
    assert rig.trace(mark) == [("run", UP), LIVE, ("s3", "list_buckets", root[0])]
    assert store_access.credential_in(rig.root_file.read_bytes()) == root
    assert swift_stream_service._start.root is None
    # The store comes to run under the node's root: the next construction
    # passes S3 and runs S4 and S5.
    rig.store.root = root
    mark = len(rig.store.events)
    rig.construct()
    assert rig.trace(mark) == started(root)
    assert swift_stream_service._start.root == root


def test_a_passed_start_is_run_once_per_process(rig):
    first = rig.construct()
    root = store_access.credential_in(rig.root_file.read_bytes())
    rig.store.users["left-by-a-running-order"] = "c" * 40
    rig.root_file.write_bytes(b"changed under the running process")
    mark = len(rig.store.events)
    second = rig.construct()
    assert rig.trace(mark) == [("run", UP), ("s3", "list_buckets", root[0])]
    assert tuple(rig.docker.runs[-1][1][name] for name in STORE_ROOT_VARIABLES) == root
    assert rig.store.users == {"left-by-a-running-order": "c" * 40}
    assert (second.client.access_key, second.client.secret_key) == root
    assert second.admin is not first.admin
    assert rig.root_file.read_bytes() == b"changed under the running process"


@pytest.mark.parametrize("pair, name", [(LITERAL_PAIR, "the literal pair"), (BUILT_IN_PAIR, "MinIO's built-in root")])
@pytest.mark.parametrize("end, outcome", [("answers", "listed"), ("denied", "S3Error AccessDenied")])
def test_a_pair_that_the_store_knows_at_s5_fails_the_start(rig, pair, name, end, outcome):
    getattr(rig.store, end).add(pair)
    with pytest.raises(RuntimeError) as failure:
        rig.construct()
    assert str(failure.value) == f"The store did not start: S5, the store answers {name} ({outcome})"
    # The failure is printed alone, without the error it was raised over.
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    assert "StoreNotStarted" not in printed_with_its_chain(failure.value)
    assert swift_stream_service._start.root is None
    assert ("admin", "policy_list") in rig.store.events
    getattr(rig.store, end).clear()
    rig.construct()
    assert swift_stream_service._start.root == store_access.credential_in(rig.root_file.read_bytes())


def test_a_root_credential_that_cannot_be_kept_fails_the_start_at_s1(rig):
    rig.root_file.parent.parent.mkdir(parents=True)
    rig.root_file.parent.write_bytes(b"a file where the directory would be")
    with pytest.raises(RuntimeError) as failure:
        rig.construct()
    assert str(failure.value).startswith("The store did not start: S1, ")
    assert rig.store.events == [] and swift_stream_service._start.root is None


def test_a_sweep_that_fails_fails_the_start_at_s4(rig):
    rig.store.failing.append("policy_list")
    with pytest.raises(RuntimeError) as failure:
        rig.construct()
    assert str(failure.value) == "The store did not start: S4, MinioAdminException"
    # The admin client's error, whose message quotes its request, is not
    # printed with the failure.
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    assert "is quoted here" in str(failure.value.__context__)
    assert "is quoted here" not in printed_with_its_chain(failure.value)
    assert swift_stream_service._start.root is None
    assert ("s3", "list_buckets", "swiftstreamadmin") not in rig.store.events


def test_a_stores_compose_that_does_not_exit_0_fails_the_start_at_s2(rig):
    rig.store.root = LITERAL_PAIR
    rig.docker.recreates_on_up = False
    rig.docker.up_code = 1
    with pytest.raises(RuntimeError) as failure:
        rig.construct()
    assert str(failure.value) == "The store did not start: S2, the store's compose exited 1"
    assert rig.trace() == [("run", UP)]
    assert swift_stream_service._start.root is None and not swift_stream_service._start.recreated
    # The forced recreate is not spent: a later start of the process, whose
    # compose exits 0 and leaves the store under the other root, runs it.
    root = store_access.credential_in(rig.root_file.read_bytes())
    rig.docker.up_code = 0
    mark = len(rig.store.events)
    rig.construct()
    assert rig.trace(mark) == [("run", UP), LIVE, ("s3", "list_buckets", root[0]),
                               ("run", FORCED), LIVE, ("s3", "list_buckets", root[0])] + started(root)[3:]
    assert swift_stream_service._start.recreated and swift_stream_service._start.root == root


def test_a_store_that_never_comes_live_fails_the_start_and_the_next_construction_starts_again(rig):
    rig.store.live_when = lambda: False
    with pytest.raises(RuntimeError) as failure:
        rig.construct()
    assert str(failure.value) == "The store did not start: S3, the root credential is not answered (not live)"
    assert rig.docker.compose_runs() == [UP, FORCED]
    assert rig.trace().count(LIVE) == 61 + 61 and rig.waited == [1] * 120
    assert rig.store.admin_calls() == []
    # The next construction recreates nothing.
    mark = len(rig.store.events)
    with pytest.raises(RuntimeError):
        rig.construct()
    assert [event[1] for event in rig.trace(mark) if event[0] == "run"] == [UP]
    rig.store.live_when = lambda: True
    mark = len(rig.store.events)
    rig.construct()
    assert rig.trace(mark) == started(store_access.credential_in(rig.root_file.read_bytes()))


def test_no_secret_key_reaches_a_log_record_or_an_argument_list(rig, caplog):
    caplog.set_level(logging.DEBUG)
    rig.store.root = LITERAL_PAIR
    rig.docker.recreates_on_up = False
    rig.store.users["ecldtz0123456789abcd"] = "d" * 40
    rig.store.secrets.add("d" * 40)
    rig.construct()
    rig.store.failing.append("user_list")
    swift_stream_service._start.root = None
    with pytest.raises(RuntimeError) as failure:
        rig.construct()
    root = store_access.credential_in(rig.root_file.read_bytes())
    assert root[1] in rig.store.secrets
    assert root[1] not in str(failure.value)
    assert_no_secret(rig.store, caplog.records, [args for args, _ in rig.docker.runs])
    assert not any(root[0] in record.getMessage() for record in caplog.records)
    assert len(caplog.records) >= 2


def test_the_stores_compose_is_run_by_the_store_service_alone():
    """Every run of the store's compose gets the root credential: the
    compose is named in swift_stream_service alone and run by compose_up
    alone, which the agent calls where an order and an integration test
    prepare their docker environment."""
    agent = ast.parse((NODE / "etny-node.py").read_text(encoding="utf-8"))
    assert [node.value for node in ast.walk(agent)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "swift-stream" in node.value
            and "docker-compose" in node.value] == []
    callers = [function.name for function in ast.walk(agent) if isinstance(function, ast.FunctionDef)
               for node in ast.walk(function)
               if isinstance(node, ast.Call) and ast.unparse(node.func) == "self.swift_stream_service.compose_up"]
    assert callers == ["__run_order", "__run_integration_test"]
    service = ast.parse((NODE / "swift_stream_service.py").read_text(encoding="utf-8"))
    named = [node for node in ast.walk(service) if isinstance(node, ast.Name) and node.id == "STORE_COMPOSE_FILE"
             and isinstance(node.ctx, ast.Load)]
    functions = [function.name for function in ast.walk(service) if isinstance(function, ast.FunctionDef)
                 and any(node in named for node in ast.walk(function))]
    assert len(named) == 1 and functions == ["compose_up"]
    assert swift_stream_service.STORE_COMPOSE_FILE == "../docker/docker-compose-swift-stream.yml"
    assert [node.value for node in ast.walk(service) if isinstance(node, ast.Constant)
            and isinstance(node.value, str) and "docker-compose" in node.value
            and node.value != swift_stream_service.STORE_COMPOSE_FILE and "\n" not in node.value] == ["docker-compose"]


def test_a_replication_only_handle_constructs_no_store_service():
    tree = ast.parse((NODE / "etny-node.py").read_text(encoding="utf-8"))
    constructions = [node for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "SwiftStreamService"]
    assert len(constructions) == 1
    guards = [node for node in ast.walk(tree) if isinstance(node, ast.If)
              and ast.unparse(node.test) == "self.__replication_only"
              and any(constructions[0] in ast.walk(branch) for branch in node.orelse)]
    assert len(guards) == 1
    assert [ast.unparse(statement) for statement in guards[0].body] == ["self.swift_stream_service = None"]
    assert [ast.unparse(argument) for argument in constructions[0].args] == [
        "logger", "self.__endpoint", "config.store_root_filepath"]
