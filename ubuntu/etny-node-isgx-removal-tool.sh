#!/bin/bash

if test $(id -u) -ne 0; then
    echo "Root privilege is required."
    exit 1
fi

echo "Checking Vagrant VM for sgx_enclave and isgx..."
vagrant_dir=$(sudo vagrant global-status | grep running | awk '{print $5}')
if [ -z "$vagrant_dir" ]; then
    echo "No running Vagrant environment found."
else
    echo "Vagrant VM:"
    # Commands executed on the Vagrant VM
    cd "$vagrant_dir"
    sudo vagrant ssh -c "
        echo 'Checking for sgx_enclave and isgx drivers...'
        # Check for sgx_enclave driver
        if ls /dev/ | grep -q 'sgx_enclave'; then
            echo '/dev/sgx_enclave driver is present on the Vagrant VM.'
            echo 'Uninstalling isgx driver...'

            # Check if the AESM service is running
            if sudo service aesmd status 2>/dev/null | grep 'Active: active (running)'; then
                echo -e 'Uninstall failed on the Vagrant VM!'
                echo -e '\nPlease stop the AESM service and uninstall the PSW package first on the Vagrant VM'
                exit 1
            fi

            # Removing the kernel module if it is inserted
            sudo modinfo isgx &> /dev/null
            if [[ $? == '0' ]]; then
                sudo modprobe -r isgx
                if [[ $? != '0' ]]; then
                    echo -e '\nUninstall failed on the Vagrant VM because the kernel module is in use'
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

            echo 'Uninstall script executed successfully on the Vagrant VM.'
        else
            echo 'sgx_enclave driver is not present on the Vagrant VM.'

            # Check for isgx driver
            if ls /dev/ | grep -q 'isgx'; then
                echo '/dev/isgx driver is present on the Vagrant VM.'
            else
                echo 'isgx driver is not present on the Vagrant VM.'
                echo 'Running install script on the Vagrant VM...'
                sudo apt-get -yq update
                sudo apt-get -yq install make gcc
                cd /tmp
                sudo wget https://github.com/ethernity-cloud/sgx-driver/raw/main/sgx_linux_x64_driver_2.11.054c9c4c.bin
                sudo chmod +x sgx_linux_x64_driver_2.11.054c9c4c.bin
                sudo ./sgx_linux_x64_driver_2.11.054c9c4c.bin
                echo 'Install script executed successfully on the Vagrant VM.'
            fi
        fi
    "
fi

echo "Checking host for sgx_enclave and isgx..."
echo "Host:"
# Commands executed on the host
# Check for sgx_enclave driver
if ls /dev/ | grep -q 'sgx_enclave'; then
    echo '/dev/sgx_enclave driver is present on the host.'
    echo 'Uninstalling isgx driver...'

    # Check if the AESM service is running
    if sudo service aesmd status 2>/dev/null | grep 'Active: active (running)'; then
        echo -e 'Uninstall failed on the host!'
        echo -e '\nPlease stop the AESM service and uninstall the PSW package first on the host'
        exit 1
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
        sudo ./sgx_linux_x64_driver_2.11.054c9c4c.bin
    fi
fi

echo "Operation completed successfully!"
