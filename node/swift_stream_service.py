from minio import Minio
from minio.error import MinioException, S3Error
from utils import run_subprocess_status
import functools
import os
import store_access
import subprocess
import threading
import time
import urllib3

# The store's compose file, relative to a network's working directory, and
# the service it defines.
STORE_COMPOSE_FILE = '../docker/docker-compose-swift-stream.yml'
STORE_SERVICE = 'swift-stream'
# The pairs a started store knows as no access key: the pair of the
# literal-named user, which exists only while an order or a test with an
# unlabelled image runs, and MinIO's built-in root.
REFUSED_PAIRS = (('the literal pair', store_access.LITERAL, store_access.LITERAL),
                 ("MinIO's built-in root", 'minioadmin', 'minioadmin'))


class StoreStart:
    """The start of the store in one process (SwiftStreamService.__init__).
    `lock` serializes the constructions. `root` is the node's root credential
    once a start has passed, None before. `recreated` says that the store's
    container was recreated by force, which is done at most once per
    process."""

    def __init__(self):
        self.lock = threading.Lock()
        self.root = None
        self.recreated = False


_start = StoreStart()


class StoreNotStarted(Exception):
    """A step of the store's start found the store in a state it does not
    go on from; str() says which."""


# The classes of an error of the S3 client: minio-py's own, S3Error among
# them, and urllib3's, which minio-py raises for a connection or an answer
# that fails.
CLIENT_ERRORS = (MinioException, urllib3.exceptions.HTTPError)


class StoreClientError(Exception):
    """An error of the S3 client left a method of SwiftStreamService; str()
    is store_access.error_text of that error: its class and, for an S3
    error, its code."""


def client_errors_named(method):
    """The method `method` of SwiftStreamService with every error of the S3
    client that leaves it (CLIENT_ERRORS) raised as a StoreClientError in
    its place, without the error as its context: a caller that logs what
    the method raised logs the error's class, never its message, which
    quotes the request. Any other error leaves the method as it is."""
    @functools.wraps(method)
    def named(*arguments, **keywords):
        try:
            return method(*arguments, **keywords)
        except CLIENT_ERRORS as error:
            raise StoreClientError(store_access.error_text(error)) from None
    return named


class SwiftStreamService:
    """The agent's client of the node's object store, under the node's root
    credential (store_access.root_credential, kept in `root_filepath`), with
    the admin client (`admin`) and the directory of the enclaves' credential
    files (`credential_dir`) an order's users are made with
    (store_access.prepare, store_access.sweep).

    The first construction in a process starts the store:

    S1  The root credential is loaded, or drawn and written.
    S2  The store's compose is run (compose_up); a run that does not exit 0
        fails the start here. The store is then waited for until it is live
        (store_access.await_live).
    S3  The root credential lists the store's buckets. If it does not, or
        the store is not live, the compose is run with --force-recreate,
        once per process, the store is waited for as in S2 and the root
        credential lists the buckets again; if it still does not, the start
        fails. The start removes nothing of the store's data.
    S4  store_access.sweep: no user of an earlier process is left.
    S5  The store knows neither of the REFUSED_PAIRS.

    A start that fails raises RuntimeError and is run again by the next
    construction. Once a start has passed, a construction runs the compose
    and connects under the root credential. The enclave containers of an
    earlier process are removed before any construction, and the
    constructing handle holds the task slot, so no enclave uses the store
    during S4.

    An error of the S3 client leaves the methods that call the store as a
    StoreClientError, which names its class alone (client_errors_named); an
    S3 error they do not retry is in the text or the value they return."""

    def __init__(self, logger, endpoint: str, root_filepath, credential_dir=store_access.CREDENTIAL_DIR):
        self.endpoint = endpoint
        self.logger = logger
        self.credential_dir = str(credential_dir)

        with _start.lock:
            if _start.root is None:
                self._start_store(root_filepath)
            else:
                self._hold_root(_start.root)
                self.compose_up()

        if not self.try_connect_minio():
            if not self.restart_etny_swift_stream_and_reconnect():
                raise RuntimeError("Failed to connect to MinIO after restarting etny-swift-stream.")

    def _hold_root(self, root):
        """Takes `root` (access key, secret key) as the credential of this
        service's S3 client and builds its admin client under it."""
        self._root = root
        self.admin = store_access.StoreAdmin(self.endpoint, *root)

    def compose_up(self, force_recreate=False):
        """Runs the store's compose (`up -d swift-stream`, with
        --force-recreate when asked) with the root credential in the
        environment of that run alone (store_access.store_compose_env);
        returns the command's exit code."""
        args = ['docker-compose', '-f', STORE_COMPOSE_FILE, 'up', '-d']
        if force_recreate:
            args.append('--force-recreate')
        code, _ = run_subprocess_status(args + [STORE_SERVICE], self.logger,
                                        env=store_access.store_compose_env(self._root))
        return code

    def _root_answer(self):
        """store_access.LISTED when the store is live and the root credential
        lists its buckets, else what kept it from that."""
        if not store_access.await_live(self.endpoint, store_access.bounded_http()):
            return 'not live'
        return store_access.list_outcome(store_access.s3_client(self.endpoint, *self._root))

    def _start_store(self, root_filepath):
        """S1 to S5 of the class's text. Raises RuntimeError naming the step
        that failed and the class of its error, and leaves _start.root None;
        sets it when S5 has passed."""
        step = 'S1'
        try:
            self._hold_root(store_access.root_credential(root_filepath))
            step = 'S2'
            code = self.compose_up()
            if code != 0:
                raise StoreNotStarted(f"the store's compose exited {code}")
            step = 'S3'
            answer = self._root_answer()
            if answer != store_access.LISTED and not _start.recreated:
                _start.recreated = True
                self.logger.warning(f"The store does not answer the node's root credential ({answer}); its "
                                    f"container is recreated")
                self.compose_up(force_recreate=True)
                answer = self._root_answer()
            if answer != store_access.LISTED:
                raise StoreNotStarted(f"the root credential is not answered ({answer})")
            step = 'S4'
            store_access.sweep(self.admin, self.credential_dir)
            step = 'S5'
            for name, access_key, secret_key in REFUSED_PAIRS:
                outcome = store_access.list_outcome(store_access.s3_client(self.endpoint, access_key, secret_key))
                if outcome != store_access.REFUSED:
                    raise StoreNotStarted(f"the store answers {name} ({outcome})")
        except StoreNotStarted as failure:
            raise RuntimeError(f"The store did not start: {step}, {failure}") from None
        except Exception as error:
            raise RuntimeError(f"The store did not start: {step}, {store_access.error_text(error)}") from None
        _start.root = self._root
        self.logger.info("The store runs under the node's root credential and holds no user")

    def _connect_client(self):
        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self._root[0],
            secret_key=self._root[1],
            secure=False
        )

    def try_connect_minio(self):
        try:
            self._connect_client()
            self.client.list_buckets()
            return True
        except S3Error as e:
            return False
        except Exception as e:
            return False

    def restart_etny_swift_stream_and_reconnect(self):
        # Every method retries an S3 error by calling this and then itself
        # again, so the restarts are capped at three per rolling five-minute
        # window; past the cap this returns False and the caller returns its
        # error.
        import time as _time
        now = _time.time()
        recent = [t for t in getattr(self, '_restart_times', []) if now - t < 300]
        if len(recent) >= 3:
            return False
        recent.append(now)
        self._restart_times = recent

        max_attempts = 10
        sleep_seconds = 5

        for attempt in range(1, max_attempts + 1):
            try:
                result = subprocess.run(
                    ["docker", "start", "etny-swift-stream"],
                    check=True,
                    capture_output=True,
                    text=True
                )
                time.sleep(35)
            except subprocess.CalledProcessError as e:
                time.sleep(sleep_seconds)
                continue

            if self.try_connect_minio():
                return True

            if attempt < max_attempts:
                time.sleep(sleep_seconds)

        return False

    @client_errors_named
    def create_bucket(self, bucket_name: str) -> (bool, str):
        try:
            if not self.client.bucket_exists(bucket_name):
                self.client.make_bucket(bucket_name)
            else:
                return False, f"Bucket, {bucket_name} already exists!"
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.create_bucket(bucket_name)
            else:
                return False, f"create_bucket failed: {store_access.error_text(err)}"

        return True, f"Bucket, {bucket_name} successfully created!"

    @client_errors_named
    def delete_bucket(self, bucket_name: str) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                file_objects = self.client.list_objects(bucket_name)
                for file_object in file_objects:
                    self.delete_file(bucket_name, file_object.object_name)
                self.client.remove_bucket(bucket_name)
            else:
                return False, f"Bucket, {bucket_name} does not exists!"
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.delete_bucket(bucket_name)
            else:
                return False, f"delete_bucket failed: {store_access.error_text(err)}"

        return True, f"Bucket, {bucket_name} successfully deleted!"

    @client_errors_named
    def delete_file(self, bucket_name: str, file_name: str) -> (bool, str):
        try:
            self.client.remove_object(bucket_name, file_name)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.delete_file(bucket_name, file_name)
            else:
                return False, f"delete_file failed: {store_access.error_text(err)}"

        return True, f"File, {file_name} successfully deleted!"

    @client_errors_named
    def delete_files(self, bucket_name: str, list_of_files: list[str]) -> (bool, str):
        try:
            errors = self.client.remove_objects(bucket_name, list_of_files)
            for error in errors:
                print(f"[Error] Error occurred when deleting file: {store_access.error_text(error)}!")
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.delete_files(bucket_name, list_of_files)
            else:
                return False, f"delete_files failed: {store_access.error_text(err)}"

        return True, f"Files, {list_of_files} successfully deleted!"

    @client_errors_named
    def upload_file(self, bucket_name: str, file_name: str, file_path: str) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                self.client.fput_object(bucket_name, file_name, file_path)
            else:
                self.create_bucket(bucket_name)
                self.client.fput_object(bucket_name, file_name, file_path)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.upload_file(bucket_name, file_name, file_path)
            else:
                return False, f"upload_file failed: {store_access.error_text(err)}"

        return True, f"{file_path} is successfully uploaded to bucket {bucket_name}."

    @client_errors_named
    def upload_files(self, bucket_name: str, list_of_files: list[str],
                     upload_file_paths: list[str]) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                for file_idx in range(len(upload_file_paths)):
                    self.client.fput_object(bucket_name,
                                            list_of_files[file_idx],
                                            upload_file_paths[file_idx])
            else:
                self.create_bucket(bucket_name)
                for file_idx in range(len(upload_file_paths)):
                    self.client.fput_object(bucket_name,
                                            list_of_files[file_idx],
                                            upload_file_paths[file_idx])
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.upload_files(bucket_name, lif_of_files, upload_file_path)
            else:
                return False, f"upload_files failed: {store_access.error_text(err)}"

        return True, f"{upload_file_paths} are successfully uploaded to bucket {bucket_name}."

    @client_errors_named
    def download_file(self, bucket_name: str, file_name: str, file_path: str) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                self.client.fget_object(bucket_name, file_name, file_path)
            else:
                return False, f"Bucket, {bucket_name} does not exists!"
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.download_file(bucket_name, file_name, file_path)
            else:
                return False, f"download_file failed: {store_access.error_text(err)}"

        return True, f"File, {file_name} from bucket {bucket_name} was downloaded in {file_path}."

    @client_errors_named
    def download_files(self, bucket_name: str, list_of_files: list[str],
                       download_file_paths: list[str]) -> (bool, str):
        try:
            if self.client.bucket_exists(bucket_name):
                for file_idx in range(len(list_of_files)):
                    self.client.fget_object(bucket_name,
                                            list_of_files[file_idx],
                                            download_file_paths[file_idx])
            else:
                return False, f"Bucket, {bucket_name} does not exists!"
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.download_files(bucket_name, list_of_files, download_file_path)
            else:
                return False, f"download_files failed: {store_access.error_text(err)}"

        return True, f"{download_file_paths} are successfully uploaded to bucket {bucket_name}."

    @client_errors_named
    def get_file_content_bytes(self, bucket_name: str, file_name: str) -> (bool, bytes):
        response = None
        try:
            response = self.client.get_object(bucket_name, file_name)
            _d = b''
            for data in response.stream(amt=1024 * 1024):
                _d = _d + data
        except S3Error as err:
            # A missing key/bucket is a legitimate answer, not a service
            # failure: restarting MinIO cannot make the object exist.
            if getattr(err, 'code', '') in ('NoSuchKey', 'NoSuchBucket'):
                return False, err
            if self.restart_etny_swift_stream_and_reconnect():
                return self.get_file_content_bytes(bucket_name, file_name)
            else:
                return False, err
        finally:
            if response is not None:
                response.close()
                response.release_conn()
        return True, _d

    def get_file_content(self, bucket_name: str, file_name: str) -> (bool, str):
        status, content = self.get_file_content_bytes(bucket_name, file_name)
        if status:
            return True, content.decode('utf-8')
        return status, content

    @client_errors_named
    def put_file_content(self, bucket_name: str, object_name: str, object_path: str,
                         object_data: object = None) -> (bool, str):
        try:
            if object_data is not None:
                self.client.put_object(bucket_name,
                                       object_name,
                                       object_data,
                                       len(object_data.getbuffer()))
            else:
                object_stat = os.stat(object_path)
                with open(object_path, 'rb') as file_data:
                    self.client.put_object(bucket_name,
                                           object_name,
                                           file_data,
                                           object_stat.st_size)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.put_file_content(bucket_name, object_name, object_path, object_data)
            else:
                return False, f"put_file_content failed: {store_access.error_text(err)}"

        return True, f"{object_name} is successfully uploaded to bucket {bucket_name}."

    @client_errors_named
    def is_object_in_bucket(self, bucket_name: str, object_name: str) -> (bool, str):
        try:
            self.client.stat_object(bucket_name, object_name)
            return True, f"{object_name} exists in {bucket_name}."
        except S3Error as err:
            # NoSuchKey is the object missing, NoSuchBucket the bucket: neither
            # is a service fault, so neither restarts MinIO.
            if err.code in ("NoSuchKey", "NoSuchBucket"):
                return False, f"{object_name} does not exist in {bucket_name}."

            if self.restart_etny_swift_stream_and_reconnect():
                return self.is_object_in_bucket(bucket_name, object_name)
            else:
                return False, f"is_object_in_bucket failed: {store_access.error_text(err)}"


    @client_errors_named
    def list_object_names(self, bucket_name: str) -> list:
        """Object names in a bucket, or [] on failure."""
        try:
            return [obj.object_name for obj in self.client.list_objects(bucket_name)]
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                try:
                    return [obj.object_name for obj in self.client.list_objects(bucket_name)]
                except S3Error:
                    return []
            return []
        except Exception:
            return []

    @client_errors_named
    def _is_bucket(self, bucket_name: str) -> bool:
        try:
            return self.client.bucket_exists(bucket_name)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self._is_bucket(bucket_name)
            else:
                print(f"_is_bucket failed: {store_access.error_text(err)}")
                return False
