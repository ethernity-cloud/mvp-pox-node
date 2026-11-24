"""The node's memory probe run (MATCHING-SYNC-PLAN.md B02; OD17 A, OD20 A,
OD22 A; pox-smart-contract-litvm 2f600f6 spec/bench-record.json inputs,
session, identity): probe_run finds a size's probe in the newest
ECImageRegistryV4 default release that lists the size and the image the
registry holds under the size's name, tries the listed sizes largest first
and keeps the first admitted record, and writes only the node's three
inputs to the integration bucket's .env; etny-node.py runs it on a network
whose pricing names a PoxMatching and that names a ValidatorRegistry, in
the task slot after the integration tests, never while an order holds the
slot or a claim can be approved, reads both objects before the bucket is
wiped, keeps the admitted record, and backs off on the same claim after a
run that admits none. The containers are stubs."""
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from web3 import exceptions

import bench_record
import capability
import probe_run

NODE = Path(__file__).resolve().parents[1]
SPEC = Path(__file__).parent / "spec"
BENCH = json.loads((SPEC / "bench-record.json").read_text())
LISTED = b"\x11" * 32
NONE = b"\x00" * 32
PATTERN = "ecld-probe-amoy-{size}g"


class Registry:
    """An ECImageRegistryV4 stand-in: `releases` lists, per release index,
    {size: measurement}; `images` maps a name to (hash, cert, compose)."""

    def __init__(self, releases, images):
        self.releases = releases
        self.images = images
        self.read = []

    def defaultReleaseCount(self):
        return len(self.releases)

    def probeMrenclave(self, index, size):
        self.read.append((index, size))
        return self.releases[index].get(size, NONE)

    def getLatestTrustedZoneImageCertPublicKey(self, name, version):
        if name not in self.images:
            raise exceptions.ContractLogicError("execution reverted")
        return list(self.images[name])


def latest_of(registry):
    def latest(name):
        try:
            return tuple(registry.getLatestTrustedZoneImageCertPublicKey(name, "v3"))
        except exceptions.ContractLogicError:
            return None
    return latest


def test_a_sizes_name_is_the_pattern_with_the_size():
    assert probe_run.probe_image_name(PATTERN, 2) == "ecld-probe-amoy-2g"
    assert probe_run.probe_image_name("", 2) is None
    assert probe_run.probe_image_name("ecld-probe", 2) is None


def test_the_newest_release_listing_the_size_is_taken():
    registry = Registry([{2: LISTED}, {2: b"\x22" * 32, 4: b"\x44" * 32}, {4: b"\x55" * 32}], {})
    assert probe_run.newest_release_listing(3, registry.probeMrenclave, 2) == (1, b"\x22" * 32)
    assert probe_run.newest_release_listing(3, registry.probeMrenclave, 4) == (2, b"\x55" * 32)
    assert probe_run.newest_release_listing(3, registry.probeMrenclave, 8) is None
    assert probe_run.newest_release_listing(0, registry.probeMrenclave, 2) is None


def test_a_probe_is_found_with_a_listed_release_and_a_registered_image():
    images = {"ecld-probe-amoy-2g": ("QmProbe2", BENCH["probeKey"]["publicKeyPem"], "QmCompose2"),
              "ecld-probe-amoy-8g": ("QmProbe8", "", "QmCompose8")}
    registry = Registry([{2: LISTED, 8: LISTED}, {2: b"\x22" * 32}], images)
    found = probe_run.find_probe(2, PATTERN, registry.defaultReleaseCount, registry.probeMrenclave, latest_of(registry))
    assert found == probe_run.ProbeImage(2, 1, b"\x22" * 32, "ecld-probe-amoy-2g", "QmProbe2",
                                         BENCH["probeKey"]["publicKeyPem"], "QmCompose2")
    # A size no release lists, a name the registry holds no image under, an
    # image without a certificate and no name are not found.
    assert probe_run.find_probe(4, PATTERN, registry.defaultReleaseCount, registry.probeMrenclave,
                                latest_of(registry)) is None
    assert probe_run.find_probe(8, PATTERN, registry.defaultReleaseCount, registry.probeMrenclave,
                                latest_of(registry)) is None
    assert probe_run.find_probe(2, "", registry.defaultReleaseCount, registry.probeMrenclave,
                                latest_of(registry)) is None


def test_the_sizes_are_tried_largest_first_up_to_the_memory_figure():
    assert probe_run.sizes_to_try(60) == [32, 16, 8, 4, 2, 1]
    assert probe_run.sizes_to_try(64) == [64, 32, 16, 8, 4, 2, 1]
    assert probe_run.sizes_to_try(0) == []


def test_the_env_holds_the_three_node_values_and_no_session_value():
    env = probe_run.probe_env("0x470D8B388f4a0AE6bD512b74038c7e87957bb8FE", 37 * 2**30, 1024)
    assert env == {"ETNY_NODE_ADDRESS": "0x470d8b388f4a0ae6bd512b74038c7e87957bb8fe",
                   "ETNY_STORAGE_FREE_BYTES": 37 * 2**30, "ETNY_EPC_MIB": 1024}
    for key in env:
        assert key in BENCH["inputs"]["bucketEnv"]
    assert not set(env) & set(BENCH["session"]["values"])


def probe(size):
    return probe_run.ProbeImage(size, 0, LISTED, f"ecld-probe-amoy-{size}g", f"QmProbe{size}", "cert", "QmCompose")


def test_the_largest_size_that_admits_a_record_is_kept():
    ran, logged = [], []
    found = {32: None, 16: probe(16), 8: probe(8), 4: probe(4)}
    outcomes = {16: None, 8: ("p8", "t8"), 4: ("p4", "t4")}

    def admit(p, objects):
        return ({"size": p.size}, None) if p.size == 4 else (None, "share: x")
    result = probe_run.run_largest([32, 16, 8, 4], found.get, lambda p: ran.append(p.size) or outcomes[p.size], admit,
                                   logged.append)
    assert result == (probe(4), {"size": 4})
    assert ran == [16, 8, 4] and len(logged) == 3
    assert probe_run.run_largest([], found.get, None, None, logged.append) == (None, None)


# ----------------------------------------------------------- etny-node.py

@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("etny_node", NODE / "etny-node.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADVERTISED = ("ecld-nodenithy-amoy", {"gflops": 250, "sig_der_hex": "3065ab"})


def probe_node(agent, monkeypatch, tmp_path, matching_network=True, registry="0x" + "70" * 20,
               pattern=PATTERN, advertised=ADVERTISED):
    node = object.__new__(agent.EtnyPoXNode)
    node.logger = logging.getLogger("test")
    node._EtnyPoXNode__matching_network = matching_network
    node._EtnyPoXNode__matching = None
    node._EtnyPoXNode__network = "polygon_amoy"
    node._EtnyPoXNode__network_config = SimpleNamespace(probe_image=pattern, epc_mib=0, name="polygon_amoy",
                                                        chain_id=80002, contract_address="0x" + "b8" * 20)
    node._EtnyPoXNode__bench_store = bench_record.BenchStore(tmp_path / "bench.json")
    node._EtnyPoXNode__probe_retry = (0, 0.0)
    node._EtnyPoXNode__replication_only = False
    node._EtnyPoXNode__integration_images = []
    node._EtnyPoXNode__last_registry_check_at = agent.time.monotonic()
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__validator_registry_address", lambda self: registry)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__advertised_record", lambda self: advertised)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__offer_figures",
                        lambda self, bench: SimpleNamespace(memory_gib=20, storage_gib=100))
    return node


@pytest.mark.parametrize("changes, due", [
    ({}, True),
    ({"matching_network": False}, False),
    ({"registry": ""}, False),
    ({"pattern": ""}, False),
    ({"advertised": None}, False),
])
def test_the_probe_is_due_only_on_a_matching_cas_network_with_a_name_and_a_trustedzone_record(
        agent, monkeypatch, tmp_path, changes, due):
    node = probe_node(agent, monkeypatch, tmp_path, **changes)
    assert node._EtnyPoXNode__probe_due(1_800_000_000, 0.0) is due


def test_the_probe_is_not_due_while_the_stored_record_stands_or_the_backoff_runs(agent, monkeypatch, tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    record = {"t0": 1_799_999_980, "t1": 1_800_000_000, "size": 2}
    node._EtnyPoXNode__bench_store.put(record, "QmProbe2", 1_800_000_000)
    assert node._EtnyPoXNode__probe_due(1_800_000_100, 0.0) is False
    assert node._EtnyPoXNode__probe_due(1_800_000_000 + 7 * 86400 - 86400, 0.0) is True
    node._EtnyPoXNode__probe_retry = (1, 600.0)
    assert node._EtnyPoXNode__probe_due(1_800_000_000 + 7 * 86400, 599.0) is False


def test_no_probe_runs_while_an_order_holds_the_task_slot_or_a_claim_can_be_approved(agent, monkeypatch, tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    ran = []
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__run_probe_if_due", lambda self: ran.append(True))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__mark_stale_integration_tests", lambda self, images: set())
    monkeypatch.setattr(agent, "get_task_running_on", lambda: "bloxberg_testnet")
    node._EtnyPoXNode__maybe_retry_integration_tests()
    assert ran == []
    monkeypatch.setattr(agent, "get_task_running_on", lambda: None)
    node._EtnyPoXNode__matching = SimpleNamespace(claim_approvable=lambda: True)
    node._EtnyPoXNode__maybe_retry_integration_tests()
    assert ran == []
    node._EtnyPoXNode__matching = None
    slots = []
    monkeypatch.setattr(agent, "acquire_task_slot", lambda name: slots.append(("acquire", name)))
    monkeypatch.setattr(agent, "release_task_slot", lambda name: slots.append(("release", name)))
    node._EtnyPoXNode__maybe_retry_integration_tests()
    assert ran == [True] and slots == [("acquire", "polygon_amoy"), ("release", "polygon_amoy")]


def test_an_admitted_record_is_stored_and_a_run_admitting_none_backs_off_on_the_same_claim(agent, monkeypatch,
                                                                                         tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    claims, tried = [], []
    monkeypatch.setattr(agent.epc_claim, "read_claim", lambda setting: (1024, "sysfs sgx_total_bytes"))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__find_probe", lambda self, size: probe(size))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__run_probe",
                        lambda self, p, claim: claims.append(claim) or ("probe", "time"))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__admit_bench_record",
                        lambda self, p, objects, advertised: tried.append(p.size) or (None, "share: refused"))
    node._EtnyPoXNode__run_probe_if_due()
    # The memory figure 20 GiB: sizes 16 down to 1, each refused.
    assert tried == [16, 8, 4, 2, 1] and set(claims) == {1024}
    assert node._EtnyPoXNode__bench_store.entry is None
    failures, due_at = node._EtnyPoXNode__probe_retry
    assert failures == 1 and due_at > agent.time.monotonic() + agent.INTEGRATION_TEST_RETRY_MIN_SECONDS - 5
    node._EtnyPoXNode__probe_retry = (1, 0.0)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__admit_bench_record",
                        lambda self, p, objects, advertised: ({"size": p.size, "encmem": p.size, "custorage": 37,
                                                               "epc": 1024, "t0": 1, "t1": 2}, None)
                        if p.size == 8 else (None, "share: refused"))
    node._EtnyPoXNode__run_probe_if_due()
    assert node._EtnyPoXNode__bench_store.entry["size"] == 8
    assert node._EtnyPoXNode__bench_store.entry["image_hash"] == "QmProbe8"
    assert node._EtnyPoXNode__probe_retry == (0, 0.0)


def test_an_unreadable_claim_runs_no_probe(agent, monkeypatch, tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    monkeypatch.setattr(agent.epc_claim, "read_claim", lambda setting: (None, "no EPC"))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__find_probe",
                        lambda self, size: pytest.fail("a probe was looked for"))
    node._EtnyPoXNode__run_probe_if_due()
    assert node._EtnyPoXNode__probe_retry[0] == 1


class Bucket:
    """The swift-stream service of a probe run: the objects the probe wrote,
    every read and the bucket's deletion, in order."""

    def __init__(self, objects):
        self.objects = objects
        self.calls = []

    def get_file_content(self, bucket, name):
        self.calls.append(("read", bucket, name))
        return (name in self.objects, self.objects.get(name))


def test_the_run_writes_the_inputs_and_reads_both_objects_before_the_wipe(agent, monkeypatch, tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    node._EtnyPoXNode__address = "0x470D8B388f4a0AE6bD512b74038c7e87957bb8FE"
    node.cache_config = SimpleNamespace(base_path=tmp_path)
    node.storage = SimpleNamespace(download_many=lambda hashes, attempts, delay: hashes == ["QmProbe2", "QmCompose"])
    bucket = Bucket({"probe.2.etny": "line\nsig\nplatform\n", "probe.time.2.etny": "a\nb\n"})
    node.swift_stream_service = bucket
    prepared, started = [], []
    monkeypatch.setattr(agent, "run_subprocess", lambda argv, logger: None)
    monkeypatch.setattr(agent.HardwareInfoProvider, "get_storage_bytes", staticmethod(lambda path: (500, 37 * 2**30)))
    monkeypatch.setattr(agent.EtnyPoXNode, "build_prerequisites_integration_test",
                        lambda self, bucket_name, order_id, compose, env: prepared.append((bucket_name, compose, env)))
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__start_enclave_environment",
                        lambda self, image_hash: started.append(image_hash))
    monkeypatch.setattr(agent.EtnyPoXNode, "wait_for_enclave_v2", lambda self, bucket_name, name, timeout: True)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__clean_up_integration_test",
                        lambda self: bucket.calls.append(("wipe",)))
    texts = node._EtnyPoXNode__run_probe(probe(2), 1024)
    assert texts == ("line\nsig\nplatform\n", "a\nb\n")
    assert prepared == [(agent.INTEGRATION_BUCKET, f"{tmp_path}/QmCompose",
                         {"ETNY_NODE_ADDRESS": "0x470d8b388f4a0ae6bd512b74038c7e87957bb8fe",
                          "ETNY_STORAGE_FREE_BYTES": 37 * 2**30, "ETNY_EPC_MIB": 1024})]
    assert started == ["QmProbe2"]
    assert bucket.calls == [("read", agent.INTEGRATION_BUCKET, "probe.2.etny"),
                            ("read", agent.INTEGRATION_BUCKET, "probe.time.2.etny"), ("wipe",)]


def test_a_probe_that_writes_no_record_gives_no_objects(agent, monkeypatch, tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    node._EtnyPoXNode__address = "0x" + "47" * 20
    node.cache_config = SimpleNamespace(base_path=tmp_path)
    node.storage = SimpleNamespace(download_many=lambda hashes, attempts, delay: True)
    bucket = Bucket({})
    node.swift_stream_service = bucket
    monkeypatch.setattr(agent, "run_subprocess", lambda argv, logger: None)
    monkeypatch.setattr(agent.HardwareInfoProvider, "get_storage_bytes", staticmethod(lambda path: (500, 100)))
    monkeypatch.setattr(agent.EtnyPoXNode, "build_prerequisites_integration_test", lambda self, *a: None)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__start_enclave_environment", lambda self, image_hash: None)
    monkeypatch.setattr(agent.EtnyPoXNode, "wait_for_enclave_v2", lambda self, bucket_name, name, timeout: False)
    monkeypatch.setattr(agent.EtnyPoXNode, "_EtnyPoXNode__clean_up_integration_test",
                        lambda self: bucket.calls.append(("wipe",)))
    assert node._EtnyPoXNode__run_probe(probe(2), 1024) is None
    assert bucket.calls == [("wipe",)]


def test_the_registry_finds_the_newer_releases_probe_and_none_on_a_registry_before_v4(agent, monkeypatch, tmp_path):
    node = probe_node(agent, monkeypatch, tmp_path)
    images = {"ecld-probe-amoy-2g": ("QmProbe2", "cert2", "QmCompose2")}
    registry = Registry([{2: LISTED}, {2: b"\x22" * 32}], images)
    node._EtnyPoXNode__image_registry = SimpleNamespace(caller=lambda: registry)
    found = node._EtnyPoXNode__find_probe(2)
    assert (found.release, found.image_hash, found.cert) == (1, "QmProbe2", "cert2")
    assert node._EtnyPoXNode__find_probe(4) is None

    class Before:
        """A registry before V4: the calls of the bundled ABI revert."""
        def defaultReleaseCount(self):
            raise exceptions.ContractLogicError("execution reverted")

        def probeMrenclave(self, index, size):
            raise exceptions.ContractLogicError("execution reverted")

        def getLatestTrustedZoneImageCertPublicKey(self, name, version):
            return ["QmProbe2", "cert2", "QmCompose2"]
    node._EtnyPoXNode__image_registry = SimpleNamespace(caller=lambda: Before())
    assert node._EtnyPoXNode__find_probe(2) is None


def test_the_bundled_registry_abi_holds_the_v4_release_reads():
    abi = json.loads((NODE / "image_registry.abi").read_text())
    names = {e.get("name"): e for e in abi}
    assert [i["type"] for i in names["probeMrenclave"]["inputs"]] == ["uint256", "uint8"]
    assert names["probeMrenclave"]["outputs"][0]["type"] == "bytes32"
    assert names["defaultReleaseCount"]["outputs"][0]["type"] == "uint256"
    selectors = json.loads((SPEC / "abi-selectors.json").read_text())["entries"]
    listed = {e["signature"] for e in selectors if e["abi"] == "IECImageRegistryV4.json"}
    assert {"probeMrenclave(uint256,uint8)", "defaultReleaseCount()"} <= listed
