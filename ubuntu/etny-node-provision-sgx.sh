#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

echo "Checking host for sgx_enclave and isgx..."

# Check for sgx_enclave driver
if ls /dev/ | grep -q sgx_enclave; then
  if [ "`lsmod | grep isgx`" != "" ]
  then
    echo 'Uninstalling isgx driver...'

    # Check if the AESM service is running
    if sudo service aesmd status 2>/dev/null | grep 'Active: active (running)'; then
        echo -e 'Warning!'
        echo -e '\nPlease stop the AESM service and uninstall the PSW package first on the host, otherwise the integration test might fail!'
    fi

    # Removing the kernel module if it is inserted
    sudo modinfo isgx &> /dev/null
    if [[ $? == '0' ]]; then
        sudo modprobe -r isgx
        if [[ $? != '0' ]]; then
            echo -e '\nUninstall failed on the host because the kernel module is in use'
            exit 1
        fi
    fi

    # Removing the .ko file
    sudo find /usr/lib/modules/ -name 'isgx.ko' -exec rm -f {} +

    # Removing from depmod
    sudo depmod

    # Removing from /etc/modules
    sudo sed -i '/^isgx$/d' /etc/modules

    sudo rm -f /etc/sysconfig/modules/isgx.modules
    sudo rm -f /etc/modules-load.d/isgx.conf

    # Removing the current folder
    sudo rm -fr /opt/intel/sgxdriver
  fi

  echo 'Uninstall script executed successfully on the host.'
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
    if [ "`sudo ./sgx_linux_x64_driver_2.11.054c9c4c.bin | grep 'There is no need to install additional SGX driver with in-kernel SGX support'`" != "" ]
    then
        echo "Driver embedded in kernel"
    fi
  fi
fi

echo "Operation completed successfully!"
