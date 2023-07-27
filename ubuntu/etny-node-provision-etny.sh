#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command filed with exit code $?."' SIGINT SIGTERM ERR EXIT

cd /home/vagrant/etny/node/
git config --global http.postBuffer 524288000
git clone https://github.com/ethernity-cloud/mvp-pox-node etny-repo

