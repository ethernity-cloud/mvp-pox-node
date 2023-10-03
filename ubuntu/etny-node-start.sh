#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs

if [ -v IPFS_HOSTNAME ]
then
  IPFS_HOST=${IPFS_HOSTNAME}
else
  IPFS_HOST="ipfs.ethernity.cloud";
fi

systemctl stop ipfs

if [ -v IPFS_LOCAL_CONNECT_URL ]
then
  systemctl disable ipfs
  IPFS_LOCAL=${IPFS_LOCAL_CONNECT_URL}
else
  systemctl enable ipfs
  systemctl start ipfs
  IPFS_LOCAL="/ip4/127.0.0.1/tcp/5001/http";
fi


resolve_ipfs_host () {
resolving=false
while [ $resolving == false ]
do
        IPFS_IP=`getent ahosts ${IPFS_HOST} | grep ${IPFS_HOST} | awk '{print $1}'`
        if [ "$IPFS_IP" != "" ]
	then
		IPFS_IP=$(echo ${IPFS_IP} | awk 'NR==1{print $1}')
		resolving=true
        else
		echo "Unable to resolve IPFS gateway ${IPFS_HOST}, please check DNS configuration"
        fi
done
}

if [ "$IPFS_HOSTNAME" == "ipfs.ethernity.cloud" ]
then

	resolve_ipfs_host

	until timeout 10 ./ipfs --api=${IPFS_LOCAL_CONNECT_URL} swarm connect /ip4/${IPFS_IP}/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
	do
        	echo "Unable to connect to IPFS gateway, please check IPFS configuration or restart the service"
		resolve_ipfs_host
		sleep 5
	done
	./ipfs --api=${IPFS_LOCAL_CONNECT_URL} bootstrap add /ip4/${IPFS_IP}/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
fi

cd /home/vagrant/etny/node/etny-repo/node/
git fetch origin
git reset --hard origin/master
git pull

/home/vagrant/etny/node/etny-repo/node/etny-node.py -a $ADDRESS -k $PRIVATE_KEY -r $RESULT_ADDRESS -j $RESULT_PRIVATE_KEY -v $TASK_EXECUTION_PRICE -n $NETWORK -i ${IPFS_HOST} -l ${IPFS_LOCAL}
