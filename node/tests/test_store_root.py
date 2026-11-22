"""The root credential of the node's object store (store_access.root_credential,
store_compose_env; utils.enclave_compose_env; config; docker/docker-compose-
swift-stream.yml): drawn once per node into ~/opt/etny/store-root.json, given
to the store's compose in the environment of its own runs alone, and named by
no file of the repository. The --access_key and --secret_key arguments are
parsed and reach no store client."""
import inspect
import os
import re
from os.path import expanduser
from pathlib import Path

import pytest

import config
import store_access
import swift_stream_service
from store_fakes import recorded_modes
from utils import STORE_ROOT_VARIABLES, enclave_compose_env, get_or_generate_uuid

NODE = Path(__file__).resolve().parents[1]
ROOT_ACCESS_KEY = re.compile(r"ecldroot[0-9a-f]{12}")
ROOT_SECRET_KEY = re.compile(r"[0-9a-f]{40}")
# The keys of the contract's vector V01 and the bytes of their file.
V01_KEYS = ("ecldtz0123456789abcd", "0123456789abcdef0123456789abcdef01234567")
V01_BYTES = b'{"accessKey":"ecldtz0123456789abcd","secretKey":"0123456789abcdef0123456789abcdef01234567"}\n'


def root_file(tmp_path):
    """The root file of a home directory that has no `opt`."""
    home = tmp_path / "home"
    home.mkdir()
    return home / "opt" / "etny" / "store-root.json"


def changed_names(env, environment):
    """The names whose value differs between the mappings `env` and
    `environment`, a name one of them lacks included. Names alone: a failed
    assertion on them prints no variable's value."""
    return sorted(name for name in set(env) | set(environment) if env.get(name) != environment.get(name))


def test_the_root_is_drawn_once_with_mode_0600_and_reused(tmp_path, monkeypatch):
    path = root_file(tmp_path)
    modes = recorded_modes(monkeypatch)
    access_key, secret_key = store_access.root_credential(path)
    assert ROOT_ACCESS_KEY.fullmatch(access_key) and ROOT_SECRET_KEY.fullmatch(secret_key)
    assert path.read_bytes() == store_access.credential_bytes(access_key, secret_key)
    assert [entry for entry in modes if entry[0] == "open"] == [("open", f"{path}.tmp", 0o600, "O_EXCL")]
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["store-root.json"]
    del modes[:]
    assert store_access.root_credential(path) == (access_key, secret_key)
    assert store_access.root_credential(str(path)) == (access_key, secret_key)
    assert modes == []


def test_the_file_has_the_format_of_a_credential_file():
    assert store_access.credential_bytes(*V01_KEYS) == V01_BYTES
    assert store_access.credential_in(V01_BYTES) == V01_KEYS


@pytest.mark.parametrize("access_key, secret_key", [
    ("ab", "12345678"), ("abcdefghijklmnopqrstu", "12345678"), ("abc-def", "12345678"), ("abc", "1234567"),
    ("abc", "1" * 41), ("abc", "1234/678"), ('abc","x":"y', "12345678"), ("abc" + chr(0xE9), "12345678"),
])
def test_a_key_outside_its_pattern_gives_no_credential_bytes(access_key, secret_key):
    with pytest.raises(ValueError):
        store_access.credential_bytes(access_key, secret_key)


def test_each_node_draws_its_own_root(tmp_path):
    first = store_access.root_credential(tmp_path / "a" / "store-root.json")
    second = store_access.root_credential(tmp_path / "b" / "store-root.json")
    assert first[0] != second[0] and first[1] != second[1]


@pytest.mark.parametrize("held", [
    b"",
    b"not a credential\n",
    V01_BYTES,
    b'{"accessKey":"swiftstreamadmin","secretKey":"swiftstreamadmin"}\n',
    b'{"accessKey":"ecldroot0123456789ab","secretKey":"0123456789abcdef0123456789abcdef01234567","bucket":"x-v3"}\n',
    b'{"secretKey":"0123456789abcdef0123456789abcdef01234567","accessKey":"ecldroot0123456789ab"}\n',
    b'{"accessKey":"ecldroot0123456789ab","secretKey":"0123456789abcdef0123456789abcdef01234567"}' + b" " * 4096,
])
def test_a_file_that_holds_no_root_credential_is_replaced(tmp_path, held):
    path = root_file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(held)
    access_key, secret_key = store_access.root_credential(path)
    assert ROOT_ACCESS_KEY.fullmatch(access_key) and ROOT_SECRET_KEY.fullmatch(secret_key)
    assert secret_key != "0123456789abcdef0123456789abcdef01234567"
    assert path.read_bytes() == store_access.credential_bytes(access_key, secret_key)
    assert store_access.root_credential(path) == (access_key, secret_key)


def test_a_root_credential_with_white_space_between_its_tokens_is_kept(tmp_path):
    path = root_file(tmp_path)
    path.parent.mkdir(parents=True)
    held = b' {\r\n\t"accessKey" : "ecldroot0123456789ab" ,\r\n\t"secretKey" : "0123456789abcdef0123456789abcdef01234567"\r\n}\r\n'
    path.write_bytes(held)
    assert store_access.root_credential(path) == ("ecldroot0123456789ab", "0123456789abcdef0123456789abcdef01234567")
    assert path.read_bytes() == held


def test_a_partial_file_left_by_an_interrupted_start_is_removed_first(tmp_path):
    path = root_file(tmp_path)
    path.parent.mkdir(parents=True)
    partial = path.with_name("store-root.json.tmp")
    partial.write_bytes(b'{"accessKey":"ecldroot')
    access_key, secret_key = store_access.root_credential(path)
    assert path.read_bytes() == store_access.credential_bytes(access_key, secret_key)
    assert not partial.exists()


def test_a_partial_file_that_is_there_at_the_creation_is_not_written_through(tmp_path, monkeypatch):
    """The partial file is created exclusively: a file that holds its name
    once the one left there is removed keeps its bytes, and no root file is
    made of it."""
    path = root_file(tmp_path)
    path.parent.mkdir(parents=True)
    partial = path.with_name("store-root.json.tmp")
    planted = b"written by another process"
    monkeypatch.setattr(os, "remove", lambda removed: Path(removed).write_bytes(planted))
    with pytest.raises(FileExistsError):
        store_access.root_credential(path)
    assert partial.read_bytes() == planted
    assert not path.exists()


def test_the_draw_in_a_home_without_opt_leaves_the_uuid_directory_to_be_made(tmp_path):
    path = root_file(tmp_path)
    store_access.root_credential(path)
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["store-root.json"]
    uuid_file = path.parent / "node" / "UUID"
    made = get_or_generate_uuid(str(uuid_file))
    assert uuid_file.read_text() == made and len(made) == 32


def test_the_root_file_is_beside_the_directory_of_the_uuid():
    assert config.store_root_filepath == Path(expanduser("~")) / "opt" / "etny" / "store-root.json"
    assert config.store_root_filepath.parent == config.uuid_filepath.parent.parent


def test_the_stores_compose_names_no_credential_and_both_variables():
    text = (NODE / "docker" / "docker-compose-swift-stream.yml").read_text()
    assert "swiftstreamadmin" not in text and "minioadmin" not in text
    assert STORE_ROOT_VARIABLES == ("ECLD_STORE_ROOT_USER", "ECLD_STORE_ROOT_PASSWORD")
    environment = re.findall(r"^\s+(MINIO_ROOT_\w+):\s*(\S+)\s*$", text, re.MULTILINE)
    assert environment == [("MINIO_ROOT_USER", "${ECLD_STORE_ROOT_USER:?unset}"),
                           ("MINIO_ROOT_PASSWORD", "${ECLD_STORE_ROOT_PASSWORD:?unset}")]


def test_a_run_of_the_stores_compose_gets_the_root_and_os_environ_does_not(monkeypatch):
    for name in STORE_ROOT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ETNY_TEST_KEPT", "kept")
    before = dict(os.environ)
    env = store_access.store_compose_env(("ecldroot0123456789ab", "0123456789abcdef0123456789abcdef01234567"))
    assert env["ECLD_STORE_ROOT_USER"] == "ecldroot0123456789ab"
    assert env["ECLD_STORE_ROOT_PASSWORD"] == "0123456789abcdef0123456789abcdef01234567"
    assert env["ETNY_TEST_KEPT"] == "kept"
    assert changed_names(env, before) == sorted(STORE_ROOT_VARIABLES)
    assert changed_names(os.environ, before) == []


@pytest.mark.parametrize("legacy", [True, False])
def test_an_enclave_compose_run_gets_neither_root_variable(monkeypatch, legacy):
    monkeypatch.delenv("ETNY_SCONE_QUEUES", raising=False)
    for name in STORE_ROOT_VARIABLES:
        monkeypatch.setenv(name, "set-by-an-operator")
    before = dict(os.environ)
    env = enclave_compose_env(8, legacy)
    assert [name for name in STORE_ROOT_VARIABLES if name in env] == []
    assert changed_names(env, before) == sorted(STORE_ROOT_VARIABLES + ("ETNY_SCONE_QUEUES",))
    assert changed_names(os.environ, before) == []


def test_the_key_arguments_are_accepted_and_reach_no_store_client():
    parsed, unknown = config.parser.parse_known_args(
        ["--privatekey", "0", "--access_key", "operator", "--secret_key", "operatorsecret"])
    assert unknown == [] and (parsed.access_key, parsed.secret_key) == ("operator", "operatorsecret")
    parsed, unknown = config.parser.parse_known_args(["--privatekey", "0", "-u", "operator", "-p", "operatorsecret"])
    assert unknown == [] and (parsed.access_key, parsed.secret_key) == ("operator", "operatorsecret")
    default, _ = config.parser.parse_known_args(["--privatekey", "0"])
    assert (default.access_key, default.secret_key) == (None, None)
    taken = [name for names in config.arguments.values() for name in names]
    assert "endpoint" in taken and "access_key" not in taken and "secret_key" not in taken
    parameters = list(inspect.signature(swift_stream_service.SwiftStreamService.__init__).parameters)
    assert parameters == ["self", "logger", "endpoint", "root_filepath", "credential_dir", "data_dir"]
