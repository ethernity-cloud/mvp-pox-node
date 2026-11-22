"""Which MinIO users an order or an integration test gets, and what each may
do (store_access.user_kinds, order_users, draw_user, trustedzone_policy,
securelock_policy, image_labelled): the plan is a function of whether the
compose has each enclave service and whether that service is planned as
labelled; a user reaches one bucket; an image is labelled by the label of
its role alone."""
import json
import re

import pytest

import store_access
from store_access import (LITERAL, SECURELOCK, TRUSTEDZONE, USER_INTEGRATION, USER_LITERAL, USER_SECURELOCK,
                          USER_TRUSTEDZONE)

BUCKET = "etny-pynithy-testnet-v3"
INTEGRATION_BUCKET = "etny-bucket-integration"

# The policies of the contract, section 5.3, with <B> for the bucket.
P_TZ = """
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow",
     "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:CreateBucket"],
     "Resource": ["arn:aws:s3:::<B>"]},
    {"Effect": "Allow",
     "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"],
     "Resource": ["arn:aws:s3:::<B>/*"]},
    {"Effect": "Deny", "Action": ["admin:*"]}
  ]
}
"""
P_SL = """
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow",
     "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"],
     "Resource": ["arn:aws:s3:::<B>"]},
    {"Effect": "Allow",
     "Action": ["s3:GetObject"],
     "Resource": ["arn:aws:s3:::<B>/*"]},
    {"Effect": "Allow",
     "Action": ["s3:PutObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"],
     "Resource": [
       "arn:aws:s3:::<B>/result.*",
       "arn:aws:s3:::<B>/result_code.*",
       "arn:aws:s3:::<B>/cert.pem",
       "arn:aws:s3:::<B>/esr.*",
       "arn:aws:s3:::<B>/state.*",
       "arn:aws:s3:::<B>/session.commit.*",
       "arn:aws:s3:::<B>/session.output.*",
       "arn:aws:s3:::<B>/session.ready*",
       "arn:aws:s3:::<B>/securelock.*"
     ]},
    {"Effect": "Deny", "Action": ["admin:*"]}
  ]
}
"""


def p_tz(bucket):
    return json.loads(P_TZ.replace("<B>", bucket))


def p_sl(bucket):
    return json.loads(P_SL.replace("<B>", bucket))


# The nine rows of the contract's table, section 5.4. A service is None when
# the compose lacks it, True when it is planned as labelled, False otherwise.
# T, S and L are the trustedzone's, the securelock's and the literal-named
# user: (trustedzone, securelock, users created, trustedzone's file,
# securelock's file).
ROWS = [
    (True, True, ["T", "S"], "T", "S"),
    (True, False, ["T", "L"], "T", "L"),
    (False, True, ["L", "S"], "L", "S"),
    (False, False, ["L"], "L", "L"),
    (True, None, ["T"], "T", None),
    (False, None, ["L"], "L", None),
    (None, True, ["S"], None, "S"),
    (None, False, ["L"], None, "L"),
    (None, None, [], None, None),
]
KIND = {"T": USER_TRUSTEDZONE, "S": USER_SECURELOCK, "L": USER_LITERAL, "G": USER_INTEGRATION, None: None}
ACCESS_KEY = {"T": r"ecldtz[0-9a-f]{14}", "S": r"ecldsl[0-9a-f]{14}", "G": r"ecldit[0-9a-f]{14}",
              "L": "swiftstreamadmin"}
POLICY = {"T": p_tz, "S": p_sl, "L": p_tz, "G": p_tz}


def letter(user):
    """T, S, G or L for a StoreUser, by its access key."""
    return next(name for name, pattern in ACCESS_KEY.items() if re.fullmatch(pattern, user.access_key))


@pytest.mark.parametrize("trustedzone, securelock, created, trustedzone_file, securelock_file", ROWS)
def test_the_plan_of_an_order(trustedzone, securelock, created, trustedzone_file, securelock_file):
    kinds = store_access.user_kinds(trustedzone, securelock)
    files = {TRUSTEDZONE: KIND[trustedzone_file], SECURELOCK: KIND[securelock_file]}
    assert kinds == {service: kind for service, kind in files.items() if kind is not None}
    assert list(kinds) == [service for service in (TRUSTEDZONE, SECURELOCK) if files[service] is not None]
    users = store_access.order_users(kinds, BUCKET)
    assert {service: letter(user) for service, user in users.items()} == {
        service: name for service, name in ((TRUSTEDZONE, trustedzone_file), (SECURELOCK, securelock_file))
        if name is not None}
    distinct = list({user.access_key: user for user in users.values()}.values())
    assert [letter(user) for user in distinct] == created
    for user in distinct:
        assert user.policy == POLICY[letter(user)](BUCKET)
        if letter(user) == "L":
            assert (user.access_key, user.secret_key) == (LITERAL, LITERAL) == ("swiftstreamadmin",) * 2
        else:
            assert re.fullmatch(r"[0-9a-f]{40}", user.secret_key)
        assert store_access.credential_in(store_access.credential_bytes(user.access_key, user.secret_key)) == (
            user.access_key, user.secret_key)


@pytest.mark.parametrize("trustedzone, securelock, kinds", [
    (True, None, {TRUSTEDZONE: USER_INTEGRATION}),
    (False, None, {TRUSTEDZONE: USER_LITERAL}),
    (True, True, {TRUSTEDZONE: USER_INTEGRATION}),
    (True, False, {TRUSTEDZONE: USER_INTEGRATION}),
    (False, True, {TRUSTEDZONE: USER_LITERAL}),
    (False, False, {TRUSTEDZONE: USER_LITERAL}),
    (None, True, {}),
    (None, False, {}),
    (None, None, {}),
])
def test_the_plan_of_an_integration_test(trustedzone, securelock, kinds):
    assert store_access.user_kinds(trustedzone, securelock, integration_test=True) == kinds
    users = store_access.order_users(kinds, INTEGRATION_BUCKET)
    assert list(users) == list(kinds)
    for user in users.values():
        assert letter(user) == ("G" if trustedzone else "L")
        assert user.policy == p_tz(INTEGRATION_BUCKET)


def test_two_unlabelled_services_share_the_one_literal_named_user():
    users = store_access.order_users(store_access.user_kinds(False, False), BUCKET)
    assert users[TRUSTEDZONE] is users[SECURELOCK]


def test_every_start_draws_its_users_anew():
    kinds = store_access.user_kinds(True, True)
    first, second = store_access.order_users(kinds, BUCKET), store_access.order_users(kinds, BUCKET)
    for service in (TRUSTEDZONE, SECURELOCK):
        assert first[service].access_key != second[service].access_key
        assert first[service].secret_key != second[service].secret_key
    assert first[TRUSTEDZONE].secret_key != first[SECURELOCK].secret_key


@pytest.mark.parametrize("bucket", [
    BUCKET, INTEGRATION_BUCKET, "etny-nodenithy-v3", "ecld-nodenithy-ethereum-sepolia-unsafe-v3", "abc", "a.b-c",
    "a" * 63,
])
def test_the_policies_are_the_contracts_for_the_bucket(bucket):
    assert store_access.trustedzone_policy(bucket) == p_tz(bucket)
    assert store_access.securelock_policy(bucket) == p_sl(bucket)
    assert json.loads(json.dumps(store_access.securelock_policy(bucket))) == p_sl(bucket)


def test_a_policy_names_its_bucket_alone():
    for policy in (store_access.trustedzone_policy(BUCKET), store_access.securelock_policy(BUCKET)):
        resources = [resource for statement in policy["Statement"] for resource in statement.get("Resource", [])]
        assert resources and all(resource == f"arn:aws:s3:::{BUCKET}" or resource.startswith(f"arn:aws:s3:::{BUCKET}/")
                                 for resource in resources)
        assert all("?" not in resource for resource in resources)
        assert policy["Statement"][-1] == {"Effect": "Deny", "Action": ["admin:*"]}
    securelock = store_access.securelock_policy(BUCKET)
    actions = [action for statement in securelock["Statement"] for action in statement["Action"]]
    assert "s3:DeleteObject" not in actions and "s3:CreateBucket" not in actions
    puts = [statement for statement in securelock["Statement"] if "s3:PutObject" in statement["Action"]]
    assert len(puts) == 1 and f"arn:aws:s3:::{BUCKET}/*" not in puts[0]["Resource"]


@pytest.mark.parametrize("bucket", [
    "etny-*-v3", "*", "etny-pynithy-v?", "etny_pynithy-v3", "Etny-pynithy-v3", "ab", "a" * 64, "-etny-v3", "etny-v3-",
    ".etny-v3", "etny/v3", "etny-v3/*", "etny v3", "", None, 3,
])
def test_a_bucket_name_outside_its_pattern_builds_no_policy(bucket):
    for build in (store_access.trustedzone_policy, store_access.securelock_policy):
        with pytest.raises(store_access.BucketRefused):
            build(bucket)
    for kind in (USER_TRUSTEDZONE, USER_SECURELOCK, USER_INTEGRATION, USER_LITERAL):
        with pytest.raises(store_access.BucketRefused):
            store_access.draw_user(kind, bucket)


class Docker:
    """`docker image inspect` in the place of utils.run_subprocess_status:
    it answers `end`, an (exit code, output), or raises it."""

    def __init__(self, end):
        self.end = end
        self.runs = []

    def __call__(self, args, logger):
        self.runs.append(list(args))
        if isinstance(self.end, Exception):
            raise self.end
        return self.end


REFERENCE = "localhost:5000/etny-trustedzone:latest"
TZ_LABEL, SL_LABEL = "cloud.ethernity.store", "cloud.ethernity.securelock.store"


def test_the_label_keys_are_one_per_role():
    assert store_access.STORE_LABELS == {TRUSTEDZONE: TZ_LABEL, SECURELOCK: SL_LABEL}
    assert store_access.STORE_LABEL_FILE == "file"


@pytest.mark.parametrize("output, trustedzone, securelock", [
    ('{"cloud.ethernity.store":"file"}\n', True, False),
    ('{"cloud.ethernity.securelock.store":"file"}\n', False, True),
    ('{"cloud.ethernity.store":"file","cloud.ethernity.securelock.store":"file","maintainer":"x"}', True, True),
    ('{"cloud.ethernity.store":"file","cloud.ethernity.securelock.store":"literal"}', True, False),
    ("null\n", False, False),
    ("{}\n", False, False),
    ('{"cloud.ethernity.store":"File","cloud.ethernity.securelock.store":" file"}', False, False),
    ('{"cloud.ethernity.store":"file ","cloud.ethernity.securelock.store":"true"}', False, False),
    ('{"cloud.ethernity.store":true,"cloud.ethernity.securelock.store":["file"]}', False, False),
    ('{"Cloud.Ethernity.Store":"file"}', False, False),
    ('["cloud.ethernity.store","file"]', False, False),
    ('"file"', False, False),
    ("", False, False),
    ("WARNING: something\n{\"cloud.ethernity.store\":\"file\"}\n", False, False),
])
def test_an_image_is_labelled_by_the_label_of_its_role_alone(output, trustedzone, securelock):
    docker = Docker((0, output))
    assert store_access.image_labelled(REFERENCE, TZ_LABEL, docker, None) is trustedzone
    assert store_access.image_labelled(REFERENCE, SL_LABEL, docker, None) is securelock
    assert docker.runs == [["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", REFERENCE]] * 2


@pytest.mark.parametrize("end", [
    (1, "Error: No such image: localhost:5000/etny-trustedzone:latest\n"),
    (1, '{"cloud.ethernity.store":"file"}\n'),
    (125, ""),
    FileNotFoundError("docker"),
    OSError("no docker"),
    ValueError("an answer that is no pair"),
])
def test_a_label_read_that_fails_is_an_unlabelled_image(end):
    docker = Docker(end)
    assert store_access.image_labelled(REFERENCE, TZ_LABEL, docker, None) is False
    assert len(docker.runs) == 1


def test_a_service_without_an_image_is_unlabelled_and_nothing_is_run():
    docker = Docker((0, '{"cloud.ethernity.store":"file"}'))
    assert store_access.image_labelled(None, TZ_LABEL, docker, None) is False
    assert docker.runs == []
