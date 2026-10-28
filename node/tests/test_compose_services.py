"""utils.compose_services lists the service names of a compose file, in
order, and only those: nested keys, other top-level sections and comments
are not services."""
from utils import compose_services

COMPOSE = """version: '3.2'
# Lines ending in the __CAS_ONLY__ tag serve CAS attestation.
services:
  las:  # __CAS_ONLY__
    container_name: las  # __CAS_ONLY__
    image: localhost:5000/etny-las  # __CAS_ONLY__
    ports:  # __CAS_ONLY__
      - target: 18766  # __CAS_ONLY__

  etny-securelock:
    container_name: etny-securelock
    image: localhost:5000/etny-securelock
    depends_on:  # __SAFE_ONLY__
      - las  # __SAFE_ONLY__
  etny-trustedzone:
    container_name: etny-trustedzone
    image: localhost:5000/etny-trustedzone
networks:
  ethernity:
    external: true
"""


def test_the_services_are_listed_in_order(tmp_path):
    compose = tmp_path / 'docker-compose.yml'
    compose.write_text(COMPOSE)
    assert compose_services(str(compose)) == ['las', 'etny-securelock', 'etny-trustedzone']


def test_a_compose_without_services_lists_none(tmp_path):
    compose = tmp_path / 'docker-compose.yml'
    compose.write_text("version: '3.2'\nnetworks:\n  ethernity:\n    external: true\n")
    assert compose_services(str(compose)) == []
