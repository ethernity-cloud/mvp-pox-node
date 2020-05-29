#1/bin/bash
cd /home/vagrant/etny/node
wget https://github.com/ipfs/go-ipfs/releases/download/v0.4.19/go-ipfs_v0.4.19_linux-386.tar.gz
tar zxvf go-ipfs_v0.4.19_linux-386.tar.gz
cd go-ipfs
./ipfs init
./ipfs daemon&
sleep 10
