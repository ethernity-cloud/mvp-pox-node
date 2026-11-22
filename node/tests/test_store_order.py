"""The store's users of an order and of an integration test (etny-node.py
__start_enclave_environment, __enclave_environment_down,
__run_integration_test; store_access.prepare, sweep, write_credential,
StoreAdmin, error_text; the error texts of swift_stream_service), against a
MinIO kept in memory and a docker that records its commands (store_fakes),
on the composes of the field (field_composes): no docker, no store, no
chain. Between the images' arrival in docker and the compose's first `up`
the labels are read, the mounts added, the store swept, the users of the
plan created and their files written; after the compose's `down` the store
is swept. A start whose credentials are not prepared starts no service."""
import importlib.util
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from minio.error import MinioAdminException

import store_access
import swift_stream_service
from field_composes import (NODENITHY_TRUSTEDZONE, PYNITHY_TRUSTEDZONE, SDK_JS_NODENITHY, SDK_JS_PYNITHY, copy_of,
                            sdk_js_published, trustedzone_published)
from store_access import SECURELOCK, TRUSTEDZONE
from store_fakes import (BUILT_IN_POLICIES, ENDPOINT, FakeDocker, FakeStore, assert_no_secret, install, recorded_modes,
                         s3_error)
from utils import STORE_ROOT_VARIABLES, compose_with_volume

NODE = Path(__file__).resolve().parents[1]
ROOT = ("ecldroot0123456789ab", "0123456789abcdef0123456789abcdef01234567")
TREE = "QmaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
TREE_COMPOSE = "QmbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
BUCKET = "etny-nodenithy-testnet-v3"
INTEGRATION_BUCKET = "etny-bucket-integration"
TZ_IMAGE = "localhost:5000/etny-trustedzone:latest"
SL_IMAGE = "localhost:5000/etny-securelock:latest"
# The labels of the images: a trustedzone that reads the file, a JS
# securelock that reads it (built FROM that trustedzone, it carries both
# keys) and one of an SDK from before the label, built on that trustedzone.
TZ_FILE = {"cloud.ethernity.store": "file"}
SL_FILE = {"cloud.ethernity.store": "file", "cloud.ethernity.securelock.store": "file"}
SL_BEFORE = {"cloud.ethernity.store": "file"}
# A dApp's compose of the JS SDK (two-space services, with its LAS), the same
# of its Python enclaves (four-space services) and the two trustedzone
# trees' own composes.
ORDER_COMPOSE = sdk_js_published(copy_of(SDK_JS_NODENITHY), True, False)
PYNITHY_ORDER_COMPOSE = sdk_js_published(copy_of(SDK_JS_PYNITHY), True, False)
NODENITHY_TREE_COMPOSE = trustedzone_published(copy_of(NODENITHY_TRUSTEDZONE), True)
PYNITHY_TREE_COMPOSE = trustedzone_published(copy_of(PYNITHY_TRUSTEDZONE), True)
V01_BYTES = b'{"accessKey":"ecldtz0123456789abcd","secretKey":"0123456789abcdef0123456789abcdef01234567"}\n'
LITERAL_BYTES = b'{"accessKey":"swiftstreamadmin","secretKey":"swiftstreamadmin"}\n'
USER_NAMES = [(r"ecldtz[0-9a-f]{14}", "T"), (r"ecldsl[0-9a-f]{14}", "S"), (r"ecldit[0-9a-f]{14}", "G"),
              (r"swiftstreamadmin", "L")]


@pytest.fixture(scope="module")
def agent():
    """etny-node.py as a module, loaded from its path (its name is no
    identifier); its entry point runs under __main__ alone."""
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def letters(text):
    """`text` with the access key of each user replaced by T, S, G or L."""
    for pattern, name in USER_NAMES:
        text = re.sub(pattern, name, text)
    return text


class Rig:
    """A node whose store runs under ROOT and holds no user, for one compose:
    `store` and `docker` (store_fakes) in the place of MinIO and of docker,
    whose images carry `labels` ({reference: labels}), the compose
    `compose_text` at `compose` under the working directory, and the
    credential files under `credential_dir`. `snapshots` holds the compose's
    text and the credential files as they are before each admin call
    ('admin') and before each `up` of the compose ('up')."""

    def __init__(self, monkeypatch, tmp_path, compose_text, labels, order_id="74"):
        self.store = FakeStore()
        self.store.root = ROOT
        self.store.secrets.add(ROOT[1])
        self.docker = FakeDocker(self.store, labels=labels)
        self.modes = recorded_modes(monkeypatch)
        install(monkeypatch, self.store, [store_access])
        monkeypatch.chdir(tmp_path)
        self.compose = f"./orders/{order_id}/docker-compose.yml"
        (tmp_path / self.compose).parent.mkdir(parents=True)
        (tmp_path / self.compose).write_text(compose_text)
        self.credential_dir = tmp_path / "ecld-store"
        self.admin = store_access.StoreAdmin(ENDPOINT, *ROOT)
        self.logger = logging.getLogger("order")
        self.snapshots = []
        self.store.before_admin = lambda method: self.snapshot("admin")
        self.docker.before = lambda args: args[3:4] == ["up"] and self.snapshot("up")

    def files(self):
        """{enclave service: {entry name: bytes}} of the credential
        directory."""
        held = {}
        for service in sorted(os.listdir(self.credential_dir)) if self.credential_dir.is_dir() else []:
            held[service] = {entry.name: entry.read_bytes() for entry in (self.credential_dir / service).iterdir()}
        return held

    def snapshot(self, name):
        self.snapshots.append((name, Path(self.compose).read_text(), self.files()))

    def prepare(self, bucket=BUCKET, integration_test=False):
        store_access.prepare(self.compose, bucket, integration_test, self.admin, str(self.credential_dir),
                             self.logger, self.docker)

    def volume(self, service):
        return store_access.credential_volume(str(self.credential_dir), service)

    def mounted(self, text=None):
        """The enclave services whose `volumes` hold the credential mount in
        the compose `text`, the compose file's when None."""
        text = Path(self.compose).read_text() if text is None else text
        return [service for service in (TRUSTEDZONE, SECURELOCK)
                if compose_with_volume(text, service, self.volume(service)) == text]

    def user(self, name):
        """(access key, secret key) of the store's user T, S, G or L."""
        found = [pair for pair in self.store.users.items() if letters(pair[0]) == name]
        assert len(found) == 1, name
        return found[0]

    def steps(self, since=0):
        """The events from index `since` on: the docker-compose runs on the
        compose as their arguments after the file, the label reads as
        ('inspect', reference), the admin calls with each user's access key
        as its letter. Every other command is left out."""
        steps = []
        for event in self.store.events[since:]:
            if event[0] == "admin":
                steps.append(tuple(letters(str(part)) for part in event))
            elif event[0] == "run" and event[1][:3] == ["docker-compose", "-f", self.compose]:
                steps.append(tuple(event[1][3:]))
            elif event[0] == "run" and event[1][:3] == ["docker", "image", "inspect"]:
                steps.append(("inspect", event[1][-1]))
        return steps


def node_of(agent, monkeypatch, tmp_path, rig, pox_is_12=True):
    """An EtnyPoXNode built without __init__, of a node with 8 CPUs, working
    in `tmp_path`, whose commands go to the rig's docker and whose store
    service holds the rig's admin client and credential directory. Docker
    holds none of the compose's images, so they are pulled."""
    monkeypatch.setattr(agent, "run_subprocess", lambda args, logger, env=None: rig.docker(args, logger, env) and None)
    monkeypatch.setattr(agent, "run_subprocess_status", rig.docker)
    monkeypatch.setattr(agent, "await_enclave_runtime", lambda name, timeout: (True, 0.0))
    monkeypatch.setattr(agent.config, "enclave_start_stagger_seconds", 90.0)
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = rig.logger
    node.cache_config = SimpleNamespace(base_path=tmp_path,
                                        tested_las_filepath=lambda name: tmp_path / f"las-tested-{name}.yml")
    node._EtnyPoXNode__number_of_cpus = 8
    node._EtnyPoXNode__pox_is_12 = pox_is_12
    node._EtnyPoXNode__enclave_up = False
    node._EtnyPoXNode__images_held = lambda tags: False
    node.order_docker_compose_file = rig.compose
    node.swift_stream_service = SimpleNamespace(
        admin=rig.admin, credential_dir=str(rig.credential_dir),
        compose_up=lambda: rig.store.events.append(("store", "compose_up")),
        get_file_content=lambda bucket_name, object_name: (True, "written"),
        delete_bucket=lambda bucket_name: rig.store.events.append(("store", "delete_bucket", bucket_name)))
    return node


O3 = [("admin", "user_list"), ("admin", "policy_list")]


def o4(*names):
    """The admin calls that create the users `names` (T, S, G, L)."""
    return [call for name in names for call in (("admin", "policy_add", f"ecld-{name}"), ("admin", "user_add", name),
                                                ("admin", "policy_set", f"ecld-{name}", name))]


def swept(*names):
    """The admin calls of a sweep that finds the users `names`."""
    return ([("admin", "user_list")] + [("admin", "user_remove", name) for name in names]
            + [("admin", "policy_list")] + [("admin", "policy_remove", f"ecld-{name}") for name in names])


def test_the_enclave_service_names_are_the_agents(agent):
    assert (store_access.TRUSTEDZONE, store_access.SECURELOCK) == (agent.ENCLAVE_TRUSTEDZONE, agent.ENCLAVE_SECURELOCK)
    assert store_access.ENCLAVE_SERVICES == agent.ENCLAVE_CONTAINERS


def test_an_order_gets_its_users_before_the_first_up_and_loses_them_after_the_down(agent, monkeypatch, tmp_path):
    for name in STORE_ROOT_VARIABLES:
        monkeypatch.setenv(name, "set-by-an-operator")
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_FILE})
    node = node_of(agent, monkeypatch, tmp_path, rig)
    node._EtnyPoXNode__start_enclave_environment(TREE, BUCKET)
    assert rig.steps() == ([("pull",), ("down",), ("inspect", TZ_IMAGE), ("inspect", SL_IMAGE)] + O3 + o4("T", "S")
                           + [("up", "-d", "las", "etny-trustedzone"), ("up", "-d", "etny-securelock")])
    trustedzone, securelock = rig.user("T"), rig.user("S")
    assert rig.store.policies[f"ecld-{trustedzone[0]}"] == store_access.trustedzone_policy(BUCKET)
    assert rig.store.policies[f"ecld-{securelock[0]}"] == store_access.securelock_policy(BUCKET)
    assert rig.store.user_policy == {trustedzone[0]: f"ecld-{trustedzone[0]}", securelock[0]: f"ecld-{securelock[0]}"}
    files = {TRUSTEDZONE: {"store.json": store_access.credential_bytes(*trustedzone)},
             SECURELOCK: {"store.json": store_access.credential_bytes(*securelock)}}
    # The mounts are in the compose before the sweep, and no file is written
    # before the last user is created.
    assert [(name, rig.mounted(text), held) for name, text, held in rig.snapshots] == (
        [("admin", [TRUSTEDZONE, SECURELOCK], {})] * 8 + [("up", [TRUSTEDZONE, SECURELOCK], files)] * 2)
    assert rig.files() == files
    assert node._EtnyPoXNode__enclave_up is True
    # The policy documents reached the admin client as files, since removed.
    assert len(rig.store.policy_files) == 2 and not any(os.path.exists(path) for path in rig.store.policy_files)
    # The modes asked for: 0700 for each directory, 0400 for each file.
    base = str(rig.credential_dir)
    assert [entry for entry in rig.modes if entry[1].startswith(base)] == [
        ("mkdir", base, 0o700),
        ("mkdir", os.path.join(base, TRUSTEDZONE), 0o700),
        ("open", os.path.join(base, TRUSTEDZONE, "store.json.tmp"), 0o400),
        ("mkdir", os.path.join(base, SECURELOCK), 0o700),
        ("open", os.path.join(base, SECURELOCK, "store.json.tmp"), 0o400)]
    # No docker-compose run on the order's compose gets a root variable.
    runs = [env for args, env in rig.docker.runs if args[:3] == ["docker-compose", "-f", rig.compose]]
    assert len(runs) == 4 and all(env is not None and not set(STORE_ROOT_VARIABLES) & set(env) for env in runs)
    assert all(env.get("ETNY_SCONE_QUEUES") == "16" for env in runs)

    mark = len(rig.store.events)
    node._EtnyPoXNode__enclave_environment_down()
    assert rig.steps(mark) == [("down",)] + swept("S", "T")
    assert rig.store.users == {} and rig.store.user_policy == {}
    assert sorted(rig.store.policies) == sorted(BUILT_IN_POLICIES)
    assert rig.files() == {TRUSTEDZONE: {}, SECURELOCK: {}}
    assert node._EtnyPoXNode__enclave_up is False
    mark = len(rig.store.events)
    node._EtnyPoXNode__enclave_environment_down()
    assert rig.store.events[mark:] == []


@pytest.mark.parametrize("labels, user, pox_is_12", [(TZ_FILE, "G", True), (None, "L", False), ({}, "L", True)])
def test_an_integration_test_gets_one_user_of_the_integration_bucket(agent, monkeypatch, tmp_path, labels, user,
                                                                     pox_is_12):
    rig = Rig(monkeypatch, tmp_path, PYNITHY_TREE_COMPOSE, {TZ_IMAGE: labels}, order_id="integration_test")
    node = node_of(agent, monkeypatch, tmp_path, rig, pox_is_12)
    prepared = []

    # The test's prerequisites are in place (its compose in the order's
    # folder) and its enclave has written the objects the agent reads.
    def prerequisites(bucket_name, order_id, docker_compose_file):
        prepared.append((bucket_name, order_id))

    node.build_prerequisites_integration_test = prerequisites
    node.wait_for_enclave_v2 = lambda bucket_name, object_name, timeout: True
    node.storage = SimpleNamespace(download_many=lambda hashes, attempts, delay: True)
    node._EtnyPoXNode__network = "bloxberg_testnet"
    node._EtnyPoXNode__trustedzones = {"etny-pynithy": "dcap"}
    node._EtnyPoXNode__latest_trustedzone_image = lambda image: (TREE, TREE_COMPOSE)
    assert node._EtnyPoXNode__run_integration_test("etny-pynithy") == (TREE, "written", "written")
    assert prepared == [(INTEGRATION_BUCKET, "integration_test")]
    assert rig.steps() == ([("pull",), ("down",), ("inspect", TZ_IMAGE)] + O3 + o4(user) + [("up", "-d")]
                           + [("down",)] + swept(user))
    (text, held), = [(text, held) for name, text, held in rig.snapshots if name == "up"]
    access_key = next(iter(held[TRUSTEDZONE].values())).split(b'"')[3].decode()
    assert (rig.mounted(text), letters(access_key), sorted(held)) == ([TRUSTEDZONE], user, [TRUSTEDZONE])
    assert [document for policy, document in rig.store.policies.items() if policy.startswith("ecld-")] == []
    # The store's compose is run through the store service, with the root
    # credential in the environment of that run, and by no command of the
    # agent's own.
    assert [event for event in rig.store.events if event[0] == "store"] == [
        ("store", "compose_up"), ("store", "delete_bucket", INTEGRATION_BUCKET)]
    assert [args for args, _ in rig.docker.runs if "swift-stream" in args] == []
    assert rig.store.users == {} and rig.files() == {TRUSTEDZONE: {}}


def test_the_user_of_an_integration_test_has_the_trustedzones_policy_of_the_integration_bucket(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, NODENITHY_TREE_COMPOSE, {TZ_IMAGE: TZ_FILE})
    rig.prepare(INTEGRATION_BUCKET, integration_test=True)
    access_key, secret_key = rig.user("G")
    assert rig.store.policies[f"ecld-{access_key}"] == store_access.trustedzone_policy(INTEGRATION_BUCKET)
    assert rig.files() == {TRUSTEDZONE: {"store.json": store_access.credential_bytes(access_key, secret_key)}}


# (the labels of the trustedzone's and the securelock's image, None for an
# image docker does not answer for; the users created; the user of the
# trustedzone's file and of the securelock's).
IMAGES = [
    (TZ_FILE, SL_FILE, ["T", "S"], "T", "S"),
    (TZ_FILE, SL_BEFORE, ["T", "L"], "T", "L"),
    (TZ_FILE, None, ["T", "L"], "T", "L"),
    ({}, SL_FILE, ["L", "S"], "L", "S"),
    ({"cloud.ethernity.securelock.store": "file"}, {"cloud.ethernity.securelock.store": "file"}, ["L", "S"], "L", "S"),
    ({"cloud.ethernity.store": "File"}, {"cloud.ethernity.securelock.store": "file "}, ["L"], "L", "L"),
    (None, None, ["L"], "L", "L"),
]


@pytest.mark.parametrize("compose", [ORDER_COMPOSE, PYNITHY_ORDER_COMPOSE], ids=["nodenithy", "pynithy"])
@pytest.mark.parametrize("trustedzone, securelock, created, trustedzone_file, securelock_file", IMAGES)
def test_each_enclave_service_gets_the_user_of_its_images_label(monkeypatch, tmp_path, compose, trustedzone,
                                                                securelock, created, trustedzone_file,
                                                                securelock_file):
    labels = {reference: held for reference, held in ((TZ_IMAGE, trustedzone), (SL_IMAGE, securelock))
              if held is not None}
    rig = Rig(monkeypatch, tmp_path, compose, labels)
    rig.prepare()
    assert rig.steps() == [("inspect", TZ_IMAGE), ("inspect", SL_IMAGE)] + O3 + o4(*created)
    assert rig.mounted() == [TRUSTEDZONE, SECURELOCK]
    assert sorted(letters(access_key) for access_key in rig.store.users) == sorted(created)
    files = rig.files()
    assert files == {TRUSTEDZONE: {"store.json": store_access.credential_bytes(*rig.user(trustedzone_file))},
                     SECURELOCK: {"store.json": store_access.credential_bytes(*rig.user(securelock_file))}}
    for name in created:
        access_key, _ = rig.user(name)
        policy = store_access.securelock_policy if name == "S" else store_access.trustedzone_policy
        assert rig.store.policies[f"ecld-{access_key}"] == policy(BUCKET)
    if "L" in created:
        assert rig.user("L") == ("swiftstreamadmin", "swiftstreamadmin")
        assert LITERAL_BYTES in [held["store.json"] for held in files.values()]


def test_the_file_of_the_contracts_first_vector_holds_its_bytes(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, NODENITHY_TREE_COMPOSE, {TZ_IMAGE: TZ_FILE})
    drawn = {7: "0123456789abcd", 20: "0123456789abcdef0123456789abcdef01234567"}
    monkeypatch.setattr(store_access.secrets, "token_hex", lambda count: drawn[count])
    rig.prepare()
    assert rig.user("T") == ("ecldtz0123456789abcd", "0123456789abcdef0123456789abcdef01234567")
    path = rig.credential_dir / "etny-trustedzone" / "store.json"
    assert path.read_bytes() == V01_BYTES and len(V01_BYTES) == 92
    assert sorted(entry.name for entry in rig.credential_dir.iterdir()) == ["etny-trustedzone"]
    assert ("open", f"{path}.tmp", 0o400) in rig.modes


def test_a_compose_without_a_securelock_gets_one_file_and_one_mount(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, PYNITHY_TREE_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_FILE})
    rig.prepare()
    assert rig.steps() == [("inspect", TZ_IMAGE)] + O3 + o4("T")
    assert rig.mounted() == [TRUSTEDZONE]
    assert rig.files() == {TRUSTEDZONE: {"store.json": store_access.credential_bytes(*rig.user("T"))}}
    assert "etny-securelock" not in Path(rig.compose).read_text()


def test_a_securelock_in_the_compose_of_an_integration_test_gets_nothing(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_FILE})
    rig.prepare(INTEGRATION_BUCKET, integration_test=True)
    assert rig.steps() == [("inspect", TZ_IMAGE)] + O3 + o4("G")
    assert rig.mounted() == [TRUSTEDZONE]
    assert sorted(rig.files()) == [TRUSTEDZONE]


def test_a_compose_without_enclave_services_gets_no_user_no_file_and_no_mount(monkeypatch, tmp_path):
    text = "services:\n  api:\n    image: localhost:5000/api\n    depends_on:\n      etny-trustedzone:\n" \
           "        condition: service_started\n"
    rig = Rig(monkeypatch, tmp_path, text, {TZ_IMAGE: TZ_FILE})
    rig.store.users["left"] = "e" * 40
    rig.prepare()
    assert rig.steps() == [("admin", "user_list"), ("admin", "user_remove", "left"), ("admin", "policy_list")]
    assert Path(rig.compose).read_text() == text and rig.files() == {} and rig.store.users == {}


def test_a_service_that_does_not_take_the_mount_is_given_the_literal_named_user(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    key = "  etny-trustedzone:\n"
    assert ORDER_COMPOSE.count(key) == 1
    text = ORDER_COMPOSE.replace(key, key + "    volumes: [/srv/data:/data]\n")
    rig = Rig(monkeypatch, tmp_path, text, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_FILE})
    rig.prepare()
    assert rig.steps() == [("inspect", TZ_IMAGE), ("inspect", SL_IMAGE)] + O3 + o4("L", "S")
    assert rig.mounted() == [SECURELOCK]
    assert "    volumes: [/srv/data:/data]\n" in Path(rig.compose).read_text()
    assert rig.files() == {TRUSTEDZONE: {"store.json": LITERAL_BYTES},
                           SECURELOCK: {"store.json": store_access.credential_bytes(*rig.user("S"))}}
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == [f"The service etny-trustedzone of {rig.compose} did not take the store credential's mount; "
                        f"it is given the literal-named user"]
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.INFO] == [
        f"Store users of {BUCKET}: etny-trustedzone literal, etny-securelock securelock"]


def test_the_next_start_sweeps_the_users_and_files_of_the_one_before(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_BEFORE})
    rig.prepare()
    first = dict(rig.store.users)
    with_mounts = Path(rig.compose).read_text()
    rig.docker.labels[SL_IMAGE] = SL_FILE
    mark = len(rig.store.events)
    rig.prepare()
    assert rig.steps(mark) == [("inspect", TZ_IMAGE), ("inspect", SL_IMAGE)] + swept("T", "L") + o4("T", "S")
    assert not set(first) & set(rig.store.users)
    assert Path(rig.compose).read_text() == with_mounts
    assert rig.files() == {TRUSTEDZONE: {"store.json": store_access.credential_bytes(*rig.user("T"))},
                           SECURELOCK: {"store.json": store_access.credential_bytes(*rig.user("S"))}}


def break_o2(rig):
    """The compose is replaced by a directory once its images' labels are
    read, so that it cannot be written with its mounts; the rig takes no
    snapshot of it."""
    def replace(args):
        if args[:3] == ["docker", "image", "inspect"] and os.path.isfile(rig.compose):
            os.remove(rig.compose)
            os.mkdir(rig.compose)
    rig.docker.before = replace
    rig.store.before_admin = lambda method: None


def break_o5(rig):
    rig.credential_dir.write_bytes(b"a file where the directory would be")


@pytest.mark.parametrize("fail, bucket, detail, calls", [
    (break_o2, BUCKET, "O2 [A-Za-z]+Error", []),
    (lambda rig: rig.store.failing.append("user_list"), BUCKET, "O3 MinioAdminException", [("admin", "user_list")]),
    (lambda rig: rig.store.failing.append("policy_list"), BUCKET, "O3 MinioAdminException", O3),
    (lambda rig: rig.store.failing.append("policy_add"), BUCKET, "O4 MinioAdminException", O3 + o4("T")[:1]),
    (lambda rig: rig.store.failing.append("user_add"), BUCKET, "O4 MinioAdminException", O3 + o4("T")[:2]),
    (lambda rig: rig.store.failing.append("policy_set"), BUCKET, "O4 MinioAdminException", O3 + o4("T")),
    (lambda rig: None, "etny-*-v3", "O4 BucketRefused", O3),
    (lambda rig: None, "etny_nodenithy-v3", "O4 BucketRefused", O3),
    (break_o5, BUCKET, "O5 [A-Za-z]+Error", O3 + o4("T", "S")),
])
def test_a_start_whose_credentials_are_not_prepared_starts_no_service(agent, monkeypatch, tmp_path, fail, bucket,
                                                                      detail, calls):
    """`detail` is the step that fails and the class of its error, a pattern
    where the class is the operating system's."""
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_FILE})
    node = node_of(agent, monkeypatch, tmp_path, rig)
    fail(rig)
    with pytest.raises(agent.EnclaveFailed) as failure:
        node._EtnyPoXNode__start_enclave_environment(TREE, bucket)
    assert re.fullmatch(f"the store credential was not prepared - {detail}", str(failure.value))
    assert rig.steps() == [("pull",), ("down",), ("inspect", TZ_IMAGE), ("inspect", SL_IMAGE)] + calls
    assert node._EtnyPoXNode__enclave_up is True
    # The caller brings the environment down as after any failed start: the
    # compose's down, then the sweep.
    if fail is break_o5:
        rig.credential_dir.unlink()
    mark = len(rig.store.events)
    node._EtnyPoXNode__enclave_environment_down()
    assert rig.steps(mark)[:2] == [("down",), ("admin", "user_list")]
    assert rig.store.users == {} and sorted(rig.store.policies) == sorted(BUILT_IN_POLICIES)
    assert not any(step[0] == "up" for step in rig.steps())


def test_a_compose_that_cannot_be_read_fails_o2(monkeypatch, tmp_path):
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {})
    os.remove(rig.compose)
    with pytest.raises(store_access.NotPrepared) as failure:
        rig.prepare()
    assert str(failure.value) == "O2 FileNotFoundError"
    assert failure.value.__cause__ is None and failure.value.__suppress_context__
    assert rig.store.events == []


def test_a_sweep_that_fails_at_the_end_is_logged_and_does_not_raise(agent, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_FILE})
    node = node_of(agent, monkeypatch, tmp_path, rig)
    node._EtnyPoXNode__start_enclave_environment(TREE, BUCKET)
    rig.store.failing.append("user_remove")
    mark = len(rig.store.events)
    node._EtnyPoXNode__enclave_environment_down()
    assert rig.steps(mark) == [("down",), ("admin", "user_list"), ("admin", "user_remove", "S")]
    assert node._EtnyPoXNode__enclave_up is False
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING] == [
        "The store's users and credential files were not removed (MinioAdminException); the next start removes them"]
    assert len(rig.store.users) == 2
    # The sweep of the next start removes what this one left.
    node._EtnyPoXNode__start_enclave_environment(TREE, BUCKET)
    assert sorted(letters(access_key) for access_key in rig.store.users) == ["S", "T"]
    assert len([policy for policy in rig.store.policies if policy.startswith("ecld-")]) == 2


def test_no_secret_key_reaches_a_log_record_an_argument_list_or_a_failure(agent, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {TZ_IMAGE: TZ_FILE, SL_IMAGE: SL_BEFORE})
    node = node_of(agent, monkeypatch, tmp_path, rig)
    node._EtnyPoXNode__start_enclave_environment(TREE, BUCKET)
    node._EtnyPoXNode__enclave_environment_down()
    # A user's creation that fails with an error whose message quotes the
    # user's secret key, and a sweep that fails at the end.
    rig.store.failing.append("user_add")
    with pytest.raises(agent.EnclaveFailed) as failure:
        node._EtnyPoXNode__start_enclave_environment(TREE, BUCKET)
    rig.store.failing.append("policy_remove")
    node._EtnyPoXNode__enclave_environment_down()
    held = sorted(rig.store.secrets)
    assert len(held) == 4 and ROOT[1] in held and "swiftstreamadmin" in held
    assert not any(secret in str(failure.value) for secret in held if secret != "swiftstreamadmin")
    assert_no_secret(rig.store, caplog.records, [args for args, _ in rig.docker.runs])
    assert not any(ROOT[0] in record.getMessage() for record in caplog.records)
    assert len(caplog.records) >= 4


class Refused:
    """minio-py's admin client in the place of StoreAdmin's, whose
    policy_add raises once it has read its file."""

    def __init__(self):
        self.read = []

    def policy_add(self, policy_name, policy_file):
        with open(policy_file, encoding="utf-8") as handle:
            self.read.append((policy_file, handle.read()))
        raise MinioAdminException("400", "the policy is quoted here")


def test_the_policy_file_is_removed_when_the_store_refuses_the_policy(monkeypatch):
    store = FakeStore()
    install(monkeypatch, store, [store_access])
    admin = store_access.StoreAdmin(ENDPOINT, *ROOT)
    admin._admin = Refused()
    with pytest.raises(MinioAdminException):
        admin.add_policy("ecld-ecldtz0123456789abcd", store_access.trustedzone_policy(BUCKET))
    (path, text), = admin._admin.read
    assert store_access.json.loads(text) == store_access.trustedzone_policy(BUCKET)
    assert not os.path.exists(path)


@pytest.mark.parametrize("listing", ["[]", "null", '"users"', "3"])
def test_a_listing_that_is_no_object_fails_the_sweep(monkeypatch, tmp_path, listing):
    rig = Rig(monkeypatch, tmp_path, ORDER_COMPOSE, {})
    rig.admin._admin.user_list = lambda: listing
    with pytest.raises(ValueError):
        store_access.sweep(rig.admin, str(rig.credential_dir))
    with pytest.raises(store_access.NotPrepared) as failure:
        rig.prepare()
    assert str(failure.value) == "O3 ValueError"


def test_an_error_is_logged_as_its_class_and_for_an_s3_error_its_code():
    assert store_access.error_text(s3_error("AccessDenied")) == "S3Error AccessDenied"
    assert store_access.error_text(s3_error("NoSuchBucket")) == "S3Error NoSuchBucket"
    assert store_access.error_text(MinioAdminException("403", "the request is quoted here")) == "MinioAdminException"
    assert store_access.error_text(OSError(13, "Permission denied", "/run/ecld-store")) == "PermissionError"
    assert store_access.error_text(ValueError("the keys are quoted here")) == "ValueError"


class Denied:
    """An S3 client whose every call is answered with AccessDenied."""

    def __getattr__(self, name):
        def call(*arguments, **named):
            raise s3_error("AccessDenied")
        return call


@pytest.mark.parametrize("method, arguments", [
    ("create_bucket", ("b",)),
    ("delete_bucket", ("b",)),
    ("delete_file", ("b", "o")),
    ("delete_files", ("b", ["o"])),
    ("upload_file", ("b", "o", "path")),
    ("upload_files", ("b", ["o"], ["path"])),
    ("download_file", ("b", "o", "path")),
    ("download_files", ("b", ["o"], ["path"])),
    ("put_file_content", ("b", "o", "", SimpleNamespace(getbuffer=lambda: b""))),
    ("is_object_in_bucket", ("b", "o")),
])
def test_the_store_services_error_texts_name_the_class_and_the_code_alone(method, arguments):
    service = object.__new__(swift_stream_service.SwiftStreamService)
    service.client = Denied()
    service.restart_etny_swift_stream_and_reconnect = lambda: False
    status, text = getattr(service, method)(*arguments)
    assert status is False
    assert text == f"{method} failed: S3Error AccessDenied"
