"""utils.compose_has_service, compose_service_image and compose_with_volume
on the composes a node runs, read back with a YAML parser: the mount of the
store credential (store_access.credential_volume) goes once into the
`volumes` of each enclave service the compose has, whatever the indentation
of its services, and every other value of the compose stays.

The composes are those of the field (field_composes): the copies of the two
trustedzone repositories' and of the JS SDK's, untagged as their publication
untags them, each with and without its LAS, and the Python SDK's, which is
yaml.safe_dump of the dictionary of its publish.py, in the key order written
and, for a dApp with serverless services, with every mapping's keys
sorted."""
import copy
import hashlib
import re

import pytest

from field_composes import (COMPOSES, COPIES, NODENITHY_TRUSTEDZONE, PYNITHY_TRUSTEDZONE, SDK_JS_NODENITHY,
                            SDK_JS_PYNITHY, copy_of, sdk_js_published, sdk_py_data, trustedzone_published)
from store_access import CREDENTIAL_DIR, SECURELOCK, TRUSTEDZONE, credential_volume
from utils import compose_has_service, compose_service_image, compose_with_volume

yaml = pytest.importorskip("yaml")

MOUNTS = {TRUSTEDZONE: "/run/ecld-store/etny-trustedzone:/run/ecld:ro",
          SECURELOCK: "/run/ecld-store/etny-securelock:/run/ecld:ro"}


class UniqueKeys(yaml.SafeLoader):
    """A YAML loader that refuses a mapping with a key twice, which
    yaml.safe_load reads as the last of the two."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        assert len(keys) == len(set(keys)), f"a key twice among {keys}"
        return super().construct_mapping(node, deep=deep)


def load(text):
    return yaml.load(text, Loader=UniqueKeys)


def sdk_py_written(data):
    """publish.py's first dump: the keys in the order written."""
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def sdk_py_sorted(data, serverless=None):
    """publish.py's dump of a dApp with serverless services (`serverless`,
    {name: service}), merged into the compose read back: every mapping's
    keys sorted."""
    main = yaml.safe_load(sdk_py_written(data))
    main["services"].update(copy.deepcopy(serverless or {}))
    return yaml.safe_dump(main, default_flow_style=False)


# A serverless service of a dApp that names an enclave service as a key of a
# mapping: depends_on in its long form.
DEPENDENT = {"api": {"image": "localhost:5000/api", "networks": ["ethernity"],
                     "depends_on": {"etny-trustedzone": {"condition": "service_started"},
                                    "etny-securelock": {"condition": "service_started"}}}}

# (name, compose text, its enclave services, whether it has a LAS).
FIELD = [
    ("etny-nodenithy, a CAS", trustedzone_published(copy_of(NODENITHY_TRUSTEDZONE), True), [TRUSTEDZONE], True),
    ("etny-nodenithy, no CAS", trustedzone_published(copy_of(NODENITHY_TRUSTEDZONE), False), [TRUSTEDZONE], False),
    ("etny-pynithy, a CAS", trustedzone_published(copy_of(PYNITHY_TRUSTEDZONE), True), [TRUSTEDZONE], True),
    ("etny-pynithy, no CAS", trustedzone_published(copy_of(PYNITHY_TRUSTEDZONE), False), [TRUSTEDZONE], False),
    ("sdk-js nodenithy, a CAS", sdk_js_published(copy_of(SDK_JS_NODENITHY), True, False), [TRUSTEDZONE, SECURELOCK],
     True),
    ("sdk-js nodenithy, no CAS", sdk_js_published(copy_of(SDK_JS_NODENITHY), False, False), [TRUSTEDZONE, SECURELOCK],
     True),
    ("sdk-js nodenithy, unsafe", sdk_js_published(copy_of(SDK_JS_NODENITHY), False, True), [TRUSTEDZONE, SECURELOCK],
     False),
    ("sdk-js pynithy, a CAS", sdk_js_published(copy_of(SDK_JS_PYNITHY), True, False), [TRUSTEDZONE, SECURELOCK], True),
    ("sdk-js pynithy, no CAS", sdk_js_published(copy_of(SDK_JS_PYNITHY), False, False), [TRUSTEDZONE, SECURELOCK],
     True),
    ("sdk-js pynithy, unsafe", sdk_js_published(copy_of(SDK_JS_PYNITHY), False, True), [TRUSTEDZONE, SECURELOCK],
     False),
    ("sdk-py written, a CAS", sdk_py_written(sdk_py_data(True, False)), [TRUSTEDZONE, SECURELOCK], True),
    ("sdk-py written, unsafe", sdk_py_written(sdk_py_data(False, True)), [TRUSTEDZONE, SECURELOCK], False),
    ("sdk-py sorted, a CAS", sdk_py_sorted(sdk_py_data(True, False)), [TRUSTEDZONE, SECURELOCK], True),
    ("sdk-py sorted, unsafe", sdk_py_sorted(sdk_py_data(False, True)), [TRUSTEDZONE, SECURELOCK], False),
    ("sdk-py sorted, a dependent service", sdk_py_sorted(sdk_py_data(True, False), DEPENDENT),
     [TRUSTEDZONE, SECURELOCK], True),
    ("sdk-py sorted, a dependent service, unsafe", sdk_py_sorted(sdk_py_data(False, True), DEPENDENT),
     [TRUSTEDZONE, SECURELOCK], False),
]
FIELD_IDS = [name for name, _, _, _ in FIELD]


def mounted(text, services):
    """`text` with the mount of each of `services` added, in turn."""
    for service in services:
        text = compose_with_volume(text, service, MOUNTS[service])
        assert text is not None, service
    return text


def without_volumes(data, services):
    """A copy of the compose `data` without the `volumes` of `services`."""
    data = copy.deepcopy(data)
    for service in services:
        del data["services"][service]["volumes"]
    return data


@pytest.mark.parametrize("name", COPIES)
def test_a_copy_matches_the_manifest(name):
    entries = dict(reversed(line.split("  ")) for line in (COMPOSES / "MANIFEST.sha256").read_text().splitlines())
    assert sorted(entries) == sorted(COPIES)
    assert hashlib.sha256((COMPOSES / name).read_bytes()).hexdigest() == entries[name]


def test_the_mount_is_the_services_directory_on_run_ecld_read_only():
    assert CREDENTIAL_DIR == "/run/ecld-store"
    assert {service: credential_volume(CREDENTIAL_DIR, service) for service in (TRUSTEDZONE, SECURELOCK)} == MOUNTS


@pytest.mark.parametrize("name, text, services, las", FIELD, ids=FIELD_IDS)
def test_a_field_compose_is_what_the_fixture_says(name, text, services, las):
    data = load(text)
    assert [service for service in (TRUSTEDZONE, SECURELOCK) if service in data["services"]] == services
    assert ("las" in data["services"]) is las
    assert not any("volumes" in service for service in data["services"].values())
    assert re.search(r"# __\w+_ONLY__\s*$", text, re.MULTILINE) is None


@pytest.mark.parametrize("name, text, services, las", FIELD, ids=FIELD_IDS)
def test_the_services_of_a_field_compose_are_found_at_their_indentation(name, text, services, las):
    for service in (TRUSTEDZONE, SECURELOCK):
        assert compose_has_service(text, service) is (service in services)
        image = f"localhost:5000/{service}:latest" if service in services else None
        assert compose_service_image(text, service) == image
    assert compose_has_service(text, "las") is las
    assert compose_service_image(text, "las") == ("localhost:5000/etny-las:latest" if las else None)
    assert not compose_has_service(text, "ethernity") and not compose_has_service(text, "etny")


@pytest.mark.parametrize("name, text, services, las", FIELD, ids=FIELD_IDS)
def test_each_enclave_service_of_a_field_compose_takes_the_mount_once(name, text, services, las):
    before = load(text)
    with_mounts = mounted(text, services)
    after = load(with_mounts)
    assert {service: after["services"][service]["volumes"] for service in services} == {
        service: [MOUNTS[service]] for service in services}
    assert without_volumes(after, services) == before
    assert mounted(with_mounts, services) == with_mounts
    assert mounted(with_mounts, list(reversed(services))) == with_mounts
    one = mounted(text, services[:1])
    assert [service for service, block in load(one)["services"].items() if "volumes" in block] == services[:1]


@pytest.mark.parametrize("name, text, services, las", FIELD, ids=FIELD_IDS)
def test_a_service_with_volumes_keeps_them_under_one_key(name, text, services, las):
    # A list the function wrote, and a second volume of another path.
    other = {service: f"/srv/{service}:/srv:ro" for service in services}
    with_mounts = mounted(text, services)
    for service in services:
        with_mounts = compose_with_volume(with_mounts, service, other[service])
    after = load(with_mounts)
    assert {service: after["services"][service]["volumes"] for service in services} == {
        service: [MOUNTS[service], other[service]] for service in services}
    assert without_volumes(after, services) == load(text)


@pytest.mark.parametrize("dump", [sdk_py_written, sdk_py_sorted])
@pytest.mark.parametrize("held", [
    ["/srv/data:/data"],
    ["/srv/data:/data", "named:/var/lib/named"],
    [{"type": "bind", "source": "/srv/data", "target": "/data", "read_only": True}],
])
def test_a_list_the_python_sdk_dumps_takes_the_mount_as_its_last_item(dump, held):
    data = sdk_py_data(True, False)
    data["services"]["etny-securelock"]["volumes"] = copy.deepcopy(held)
    data["services"]["las"]["volumes"] = ["/var/run/aesmd:/var/run/aesmd"]
    text = dump(data)
    with_mounts = mounted(text, [TRUSTEDZONE, SECURELOCK])
    expected = load(text)
    expected["services"]["etny-securelock"]["volumes"] = held + [MOUNTS[SECURELOCK]]
    expected["services"]["etny-trustedzone"]["volumes"] = [MOUNTS[TRUSTEDZONE]]
    assert load(with_mounts) == expected
    assert mounted(with_mounts, [TRUSTEDZONE, SECURELOCK]) == with_mounts


def test_a_key_of_another_services_mapping_is_no_service():
    text = sdk_py_sorted(sdk_py_data(True, False), DEPENDENT)
    lines = text.splitlines()
    nested = [index for index, line in enumerate(lines) if line.strip() == "etny-trustedzone:"]
    assert len(nested) == 2 and lines[nested[0]].startswith("      ") and lines[nested[1]] == "  etny-trustedzone:"
    after = load(mounted(text, [TRUSTEDZONE, SECURELOCK]))
    assert after["services"]["api"] == DEPENDENT["api"]
    assert after["services"]["etny-trustedzone"]["volumes"] == [MOUNTS[TRUSTEDZONE]]
    assert compose_service_image(text, TRUSTEDZONE) == "localhost:5000/etny-trustedzone:latest"
    # A compose that names the enclave services in such a mapping alone has
    # neither.
    data = sdk_py_data(True, False)
    del data["services"]["etny-trustedzone"], data["services"]["etny-securelock"]
    alone = sdk_py_sorted(data, DEPENDENT)
    assert "etny-trustedzone:" in alone
    for service in (TRUSTEDZONE, SECURELOCK):
        assert not compose_has_service(alone, service)
        assert compose_service_image(alone, service) is None
        assert compose_with_volume(alone, service, MOUNTS[service]) is None


@pytest.mark.parametrize("name, text, services, las", FIELD, ids=FIELD_IDS)
def test_a_compose_with_carriage_returns_takes_the_mount(name, text, services, las):
    returned = text.replace("\n", "\r\n")
    after = load(mounted(returned, services))
    assert without_volumes(after, services) == load(text)
    assert all(compose_service_image(returned, service) == f"localhost:5000/{service}:latest" for service in services)


def test_a_compose_that_ends_in_its_volumes_without_a_line_feed_takes_the_mount():
    # The Python SDK's compose of a network without the `ethernity` network
    # section, whose last service ends the file.
    data = sdk_py_data(False, True)
    del data["networks"]
    data["services"]["etny-trustedzone"]["volumes"] = ["/srv/data:/data"]
    ended = sdk_py_written(data).rstrip("\n")
    assert ended.endswith("    volumes:\n    - /srv/data:/data")
    after = load(mounted(ended, [TRUSTEDZONE, SECURELOCK]))
    assert after["services"]["etny-trustedzone"]["volumes"] == ["/srv/data:/data", MOUNTS[TRUSTEDZONE]]
    assert without_volumes(after, [TRUSTEDZONE, SECURELOCK]) == without_volumes(load(ended), [TRUSTEDZONE])


@pytest.mark.parametrize("volumes", ["[/srv/data:/data]", "*shared", "[]"])
def test_volumes_that_are_no_block_list_take_no_mount(volumes):
    text = sdk_py_written(sdk_py_data(True, False)).replace(
        "  etny-trustedzone:\n", f"  etny-trustedzone:\n    volumes: {volumes}\n")
    assert f"    volumes: {volumes}\n" in text
    assert compose_has_service(text, TRUSTEDZONE)
    assert compose_with_volume(text, TRUSTEDZONE, MOUNTS[TRUSTEDZONE]) is None
    assert compose_with_volume(text, SECURELOCK, MOUNTS[SECURELOCK]) is not None


def test_a_service_written_as_a_flow_mapping_is_there_and_takes_no_mount():
    block = "  etny-trustedzone:\n    container_name: etny-trustedzone\n"
    text = sdk_py_written(sdk_py_data(False, True))
    assert block in text
    flow = text.replace(block, "  etny-trustedzone: {image: 'localhost:5000/etny-trustedzone'}\n  held:\n"
                               "    container_name: etny-trustedzone\n")
    assert load(flow)["services"]["etny-trustedzone"] == {"image": "localhost:5000/etny-trustedzone"}
    assert compose_has_service(flow, TRUSTEDZONE)
    assert compose_service_image(flow, TRUSTEDZONE) is None
    assert compose_with_volume(flow, TRUSTEDZONE, MOUNTS[TRUSTEDZONE]) is None
    assert load(mounted(flow, [SECURELOCK]))["services"]["etny-securelock"]["volumes"] == [MOUNTS[SECURELOCK]]


@pytest.mark.parametrize("host", ["C:\\Users\\node\\ecld-store", "/run/it's here/ecld-store", "/run/ecld store"])
def test_a_mount_of_any_host_path_is_read_back_as_written(host):
    text = sdk_py_written(sdk_py_data(True, False))
    volume = credential_volume(host, TRUSTEDZONE)
    with_mount = compose_with_volume(text, TRUSTEDZONE, volume)
    assert load(with_mount)["services"]["etny-trustedzone"]["volumes"] == [volume]
    assert compose_with_volume(with_mount, TRUSTEDZONE, volume) == with_mount


@pytest.mark.parametrize("text", ["", "version: '3.2'\n", "services:\n", "services:\nnetworks:\n  ethernity: {}\n",
                                  "etny-trustedzone:\n  image: x\n"])
def test_a_compose_without_services_has_none(text):
    assert not compose_has_service(text, TRUSTEDZONE)
    assert compose_service_image(text, TRUSTEDZONE) is None
    assert compose_with_volume(text, TRUSTEDZONE, MOUNTS[TRUSTEDZONE]) is None
