"""The release's version (PHASE-E-PLAN.md E39, WS-G): the changelog's top
entry is the version node/.env carries (config.version, logged at start and
posted by the heartbeat as v<version>), and the v3.8.0 entry names what an
operator configures or sees of 3.8.0."""
import re
from pathlib import Path

import config

CHANGELOG = Path(__file__).resolve().parents[2] / "changelog"
VERSION_LINE = re.compile(r"^v(\d+\.\d+\.\d+)$")


def entries():
    """[(version, text)] of the changelog, top entry first."""
    found = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        match = VERSION_LINE.match(line.strip())
        if match:
            found.append((match.group(1), []))
        elif found:
            found[-1][1].append(line)
    return [(version, "\n".join(lines)) for version, lines in found]


def test_the_changelog_opens_with_the_release():
    top, _ = entries()[0]
    assert top == "3.8.15"


def test_the_3815_entry_names_the_polygon_mainnet_fee_ceiling():
    text = dict(entries())["3.8.15"]
    for item in ("POLYGON_MAINNET_MAX_FEE_PER_GAS", "500 gwei", "where it was 200", "int(baseFee * 1.1)", "32 gwei",
                 "get_transaction_build", "308.1 gwei", "152.7 gwei", "425.4 gwei", "Network base fee is too high!",
                 "20 tries", "Restarting due to error", "Warning while sending heartbeat", "no DO request",
                 "heartbeat", "paid in POL"):
        assert item in text, item


def test_the_3815_entry_names_the_ways_to_another_ceiling():
    text = dict(entries())["3.8.15"]
    for item in ("POLYGON_MAINNET_MAX_FEE_PER_GAS in the service's environment", "etny-node.service",
                 "config.parse_networks", "load_dotenv", "--polygon_mainnet_max_fee_per_gas", "parse_config_to_args",
                 "POLYGON_MAINNET_MAX_FEE_PER_GAS=<gwei>", "/home/vagrant/etny/node/config", "with a newline",
                 "origin/master"):
        assert item in text, item


def test_the_3814_entry_names_the_queues_per_generation():
    text = dict(entries())["3.8.14"]
    for item in ("ETNY_SCONE_QUEUES", "utils.enclave_compose_env", "SCONE_QUEUES_LEGACY_MAX", "--tcs",
                 "within 4 and 8", "legacy", "pox=", "order 22069"):
        assert item in text, item


def test_the_3813_entry_names_the_gflops_unit():
    text = dict(entries())["3.8.13"]
    for item in ("cuv 4", "GFLOPS", "GFLOPS_PER_CU", "CU_MAX_GFLOPS", "(6400", "CU_VERSION_MIN", "cugflops",
                 "GflopsAboveMax", "CuVersionBelowMinimum", "node above maxGflops", "RecordStore.retest_pending",
                 "predecessor_gflops", "maxGflops", "gflopsMax"):
        assert item in text, item


def test_the_3813_entry_names_the_gflops_contracts():
    text = dict(entries())["3.8.13"]
    for item in ("0x830c781F0352050343211B6BFA83600B3e77647C", "0xd1182deA61CDa785C320cdBE5888EaC4178cAAF2",
                 "bloxberg_testnet_unsafe", "polygon_amoy_unsafe", "node/.env"):
        assert item in text, item


def test_the_3813_entry_names_the_record_missing_hold():
    text = dict(entries())["3.8.13"]
    for item in ("9 RecordMissing", "capability.RecordMissingHold", "record_missing_hold",
                 "node/<network>/broker_cursor.json", "RETEST_REASONS 1..6, 8 and 9", "RECORD_MISSING_POLL_SECONDS",
                 "requires dcap", "has no hold", "capability.unsafe_only", "capability.cancel_reaction",
                 "RecordStore.image_of", "indexed dproc topic", "dp_request_cancelled"):
        assert item in text, item


def test_the_3812_entry_names_the_intakes_cors_answers():
    text = dict(entries())["3.8.12"]
    for item in ("ipfs_intake.py", "CORS", "OPTIONS", "Access-Control-Allow-Origin: *",
                 "X-Etny-Owner-Signature", "refusals included"):
        assert item in text, item


def test_the_3811_entry_names_the_scone_queues():
    text = dict(entries())["3.8.11"]
    for item in ("ETNY_SCONE_QUEUES", "utils.scone_queues", "SCONE_QUEUES", "64 TCS", "cuv 3"):
        assert item in text, item


def test_the_3810_entry_names_the_settlement_reward():
    text = dict(entries())["3.8.10"]
    for item in ("settlement_reward_wei", "settlementOf", "legacy"):
        assert item in text


def test_the_389_entry_names_the_standing_request_gate():
    text = dict(entries())["3.8.9"]
    for item in ("has_standing_dp_request", "DP request 0", "DPRequestCancelled"):
        assert item in text


def test_the_388_entry_names_the_bloxberg_testnet_contracts():
    text = dict(entries())["3.8.8"]
    for item in ("0x7E7C6eB435E3687CfDfe18C09C0340eb70567d7b",
                 "0xc346BfFC83f318FbbC34987cEF85716a3f7dCcB0",
                 "0x935c2AD8C402DB922131a48D9d92ceeD02e74720", "bloxberg_testnet_unsafe"):
        assert item in text, item


def test_the_387_entry_names_the_ipv4_bind():
    text = dict(entries())["3.8.7"]
    for item in ("/usr/local/bin/las", '"-a 0.0.0.0"', "tested tree", "templates"):
        assert item in text, item


def test_the_386_entry_names_the_installers_ipv6_settings():
    text = dict(entries())["3.8.6"]
    for item in ("IPv6 disabled", "disable_ipv6 = 1", "/etc/sysctl.conf", "binds IPv4"):
        assert item in text, item


def test_the_385_entry_names_the_templates_las_start():
    text = dict(entries())["3.8.5"]
    for item in ('"/las_entrypoint.sh"', '"/usr/local/bin/las -a 0.0.0.0"', "integration test"):
        assert item in text, item


def test_the_384_entry_names_the_absolute_compose_path():
    text = dict(entries())["3.8.4"]
    for item in ("absolute path", "working", "node/<network>/", "warning"):
        assert item in text, item


def test_the_383_entry_names_the_tested_las_tag():
    text = dict(entries())["3.8.3"]
    for item in ("localhost:5000/etny-las:tested-<trustedzone>", "las-tested-<trustedzone>.yml", "integration test",
                 "platform requirement"):
        assert item in text, item


def test_the_382_entry_names_the_headroom_and_the_resource_refusals_file():
    text = dict(entries())["3.8.2"]
    for item in ("DP_REQUEST_GAS_HEADROOM_PERCENT", "25", "1.5 %", "doreq_resources.json", "doreq_cache.txt"):
        assert item in text, item


def test_the_381_entry_names_the_litvm_rpc_and_the_log_chunk():
    text = dict(entries())["3.8.1"]
    for item in ("https://rpc.testnet.liteforge.zeeve.net/", "_LOGS_CHUNK_BLOCKS", "3000", "litvm_liteforge_unsafe"):
        assert item in text, item


def test_the_version_file_carries_the_changelogs_top_entry():
    top, _ = entries()[0]
    assert config.version == top


def test_the_380_entry_names_what_the_operator_sees():
    text = dict(entries())["3.8.0"]
    for item in ("_TIP_PER_CU_HOUR_WEI", "_ACCEPT_RESERVED", "_FARM_INVITE", "_FARM_ACCEPT",
                 "addDPRequestV2", "_addResultToOrderV2", "commitForBatch", "TimeoutStopSec=21600",
                 "SIGHUP", "--handover", "cryptography 3.4.8", "0x3Aa1A0d7A8A9DaE9e13345D3261afe86D67326A4",
                 "INTEGRATION_TEST_SECONDS"):
        assert item in text, item
    assert "3.7.56" not in text
