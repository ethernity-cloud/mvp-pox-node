"""ubuntu/etny-node-provision-kiri.sh moves a node VM from dockerd to kiri. The
order of its steps is read from the script's text: kiri-init is started and
its nftables table checked before anything of dockerd's changes; an order's
containers refuse the switch and etny-node is stopped before dockerd stops,
since stopping dockerd stops every container it runs; the DOCKER_HOST drop-in
is written before etny-node is started again."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / 'ubuntu' / 'etny-node-provision-kiri.sh').read_text(encoding='utf-8')

KIRI_STARTED = 'systemctl enable --now kiri.service'
KIRI_INIT_STARTED = '-X POST http://localhost/kiri/wake'
TABLE_CHECKED = 'nft list table ip kiri >/dev/null 2>&1 \\\n    || fail'
RUNNING_LISTED = "ps --format '{{.Names}}'"
NODE_STOPPED = 'systemctl stop etny-node.service'
DOCKERD_STOPPED = 'systemctl disable --now "${UNITS[@]}"'
NODE_DROPIN = 'Environment=DOCKER_HOST=unix://${KIRI_SOCKET}'
NODE_STARTED = 'systemctl start etny-node.service'


def position(snippet):
    assert SCRIPT.count(snippet) == 1, snippet
    return SCRIPT.index(snippet)


def test_kiri_init_and_its_table_are_checked_after_kiri_starts_and_before_dockerd_stops():
    assert position(KIRI_STARTED) < position(KIRI_INIT_STARTED) < position(TABLE_CHECKED) < position(DOCKERD_STOPPED)


def test_running_containers_other_than_the_registry_and_the_object_store_refuse_the_switch():
    assert position(RUNNING_LISTED) < position(DOCKERD_STOPPED)
    assert 'REGISTRY_CONTAINER=registry\n' in SCRIPT
    compose = (ROOT / 'node' / 'docker' / 'docker-compose-swift-stream.yml').read_text(encoding='utf-8')
    object_store = re.search(r'^\s*container_name:\s*(\S+)', compose, re.MULTILINE).group(1)
    assert f'OBJECT_STORE_CONTAINER={object_store}\n' in SCRIPT
    assert 'grep -vxF -e "${REGISTRY_CONTAINER}" -e "${OBJECT_STORE_CONTAINER}"' in SCRIPT


def test_etny_node_is_stopped_before_dockerd_and_started_after_the_drop_in():
    assert position(NODE_STOPPED) < position(DOCKERD_STOPPED) < position(NODE_DROPIN) < position(NODE_STARTED)
    assert 'previous engine' not in SCRIPT
