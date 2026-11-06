"""utils.container_engine_in reads the engine's platform name and version from
the output of `docker version --format CONTAINER_ENGINE_FORMAT`, for Docker
and for Kiri (whose /version answers Platform.Name "Kiri" with its crate
version); utils.container_engine runs that command and is None when docker
does not answer. No docker: the command runner is replaced."""
import subprocess

import utils
from utils import CONTAINER_ENGINE_FORMAT, container_engine, container_engine_in

DOCKER = 'Docker Engine - Community 29.9.2\n'
KIRI = 'Kiri 0.1.18-etny1\n'


def test_docker_names_its_platform_in_several_words():
    assert container_engine_in(DOCKER) == ('Docker Engine - Community', '29.9.2')


def test_kiri_names_itself_with_its_crate_version():
    assert container_engine_in(KIRI) == ('Kiri', '0.1.18-etny1')
    assert container_engine_in('Kiri 0.1.18') == ('Kiri', '0.1.18')


def test_a_missing_platform_name_leaves_the_version_and_no_field_is_none():
    assert container_engine_in(' 27.3.1\n') == ('', '27.3.1')
    assert container_engine_in('\n\nKiri 0.1.19\n') == ('Kiri', '0.1.19')
    assert container_engine_in('') is None
    assert container_engine_in(None) is None


def test_the_engine_is_read_from_docker_version_over_the_format(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=KIRI, stderr='')

    monkeypatch.setattr(utils.subprocess, 'run', run)
    assert container_engine() == ('Kiri', '0.1.18-etny1')
    assert calls == [['docker', 'version', '--format', CONTAINER_ENGINE_FORMAT]]
    assert CONTAINER_ENGINE_FORMAT == '{{.Server.Platform.Name}} {{.Server.Version}}'


def test_no_engine_is_none(monkeypatch):
    def refused(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 1, stdout='', stderr='Cannot connect to the Docker daemon at unix:///run/kiri/docker.sock.\n')

    monkeypatch.setattr(utils.subprocess, 'run', refused)
    assert container_engine() is None

    def missing(args, **kwargs):
        raise FileNotFoundError(2, 'docker')

    monkeypatch.setattr(utils.subprocess, 'run', missing)
    assert container_engine() is None

    def stalled(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs.get('timeout'))

    monkeypatch.setattr(utils.subprocess, 'run', stalled)
    assert container_engine(timeout=1) is None
