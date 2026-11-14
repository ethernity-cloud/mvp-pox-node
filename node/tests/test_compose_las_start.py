"""utils.compose_service_block, las_start_lines, compose_with_las_start and
candidate_las_names: an order's compose takes the entrypoint and command of
the las service of the trustedzone tree that passed this node's integration
test, and the names whose tested LAS an order may run (etny-node.py
__run_integration_test, __start_enclave_environment)."""
from utils import (LAS_TEMPLATE_START, candidate_las_names, compose_service_block, compose_with_las_start,
                   las_start_lines, las_start_to_apply, with_ipv4_bind)

# The las service of a nodenithy trustedzone tree (bloxberg testnet tree
# Qmd2B8..., as the compose reads on a node): two-space services, four-space
# keys, the start keys set.
TESTED_BLOCK = """  las:
    container_name: las
    privileged: true
    image: localhost:5000/etny-las
    entrypoint: "/las_entrypoint.sh"
    # -a 0.0.0.0: LAS defaults to the IPv6 wildcard and cannot bind at all on a
    # host booted with ipv6.disable=1. See docker-compose.yml.
    command: "/usr/local/bin/las -a 0.0.0.0"
    networks:
      - ethernity
    devices:
      - "/dev/sgx_enclave:/dev/sgx_enclave"
    restart: on-failure
    ports:
      - target: 18766
        published: 18766
        protocol: tcp
        mode: host
"""

# A pynithy trustedzone tree's compose (etny-pynithy v3/run/docker-compose-
# final.yml): four-space services, eight-space keys.
PYNITHY_TESTED_COMPOSE = """version: '3'
services:
    etny-trustedzone:
      image: localhost:5000/etny-trustedzone
    las:
        container_name: las
        image: localhost:5000/etny-las
        entrypoint: "/las_entrypoint.sh"
        command: "/usr/local/bin/las -a 0.0.0.0"
        restart: on-failure
networks:
    ethernity:
        external: true
"""

# A dApp tree whose las service sets no start keys (bloxberg mainnet order
# 1939): the LAS starts as its image does.
OLD_ORDER_COMPOSE = """version: '3'
services:
  etny-securelock:
    image: localhost:5000/etny-securelock
  las:
    container_name: las
    privileged: true
    image: localhost:5000/etny-las
    restart: unless-stopped
    ports:
    - target: 18766
      published: 18766
      protocol: tcp
      mode: host
    healthcheck:
      test:
      - CMD
      - bash
      - -c
      - echo > /dev/tcp/127.0.0.1/18766
  etny-trustedzone:
    image: localhost:5000/etny-trustedzone
"""

# A dApp tree sdk-py publishes (yaml.safe_dump: sequences at the key's
# indentation, the start keys plain scalars after the healthcheck).
SDK_ORDER_COMPOSE = """services:
  las:
    container_name: las
    image: localhost:5000/etny-las
    ports:
    - target: 18766
      published: 18766
    healthcheck:
      test:
      - CMD
      - bash
      - -c
      - echo > /dev/tcp/127.0.0.1/18766
    entrypoint: /las_entrypoint.sh
    command: bash -c '/las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 | tee /var/log/las.log'
    restart: on-failure
  etny-trustedzone:
    image: localhost:5000/etny-trustedzone
"""

# A las service whose command is a sequence at the key's indentation.
LIST_COMMAND_COMPOSE = """services:
  las:
    image: localhost:5000/etny-las
    entrypoint: /las_entrypoint.sh
    command:
    - bash
    - -c
    - /las_entrypoint.sh && /usr/local/bin/las | tee /var/log/las.log
    restart: on-failure
  etny-trustedzone:
    image: localhost:5000/etny-trustedzone
"""

# A pynithy dApp tree the JS SDK publishes (pynithy/run/docker-compose-
# final.yml.tmpl): four-space services, eight-space keys, no start keys.
PYNITHY_ORDER_COMPOSE = """services:
    las:
        container_name: las
        image: localhost:5000/etny-las
        restart: unless-stopped
    etny-trustedzone:
        image: localhost:5000/etny-trustedzone
"""

TESTED_START = ['    entrypoint: "/las_entrypoint.sh"\n', '    command: "/usr/local/bin/las -a 0.0.0.0"\n']


def compose_file(tmp_path, text):
    path = tmp_path / "docker-compose.yml"
    path.write_text(text)
    return str(path)


def test_the_las_block_runs_from_its_key_to_the_next_service(tmp_path):
    block = compose_service_block(compose_file(tmp_path, OLD_ORDER_COMPOSE), "las")
    assert block.startswith("  las:\n    container_name: las\n")
    assert block.endswith("      - echo > /dev/tcp/127.0.0.1/18766\n")
    assert "etny-trustedzone" not in block
    assert compose_service_block(compose_file(tmp_path, OLD_ORDER_COMPOSE), "securelock") is None


def test_a_four_space_las_block_is_found_and_ends_at_the_top_level_key(tmp_path):
    block = compose_service_block(compose_file(tmp_path, PYNITHY_TESTED_COMPOSE), "las")
    assert block == ('    las:\n        container_name: las\n        image: localhost:5000/etny-las\n'
                     '        entrypoint: "/las_entrypoint.sh"\n        command: "/usr/local/bin/las -a 0.0.0.0"\n'
                     '        restart: on-failure\n')
    assert las_start_lines(block) == ['        entrypoint: "/las_entrypoint.sh"\n',
                                      '        command: "/usr/local/bin/las -a 0.0.0.0"\n']


def test_the_start_lines_are_the_entrypoint_and_command_with_their_values(tmp_path):
    assert las_start_lines(TESTED_BLOCK) == TESTED_START
    sdk = compose_service_block(compose_file(tmp_path, SDK_ORDER_COMPOSE), "las")
    assert las_start_lines(sdk) == [
        "    entrypoint: /las_entrypoint.sh\n",
        "    command: bash -c '/las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 | tee /var/log/las.log'\n"]
    listed = compose_service_block(compose_file(tmp_path, LIST_COMMAND_COMPOSE), "las")
    assert las_start_lines(listed) == ["    entrypoint: /las_entrypoint.sh\n", "    command:\n", "    - bash\n",
                                       "    - -c\n", "    - /las_entrypoint.sh && /usr/local/bin/las | tee /var/log/las.log\n"]
    old = compose_service_block(compose_file(tmp_path, OLD_ORDER_COMPOSE), "las")
    assert las_start_lines(old) == []


def test_an_old_tree_takes_the_tested_start_after_its_image():
    text = compose_with_las_start(OLD_ORDER_COMPOSE, TESTED_START)
    assert ('    image: localhost:5000/etny-las\n'
            '    entrypoint: "/las_entrypoint.sh"\n'
            '    command: "/usr/local/bin/las -a 0.0.0.0"\n'
            '    restart: unless-stopped\n') in text
    assert text.count("entrypoint:") == 1 and text.count("command:") == 1
    assert text.startswith("version: '3'\nservices:\n  etny-securelock:\n") and text.endswith(
        "  etny-trustedzone:\n    image: localhost:5000/etny-trustedzone\n")


def test_a_trees_own_start_lines_are_replaced_whole():
    for compose in (SDK_ORDER_COMPOSE, LIST_COMMAND_COMPOSE):
        text = compose_with_las_start(compose, TESTED_START)
        assert ('    image: localhost:5000/etny-las\n'
                '    entrypoint: "/las_entrypoint.sh"\n'
                '    command: "/usr/local/bin/las -a 0.0.0.0"\n') in text
        assert "tee /var/log/las.log" not in text and text.count("command:") == 1
        assert text.endswith("  etny-trustedzone:\n    image: localhost:5000/etny-trustedzone\n")
    assert "- echo > /dev/tcp/127.0.0.1/18766\n" in compose_with_las_start(SDK_ORDER_COMPOSE, TESTED_START)


def test_the_start_lines_take_the_order_composes_key_indentation(tmp_path):
    eight = las_start_lines(compose_service_block(compose_file(tmp_path, PYNITHY_TESTED_COMPOSE), "las"))
    assert compose_with_las_start(OLD_ORDER_COMPOSE, eight) == compose_with_las_start(OLD_ORDER_COMPOSE, TESTED_START)
    text = compose_with_las_start(PYNITHY_ORDER_COMPOSE, TESTED_START)
    assert text == ('services:\n    las:\n        container_name: las\n        image: localhost:5000/etny-las\n'
                    '        entrypoint: "/las_entrypoint.sh"\n        command: "/usr/local/bin/las -a 0.0.0.0"\n'
                    '        restart: unless-stopped\n    etny-trustedzone:\n'
                    '        image: localhost:5000/etny-trustedzone\n')


def test_a_compose_without_a_las_or_without_start_lines_is_unchanged():
    no_las = "services:\n  etny-trustedzone:\n    image: localhost:5000/etny-trustedzone\n"
    assert compose_with_las_start(no_las, TESTED_START) == no_las
    assert compose_with_las_start(OLD_ORDER_COMPOSE, []) == OLD_ORDER_COMPOSE


# The las service of the bloxberg mainnet pynithy tree QmW1aAD9... (the
# compose QmRSa8...): four-space services, a command without the IPv4 bind.
MAINNET_COMPOSE = """version: '3'
services:
    las:
        container_name: las
        privileged: true
        image: localhost:5000/etny-las
        entrypoint: "/las_entrypoint.sh"
        command: "bash -c '/las_entrypoint.sh && /usr/local/bin/las | tee /var/log/las.log'"
        restart: unless-stopped
    etny-trustedzone:
        image: localhost:5000/etny-trustedzone
"""


def test_a_command_without_the_bind_gets_it_and_one_with_it_is_kept():
    unbound = ['    command: "bash -c \'/las_entrypoint.sh && /usr/local/bin/las | tee /var/log/las.log\'"\n']
    assert with_ipv4_bind(unbound) == [
        '    command: "bash -c \'/las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 | tee /var/log/las.log\'"\n']
    assert with_ipv4_bind(TESTED_START) == TESTED_START
    assert with_ipv4_bind(LAS_TEMPLATE_START) == LAS_TEMPLATE_START
    listed = ["    command:\n", "    - bash\n", "    - -c\n", "    - /las_entrypoint.sh && /usr/local/bin/las | tee x\n"]
    assert with_ipv4_bind(listed)[3] == "    - /las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 | tee x\n"
    assert with_ipv4_bind(['    entrypoint: "/las_entrypoint.sh"\n']) == ['    entrypoint: "/las_entrypoint.sh"\n']


def test_the_start_applied_is_the_tested_trees_else_the_own_else_the_templates_bound_to_ipv4(tmp_path):
    old = compose_service_block(compose_file(tmp_path, OLD_ORDER_COMPOSE), "las")
    sdk = compose_service_block(compose_file(tmp_path, SDK_ORDER_COMPOSE), "las")
    mainnet = compose_service_block(compose_file(tmp_path, MAINNET_COMPOSE), "las")
    assert las_start_to_apply(old, TESTED_START) == TESTED_START
    assert las_start_to_apply(sdk, TESTED_START) == TESTED_START
    assert las_start_to_apply(old, []) == LAS_TEMPLATE_START
    assert las_start_to_apply(None, []) == LAS_TEMPLATE_START
    assert las_start_to_apply(sdk, []) == []
    assert las_start_to_apply(mainnet, []) == [
        '        entrypoint: "/las_entrypoint.sh"\n',
        '        command: "bash -c \'/las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 | tee /var/log/las.log\'"\n']
    assert las_start_to_apply(mainnet, las_start_lines(mainnet)) == las_start_to_apply(mainnet, [])
    text = compose_with_las_start(MAINNET_COMPOSE, las_start_to_apply(mainnet, []))
    assert ('        image: localhost:5000/etny-las\n'
            '        entrypoint: "/las_entrypoint.sh"\n'
            '        command: "bash -c \'/las_entrypoint.sh && /usr/local/bin/las -a 0.0.0.0 | tee /var/log/las.log\'"\n'
            '        restart: unless-stopped\n') in text
    text = compose_with_las_start(OLD_ORDER_COMPOSE, las_start_to_apply(old, []))
    assert ('    image: localhost:5000/etny-las\n'
            '    entrypoint: "/las_entrypoint.sh"\n'
            '    command: "/usr/local/bin/las -a 0.0.0.0"\n'
            '    restart: unless-stopped\n') in text


def test_the_candidates_are_the_own_name_then_the_passed_of_its_requirement():
    requirements = {"etny-pynithy": "dcap", "etny-nodenithy": "dcap", "etny-pynithy-unsafe": "sgx"}
    passed = {"etny-pynithy": "QmA", "etny-pynithy-unsafe": "QmB"}
    assert candidate_las_names("etny-nodenithy", requirements, passed) == ["etny-nodenithy", "etny-pynithy"]
    assert candidate_las_names("etny-pynithy", requirements, passed) == ["etny-pynithy"]
    assert candidate_las_names("etny-pynithy-unsafe", requirements, passed) == ["etny-pynithy-unsafe"]
