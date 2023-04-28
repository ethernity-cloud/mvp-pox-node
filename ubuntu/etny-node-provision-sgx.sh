#!/bin/bash
set -e

trap 'echo "Installer error: \"${BASH_COMMAND}\"command filed with exit code $?."' SIGINT SIGTERM ERR EXIT

export DEBIAN_FRONTEND=noninteractive
apt-get -yq update
apt-get -yq dist-upgrade
apt-get -yq install make gcc

cd /tmp
wget https://github.com/ethernity-cloud/sgx-driver/raw/main/sgx_linux_x64_driver_2.11.054c9c4c.bin
chmod +x sgx_linux_x64_driver_2.11.054c9c4c.bin
./sgx_linux_x64_driver_2.11.054c9c4c.bin
