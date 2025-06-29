from minio import Minio
from minio.error import S3Error
import os
import subprocess
import time

class SwiftStreamService:

    def __init__(self, endpoint: str, access_key: str, secret_key: str):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key

        if not self.try_connect_minio():
            if not self.restart_etny_swift_stream_and_reconnect():
                raise RuntimeError("Failed to connect to MinIO after restarting etny-swift-stream.")

    def _connect_client(self):
        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
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
                return False, f"create_bucket failed: {err}"

        return True, f"Bucket, {bucket_name} successfully created!"

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
                return False, f"delete_bucket failed: {err}"

        return True, f"Bucket, {bucket_name} successfully deleted!"

    def delete_file(self, bucket_name: str, file_name: str) -> (bool, str):
        try:
            self.client.remove_object(bucket_name, file_name)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.delete_file(bucket_name, file_name)
            else:
                return False, f"delete_file failed: {err}"

        return True, f"File, {file_name} successfully deleted!"

    def delete_files(self, bucket_name: str, list_of_files: list[str]) -> (bool, str):
        try:
            errors = self.client.remove_objects(bucket_name, list_of_files)
            for error in errors:
                print(f"[Error] Error occurred when deleting file: {error}!")
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.delete_files(bucket_name, list_of_files)
            else:
                return False, f"delete_files failed: {err}"

        return True, f"Files, {list_of_files} successfully deleted!"

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
                return False, f"upload_file failed: {err}"

        return True, f"{file_path} is successfully uploaded to bucket {bucket_name}."

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
                return False, f"upload_files failed: {err}"

        return True, f"{upload_file_paths} are successfully uploaded to bucket {bucket_name}."

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
                return False, f"download_file failed: {err}"

        return True, f"File, {file_name} from bucket {bucket_name} was downloaded in {file_path}."

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
                return False, f"download_files failed: {err}"

        return True, f"{download_file_paths} are successfully uploaded to bucket {bucket_name}."
    def get_file_content_bytes(self, bucket_name: str, file_name: str) -> (bool, bytes):
        response = None
        try:
            response = self.client.get_object(bucket_name, file_name)
            _d = b''
            for data in response.stream(amt=1024 * 1024):
                _d = _d + data
        except S3Error as err:
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
                return False, err

        return True, f"{object_name} is successfully uploaded to bucket {bucket_name}."

    def is_object_in_bucket(self, bucket_name: str, object_name: str) -> (bool, str):
        try:
            result = self.client.stat_object(bucket_name, object_name)
            if result:
                return True, f"{object_name} exists inside {bucket_name}."
            else:
                return False, f"{object_name} doesn't exist inside {bucket_name}."
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self.is_object_in_bucket(bucket_name, object_name)
            else:
                return False, err


    def _list_buckets(self) -> None:
        try:
            buckets = self.client.list_buckets()
            for bucket in buckets:
                print(bucket.name, bucket.creation_date)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self._list_buckets()
            else:
                print(f"_list_buckets failed: {err}")

    def _list_objects(self, bucket_name: str) -> None:
        try:
            objects = self.client.list_objects(bucket_name)
            for obj in objects:
                print("-> ", obj.object_name, obj.owner_name)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self._list_objects(bucket_name)
            else:
                print(f"_list_objects failed: {err}")

    def _is_bucket(self, bucket_name: str) -> bool:
        try:
            return self.client.bucket_exists(bucket_name)
        except S3Error as err:
            if self.restart_etny_swift_stream_and_reconnect():
                return self._is_bucket(bucket_name)
            else:
                print(f"_is_bucket failed: {err}")
                return False
