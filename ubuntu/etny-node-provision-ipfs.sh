#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

cd /home/vagrant/etny/node
wget https://github.com/ipfs/go-ipfs/releases/download/v0.6.0/go-ipfs_v0.6.0_linux-386.tar.gz
tar zxvf go-ipfs_v0.6.0_linux-386.tar.gz
