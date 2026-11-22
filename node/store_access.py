"""
The credentials of the node's object store (MinIO, the container
etny-swift-stream), through which the enclaves of a task exchange their
files.

- The root credential is drawn once per node and kept in a file of the
  agent's (root_credential). The store's compose gets it in the environment
  of its own runs alone (store_compose_env). The agent's S3 client and its
  admin client (StoreAdmin) use it; no enclave does.
- An order or an integration test gets MinIO users of its own for the one
  bucket it works in (user_kinds, order_users, create_users), each with one
  policy (trustedzone_policy, securelock_policy). The credential of the user
  of an enclave service is written to a file (write_credential) in a
  directory mounted read-only into that service's container
  (credential_volume). An enclave image that declares by its label that it
  reads the file (image_labelled) gets a user of a random name; any other
  gets the user named LITERAL, the pair such an image holds.
- prepare runs these steps for one start of the enclave environment; sweep
  removes every user, every policy of the agent's and every credential file.
"""
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from collections import namedtuple

import urllib3
from minio import Minio, MinioAdmin
from minio.credentials import StaticProvider
from minio.error import S3Error

from utils import STORE_ROOT_VARIABLES, compose_has_service, compose_service_image, compose_with_volume

# The access key and the secret key of the user an enclave image without the
# label gets: the pair such an image holds and uses.
LITERAL = 'swiftstreamadmin'
# The compose services of the two enclaves (the container names every
# published compose uses).
TRUSTEDZONE = 'etny-trustedzone'
SECURELOCK = 'etny-securelock'
ENCLAVE_SERVICES = (TRUSTEDZONE, SECURELOCK)
# The image label by which the image of an enclave service declares that it
# reads the credential file, per service, and the value that declares it.
STORE_LABELS = {TRUSTEDZONE: 'cloud.ethernity.store', SECURELOCK: 'cloud.ethernity.securelock.store'}
STORE_LABEL_FILE = 'file'
# The argument of `docker image inspect --format` that prints an image's
# labels as one JSON value.
LABELS_FORMAT = '{{json .Config.Labels}}'

# The host directory that holds one directory per enclave service, each with
# that service's credential file as its only entry, and the directory of the
# container the service's directory is mounted on, read-only.
CREDENTIAL_DIR = '/run/ecld-store'
CREDENTIAL_FILE = 'store.json'
CREDENTIAL_MOUNT = '/run/ecld'
# A file of more bytes than this is not a credential.
CREDENTIAL_MAX_BYTES = 4096
# A whole credential file: one JSON object of the members accessKey and
# secretKey, in that order, each a string of ASCII letters and digits, with
# JSON white space between the tokens (the pattern of the enclaves' readers).
CREDENTIAL = re.compile(
    rb'[ \t\r\n]*\{[ \t\r\n]*"accessKey"[ \t\r\n]*:[ \t\r\n]*"([A-Za-z0-9]{3,20})"[ \t\r\n]*,'
    rb'[ \t\r\n]*"secretKey"[ \t\r\n]*:[ \t\r\n]*"([A-Za-z0-9]{8,40})"[ \t\r\n]*\}[ \t\r\n]*')

# The access key of the node's root credential begins with ROOT_PREFIX.
ROOT_PREFIX = 'ecldroot'
# The kinds of user and the prefix of the access key of each kind but
# USER_LITERAL, whose access key is LITERAL.
USER_TRUSTEDZONE = 'trustedzone'
USER_SECURELOCK = 'securelock'
USER_INTEGRATION = 'integration'
USER_LITERAL = 'literal'
ACCESS_KEY_PREFIXES = {USER_TRUSTEDZONE: 'ecldtz', USER_SECURELOCK: 'ecldsl', USER_INTEGRATION: 'ecldit'}
# The policy of a user is named POLICY_PREFIX + its access key.
POLICY_PREFIX = 'ecld-'
# A bucket a policy is built for: no character a policy resource reads as a
# wildcard (`*`, `?`).
BUCKET = re.compile(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]')
# The object names the securelock's user may put, as policy resource
# patterns under the bucket: every name the published securelocks write.
SECURELOCK_WRITES = ('result.*', 'result_code.*', 'cert.pem', 'esr.*', 'state.*', 'session.commit.*',
                     'session.output.*', 'session.ready*', 'securelock.*')

# The connect and the read timeout, in seconds, of every HTTP call of the
# admin client, of the checks of who lists the store's buckets and of the
# liveness wait; none is retried.
HTTP_TIMEOUT_SECONDS = 10
# The store's liveness path, and how long await_live asks it, once a second.
LIVE_PATH = '/minio/health/live'
LIVE_WAIT_SECONDS = 60
# list_outcome of a client that listed the store's buckets, and of one whose
# access key the store does not know.
LISTED = 'listed'
REFUSED = 'S3Error InvalidAccessKeyId'

# A MinIO user of an order or a test: its keys and its policy document.
StoreUser = namedtuple('StoreUser', ['access_key', 'secret_key', 'policy'])


class BucketRefused(ValueError):
    """A policy is asked for a bucket whose name BUCKET does not match."""


class NotPrepared(Exception):
    """prepare did not complete; str() is the step that failed (O2 to O5)
    and the class of its error."""


def error_text(error):
    """How an error of the store's S3 or admin client is logged: its class
    and, for an S3 error, its code. Never its message, which quotes the
    request."""
    name = type(error).__name__
    return f'{name} {error.code}' if isinstance(error, S3Error) else name


def credential_bytes(access_key, secret_key):
    """The bytes of a credential file for the two keys:
    {"accessKey":"<access key>","secretKey":"<secret key>"} and one line
    feed, in ASCII. Raises ValueError for a key outside its pattern
    (CREDENTIAL)."""
    data = f'{{"accessKey":"{access_key}","secretKey":"{secret_key}"}}\n'.encode('ascii')
    if CREDENTIAL.fullmatch(data) is None:
        raise ValueError('a key is outside its pattern')
    return data


def credential_in(data):
    """(access key, secret key) of the bytes `data` of a credential file;
    None when they are no credential (CREDENTIAL, CREDENTIAL_MAX_BYTES)."""
    found = CREDENTIAL.fullmatch(data) if len(data) <= CREDENTIAL_MAX_BYTES else None
    return None if found is None else (found.group(1).decode('ascii'), found.group(2).decode('ascii'))


def _write_new(path, data, mode):
    """Writes `data` to the file `path`: to `path`.tmp, created exclusively
    with the mode `mode` (one left there is removed first), then renamed
    over `path`, so that no reader finds the file half written."""
    partial = f'{path}.tmp'
    try:
        os.remove(partial)
    except FileNotFoundError:
        pass
    with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), 'wb') as handle:
        handle.write(data)
    os.replace(partial, path)


def root_credential(path):
    """(access key, secret key) of the node's root credential, kept in the
    file `path` in the format of a credential file with the mode 0600. A file
    that holds no credential whose access key begins with ROOT_PREFIX, or no
    file, is replaced by a credential drawn from the operating system's
    random source: ROOT_PREFIX and 12 hexadecimal digits, and 40 hexadecimal
    digits. The directory of `path` is created when it is missing, and no
    other."""
    path = str(path)
    try:
        with open(path, 'rb') as handle:
            held = credential_in(handle.read(CREDENTIAL_MAX_BYTES + 1))
    except FileNotFoundError:
        held = None
    if held is not None and held[0].startswith(ROOT_PREFIX):
        return held
    drawn = (ROOT_PREFIX + secrets.token_hex(6), secrets.token_hex(20))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_new(path, credential_bytes(*drawn), 0o600)
    return drawn


def store_compose_env(root):
    """The environment of a docker-compose run on the store's compose: a
    copy of the process's own with the root credential `root` (access key,
    secret key) under the STORE_ROOT_VARIABLES, which that compose names.
    os.environ never holds them: every docker-compose run on an order's
    compose gets the process's environment."""
    return {**os.environ, **dict(zip(STORE_ROOT_VARIABLES, root))}


def bounded_http():
    """A urllib3 pool whose calls wait HTTP_TIMEOUT_SECONDS for the
    connection and for the answer and are not retried. minio-py's own waits
    five minutes and retries five times."""
    return urllib3.PoolManager(timeout=urllib3.Timeout(connect=HTTP_TIMEOUT_SECONDS, read=HTTP_TIMEOUT_SECONDS),
                               retries=False)


def await_live(endpoint, http, sleep=time.sleep, now=time.monotonic):
    """Whether the store at `endpoint` (host:port) answers 200 on LIVE_PATH,
    asked through `http` once a second for at most LIVE_WAIT_SECONDS."""
    deadline = now() + LIVE_WAIT_SECONDS
    while True:
        try:
            if http.request('GET', f'http://{endpoint}{LIVE_PATH}').status == 200:
                return True
        except Exception:
            pass
        if now() >= deadline:
            return False
        sleep(1)


def s3_client(endpoint, access_key, secret_key):
    """An S3 client of the store at `endpoint` under the given keys, over
    bounded_http."""
    return Minio(endpoint=endpoint, access_key=access_key, secret_key=secret_key, secure=False,
                 http_client=bounded_http())


def list_outcome(client):
    """LISTED when the S3 client `client` lists the store's buckets, else
    error_text of what the call raised: REFUSED for an access key the store
    does not know."""
    try:
        client.list_buckets()
    except Exception as error:
        return error_text(error)
    return LISTED


class StoreAdmin:
    """MinIO's admin API as the agent uses it, through minio-py's MinioAdmin
    over bounded_http, with the commands RELEASE.2023-03-13 serves:
    add-canned-policy, add-user, set-user-or-group-policy, remove-user,
    remove-canned-policy, list-users and list-canned-policies."""

    def __init__(self, endpoint, access_key, secret_key):
        self._admin = MinioAdmin(endpoint=endpoint, credentials=StaticProvider(access_key, secret_key),
                                 secure=False, http_client=bounded_http())

    def add_policy(self, name, document):
        """Adds the policy `name` with the document `document` (a dict).
        MinioAdmin.policy_add takes the path of a file: the document is
        written to a temporary file, removed after the call."""
        handle, path = tempfile.mkstemp(prefix='ecld-policy-', suffix='.json')
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as policy_file:
                json.dump(document, policy_file)
            self._admin.policy_add(name, path)
        finally:
            os.remove(path)

    def add_user(self, access_key, secret_key):
        self._admin.user_add(access_key, secret_key)

    def set_policy(self, name, access_key):
        """Sets the policy `name` on the user `access_key`."""
        self._admin.policy_set(name, user=access_key)

    def remove_user(self, access_key):
        self._admin.user_remove(access_key)

    def remove_policy(self, name):
        self._admin.policy_remove(name)

    def users(self):
        """The access keys of the store's users, sorted."""
        return self._names(self._admin.user_list())

    def policies(self):
        """The names of the store's policies, sorted."""
        return self._names(self._admin.policy_list())

    @staticmethod
    def _names(listing):
        """The member names of the JSON object `listing`, the form of
        MinIO's list of users and of policies, sorted."""
        entries = json.loads(listing)
        if not isinstance(entries, dict):
            raise ValueError('the listing is no JSON object')
        return sorted(entries)


def _bucket_arn(bucket):
    """The ARN of the bucket `bucket`; raises BucketRefused for a name
    BUCKET does not match."""
    if not isinstance(bucket, str) or BUCKET.fullmatch(bucket) is None:
        raise BucketRefused('the bucket name is outside its pattern')
    return f'arn:aws:s3:::{bucket}'


def trustedzone_policy(bucket):
    """The policy document of a trustedzone's user for `bucket`: everything
    the trustedzones' clients call inside that bucket, the multipart calls
    included, nothing on any other bucket and nothing of the admin API."""
    arn = _bucket_arn(bucket)
    return {
        'Version': '2012-10-17',
        'Statement': [
            {'Effect': 'Allow',
             'Action': ['s3:GetBucketLocation', 's3:ListBucket', 's3:ListBucketMultipartUploads', 's3:CreateBucket'],
             'Resource': [arn]},
            {'Effect': 'Allow',
             'Action': ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListMultipartUploadParts',
                        's3:AbortMultipartUpload'],
             'Resource': [f'{arn}/*']},
            {'Effect': 'Deny', 'Action': ['admin:*']},
        ],
    }


def securelock_policy(bucket):
    """The policy document of a securelock's user for `bucket`: it reads
    every object of that bucket, puts the names of SECURELOCK_WRITES alone,
    removes nothing, creates no bucket, and has nothing on any other bucket
    and nothing of the admin API."""
    arn = _bucket_arn(bucket)
    return {
        'Version': '2012-10-17',
        'Statement': [
            {'Effect': 'Allow',
             'Action': ['s3:GetBucketLocation', 's3:ListBucket', 's3:ListBucketMultipartUploads'],
             'Resource': [arn]},
            {'Effect': 'Allow',
             'Action': ['s3:GetObject'],
             'Resource': [f'{arn}/*']},
            {'Effect': 'Allow',
             'Action': ['s3:PutObject', 's3:ListMultipartUploadParts', 's3:AbortMultipartUpload'],
             'Resource': [f'{arn}/{name}' for name in SECURELOCK_WRITES]},
            {'Effect': 'Deny', 'Action': ['admin:*']},
        ],
    }


def image_labelled(reference, label, run, logger):
    """Whether docker's image `reference` carries the label `label` with the
    value STORE_LABEL_FILE, compared as it is: `docker image inspect` exits
    0 and prints a JSON object with that member. False for anything else: no
    reference, a command that fails or cannot be run, `null`, another value.
    `run` runs a command given as a list, without a shell, and returns
    (exit code, output) (utils.run_subprocess_status)."""
    if reference is None:
        return False
    try:
        code, output = run(['docker', 'image', 'inspect', '--format', LABELS_FORMAT, reference], logger)
        labels = json.loads(output) if code == 0 else None
    except Exception:
        return False
    return isinstance(labels, dict) and labels.get(label) == STORE_LABEL_FILE


def user_kinds(trustedzone, securelock, integration_test=False):
    """{enclave service: the kind of user whose credential its file holds},
    for the services the compose has. `trustedzone` and `securelock` are
    None for a service the compose lacks, True for one planned as labelled
    (its image carries the label and the service carries the mount) and
    False for any other. A trustedzone planned as labelled gets
    USER_TRUSTEDZONE, in an integration test USER_INTEGRATION; a securelock
    planned as labelled gets USER_SECURELOCK; every other service gets
    USER_LITERAL. In an integration test a securelock service gets no
    user."""
    kinds = {}
    if trustedzone is not None:
        own = USER_INTEGRATION if integration_test else USER_TRUSTEDZONE
        kinds[TRUSTEDZONE] = own if trustedzone else USER_LITERAL
    if securelock is not None and not integration_test:
        kinds[SECURELOCK] = USER_SECURELOCK if securelock else USER_LITERAL
    return kinds


def draw_user(kind, bucket):
    """A StoreUser of the kind `kind` for `bucket`. Its policy is
    securelock_policy for USER_SECURELOCK and trustedzone_policy for every
    other kind. USER_LITERAL has LITERAL for both keys; any other kind has
    the prefix of its kind and 14 hexadecimal digits for its access key and
    40 hexadecimal digits for its secret key, drawn anew at every call."""
    policy = securelock_policy(bucket) if kind == USER_SECURELOCK else trustedzone_policy(bucket)
    if kind == USER_LITERAL:
        return StoreUser(LITERAL, LITERAL, policy)
    return StoreUser(ACCESS_KEY_PREFIXES[kind] + secrets.token_hex(7), secrets.token_hex(20), policy)


def order_users(kinds, bucket):
    """{enclave service: StoreUser} for `kinds` (user_kinds) and `bucket`:
    one user per kind, which the services of that kind share."""
    drawn = {}
    for kind in kinds.values():
        if kind not in drawn:
            drawn[kind] = draw_user(kind, bucket)
    return {service: drawn[kind] for service, kind in kinds.items()}


def create_users(admin, users):
    """Creates each StoreUser of `users` through `admin` (StoreAdmin): its
    policy, named POLICY_PREFIX and its access key, then the user, then the
    policy set on the user."""
    for user in users:
        name = POLICY_PREFIX + user.access_key
        admin.add_policy(name, user.policy)
        admin.add_user(user.access_key, user.secret_key)
        admin.set_policy(name, user.access_key)


def credential_volume(credential_dir, service):
    """The volume that mounts the directory of `service` under
    `credential_dir` read-only on CREDENTIAL_MOUNT of its container. The
    directory is mounted, not the file: a rename replaces the file's
    inode."""
    return f'{credential_dir}/{service}:{CREDENTIAL_MOUNT}:ro'


def write_credential(credential_dir, service, access_key, secret_key):
    """Writes the credential file of `service` under `credential_dir`:
    <credential_dir>/<service>/CREDENTIAL_FILE with the mode 0400, each
    directory created with the mode 0700 when it is missing."""
    directory = os.path.join(credential_dir, service)
    for path in (credential_dir, directory):
        if not os.path.isdir(path):
            os.mkdir(path, 0o700)
    _write_new(os.path.join(directory, CREDENTIAL_FILE), credential_bytes(access_key, secret_key), 0o400)


def remove_entries(directory):
    """Removes every entry of `directory`, which stays; a directory that is
    not there holds none."""
    if not os.path.isdir(directory):
        return
    for entry in os.listdir(directory):
        path = os.path.join(directory, entry)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


def sweep(admin, credential_dir):
    """Removes every user of the store, then every policy whose name begins
    with POLICY_PREFIX, then every entry of the directories of the
    ENCLAVE_SERVICES under `credential_dir`. The store holds no user but the
    agent's. Run where no enclave uses the store: under the task slot, with
    the enclave containers down."""
    for access_key in admin.users():
        admin.remove_user(access_key)
    for name in admin.policies():
        if name.startswith(POLICY_PREFIX):
            admin.remove_policy(name)
    for service in ENCLAVE_SERVICES:
        remove_entries(os.path.join(credential_dir, service))


def prepare(compose_file, bucket, integration_test, admin, credential_dir, logger, run):
    """Prepares the store for one start of the enclave environment of the
    compose file `compose_file`, whose images docker holds and whose
    services are down, before its first `up`. `bucket` is the bucket the
    caller staged; `integration_test` says whether the start is a
    trustedzone's integration test, which concerns the trustedzone service
    alone.

    O1  The label of the image of each enclave service the compose has is
        read (image_labelled, through `run`). A label that is not read is an
        unlabelled image.
    O2  The compose gets the credential_volume of each of those services
        (utils.compose_with_volume). A service that does not take it is
        logged at WARNING and planned as unlabelled, so that its enclave,
        which finds no file, meets the user named LITERAL.
    O3  sweep.
    O4  The users of the plan (user_kinds, order_users) are created
        (create_users).
    O5  The credential file of each service is written (write_credential).

    Raises NotPrepared when O2 to O5 do not complete, naming the step and
    the class of the error."""
    step = 'O2'
    try:
        with open(compose_file) as handle:
            text = handle.read()
        services = [service for service in ((TRUSTEDZONE,) if integration_test else ENCLAVE_SERVICES)
                    if compose_has_service(text, service)]
        planned = {service: image_labelled(compose_service_image(text, service), STORE_LABELS[service], run, logger)
                   for service in services}
        edited = text
        for service in services:
            with_volume = compose_with_volume(edited, service, credential_volume(credential_dir, service))
            if with_volume is None:
                logger.warning(f"The service {service} of {compose_file} did not take the store credential's "
                               f"mount; it is given the literal-named user")
                planned[service] = False
            else:
                edited = with_volume
        if edited != text:
            with open(compose_file, 'w') as handle:
                handle.write(edited)
        kinds = user_kinds(planned.get(TRUSTEDZONE), planned.get(SECURELOCK), integration_test)
        step = 'O3'
        sweep(admin, credential_dir)
        step = 'O4'
        users = order_users(kinds, bucket)
        create_users(admin, {user.access_key: user for user in users.values()}.values())
        step = 'O5'
        for service, user in users.items():
            write_credential(credential_dir, service, user.access_key, user.secret_key)
    except Exception as error:
        raise NotPrepared(f'{step} {error_text(error)}') from None
    logger.info(f"Store users of {bucket}: "
                + (', '.join(f'{service} {kind}' for service, kind in kinds.items()) or 'none'))
