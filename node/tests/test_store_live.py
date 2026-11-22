"""What the users the agent makes can and cannot do on a real MinIO, through
the agent's own code (store_access) and minio-py: the cases of the store
credential's contract, section 6 (A1 to A5, N1 to N8).

Skipped unless ECLD_TEST_STORE_ENDPOINT names host:port of a MinIO
RELEASE.2023-03-13T19-46-17Z, with its root credential in
ECLD_TEST_STORE_ROOT_USER and ECLD_TEST_STORE_ROOT_PASSWORD. The store must
be one that serves no node: every test removes all of its users, the
policies named ecld-*, and the buckets B, X and I below."""
import io
import logging
import os

import pytest
from minio import Minio, MinioAdmin
from minio.credentials import StaticProvider
from minio.error import MinioAdminException, S3Error

import store_access
from field_composes import SDK_JS_NODENITHY, copy_of, sdk_js_published
from store_access import LITERAL, SECURELOCK, TRUSTEDZONE, USER_LITERAL
from store_fakes import recorded_modes

ENDPOINT = os.environ.get("ECLD_TEST_STORE_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="ECLD_TEST_STORE_ENDPOINT names no MinIO")

# B is an order's bucket, X another order's, I the integration bucket.
B = "ecld-store-test-v3"
X = "other-v3"
I = "etny-bucket-integration"
MADE = "ecld-store-test-made"
PART = 5 * 1024 * 1024
# Every name the published securelocks put (the contract's table of 5.3).
SECURELOCK_NAMES = [
    "result.txt", "result.txt.securelock", "result.txt.sig.securelock", "result.trustedzone",
    "result.trustedzone.sig", "result_code.txt.securelock", "result_code.txt.sig.securelock",
    "result_code.trustedzone", "result_code.trustedzone.sig", "cert.pem", "esr.authorizations.json",
    "esr.commit.0123456789abcdef.1.json", "state.key.enc", "state.key.cid", "state.fence.0123456789abcdef.enc",
    "state.fence.0123456789abcdef.cid", "session.commit.0.0123456789abcdef.1.json", "session.output.0.securelock",
    "session.output.0.sig.securelock", "session.output.0", "session.output.0.sig", "session.ready.securelock",
    "session.ready.sig.securelock", "session.ready", "session.ready.sig", "securelock.heartbeat.securelock",
    "securelock.ready.securelock", "securelock.ready.sig.securelock", "securelock.heartbeat", "securelock.ready",
    "securelock.ready.sig",
]
# Names the securelock's user may not put (the contract's N5).
KEPT_FROM_THE_SECURELOCK = [
    ".env", "payload.etny", "input.txt", "transaction.txt", "payload.etny.securelock", "payload.securelock",
    "input.txt.securelock", "task.securelock", "caller.securelock", "session.config.securelock", "session.input.0",
    "session.input.0.securelock", "session.control.securelock", "session.fence.securelock", "session.replay.0",
    "session.wallet.proof", "payload.etny.links", "x.links",
]


def root_pair():
    return os.environ["ECLD_TEST_STORE_ROOT_USER"], os.environ["ECLD_TEST_STORE_ROOT_PASSWORD"]


def located(access_key, secret_key):
    """An S3 client with a region, which asks no bucket's location: its
    first request on a bucket is the call itself."""
    return Minio(endpoint=ENDPOINT, access_key=access_key, secret_key=secret_key, secure=False, region="us-east-1",
                 http_client=store_access.bounded_http())


def put(client, bucket, name, data=b"x"):
    client.put_object(bucket_name=bucket, object_name=name, data=io.BytesIO(data), length=len(data))


def get(client, bucket, name):
    response = client.get_object(bucket_name=bucket, object_name=name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def s3_code(call):
    """The code of the S3 error `call` ends in."""
    with pytest.raises(S3Error) as failure:
        call()
    return failure.value.code


def admin_failure(call):
    """The text of the admin error `call` ends in."""
    with pytest.raises(MinioAdminException) as failure:
        call()
    return str(failure.value)


def empty(root, bucket):
    """Removes the bucket `bucket` and its objects, when it is there."""
    if not root.bucket_exists(bucket_name=bucket):
        return
    for found in root.list_objects(bucket_name=bucket, recursive=True):
        root.remove_object(bucket_name=bucket, object_name=found.object_name)
    root.remove_bucket(bucket_name=bucket)


class Staged:
    """A store swept by the agent's code, holding the buckets B, X and I
    with a `.env` each, the root's clients, and the users the agent's code
    makes: T (the trustedzone's of B), S (the securelock's of B), L (the
    literal-named user of B) and G (the integration test's of I)."""

    def __init__(self, credential_dir):
        self.credential_dir = str(credential_dir)
        self.admin = store_access.StoreAdmin(ENDPOINT, *root_pair())
        self.root = located(*root_pair())
        store_access.sweep(self.admin, self.credential_dir)
        for bucket in (B, X, I, MADE):
            empty(self.root, bucket)
        for bucket in (B, X, I):
            self.root.make_bucket(bucket_name=bucket)
            put(self.root, bucket, ".env", b"ETNY_MODE=TEST\n")

    def make_users(self, literal=True):
        order = store_access.order_users(store_access.user_kinds(True, True), B)
        self.T, self.S = order[TRUSTEDZONE], order[SECURELOCK]
        self.G = store_access.order_users(store_access.user_kinds(True, None, True), I)[TRUSTEDZONE]
        self.L = store_access.draw_user(USER_LITERAL, B)
        users = [self.T, self.S, self.G] + ([self.L] if literal else [])
        store_access.create_users(self.admin, users)
        return users

    def close(self):
        store_access.sweep(self.admin, self.credential_dir)
        for bucket in (B, X, I, MADE):
            empty(self.root, bucket)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    # On a Windows workstation a credential file is created removable
    # (store_fakes.recorded_modes).
    recorded_modes(monkeypatch)
    store = Staged(tmp_path / "ecld-store")
    yield store
    store.close()


def of(user):
    return located(user.access_key, user.secret_key)


def test_a1_a2_each_user_reaches_its_bucket_and_an_absent_object_is_not_found(staged):
    staged.make_users()
    for user, bucket in ((staged.T, B), (staged.S, B), (staged.L, B), (staged.G, I)):
        # A client without a region asks the bucket's location before its
        # first call on it, then the HEAD of the bucket.
        fresh = store_access.s3_client(ENDPOINT, user.access_key, user.secret_key)
        assert fresh.bucket_exists(bucket_name=bucket) is True
        assert of(user).bucket_exists(bucket_name=bucket) is True
        assert s3_code(lambda: of(user).stat_object(bucket_name=bucket, object_name="absent")) == "NoSuchKey"
        assert s3_code(lambda: get(of(user), bucket, "absent")) == "NoSuchKey"


def test_a3_a_trustedzones_user_puts_gets_stats_and_removes_any_object(staged):
    staged.make_users()
    for user, bucket in ((staged.T, B), (staged.L, B), (staged.G, I)):
        client = of(user)
        for name in ("any.name", "payload.etny.securelock", "x.links"):
            put(client, bucket, name, b"written")
            assert get(client, bucket, name) == b"written"
            assert client.stat_object(bucket_name=bucket, object_name=name).size == 7
            client.remove_object(bucket_name=bucket, object_name=name)
            assert s3_code(lambda: client.stat_object(bucket_name=bucket, object_name=name)) == "NoSuchKey"
        assert get(client, bucket, ".env") == b"ETNY_MODE=TEST\n"


def test_a4_the_securelocks_user_reads_every_object_and_puts_its_own_names(staged):
    staged.make_users()
    client = of(staged.S)
    put(staged.root, B, "payload.etny.securelock", b"forwarded")
    for name in (".env", "payload.etny.securelock"):
        assert client.stat_object(bucket_name=B, object_name=name).size > 0
        assert get(client, B, name) in (b"ETNY_MODE=TEST\n", b"forwarded")
    for name in SECURELOCK_NAMES:
        put(client, B, name, b"own")
        assert get(of(staged.T), B, name) == b"own"


def test_a5_an_object_of_two_parts_goes_through_minio_pys_multipart_upload(staged):
    staged.make_users()
    data = bytes(PART + 1024 * 1024)
    for user in (staged.T, staged.S):
        put(of(user), B, "result.txt", data)
        assert staged.root.stat_object(bucket_name=B, object_name="result.txt").size == len(data)
        staged.root.remove_object(bucket_name=B, object_name="result.txt")


def test_n1_a_user_lists_its_own_bucket_alone_and_is_refused_while_it_does_not_exist(staged):
    staged.make_users()
    for users, bucket in (((staged.T, staged.S, staged.L), B), ((staged.G,), I)):
        for user in users:
            # A client with a region, and the agent's, which has none.
            for client in (of(user), store_access.s3_client(ENDPOINT, user.access_key, user.secret_key)):
                assert [found.name for found in client.list_buckets()] == [bucket]
        empty(staged.root, bucket)
        for user in users:
            for client in (of(user), store_access.s3_client(ENDPOINT, user.access_key, user.secret_key)):
                assert s3_code(lambda: client.list_buckets()) == "AccessDenied"
    assert staged.root.bucket_exists(bucket_name=X)


def test_n2_no_user_reaches_another_bucket(staged):
    staged.make_users()
    others = [(staged.T, (X, I)), (staged.S, (X, I)), (staged.L, (X, I)), (staged.G, (B, X))]
    for user, buckets in others:
        for bucket in buckets:
            client = of(user)
            fresh = store_access.s3_client(ENDPOINT, user.access_key, user.secret_key)
            assert s3_code(lambda: fresh.bucket_exists(bucket_name=bucket)) == "AccessDenied"
            assert s3_code(lambda: client.bucket_exists(bucket_name=bucket)) == "AccessDenied"
            assert s3_code(lambda: list(client.list_objects(bucket_name=bucket))) == "AccessDenied"
            assert s3_code(lambda: get(client, bucket, ".env")) == "AccessDenied"
            assert s3_code(lambda: client.stat_object(bucket_name=bucket, object_name=".env")) == "AccessDenied"
            assert s3_code(lambda: put(client, bucket, ".env", b"ETNY_RUN_INTEGRATION_TEST=1\n")) == "AccessDenied"
            assert s3_code(lambda: client.remove_object(bucket_name=bucket, object_name=".env")) == "AccessDenied"
            assert get(staged.root, bucket, ".env") == b"ETNY_MODE=TEST\n"


def test_n3_no_user_makes_another_bucket_or_removes_its_own(staged):
    for user in staged.make_users():
        client = of(user)
        assert s3_code(lambda: client.make_bucket(bucket_name=MADE)) == "AccessDenied"
        for bucket in (B, I):
            assert s3_code(lambda: client.remove_bucket(bucket_name=bucket)) == "AccessDenied"
    assert s3_code(lambda: of(staged.S).make_bucket(bucket_name=B)) == "AccessDenied"
    assert not staged.root.bucket_exists(bucket_name=MADE)
    assert staged.root.bucket_exists(bucket_name=B) and staged.root.bucket_exists(bucket_name=I)


def test_n4_no_user_administers_the_store(staged):
    users = staged.make_users()
    for user in users:
        admin = store_access.StoreAdmin(ENDPOINT, user.access_key, user.secret_key)
        raw = MinioAdmin(endpoint=ENDPOINT, credentials=StaticProvider(user.access_key, user.secret_key),
                         secure=False, http_client=store_access.bounded_http())
        refused = [
            lambda: admin.users(),
            lambda: admin.policies(),
            lambda: admin.add_user("ecldxx0123456789abcd", "0" * 40),
            lambda: admin.add_policy("ecld-ecldxx0123456789abcd", store_access.trustedzone_policy(X)),
            lambda: admin.set_policy("readwrite", user.access_key),
            lambda: admin.remove_user(user.access_key),
            lambda: raw.info(),
            lambda: raw.add_service_account(access_key="ecldsa0123456789abcd", secret_key="1" * 40),
        ]
        for call in refused:
            assert "Status: 403" in admin_failure(call)
    assert staged.admin.users() == sorted(user.access_key for user in users)
    assert "ecld-ecldxx0123456789abcd" not in staged.admin.policies()
    for user, bucket in ((staged.T, X), (staged.S, X), (staged.L, X), (staged.G, B)):
        assert s3_code(lambda: get(of(user), bucket, ".env")) == "AccessDenied"


def test_n5_the_securelocks_user_removes_nothing_and_puts_no_name_but_its_own(staged):
    staged.make_users()
    client = of(staged.S)
    put(staged.root, B, "result.txt", b"the trustedzone's")
    for name in (".env", "result.txt", "absent"):
        assert s3_code(lambda: client.remove_object(bucket_name=B, object_name=name)) == "AccessDenied"
    two_parts = bytes(PART + 1)
    for name in KEPT_FROM_THE_SECURELOCK:
        assert s3_code(lambda: put(client, B, name, b"the securelock's")) == "AccessDenied"
        assert s3_code(lambda: put(client, B, name, two_parts)) == "AccessDenied"
    assert get(staged.root, B, ".env") == b"ETNY_MODE=TEST\n"
    assert sorted(found.object_name for found in staged.root.list_objects(bucket_name=B, recursive=True)) == [
        ".env", "result.txt"]


def assert_unknown(access_key, secret_key):
    """Every S3 and admin call under the pair ends in InvalidAccessKeyId.
    The answer to a HEAD carries no error body, and minio-py names every
    403 of one AccessDenied."""
    client = located(access_key, secret_key)
    assert store_access.list_outcome(store_access.s3_client(ENDPOINT, access_key, secret_key)) == store_access.REFUSED
    calls = [
        lambda: client.list_buckets(),
        lambda: get(client, B, ".env"),
        lambda: put(client, B, "result.txt", b"x"),
        lambda: client.remove_object(bucket_name=B, object_name=".env"),
        lambda: client.make_bucket(bucket_name=MADE),
        lambda: list(client.list_objects(bucket_name=B)),
    ]
    for call in calls:
        assert s3_code(call) == "InvalidAccessKeyId"
    heads = [
        lambda: client.bucket_exists(bucket_name=B),
        lambda: client.stat_object(bucket_name=B, object_name=".env"),
    ]
    for call in heads:
        assert s3_code(call) == "AccessDenied"
    admin = store_access.StoreAdmin(ENDPOINT, access_key, secret_key)
    for call in (lambda: admin.users(), lambda: admin.policies(),
                 lambda: admin.add_user("ecldxx0123456789abcd", "0" * 40)):
        assert "InvalidAccessKeyId" in admin_failure(call)


def test_n6_after_the_sweep_no_user_is_known(staged):
    users = staged.make_users()
    store_access.sweep(staged.admin, staged.credential_dir)
    assert staged.admin.users() == []
    assert [name for name in staged.admin.policies() if name.startswith("ecld-")] == []
    for user in users:
        assert_unknown(user.access_key, user.secret_key)
    assert get(staged.root, B, ".env") == b"ETNY_MODE=TEST\n"


def test_n7_on_a_store_at_rest_the_literal_pair_and_the_built_in_root_are_unknown(staged):
    assert_unknown(LITERAL, LITERAL)
    assert_unknown("minioadmin", "minioadmin")


def test_n8_during_an_order_of_two_labelled_images_the_literal_pair_is_unknown(staged):
    staged.make_users(literal=False)
    assert_unknown(LITERAL, LITERAL)
    assert of(staged.T).bucket_exists(bucket_name=B) is True


def labels_of(labelled):
    """`docker image inspect` in the place of utils.run_subprocess_status,
    for images that carry both labels (`labelled`) or none."""
    labels = '{"cloud.ethernity.store":"file","cloud.ethernity.securelock.store":"file"}' if labelled else "null"
    return lambda args, logger: (0, labels + "\n")


@pytest.mark.parametrize("labelled", [True, False])
def test_the_store_accepts_the_credential_of_each_file_the_agent_writes(staged, tmp_path, labelled):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(sdk_js_published(copy_of(SDK_JS_NODENITHY), True, False))
    store_access.prepare(str(compose), B, False, staged.admin, staged.credential_dir, logging.getLogger("live"),
                         labels_of(labelled))
    pairs = {}
    for service in (TRUSTEDZONE, SECURELOCK):
        with open(os.path.join(staged.credential_dir, service, "store.json"), "rb") as handle:
            pairs[service] = store_access.credential_in(handle.read())
    if labelled:
        assert pairs[TRUSTEDZONE][0].startswith("ecldtz") and pairs[SECURELOCK][0].startswith("ecldsl")
        assert staged.admin.users() == sorted(pair[0] for pair in pairs.values())
    else:
        assert pairs == {TRUSTEDZONE: (LITERAL, LITERAL), SECURELOCK: (LITERAL, LITERAL)}
        assert staged.admin.users() == [LITERAL]
    for service, pair in pairs.items():
        client = store_access.s3_client(ENDPOINT, *pair)
        assert client.bucket_exists(bucket_name=B) is True
        assert get(client, B, ".env") == b"ETNY_MODE=TEST\n"
        put(client, B, "result.txt", service.encode())
        assert s3_code(lambda: get(located(*pair), X, ".env")) == "AccessDenied"
        assert s3_code(lambda: get(located(*pair), I, ".env")) == "AccessDenied"
    store_access.sweep(staged.admin, staged.credential_dir)
    for pair in pairs.values():
        assert_unknown(*pair)
    assert os.listdir(os.path.join(staged.credential_dir, TRUSTEDZONE)) == []


def test_an_integration_tests_user_reaches_the_integration_bucket_alone(staged, tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(sdk_js_published(copy_of(SDK_JS_NODENITHY), True, False))
    store_access.prepare(str(compose), I, True, staged.admin, staged.credential_dir, logging.getLogger("live"),
                         labels_of(True))
    with open(os.path.join(staged.credential_dir, TRUSTEDZONE, "store.json"), "rb") as handle:
        pair = store_access.credential_in(handle.read())
    assert pair[0].startswith("ecldit") and staged.admin.users() == [pair[0]]
    assert not os.path.exists(os.path.join(staged.credential_dir, SECURELOCK))
    client = located(*pair)
    put(client, I, "context_test.etny", b"1")
    client.remove_object(bucket_name=I, object_name="context_test.etny")
    assert s3_code(lambda: get(client, B, ".env")) == "AccessDenied"
    assert s3_code(lambda: client.make_bucket(bucket_name=MADE)) == "AccessDenied"
    assert s3_code(lambda: put(client, B, "cert.pem", b"x")) == "AccessDenied"
