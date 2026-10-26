"""utils.enclave_image_trees, record_enclave_image_tree, compose_images and
tree_image_tag: the record of the image trees whose enclave images docker
keeps under per-tree tags, so an order of a tree run recently brings its
compose up on the images already there (bloxberg testnet rig, 2026-10-26:
every switch between the Python and the JS clients' trees pruned all images
and pulled 8 GB again, 2.4 to 4.5 minutes per order)."""
import json

from utils import (ENCLAVE_IMAGES_MARKER, compose_images, enclave_image_trees, record_enclave_image_tree,
                   tree_image_tag)

PY_TREE = "QmbmhTNmjQbQQRwMpdUZbbVvd3Q3u1qFSRjEYEsXYAKWxN"
JS_TREE = "QmNggKcVmmvH8AkrxjrCJmikee8XuxzKdTnwG8E9NxjJsS"
TEST_TREE = "QmT7h7TvJb9Ww4BnYSanmPWP2YrfHR9WDWMcEbNTfWedHE"


def test_no_marker_means_no_trees(tmp_path):
    assert enclave_image_trees(str(tmp_path)) == []


def test_the_last_run_tree_comes_first_and_the_oldest_beyond_keep_is_dropped(tmp_path):
    assert record_enclave_image_tree(str(tmp_path), PY_TREE, keep=2) == []
    assert record_enclave_image_tree(str(tmp_path), JS_TREE, keep=2) == []
    assert enclave_image_trees(str(tmp_path)) == [JS_TREE, PY_TREE]
    assert record_enclave_image_tree(str(tmp_path), PY_TREE, keep=2) == []
    assert enclave_image_trees(str(tmp_path)) == [PY_TREE, JS_TREE]
    assert record_enclave_image_tree(str(tmp_path), TEST_TREE, keep=2) == [JS_TREE]
    assert enclave_image_trees(str(tmp_path)) == [TEST_TREE, PY_TREE]
    assert json.loads((tmp_path / ENCLAVE_IMAGES_MARKER).read_text()) == [TEST_TREE, PY_TREE]


def test_a_bare_hash_marker_of_an_earlier_node_version_is_that_one_tree(tmp_path):
    (tmp_path / ENCLAVE_IMAGES_MARKER).write_text(f"{PY_TREE}\n")
    assert enclave_image_trees(str(tmp_path)) == [PY_TREE]
    assert record_enclave_image_tree(str(tmp_path), JS_TREE, keep=3) == []
    assert enclave_image_trees(str(tmp_path)) == [JS_TREE, PY_TREE]


def test_an_unreadable_marker_counts_as_no_trees(tmp_path):
    (tmp_path / ENCLAVE_IMAGES_MARKER).write_text("[not json")
    assert enclave_image_trees(str(tmp_path)) == []
    (tmp_path / ENCLAVE_IMAGES_MARKER).write_text("  \n")
    assert enclave_image_trees(str(tmp_path)) == []


def test_compose_images_are_the_services_images_with_their_tags(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "version: '3.2'\n"
        "services:\n"
        "  las:\n"
        "    container_name: las\n"
        "    image: localhost:5000/etny-las\n"
        "  etny-securelock:\n"
        "    image: \"localhost:5000/etny-securelock:v3\"\n"
        "    command:\n"
        "    - /usr/local/bin/python\n"
        "  etny-trustedzone:\n"
        "    image: localhost:5000/etny-trustedzone   # pulled from the order's registry\n"
        "networks:\n"
        "  ethernity:\n")
    assert compose_images(str(compose)) == ["localhost:5000/etny-las:latest", "localhost:5000/etny-securelock:v3",
                                            "localhost:5000/etny-trustedzone:latest"]


def test_the_tree_tag_replaces_the_images_tag_and_leaves_the_registry_port():
    assert tree_image_tag("localhost:5000/etny-las:latest", PY_TREE) == f"localhost:5000/etny-las:{PY_TREE}"
    assert tree_image_tag("etny-trustedzone:v3", JS_TREE) == f"etny-trustedzone:{JS_TREE}"
