"""utils.las_image and passed_las_tag: an order's compose runs the LAS of the
trustedzone tree that passed this node's integration test, kept under
<repository>:tested-<trustedzone> (etny-node.py __run_integration_test,
__start_enclave_environment)."""
from utils import compose_images, las_image, passed_las_tag, tree_image_tag

IMAGES = ["localhost:5000/etny-las:latest", "localhost:5000/etny-trustedzone:latest",
          "localhost:5000/etny-securelock:latest"]


def test_the_las_image_is_the_one_named_etny_las():
    assert las_image(IMAGES) == "localhost:5000/etny-las:latest"
    assert las_image(["registry.example/etny/etny-las:1.2", "x:1"]) == "registry.example/etny/etny-las:1.2"


def test_a_compose_without_a_las_has_none():
    assert las_image(IMAGES[1:]) is None
    assert las_image([]) is None
    assert las_image(["localhost:5000/etny-las-tools:latest"]) is None


def test_the_tested_tag_keeps_the_repository_and_names_the_trustedzone():
    assert passed_las_tag("localhost:5000/etny-las:latest", "etny-pynithy") == "localhost:5000/etny-las:tested-etny-pynithy"
    tree = "QmW1aAD9QQ3tGA9FPQSiPTKAfmyHZ1kwhr8tUWZKfKFobp"
    assert passed_las_tag(tree_image_tag("localhost:5000/etny-las:latest", tree), "ecld-pynithy-amoy") == \
        "localhost:5000/etny-las:tested-ecld-pynithy-amoy"


def test_the_las_of_a_compose_file_is_found(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  las:\n    image: localhost:5000/etny-las\n"
                       "  etny-trustedzone:\n    image: localhost:5000/etny-trustedzone\n")
    assert las_image(compose_images(str(compose))) == "localhost:5000/etny-las:latest"
