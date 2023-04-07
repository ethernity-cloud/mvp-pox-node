import unittest

from miniolib.file_service import FileService

class TestFileService(unittest.TestCase):        

    def setUp(self, endpoint):
        self.file_service = FileService(endpoint="play.min.io",
                                        access_key="Q3AM3UQ867SPQQA43P2F",
                                        secret_key="zuf+tfteSlswRu7BJ86wekitnifILbZam1KYY3TG")

    def test_file_service(self):
        (status, msg) = self.file_service.create_bucket("fileservicebucket")
        self.assertEqual(status, True)

        (status, msg) = self.file_service.upload_file("fileservicebucket",
                                                      "hello_world.py",
                                                      "data/hello_world.py")
        self.assertEqual(status, True)

        (status, msg) = self.file_service.download_file("fileservicebucket",
                                                        "hello_world.py",
                                                        "hello_world.py")
        self.assertEqual(status, True)

        (status, msg) = self.file_service.delete_file("fileservicebucket",
                                                      "hello_world.py")
        self.assertEqual(status, True)

        (status, msg) = self.file_service.delete_bucket("fileservicebucket")
        self.assertEqual(status, True)


if __name__ == '__main__':
    unittest.main()
