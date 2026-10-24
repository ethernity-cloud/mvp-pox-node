#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

cd /home/vagrant/etny/node
# The same release is published on dist.ipfs.tech and on GitHub; dist.ipfs.tech
# is not reachable from every network, so GitHub is the second source.
KUBO=kubo_v0.32.1_linux-amd64.tar.gz
wget -q --tries=2 --timeout=30 -O "${KUBO}" "https://dist.ipfs.tech/kubo/v0.32.1/${KUBO}" \
  || wget -q --tries=3 --timeout=60 -O "${KUBO}" "https://github.com/ipfs/kubo/releases/download/v0.32.1/${KUBO}"
tar zxvf "${KUBO}"
mv -f kubo go-ipfs
