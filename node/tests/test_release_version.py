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
    assert top == "3.8.18"


def test_the_3818_entry_names_the_do_request_age_rule():
    text = dict(entries())["3.8.18"]
    for item in ("DO_REQUEST_MAX_AGE_SECONDS", "86400 s", "<NETWORK>_DO_REQUEST_MAX_AGE_SECONDS",
                 "--<network>_do_request_max_age_seconds", "pinned to the node", "createdAt", "eth_getStorageAt",
                 "do_request_age.py", "ethStorageAddress()", "layout cloud", "layout v05", "slot 3", "slot 19",
                 "keccak256(3) + 10 id + 1", "keccak256(19) + 9 id", "0x549A6E06BB2084100148D50F51CF77a3436C3Ae7",
                 "0x6aAafE99746Bd9f9258DbC1A8C82f3C15563636C", "0xEE3aAa1A7a8C00BeBBb5DfC570F80f6FA12a968d",
                 "within 16", "binary-searches", "never moves back", "never reads a request",
                 "DO requests below <boundary>", "DO request ages are not established",
                 "DO request ages were not read", "at most once an hour", "ceil(log2(count))", "23 on 34,542"):
        assert item in text, item
    for absent in ("requests 98", "2024-12-20", "where it was", "until now"):
        assert absent not in text, absent


def test_the_3817_entry_names_the_repo_size_read_before_a_pin():
    text = dict(entries())["3.8.17"]
    for item in ("utils.Storage.repo_size", "repo/stat", "up to three times, 5 s apart", "every 10 s",
                 "three failed reads in a row", "getting disk usage at /", "leveldb"):
        assert item in text, item


def test_the_3816_entry_names_the_polygon_mainnet_rpc_delay():
    text = dict(entries())["3.8.16"]
    for item in ("POLYGON_MAINNET_RPC_DELAY", "200 ms", "bloxberg testnet pair (0)",
                 "https://polygon-bor-rpc.publicnode.com/", "1,200 requests", "run_esr_replication_loop",
                 "ESR_REPLICATION_INTERVAL_SECONDS", "ESR_RESULT_SCAN_ORDERS", "DO_REQUEST_SCAN_REQUESTS", "160 s",
                 "up to 0.19 s later", "0.38 to 0.57 s later", "doreq_cache.txt"):
        assert item in text, item


def test_the_3816_entry_names_the_ways_to_another_delay():
    text = dict(entries())["3.8.16"]
    for item in ("0 or more whole milliseconds", "POLYGON_MAINNET_RPC_DELAY in the service's environment",
                 "etny-node.service", "--polygon_mainnet_rpc_delay", "config.parse_networks", "load_dotenv",
                 "parse_config_to_args", "POLYGON_MAINNET_RPC_DELAY=<milliseconds>", "/home/vagrant/etny/node/config",
                 "passes no config line", "origin/master"):
        assert item in text, item


def test_the_3815_entry_names_the_polygon_mainnet_fee_ceiling():
    text = dict(entries())["3.8.15"]
    for item in ("POLYGON_MAINNET_MAX_FEE_PER_GAS", "500 gwei", "int(baseFee * 1.1)", "32 gwei",
                 "get_transaction_build", "base fee is too high!", "425.4 gwei", "heartbeat", "paid in POL"):
        assert item in text, item


def test_the_3815_entry_names_the_ways_to_another_ceiling():
    text = dict(entries())["3.8.15"]
    for item in ("etny-node.service", "config.parse_networks", "load_dotenv", "--polygon_mainnet_max_fee_per_gas",
                 "parse_config_to_args", "POLYGON_MAINNET_MAX_FEE_PER_GAS=<gwei>", "/home/vagrant/etny/node/config",
                 "origin/master"):
        assert item in text, item


def test_the_3814_entry_names_the_queues_per_generation():
    text = dict(entries())["3.8.14"]
    for item in ("ETNY_SCONE_QUEUES", "utils.enclave_compose_env", "SCONE_QUEUES_LEGACY_MAX", "--tcs",
                 "within 4 and 8", "legacy", "pox="):
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
    for item in ("DP_REQUEST_GAS_HEADROOM_PERCENT", "25", "doreq_resources.json", "doreq_cache.txt"):
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
