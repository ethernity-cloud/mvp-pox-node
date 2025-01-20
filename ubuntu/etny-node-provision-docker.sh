#!/bin/bash
set -e

grep net.ipv6.conf.all.disable_ipv6 /etc/sysctl.conf || (echo; "net.ipv6.conf.all.disable_ipv6 = 1" | sudo tee -a /etc/sysctl.conf)
grep net.ipv6.conf.default.disable_ipv6 /etc/sysctl.conf || (echo; "net.ipv6.conf.all.disable_ipv6 = 1" | sudo tee -a /etc/sysctl.conf)
grep net.ipv6.conf.lo.disable_ipv6 /etc/sysctl.conf || (echo; "net.ipv6.conf.all.disable_ipv6 = 1" | sudo tee -a /etc/sysctl.conf)

sysctl -p

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

# Add DNS settings to /etc/systemd/resolved.conf
echo -e "[Resolve]\nDNS=8.8.8.8 8.8.4.4 1.1.1.1" | sudo tee -a /etc/systemd/resolved.conf

# Restart the resolver
sudo systemctl restart systemd-resolved

# Array of domains to check
domains=("ipfs.ethernity.cloud" "core.bloxberg.org" "bloxberg.ethernity.cloud")

# Loop through domains and check their resolution
for domain in "${domains[@]}"; do
    if ! dig +short "$domain" &> /dev/null; then
        echo "Error: $domain does not resolve, check your DNS settings."
        exit 1
    fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get -yq update
apt-get -yq dist-upgrade
apt-get -yq  install apt-transport-https ca-certificates curl software-properties-common
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
add-apt-repository "deb [arch=$(dpkg --print-architecture)] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
apt-get -yq  install docker-ce
sudo curl -L "https://github.com/docker/compose/releases/download/1.25.5/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

ufw --force enable

ufw allow from 127.0.0.1/8
ufw allow to 127.0.0.1/8
ufw allow out to any port 53
ufw allow in 22/tcp

IP=`getent hosts ipfs.ethernity.cloud | awk '{print $1}'`
ufw allow out from any to $IP port 4001

IP=`getent hosts core.bloxberg.org | awk '{print $1}'`
ufw allow out from any to $IP port 443

for IP in `dig registry-1.docker.io a | grep ^registry-1 | awk '{print $5}' | sort`; do 
    ufw allow out from any to $IP port 443
done

for IP in `dig production.cloudflare.docker.com a | grep ^production | awk '{print $5}' | sort`; do
    ufw allow out from any to $IP port 443
done

ufw reload
