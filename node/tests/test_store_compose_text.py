"""utils.compose_has_service, compose_service_image and compose_with_volume
on what no compose of the field holds as published: an image reference with
a tag, a digest or no registry, and comment lines at or left of the
services' indentation. Each case is a compose of the field (field_composes)
with that one change, and each result is compared as text: no YAML parser
takes part."""
import pytest

from field_composes import PYNITHY_TRUSTEDZONE, SDK_JS_NODENITHY, copy_of, sdk_js_published, trustedzone_published
from store_access import SECURELOCK, TRUSTEDZONE
from utils import compose_has_service, compose_images, compose_service_image, compose_with_volume

# A dApp's compose of the JS SDK, its services at two spaces, and the Python
# trustedzone's own, its services at four.
ORDER_COMPOSE = sdk_js_published(copy_of(SDK_JS_NODENITHY), True, False)
PYNITHY_TREE_COMPOSE = trustedzone_published(copy_of(PYNITHY_TRUSTEDZONE), True)
TRUSTEDZONE_IMAGE = "    image: localhost:5000/etny-trustedzone\n"
TRUSTEDZONE_KEY = "  etny-trustedzone:\n"
SECURELOCK_KEY = "  etny-securelock:\n"
MOUNT = "/run/ecld-store/etny-trustedzone:/run/ecld:ro"
DIGEST = "sha256:" + "0123456789abcdef" * 4


def changed(text, old, new):
    """`text` with its one `old` replaced by `new`."""
    assert text.count(old) == 1, old
    return text.replace(old, new)


def test_the_fixtures_are_the_field_composes():
    for part in (TRUSTEDZONE_IMAGE, TRUSTEDZONE_KEY, SECURELOCK_KEY):
        assert ORDER_COMPOSE.count(part) == 1
    assert PYNITHY_TREE_COMPOSE.count("    etny-trustedzone:\n") == 1


# (the value of `image:`, the reference the label is read of): `latest` is
# the tag of a reference whose last path segment has none.
REFERENCES = [
    ("localhost:5000/etny-trustedzone", "localhost:5000/etny-trustedzone:latest"),
    ("localhost:5000/etny-trustedzone:v3", "localhost:5000/etny-trustedzone:v3"),
    ("localhost:5000/etny-trustedzone:latest", "localhost:5000/etny-trustedzone:latest"),
    ("registry.example:5000/dapp/etny-trustedzone", "registry.example:5000/dapp/etny-trustedzone:latest"),
    ("registry.example:5000/dapp/etny-trustedzone:1.2", "registry.example:5000/dapp/etny-trustedzone:1.2"),
    ("etny-trustedzone", "etny-trustedzone:latest"),
    ("etny-trustedzone:v3", "etny-trustedzone:v3"),
    ("dapp/etny-trustedzone", "dapp/etny-trustedzone:latest"),
    (f"localhost:5000/etny-trustedzone@{DIGEST}", f"localhost:5000/etny-trustedzone@{DIGEST}"),
    (f"registry.example:5000/dapp/etny-trustedzone:1.2@{DIGEST}",
     f"registry.example:5000/dapp/etny-trustedzone:1.2@{DIGEST}"),
    ('"localhost:5000/etny-trustedzone:v3"', "localhost:5000/etny-trustedzone:v3"),
    ("'localhost:5000/etny-trustedzone'", "localhost:5000/etny-trustedzone:latest"),
    ("localhost:5000/etny-trustedzone:v3  # the tag of this release", "localhost:5000/etny-trustedzone:v3"),
]


@pytest.mark.parametrize("value, reference", REFERENCES)
def test_the_reference_of_a_service_takes_latest_only_without_a_tag(tmp_path, value, reference):
    text = changed(ORDER_COMPOSE, TRUSTEDZONE_IMAGE, f"    image: {value}\n")
    assert compose_service_image(text, TRUSTEDZONE) == reference
    assert compose_service_image(text, SECURELOCK) == "localhost:5000/etny-securelock:latest"
    # The reference is the one compose_images reads of the same line.
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(text)
    assert compose_images(compose) == ["localhost:5000/etny-las:latest", "localhost:5000/etny-securelock:latest",
                                       reference]


def mounted(text, key, indent):
    """`text` with MOUNT as the `volumes` list of the service whose key line
    is `key`, written after that line with its keys at `indent` spaces."""
    return changed(text, key, f"{key}{' ' * indent}volumes:\n{' ' * (indent + 2)}- '{MOUNT}'\n")


# Comment lines put into the JS SDK's compose: (the line the comment goes
# before, the comment line).
COMMENTS = [
    ("  las:\n", "# the services of the task\n"),
    ("  las:\n", "      # the services of the task\n"),
    ("  las:\n", "  # the local attestation service\n"),
    (SECURELOCK_KEY, "# the enclave that runs the payload\n"),
    (SECURELOCK_KEY, "  # the enclave that runs the payload\n"),
    (TRUSTEDZONE_KEY, "# the enclave that reads the order\n"),
    (TRUSTEDZONE_KEY, " # the enclave that reads the order\n"),
    (TRUSTEDZONE_IMAGE, "# the image of this network\n"),
    (TRUSTEDZONE_IMAGE, "  # the image of this network\n"),
    ("    container_name: etny-trustedzone\n", "# the name the agent polls\n"),
    ("    container_name: etny-trustedzone\n", "  # the name the agent polls\n"),
]


@pytest.mark.parametrize("before, comment", COMMENTS)
def test_a_comment_line_ends_neither_the_services_nor_a_service(before, comment):
    text = changed(ORDER_COMPOSE, before, comment + before)
    for service in (TRUSTEDZONE, SECURELOCK):
        assert compose_has_service(text, service)
        assert compose_service_image(text, service) == f"localhost:5000/{service}:latest"
    assert compose_with_volume(text, TRUSTEDZONE, MOUNT) == mounted(text, TRUSTEDZONE_KEY, 4)
    assert compose_with_volume(mounted(text, TRUSTEDZONE_KEY, 4), TRUSTEDZONE, MOUNT) == mounted(text, TRUSTEDZONE_KEY, 4)


@pytest.mark.parametrize("comment", ["# the operator's data\n", "  # the operator's data\n", " # the operator's data\n"])
def test_a_list_of_volumes_below_a_comment_line_takes_the_mount_under_its_one_key(comment):
    held = "    volumes:\n      - /srv/data:/data\n"
    restart = "    restart: on-failure\n    networks:\n      - ethernity\n    devices:\n" \
              "      - \"/dev/sgx_enclave:/dev/sgx_enclave\"\n    depends_on:\n      - las\nnetworks:\n"
    text = changed(ORDER_COMPOSE, restart, comment + held + restart)
    with_mount = compose_with_volume(text, TRUSTEDZONE, MOUNT)
    assert with_mount == changed(text, held, f"{held}      - '{MOUNT}'\n")
    assert with_mount.count("    volumes:\n") == 1
    assert compose_with_volume(with_mount, TRUSTEDZONE, MOUNT) == with_mount


@pytest.mark.parametrize("comment", ["# the enclave that reads the order\n", "    # the enclave that reads the order\n",
                                     "  # the enclave that reads the order\n"])
def test_a_comment_line_among_services_at_four_spaces(comment):
    key = "    etny-trustedzone:\n"
    text = changed(PYNITHY_TREE_COMPOSE, "      privileged: true\n      image: localhost:5000/etny-trustedzone\n",
                   f"      privileged: true\n{comment}      image: localhost:5000/etny-trustedzone\n")
    text = changed(text, key, comment + key)
    assert compose_has_service(text, TRUSTEDZONE) and compose_has_service(text, "las")
    assert compose_service_image(text, TRUSTEDZONE) == "localhost:5000/etny-trustedzone:latest"
    assert compose_with_volume(text, TRUSTEDZONE, MOUNT) == mounted(text, key, 6)
