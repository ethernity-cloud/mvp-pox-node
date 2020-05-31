#!/bin/bash
. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs
nohup ./ipfs daemon &

cd /home/vagrant/etny/node
nohup /home/vagrant/etny/node/etny-node.py -a $ADDRESS -k $PRIVATE_KEY -r $RESULT_ADDRESS -j $RESULT_PRIVATE_KEY &
