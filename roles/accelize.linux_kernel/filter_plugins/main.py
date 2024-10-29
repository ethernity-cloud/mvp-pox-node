"""Extra Ansible filters"""


def _rhel_kernel_info(packages, kernel_version, current_version):
    """
    Return kernel to install with associated repository.

    Args:
        packages (dict): DNF/YUM list output.
        kernel_version (str): Kernel version to install.
        current_version (str): Current kernel version.

    Returns:
       dict: kernel version, repository
    """
    kernels = list()

    if current_version.startswith(kernel_version):
        kernel_version = current_version.rsplit(".", 1)[0]

    for line in packages["stdout"].splitlines():
        if line.startswith("kernel.") and not line.startswith("kernel.src"):
            package = line.strip().split()
            kernels.append(dict(version=package[1], repo=package[2]))

    for kernel in reversed(kernels):
        if kernel["version"].startswith(kernel_version):
            return kernel

    raise RuntimeError(
        'No kernel matching to "%s". Available kernel versions: %s'
        % (kernel_version, ", ".join(kernel["version"] for kernel in kernels))
    )


def rhel_kernel(packages, kernel_version, current_version):
    """
    Return matching kernel version to install.

    Args:
        packages (dict): DNF/YUM list output.
        kernel_version (str): Kernel version to install.
        current_version (str): Current kernel version.

    Returns:
       str: kernel version.
    """
    return _rhel_kernel_info(packages, kernel_version, current_version)["version"]


def rhel_repo(packages, kernel_version, current_version):
    """
    Return repository where found specified kernel version.

    Args:
        packages (dict): DNF/YUM list output.
        kernel_version (str): Kernel version to install.
        current_version (str): Current kernel version.

    Returns:
       str: repository name
    """
    return _rhel_kernel_info(packages, kernel_version, current_version)["repo"]


def deb_kernel(packages, kernel_version, current_version, variant=None):
    """
    Return best matching kernel version.

    Args:
        packages (dict): apt-cache showpkg output.
        kernel_version (str): Kernel version to install.
        current_version (str): Current kernel version.
        variant (str): Kernel variant to use ("common", ...) If not specified use
            current variant.

    Returns:
       str: kernel version.
    """
    if current_version.startswith(kernel_version) and not variant:
        return current_version

    import re

    kernels = set()
    kernels_add = kernels.add
    current_version, current_variant = re.match(
        r"^([0-9-.]+)(-[a-z0-9]+)?$", current_version
    ).groups()
    variant = "-" + (
        variant
        if not (variant is None or variant.startswith("__omit_place_holder__"))
        else (current_variant or "")
    ).lstrip("-")
    match = re.compile(r"^Package: linux-headers-([a-z0-9-.]+%s)\s*$" % variant).match

    for line in packages["stdout"].splitlines():
        line_match = match(line)
        if line_match:
            kernels_add(line_match.group(1))

    versions = {}
    for kernel in kernels:
        version_info = kernel.split("-")
        version = version_info[0]
        build = version_info[1]
        versions[kernel] = list(int(ver) for ver in version.split(".")) + [build]
    kernels = sorted(versions.keys(), key=versions.get, reverse=True)

    for kernel in kernels:
        if kernel.startswith(kernel_version):
            return kernel

    raise RuntimeError(
        'No kernel matching to "%s". Current version: %s. Available kernel versions: %s'
        % (kernel_version, current_version, ", ".join(reversed(kernels)))
    )


def deb_installed_kernel(installed, kernel_version, arch):
    """
    Return old kernel packages to remove.

    Args:
        installed (dict): dpkg -l output.
        kernel_version (str): Kernel version to install.
        arch (str): Architecture.

    Returns:
       list of str: Kernel packages to remove.
    """
    packages = ("linux-image-", "linux-headers-")
    to_keep = tuple(
        deb_kernel_package(name.rstrip("-"), kernel_version, arch) for name in packages
    )

    to_remove = []
    for line in installed["stdout"].splitlines():
        if " linux-" not in line:
            continue
        package = line.split()[1].strip()
        if any(package.startswith(name) for name in packages) and not any(
            package.startswith(name) for name in to_keep
        ):
            to_remove.append(package)
    return to_remove


def deb_kernel_package(name, kernel_version, arch):
    """
    Check if kernel version match.

    Args:
        name (str): package name.
        kernel_version (str): Kernel version to install.
        arch (str): Architecture.

    Returns:
        str: Package name.
    """
    package = "%s-%s" % (name, kernel_version)
    if name == "linux-image":
        # Debian "image" packages does not end by the variant like headers
        package = package.replace("-common", "-" + arch.replace("x86_64", "amd64"))
    return package


def kernel_match(kernel, kernel_spec):
    """
    Check if kernel version match.

    Args:
        kernel (str): Kernel
        kernel_spec (str): Kernel to match.

    Returns:
        bool: True if Kernel match.
    """
    return kernel.startswith(kernel_spec)


class FilterModule(object):
    """Return filter plugin"""

    @staticmethod
    def filters():
        """Return filter"""
        return {
            "rhel_kernel": rhel_kernel,
            "rhel_repo": rhel_repo,
            "deb_kernel": deb_kernel,
            "deb_installed_kernel": deb_installed_kernel,
            "deb_kernel_package": deb_kernel_package,
            "kernel_match": kernel_match,
        }
