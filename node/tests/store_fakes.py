"""Stand-ins of the node's object store and of docker for the tests of the
store's credentials (store_access, swift_stream_service, etny-node.py): a
MinIO kept in memory (FakeStore) with the admin and the S3 client classes
that read it in the place of minio-py's, and a docker that records the
commands it is given (FakeDocker). Every call is appended to one list,
FakeStore.events, so a test reads the order of the calls of the two clients
and of docker together."""
import json
import os

from minio.error import MinioAdminException, S3Error

import store_access
from utils import STORE_ROOT_VARIABLES

ENDPOINT = "localhost:9000"
# The policies a MinIO holds before any is added.
BUILT_IN_POLICIES = ("consoleAdmin", "diagnostics", "readonly", "readwrite", "writeonly")


class Environment(dict):
    """A copy of an environment whose repr is its variables' names. pytest
    prints the operands of a failed assertion and the arguments of the
    function it fails in; of this mapping it prints no variable's value."""

    def __repr__(self):
        return f"Environment({sorted(self)})"


def s3_error(code):
    """The S3Error minio-py raises for an error response of code `code`; its
    message quotes the request, as a server's does."""
    return S3Error(code=code, message=f"the request of {code} is quoted here", resource="/", request_id="1",
                   host_id="1", response=None)


class FakeStore:
    """A MinIO in memory. `root` is the pair its container runs under, None
    while it has no container; `users` {access key: secret key},
    `user_policy` {access key: policy name} and `policies` {name: document}
    are its users and policies; `live_when` says whether it answers at all;
    `answers` holds further pairs that list its buckets and `denied` pairs
    whose listing is denied. An admin call whose name is in `failing` raises
    once, with a message that quotes its request, a new user's secret key
    included. `events` records
    the calls: ('admin', method, argument...), ('s3', 'list_buckets',
    access key), ('http', method, url), ('run', arguments, Environment or
    None)."""

    def __init__(self):
        self.events = []
        self.root = None
        self.users = {}
        self.user_policy = {}
        self.policies = {name: {} for name in BUILT_IN_POLICIES}
        self.live_when = lambda: True
        self.answers = set()
        self.denied = set()
        # Every secret key the store was given: its roots' and its users'.
        self.secrets = set()
        # The policy files given to policy_add, and the method names whose
        # call raises, each once.
        self.policy_files = []
        self.failing = []
        # Called with the name of each admin call before it is recorded.
        self.before_admin = lambda method: None

    @property
    def live(self):
        return self.root is not None and self.live_when()

    def admin_calls(self):
        return [event[1:] for event in self.events if event[0] == "admin"]

    def minio_admin(self):
        """A class in the place of minio.MinioAdmin, of this store."""
        store = self

        class FakeMinioAdmin:
            def __init__(self, *, endpoint, credentials, secure, http_client):
                self.endpoint = endpoint
                self.credentials = credentials.retrieve()
                self.secure = secure
                self.http_client = http_client

            def _call(self, method, *arguments, quoted=""):
                store.before_admin(method)
                store.events.append(("admin", method, *arguments))
                if not store.live:
                    raise ConnectionError("the store does not answer")
                if (self.credentials.access_key, self.credentials.secret_key) != store.root:
                    raise MinioAdminException("403", "the request under another root is quoted here")
                if method in store.failing:
                    store.failing.remove(method)
                    raise MinioAdminException("500", f"the request of {method} is quoted here: {quoted}")

            def policy_add(self, policy_name, policy_file):
                with open(policy_file, encoding="utf-8") as handle:
                    document = json.load(handle)
                store.policy_files.append(policy_file)
                self._call("policy_add", policy_name)
                store.policies[policy_name] = document

            def user_add(self, access_key, secret_key):
                store.secrets.add(secret_key)
                self._call("user_add", access_key, quoted=secret_key)
                store.users[access_key] = secret_key

            def policy_set(self, policy_name, user=None, group=None):
                self._call("policy_set", policy_name, user)
                if policy_name not in store.policies or user not in store.users:
                    raise MinioAdminException("400", "no such policy or user")
                store.user_policy[user] = policy_name

            def user_remove(self, access_key):
                self._call("user_remove", access_key)
                del store.users[access_key]
                store.user_policy.pop(access_key, None)

            def policy_remove(self, policy_name):
                self._call("policy_remove", policy_name)
                if policy_name in store.user_policy.values():
                    raise MinioAdminException("400", "the policy is in use")
                del store.policies[policy_name]

            def user_list(self):
                self._call("user_list")
                return json.dumps({access_key: {"status": "enabled", "policyName": store.user_policy.get(access_key, "")}
                                   for access_key in store.users})

            def policy_list(self):
                self._call("policy_list")
                return json.dumps(store.policies)

        return FakeMinioAdmin

    def minio(self):
        """A class in the place of minio.Minio, of this store. A client lists
        the buckets under the root pair and under a pair of `answers`; a
        user's pair is denied the listing; any other access key is
        unknown."""
        store = self

        class FakeMinio:
            def __init__(self, endpoint, access_key, secret_key, secure, http_client=None):
                self.endpoint = endpoint
                self.access_key = access_key
                self.secret_key = secret_key
                self.http_client = http_client

            def list_buckets(self):
                store.events.append(("s3", "list_buckets", self.access_key))
                if not store.live:
                    raise ConnectionError("the store does not answer")
                pair = (self.access_key, self.secret_key)
                if pair == store.root or pair in store.answers:
                    return []
                if self.access_key == store.root[0]:
                    raise s3_error("SignatureDoesNotMatch")
                if store.users.get(self.access_key) == self.secret_key or pair in store.denied:
                    raise s3_error("AccessDenied")
                raise s3_error("InvalidAccessKeyId")

        return FakeMinio

    def http(self):
        """A urllib3 pool in the place of store_access.bounded_http's: it
        answers the liveness path with 200 while the store is live and
        refuses the connection otherwise."""
        store = self

        class Response:
            status = 200

        class FakeHttp:
            def request(self, method, url):
                store.events.append(("http", method, url))
                if not store.live:
                    raise ConnectionError("connection refused")
                return Response()

        return FakeHttp()


class FakeDocker:
    """run_subprocess_status in the place of utils': it records each command
    in the store's events and in `runs` as (arguments, Environment or None),
    runs none, and moves the store as docker would. A run of the store's
    compose gives a store without a container one under the root pair of the
    run's environment; `up -d` of a store with a container puts it under
    that pair when `recreates_on_up`, and exits `up_code`, moving nothing
    when that is not 0; `--force-recreate` does when
    `force_recreates`, and exits `force_code`; `docker rm -f` of the store's
    container exits `rm_code` and, at 0, leaves the store without one. `docker
    image inspect` prints `labels`[reference] as JSON and exits 0, or exits 1
    for a reference `labels` lacks."""

    def __init__(self, store, recreates_on_up=True, force_recreates=True, force_code=0, rm_code=0, labels=None):
        self.store = store
        self.runs = []
        self.recreates_on_up = recreates_on_up
        self.force_recreates = force_recreates
        self.up_code = 0
        self.force_code = force_code
        self.rm_code = rm_code
        self.labels = labels or {}
        # Called with the arguments of each command before it is recorded.
        self.before = lambda args: None

    def __call__(self, args, logger, env=None):
        args = list(args)
        self.before(args)
        recorded = None if env is None else Environment(env)
        self.runs.append((args, recorded))
        self.store.events.append(("run", args, recorded))
        if args[0] == "docker-compose" and "swift-stream" in args:
            pair = tuple((env or {}).get(name) for name in STORE_ROOT_VARIABLES)
            self.store.secrets.add(pair[1])
            forced = "--force-recreate" in args
            if not forced and self.up_code != 0:
                return self.up_code, ""
            if self.store.root is None or (forced and self.force_recreates) or (not forced and self.recreates_on_up):
                self.store.root = pair
            return (self.force_code if forced else 0), ""
        if args[:3] == ["docker", "rm", "-f"] and args[3] == "etny-swift-stream":
            if self.rm_code == 0:
                self.store.root = None
            return self.rm_code, ""
        if args[:3] == ["docker", "image", "inspect"]:
            reference = args[-1]
            if reference not in self.labels:
                return 1, f"Error: No such image: {reference}\n"
            return 0, json.dumps(self.labels[reference]) + "\n"
        return 0, ""

    def compose_runs(self):
        """The arguments after the compose file of the runs of the store's
        compose, in order."""
        return [args[3:] for args, _ in self.runs if args[0] == "docker-compose" and "swift-stream" in args]


def install(monkeypatch, store, modules):
    """Puts the store's clients in the place of minio-py's in `modules`
    (store_access, and swift_stream_service when given), and its pool in the
    place of store_access.bounded_http's."""
    for module in modules:
        monkeypatch.setattr(module, "Minio", store.minio())
    monkeypatch.setattr(store_access, "MinioAdmin", store.minio_admin())
    monkeypatch.setattr(store_access, "bounded_http", store.http)


def recorded_modes(monkeypatch):
    """[('mkdir', path, mode)] of every os.mkdir and [('open', path, mode,
    'O_EXCL' or '')] of every os.open that creates a file, from here to the
    end of the test: the modes the agent asks for, and whether it asks for
    a file that is not there yet (os.O_EXCL). This workstation's file system
    keeps no POSIX mode: a file created there without the owner's write
    permission is read-only and is not removed by os.remove, so on it the
    file is created with that permission; the mode recorded is the one asked
    for."""
    modes = []
    real_open, real_mkdir = os.open, os.mkdir

    def recording_open(path, flags, mode=0o777, **kwargs):
        if flags & os.O_CREAT:
            modes.append(("open", str(path), mode, "O_EXCL" if flags & os.O_EXCL else ""))
        return real_open(path, flags, mode | 0o200 if os.name == "nt" else mode, **kwargs)

    def recording_mkdir(path, mode=0o777, **kwargs):
        modes.append(("mkdir", str(path), mode))
        return real_mkdir(path, mode, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "mkdir", recording_mkdir)
    return modes


def secrets_of(store):
    """The secret keys the store was given, its roots' and its users', but
    the literal-named user's, which is that user's public name."""
    return {secret for secret in store.secrets if secret and secret != store_access.LITERAL}


def assert_no_secret(store, records, argument_lists=()):
    """No secret key of `store` is in the message of a log record of
    `records` or in an argument of `argument_lists`. At least one secret is
    known, so the check is not empty."""
    held = secrets_of(store)
    assert held
    for record in records:
        message = record.getMessage()
        assert not any(secret in message for secret in held), record.name
    for arguments in argument_lists:
        assert not any(secret in str(argument) for secret in held for argument in arguments), arguments[:3]
