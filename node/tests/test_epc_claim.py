"""epc_claim (MATCHING-SYNC-PLAN.md B03; OD20 A, OD21 A): the EPC a node
claims for its probe is the sum of its EPC sections in whole MiB, read from
the kernel's sgx_total_bytes where it has them and else from CPUID leaf 0x12
through sgx/epc_sections, lowered by the operator's setting only, a plain
figure of 1 to 2^31 - 1 MiB or none. The register values below are laid out
as the Intel SDM's SGX EPC enumeration leaf gives them (type 1 in EAX bits
3:0, the size in ECX bits 31:12 and EDX bits 19:0): a 93.5 MiB section of a
client part, two 64 GiB sections of a two-socket server, a 2 GiB section a
hypervisor gives a guest."""
from types import SimpleNamespace

import pytest

import epc_claim

CLIENT = "2 70200001 00000000 05d80001 00000000\n"
SERVER = "2 00000001 00000080 00000001 00000010\n3 00000001 00000100 00000001 00000010\n"
GUEST = "2 c0000001 0000000f 80000001 00000000\n"


@pytest.mark.parametrize("output, mib", [(CLIENT, 93), (SERVER, 131072), (GUEST, 2048)])
def test_the_sections_sum_to_the_claim_in_whole_mib(output, mib):
    assert epc_claim.claim_mib(epc_claim.sections_total_bytes(output), 0) == mib


def test_a_section_size_is_ecx_bits_31_to_12_and_edx_bits_19_to_0():
    assert epc_claim.section_bytes(0x70200001, 0, 0x05D80001, 0) == 0x05D80000
    assert epc_claim.section_bytes(0x1, 0, 0x00000FFF, 0x10) == 0x10 << 32
    assert epc_claim.section_bytes(0x1, 0, 0, 0xFFF00001) == 1 << 32
    assert epc_claim.section_bytes(0x0, 0, 0x05D80001, 0) is None
    assert epc_claim.section_bytes(0x2, 0, 0x05D80001, 0) is None


@pytest.mark.parametrize("output", ["", "2 70200001 00000000 05d80001\n", "2 70200000 00000000 05d80001 00000000\n",
                                    "2 zz 00000000 05d80001 00000000\n"])
def test_a_malformed_or_empty_output_is_refused(output):
    with pytest.raises(ValueError):
        epc_claim.sections_total_bytes(output)


@pytest.mark.parametrize("total, setting, mib", [
    (2048 * 2**20, 0, 2048),
    (2048 * 2**20, 1024, 1024),
    (2048 * 2**20, 4096, 2048),
    (2**20 - 1, 0, None),
    (0, 0, None),
    (None, 0, None),
    ((2**31) * 2**20, 0, None),
    ((2**31) * 2**20, 2**31 - 1, 2**31 - 1),
])
def test_the_setting_only_lowers_the_claim_and_the_claim_is_one_to_two_to_the_31_minus_one(total, setting, mib):
    assert epc_claim.claim_mib(total, setting) == mib


def test_the_kernels_sysfs_is_read_first(tmp_path):
    for node, value in (("node0", "68719476736"), ("node1", "68719476736")):
        (tmp_path / node / "x86").mkdir(parents=True)
        (tmp_path / node / "x86" / "sgx_total_bytes").write_text(value + "\n")
    ran = []
    claim, source = epc_claim.read_claim(0, str(tmp_path / "node*" / "x86" / "sgx_total_bytes"),
                                         run=lambda *a, **k: ran.append(a))
    assert (claim, source) == (131072, "sysfs sgx_total_bytes") and ran == []


def test_without_sysfs_the_cpuid_helper_is_read(tmp_path):
    def run(argv, **kwargs):
        assert argv == [str(tmp_path / "epc_sections")]
        return SimpleNamespace(returncode=0, stdout=GUEST)
    claim, source = epc_claim.read_claim(1024, str(tmp_path / "none*"), helper=tmp_path / "epc_sections", run=run)
    assert (claim, source) == (1024, "CPUID leaf 0x12")


@pytest.mark.parametrize("run", [
    lambda argv, **kw: SimpleNamespace(returncode=1, stdout=""),
    lambda argv, **kw: SimpleNamespace(returncode=0, stdout="garbage\n"),
    lambda argv, **kw: (_ for _ in ()).throw(FileNotFoundError("no helper")),
])
def test_an_unreadable_claim_is_none_with_its_reason(tmp_path, run):
    claim, reason = epc_claim.read_claim(0, str(tmp_path / "none*"), helper=tmp_path / "epc_sections", run=run)
    assert claim is None and reason


def test_a_sysfs_file_that_holds_no_decimal_gives_no_figure(tmp_path):
    (tmp_path / "sgx_total_bytes").write_text("x\n")
    assert epc_claim.sysfs_total_bytes([tmp_path / "sgx_total_bytes"]) is None
    assert epc_claim.sysfs_total_bytes([]) is None


def test_the_helper_is_built_by_the_installers_make_target():
    root = epc_claim.HELPER.parents[1]
    makefile = (root / "sgx" / "Makefile").read_text()
    assert "all: sgx_enable epc_sections" in makefile
    assert "make all -C sgx/" in (root / "roles" / "ethernity_agent_node" / "tasks" / "cap" / "sgx.yml").read_text()
    source = (root / "sgx" / "epc_sections.c").read_text()
    assert "__cpuid_count(EPC_LEAF, subleaf" in source and "#define FIRST_SECTION_SUBLEAF 2" in source
