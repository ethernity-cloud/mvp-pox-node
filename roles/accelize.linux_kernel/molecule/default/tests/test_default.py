import os

from pytest import skip
import testinfra.utils.ansible_runner

testinfra_hosts = testinfra.utils.ansible_runner.AnsibleRunner(
    os.environ["MOLECULE_INVENTORY_FILE"]
).get_hosts("all")


def test_packages_installed(host):
    """
    Test that packages are installed
    """
    installed = False
    for name in (
        "kernel",
        "kernel-common",
        "kernel-devel",
        "kernel-headers",
        "linux-headers",
        "linux-image",
    ):
        package = host.package(name)
        if not package.is_installed:
            continue

        version = "-".join(ver for ver in (package.version, package.release) if ver)
        assert version.startswith(
            host.ansible.get_variables().get("kernel_version", "")
        )
        installed = True

    if not installed:
        # Packages names does not match for Debian/Ubuntu packages
        skip("No kernel package found")
