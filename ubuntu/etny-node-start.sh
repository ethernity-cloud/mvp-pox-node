#!/bin/bash
. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs

IP=`getent hosts ipfs.ethernity.cloud | awk '{print $1}'`

until ./ipfs swarm connect /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
do
  nohup ./ipfs daemon &
  IP=`getent hosts ipfs.ethernity.cloud | awk '{print $1}'`
  sleep 5
done

./ipfs bootstrap add /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5

cd /home/vagrant/etny/node/etny-repo/node/
git pull

/home/vagrant/etny/node/etny-repo/node/etny-node.py -a $ADDRESS -k $PRIVATE_KEY -r $RESULT_ADDRESS -j $RESULT_PRIVATE_KEY
