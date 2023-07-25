#!/bin/bash
set -e

trap 'echo "Installer error: \"${BASH_COMMAND}\"command filed with exit code $?."' SIGINT SIGTERM ERR EXIT

export DEBIAN_FRONTEND=noninteractive
apt-get -yq  install git python3 python3-pip
pip3 install web3==5.31.1
pip3 install python-dotenv==0.21.0
pip3 install psutil==5.9.2
touch /var/log/etny-node.log
