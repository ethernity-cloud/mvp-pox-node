import sys
import subprocess
import os

# Define the required packages and their specific versions
REQUIRED_PACKAGES = {
    "psutil": "6.1.1",
    "python-dotenv": "1.0.1",
    "minio": "7.2.13",
    "web3": "7.6.1"
}


# Initial attempt to import 'packaging'
try:
    from packaging import version
except ImportError:
    # If 'packaging' is not installed, install it first
    print("The 'packaging' module is missing. Installing it now...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "packaging"])
        from packaging import version
        print("'packaging' has been successfully installed.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install 'packaging'. Error: {e}")
        sys.exit(1)

def get_installed_version(package_name):
    """
    Retrieves the installed version of a package.
    Returns None if the package is not installed.
    """
    try:
        # For Python 3.8 and above
        if sys.version_info >= (3, 8):
            from importlib.metadata import version as get_version
        else:
            # For Python <3.8, requires 'importlib_metadata' package
            from importlib_metadata import version as get_version

        return get_version(package_name)
    except Exception:
        return None

def install_packages(packages):
    """
    Installs the specified packages using pip.
    """
    try:
        print(f"Installing packages: {', '.join(packages)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + packages)
    except subprocess.CalledProcessError as e:
        print(f"Error occurred while installing packages: {e}")
        sys.exit(1)

def restart_script():
    """
    Restarts the current script.
    """
    print("Restarting the script to apply updates...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

def ensure_packages():
    """
    Ensures that all required packages are installed with the specified versions.
    If not, installs or updates them and restarts the script.
    """
    packages_to_install = []

    for package, required_version in REQUIRED_PACKAGES.items():
        installed_version = get_installed_version(package)
        if installed_version is None:
            print(f"{package} is not installed.")
            packages_to_install.append(f"{package}=={required_version}")
        elif version.parse(installed_version) != version.parse(required_version):
            print(f"{package} version {installed_version} is installed, but {required_version} is required.")
            packages_to_install.append(f"{package}=={required_version}")
        else:
            print(f"{package}=={installed_version} is already installed.")

    if packages_to_install:
        install_packages(packages_to_install)
        restart_script()
    else:
        print("All required packages are up to date.")

def handle_import_error():
    """
    Handles ImportError by ensuring packages are installed.
    """
    ensure_packages()

def check_dependencies():
    """
    Attempts to import all required modules.
    If any ImportError occurs, handles it.
    """
    try:
        for package in REQUIRED_PACKAGES.keys():
            __import__(package)
        print("All required modules are successfully imported.")
    except ImportError:
        handle_import_error()
