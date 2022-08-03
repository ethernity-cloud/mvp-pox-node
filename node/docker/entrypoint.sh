#!/bin/sh

cd /go-ipfs
./ipfs init
./ipfs daemon&

IP=`getent hosts ipfs.ethernity.cloud | awk '{print $1}'`

until ./ipfs swarm connect /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
do
  sleep 1
done

./ipfs bootstrap add /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
./ipfs get $2
./ipfs get $3

rm -rf payload.py
ln -s $2 payload.py

rm -rf fileset
ln -s $3 fileset

python3 payload.py 2>&1 | tail -n +20 > results.txt

hash=`./ipfs add results.txt -q`

./ipfs pin add $hash 2>&1 > /dev/null

python3 /etny-result.py -o $1 -r $hash -p $4 -k $5

until ./ipfs swarm connect /ip4/$IP/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
do
  sleep 1
done

UPLOADED=no
TRY=0

while [ "$UPLOADED" == "no" ] && [ $TRY -le 15 ]
do
  for DHT in `( ./ipfs dht findprovs $hash 2>/dev/null ) & pid=$! && ( sleep 1 && kill -HUP $pid ) 2>/dev/null`
  do
    if [ "$DHT" == "QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5" ]
    then
      UPLOADED=yes;
      echo "Result pinned by ipfs.ethernity.cloud"
    fi
  done
  ./ipfs pin add $hash 2>&1 > /dev/null
  TRY=$(($TRY+1))
done

echo "Exiting..."