#1/bin/bash
export DEBIAN_FRONTEND=noninteractive
apt-get -yq update
apt-get -yq dist-upgrade
apt-get -yq install make gcc

cd /tmp
wget https://download.01.org/intel-sgx/latest/linux-latest/distro/ubuntu20.04-server/sgx_linux_x64_driver_2.11.054c9c4c.bin
chmod +x sgx_linux_x64_driver_2.11.054c9c4c.bin
./sgx_linux_x64_driver_2.11.054c9c4c.bin

