"""The EPC a node claims for its memory probe, in MiB (MATCHING-SYNC-PLAN.md
B03; OD20 A, OD21 A; pox-smart-contract-litvm 2f600f6 spec/bench-record.json
inputs, ETNY_EPC_MIB): the sum of the platform's EPC sections, read from the
kernel's sysfs where it has it (the sgx_total_bytes of each NUMA node) and
else from CPUID leaf 0x12 through sgx/epc_sections (built at install from
sgx/epc_sections.c), lowered by the operator's <NETWORK>_EPC_MIB when that
is above 0 and never raised by it. A claim the agent cannot read, or one
outside 1 to 2^31 - 1 MiB, is none: no probe runs on it."""
import glob
import subprocess
from pathlib import Path

MIB = 2**20
CLAIM_MAX_MIB = 2**31 - 1
# The type of a CPUID leaf 0x12 sub-leaf that enumerates an EPC section (EAX
# bits 3:0).
SECTION_TYPE = 1
# The kernel's EPC size per NUMA node (Linux sgx_total_bytes).
SYSFS_GLOB = '/sys/devices/system/node/node*/x86/sgx_total_bytes'
# The helper sgx/Makefile builds at install, beside node/.
HELPER = Path(__file__).resolve().parents[1] / 'sgx' / 'epc_sections'
HELPER_TIMEOUT_SECONDS = 10


def section_bytes(eax, ebx, ecx, edx):
    """The size in bytes of the EPC section a CPUID leaf 0x12 sub-leaf
    enumerates (Intel SDM, the SGX EPC enumeration leaf): ECX bits 31:12 are
    the size's bits 31:12 and EDX bits 19:0 its bits 51:32; None for a
    sub-leaf whose type (EAX bits 3:0) is not 1."""
    if int(eax) & 0xF != SECTION_TYPE:
        return None
    return ((int(edx) & 0xFFFFF) << 32) | (int(ecx) & 0xFFFFF000)


def sections_total_bytes(text):
    """The sum of the EPC sections of sgx/epc_sections's output, one
    '<sub-leaf> <eax> <ebx> <ecx> <edx>' line per section, the registers in
    hexadecimal. Raises ValueError on a malformed line, a sub-leaf that is
    not a section, or no section."""
    total = 0
    lines = [line for line in str(text).splitlines() if line.strip()]
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f'not a sub-leaf line: {line!r}')
        size = section_bytes(*(int(part, 16) for part in parts[1:]))
        if size is None:
            raise ValueError(f'sub-leaf {parts[0]} is not an EPC section')
        total += size
    if not lines:
        raise ValueError('no EPC section')
    return total


def sysfs_total_bytes(paths):
    """The sum of the kernel's sgx_total_bytes files `paths`; None without
    one or with one that does not hold a decimal."""
    total = 0
    found = False
    for path in paths:
        try:
            text = Path(path).read_text().strip()
        except OSError:
            return None
        if not text.isdigit():
            return None
        total += int(text)
        found = True
    return total if found else None


def claim_mib(total_bytes, setting_mib):
    """The claim in MiB of `total_bytes` of EPC: floor(total / 2^20), at most
    `setting_mib` when that is above 0; None when it is not 1 to
    CLAIM_MAX_MIB."""
    if total_bytes is None:
        return None
    mib = int(total_bytes) // MIB
    if int(setting_mib) > 0:
        mib = min(mib, int(setting_mib))
    return mib if 1 <= mib <= CLAIM_MAX_MIB else None


def read_claim(setting_mib, sysfs_glob=SYSFS_GLOB, helper=HELPER, run=subprocess.run):
    """(claim MiB or None, where it was read): the kernel's sysfs when it
    holds the EPC size, else sgx/epc_sections, the claim by claim_mib; None
    and the reason when neither is read."""
    total = sysfs_total_bytes(sorted(glob.glob(sysfs_glob)))
    if total is not None:
        return claim_mib(total, setting_mib), 'sysfs sgx_total_bytes'
    try:
        done = run([str(helper)], capture_output=True, text=True, timeout=HELPER_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f'{helper} did not run ({e})'
    if done.returncode != 0:
        return None, f'{helper} exited {done.returncode}: the processor reports no SGX EPC'
    try:
        total = sections_total_bytes(done.stdout)
    except ValueError as e:
        return None, f'{helper}: {e}'
    return claim_mib(total, setting_mib), 'CPUID leaf 0x12'
