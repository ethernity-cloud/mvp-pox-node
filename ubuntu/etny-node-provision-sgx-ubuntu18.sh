#1/bin/bash
export DEBIAN_FRONTEND=noninteractive
apt-get -yq update
apt-get -yq dist-upgrade
apt-get -yq install make gcc

cd /tmp
wget https://download.01.org/intel-sgx/sgx-linux/2.8/distro/ubuntu18.04-server/sgx_linux_x64_driver_2.6.0_51c4821.bin
chmod +x sgx_linux_x64_driver_2.6.0_51c4821.bin
./sgx_linux_x64_driver_2.6.0_51c4821.bin
