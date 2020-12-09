#1/bin/bash

export DEBIAN_FRONTEND=noninteractive
apt-get -yq update
apt-get -yq dist-upgrade
apt-get -yq  install apt-transport-https ca-certificates curl software-properties-common
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu bionic stable"
apt-get -yq  install docker-ce
sudo curl -L "https://github.com/docker/compose/releases/download/1.25.5/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

ufw allow in from 127.0.0.1/8
ufw allow out to 127.0.0.1/8
ufw allow out from any to 4.2.2.1 port 53
ufw allow out from any to 4.2.2.2 port 53
ufw allow out from any to 127.0.0.53 port 53
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

ufw enable
