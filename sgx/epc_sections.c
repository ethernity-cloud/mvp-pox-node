/*
 * The EPC sections of this processor, read from CPUID leaf 0x12 (Intel SDM
 * Vol. 2A, CPUID, "Intel SGX EPC Enumeration Leaf"): sub-leaf 2 and each
 * sub-leaf after it while its type, EAX bits 3:0, is 1 (an EPC section).
 * One line per section is written to stdout, the sub-leaf and the four
 * registers in hexadecimal:
 *
 *     <sub-leaf> <eax> <ebx> <ecx> <edx>
 *
 * The node agent (node/epc_claim.py) sums the sections' sizes, ECX bits
 * 31:12 and EDX bits 19:0, into the EPC its memory probe claims. The exit
 * status is 1, with nothing written, on a processor whose highest basic
 * leaf is below 0x12 or that reports no SGX (CPUID.(EAX=7,ECX=0):EBX bit 2).
 */
#include <cpuid.h>
#include <stdio.h>

#define EPC_LEAF 0x12
#define FIRST_SECTION_SUBLEAF 2
#define MAX_SECTIONS 64
#define SECTION_TYPE 1

int main(void)
{
    unsigned int eax, ebx, ecx, edx;
    unsigned int subleaf;

    if (__get_cpuid_max(0, NULL) < EPC_LEAF)
        return 1;
    __cpuid_count(7, 0, eax, ebx, ecx, edx);
    if (!(ebx & (1u << 2)))
        return 1;
    for (subleaf = FIRST_SECTION_SUBLEAF; subleaf < FIRST_SECTION_SUBLEAF + MAX_SECTIONS; subleaf++) {
        __cpuid_count(EPC_LEAF, subleaf, eax, ebx, ecx, edx);
        if ((eax & 0xf) != SECTION_TYPE)
            break;
        printf("%u %08x %08x %08x %08x\n", subleaf, eax, ebx, ecx, edx);
    }
    return 0;
}
