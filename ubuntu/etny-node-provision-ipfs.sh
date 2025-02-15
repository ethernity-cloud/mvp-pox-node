#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

cd /home/vagrant/etny/node
wget https://dist.ipfs.tech/kubo/v0.32.1/kubo_v0.32.1_linux-amd64.tar.gz
tar zxvf kubo_v0.32.1_linux-amd64.tar.gz 
mv -f kubo go-ipfs
cd go-ipfs
./ipfs config Addresses.Gateway /ip4/172.17.0.1/tcp/8080
