#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs

connect_ipfs () {
resolving=false
while [ $resolving == false ]
do
        IP=`getent hosts ipfs.ethernity.cloud`
        if [ $? == 0 ]
	then
		IP=$(echo $IP | awk 'NR==1{print $1}')
		resolving=true
        else
		resolving=false
        fi
done
}

until ./ipfs swarm connect /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
do
	connect_ipfs
	sleep 5
done

./ipfs bootstrap add /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5

cd /home/vagrant/etny/node/etny-repo/node/
git fetch origin testnet_v2
git reset --hard origin/testnet_v2
git pull

/home/vagrant/etny/node/etny-repo/node/etny-node.py -a $ADDRESS -k $PRIVATE_KEY -r $RESULT_ADDRESS -j $RESULT_PRIVATE_KEY -v $TASK_EXECUTION_PRICE -n $NETWORK
