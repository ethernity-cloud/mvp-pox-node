#!/bin/bash
set -e

# IPv6 stays enabled: the lines earlier installers added are removed.
sudo sed -i '/^net\.ipv6\.conf\.\(all\|default\|lo\)\.disable_ipv6 = 1$/d' /etc/sysctl.conf
sudo sysctl -w net.ipv6.conf.all.disable_ipv6=0 net.ipv6.conf.default.disable_ipv6=0 net.ipv6.conf.lo.disable_ipv6=0

sysctl -p /etc/sysctl.conf

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

export DEBIAN_FRONTEND=noninteractive
apt-get -yq  install git python3 python3-pip
pip3 install web3==5.31.1
pip3 install python-dotenv==0.21.0
pip3 install psutil==5.9.2
touch /var/log/etny-node.log
