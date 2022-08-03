#!/bin/bash
cd /home/vagrant/etny/node/
git config --global http.postBuffer 524288000
git clone -b master-with-node-refactoring-new https://github.com/ethernity-cloud/mvp-pox-node etny-repo

