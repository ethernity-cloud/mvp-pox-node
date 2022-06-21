#1/bin/bash

export DEBIAN_FRONTEND=noninteractive
apt-get -yq update
apt-get -yq dist-upgrade
apt-get -yq  install git python3 python3-pip
pip3 install web3
pip3 install python-dotenv
pip3 install psutil