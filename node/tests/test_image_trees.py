"""utils.image_trees, stale_image_trees, directory_size, remove_tree and
removal_leftovers: the enclave image trees under a network's directory, those
old enough and not kept to remove, the bytes a tree holds, and a removal that
leaves nothing under the tree's CID when it fails part-way."""
import os

from utils import (IMAGE_TREE_REGISTRY, REMOVING_SUFFIX, directory_size, image_trees, removal_leftovers,
                   remove_tree, stale_image_trees)

TREE = "QmWVrXJYqXbgWNmtZAPqjnXai1NASKQumgwB3XytD3JEvf"
OTHER = "QmcKp7ph4PGJrP2H57oAWMFWG7fYEDawnucG6WEWzmSZxb"
BLOB = "bafkreibpi3gk3zkv4rbnsdjzo72yyxiq6la6zkeqafd3j4cikcdkmof66e"


def tree(base, cid, mtime, layer_bytes=b"x" * 10):
    repo = base / cid / IMAGE_TREE_REGISTRY / "etny-securelock"
    repo.mkdir(parents=True)
    (repo / "layer").write_bytes(layer_bytes)
    os.utime(base / cid, (mtime, mtime))


def test_only_cid_named_directories_with_the_registry_layout_are_trees(tmp_path):
    tree(tmp_path, TREE, 1_000.0)
    (tmp_path / OTHER).mkdir()                       # a CID-named directory without the layout
    (tmp_path / "orders").mkdir()                    # not a CID
    (tmp_path / BLOB).write_bytes(b"blob")           # a CID-named file
    (tmp_path / (TREE + ".tar.gz")).write_bytes(b"") # a download leftover
    (tmp_path / (OTHER + REMOVING_SUFFIX) / IMAGE_TREE_REGISTRY).mkdir(parents=True)  # a part-removed tree
    assert image_trees(str(tmp_path)) == {TREE: 1_000.0}


def test_stale_trees_are_the_old_ones_not_kept_oldest_first(tmp_path):
    tree(tmp_path, TREE, 1_000.0)
    tree(tmp_path, OTHER, 5_000.0)
    trees = image_trees(str(tmp_path))
    assert stale_image_trees(trees, set(), now=10_000.0, retention_seconds=4_000.0) == [TREE, OTHER]
    assert stale_image_trees(trees, set(), now=10_000.0, retention_seconds=6_000.0) == [TREE]
    assert stale_image_trees(trees, {TREE}, now=10_000.0, retention_seconds=0.0) == [OTHER]
    assert stale_image_trees(trees, {TREE, OTHER}, now=10_000.0, retention_seconds=0.0) == []


def test_a_tree_counts_as_used_no_earlier_than_since():
    trees = {TREE: 1_000.0, OTHER: 7_000.0}
    assert stale_image_trees(trees, set(), now=10_000.0, retention_seconds=4_000.0, since=6_000.0) == []
    assert stale_image_trees(trees, set(), now=10_000.0, retention_seconds=2_000.0, since=6_000.0) == [TREE, OTHER]
    assert stale_image_trees(trees, set(), now=10_000.0, retention_seconds=2_000.0, since=8_500.0) == []


def test_directory_size_sums_the_regular_files(tmp_path):
    tree(tmp_path, TREE, 1_000.0, layer_bytes=b"y" * 2_048)
    (tmp_path / TREE / "manifest").write_bytes(b"z" * 100)
    assert directory_size(str(tmp_path / TREE)) == 2_148


def test_remove_tree_removes_the_tree_and_leaves_no_leftover(tmp_path):
    tree(tmp_path, TREE, 1_000.0)
    remove_tree(str(tmp_path / TREE))
    assert not (tmp_path / TREE).exists()
    assert not (tmp_path / (TREE + REMOVING_SUFFIX)).exists()
    assert removal_leftovers(str(tmp_path)) == []


def test_removal_leftovers_are_the_part_removed_directories(tmp_path):
    tree(tmp_path, TREE, 1_000.0)
    leftover = tmp_path / (OTHER + REMOVING_SUFFIX)
    (leftover / IMAGE_TREE_REGISTRY).mkdir(parents=True)
    (tmp_path / (BLOB + REMOVING_SUFFIX)).write_bytes(b"")  # a file, not a directory
    assert removal_leftovers(str(tmp_path)) == [str(leftover)]
