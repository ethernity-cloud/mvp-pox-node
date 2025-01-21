#!/bin/bash
set -e

grep net.ipv6.conf.all.disable_ipv6 /etc/sysctl.conf || (echo "net.ipv6.conf.all.disable_ipv6 = 1" | sudo tee -a /etc/sysctl.conf)
grep net.ipv6.conf.default.disable_ipv6 /etc/sysctl.conf || (echo "net.ipv6.conf.default.disable_ipv6 = 1" | sudo tee -a /etc/sysctl.conf)
grep net.ipv6.conf.lo.disable_ipv6 /etc/sysctl.conf || (echo "net.ipv6.conf.lo.disable_ipv6 = 1" | sudo tee -a /etc/sysctl.conf)

sysctl -p /etc/sysctl.conf

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

export DEBIAN_FRONTEND=noninteractive
apt-get -yq  install git python3 python3-pip
pip3 install web3==5.31.1
pip3 install python-dotenv==0.21.0
pip3 install psutil==5.9.2
touch /var/log/etny-node.log
