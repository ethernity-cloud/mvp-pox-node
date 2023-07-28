#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

echo "Checking host for sgx_enclave and isgx..."

# Check for sgx_enclave driver
if ls /dev/ | grep -q sgx_enclave; then
   echo 'Skiping isgx'
else
    echo 'sgx_enclave driver is not present on the host.'

    # Check for isgx driver
    if ls /dev/ | grep -q 'isgx'; then
        echo '/dev/isgx driver is present on the host.'
    else
        echo 'isgx driver is not present on the host.'
        echo 'Running install script on the host...'
        sudo apt-get -yq update
        sudo apt-get -yq install make gcc
        cd /tmp
        sudo wget https://github.com/ethernity-cloud/sgx-driver/raw/main/sgx_linux_x64_driver_2.11.054c9c4c.bin
        sudo chmod +x sgx_linux_x64_driver_2.11.054c9c4c.bin
        sudo ./sgx_linux_x64_driver_2.11.054c9c4c.bin
    fi
fi

echo "Operation completed successfully!"
