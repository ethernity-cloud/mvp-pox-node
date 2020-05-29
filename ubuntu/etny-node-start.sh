#!/bin/bash
. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs
nohup ./ipfs daemon &

cd /home/vagrant/etny/node
nohup /home/vagrant/etny/node/etny-node.py -p $PUBLIC_KEY -k $PRIVATE_KEY -o $RESULT_PUBLIC_KEY -j $RESULT_PRIVATE_KEY &
