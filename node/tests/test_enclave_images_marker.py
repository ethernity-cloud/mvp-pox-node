"""utils.loaded_enclave_images and record_enclave_images: the marker under the
cache base path that names the image tree whose enclave images docker holds,
so an order of the same tree brings its compose up on the images already
there instead of pruning and pulling them again."""
from utils import ENCLAVE_IMAGES_MARKER, loaded_enclave_images, record_enclave_images


def test_no_marker_means_no_images_loaded(tmp_path):
    assert loaded_enclave_images(str(tmp_path)) is None


def test_the_recorded_tree_is_read_back_and_none_clears_it(tmp_path):
    record_enclave_images(str(tmp_path), "QmNggKcVmmvH8AkrxjrCJmikee8XuxzKdTnwG8E9NxjJsS")
    assert (tmp_path / ENCLAVE_IMAGES_MARKER).read_text() == "QmNggKcVmmvH8AkrxjrCJmikee8XuxzKdTnwG8E9NxjJsS"
    assert loaded_enclave_images(str(tmp_path)) == "QmNggKcVmmvH8AkrxjrCJmikee8XuxzKdTnwG8E9NxjJsS"
    record_enclave_images(str(tmp_path), None)
    assert loaded_enclave_images(str(tmp_path)) is None
    record_enclave_images(str(tmp_path), None)


def test_an_empty_marker_counts_as_none(tmp_path):
    (tmp_path / ENCLAVE_IMAGES_MARKER).write_text("  \n")
    assert loaded_enclave_images(str(tmp_path)) is None
