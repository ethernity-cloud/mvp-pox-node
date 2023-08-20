#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs

if [ -z ${IPFS_HOSTNAME+x} ]
then
  IPFS_HOST=${IPFS_HOSTNAME}
else
  IPFS_HOST="ipfs.ethernity.cloud";
fi

if [ -z ${IPFS_LOCAL_CONNECT_URL+x} ]
then
  IPFS_LOCAL=${IPFS_LOCAL_CONNECT_URL}
else
  IPFS_LOCAL="/ip4/127.0.0.1/tcp/5001/http";
fi


connect_ipfs () {
resolving=false
while [ $resolving == false ]
do
        IPFS_IP=`getent hosts ${IPFS_HOST}`
        if [ $? == 0 ]
	then
		IPFS_IP=$(echo ${IPFS_IP} | awk 'NR==1{print $1}')
		resolving=true
        else
		resolving=false
        fi
done
}

until ./ipfs swarm connect /ip4/${IPFS_IP}/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
do
	connect_ipfs
	sleep 5
done

./ipfs bootstrap add /ip4/${IPFS_IP}/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5

cd /home/vagrant/etny/node/etny-repo/node/
git fetch origin testnet_v3_1
git reset --hard origin/testnet_v3_1
git pull
git checkout testnet_v3_1

/home/vagrant/etny/node/etny-repo/node/etny-node.py -a $ADDRESS -k $PRIVATE_KEY -r $RESULT_ADDRESS -j $RESULT_PRIVATE_KEY -v $TASK_EXECUTION_PRICE -n $NETWORK -i ${IPFS_HOST} -l ${IPFS_LOCAL}
