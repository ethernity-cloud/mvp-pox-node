# Ethernity NODE

This repository provides requirements and setup instructions to enable Ethernity NODE capabilities on a computing system.

## Hardware requirements:

### CPU

Lists with SGX feature enabled are maintained by Intel at: 

[SGX with Intel® ME](https://ark.intel.com/content/www/us/en/ark/search/featurefilter.html?productType=873&2_SoftwareGuardExtensions=Yes%20with%20Intel%C2%AE%20ME)
[SGX with Intel® SPS and Intel® ME](https://ark.intel.com/content/www/us/en/ark/search/featurefilter.html?productType=873&2_SoftwareGuardExtensions=Yes%20with%20both%20)

### BIOS

SGX must be set to ENABLED by the system owner via BIOS.

### Compatible Systems

List of compatible systems:

A list of SGX compatible systems that support Intel SGX is maintained here:
<https://github.com/ayeks/SGX-hardware>

### Tested systems

We have tested successfully the following hardware:
DELL Optiplex 5040

## Software requirements:

Currently the following operating systems are suported:

```
Ubuntu 20.04
Ubuntu 22.04
Ubuntu 24.04
Slackware 15-current
Debian 12.0
```

We are planning support for the following operating systems:

```
Fedora
Rocky
AlmaLinux
```

## Automated Installation

This installer provides an easy way to automate the installation process of an Ethernity Node as much as possible.

Features:
-	Automates the system update, kernel update (5.0.0-050000-generic for ubuntu 18.04 and 5.13.0-41-generic for ubuntu 20.04) and runs the ansible-playbook installation process
-	Asks the user to generate (using the “ethkey” tool) or to input wallet details from console (node and result)
-	Checks wallet balance for Bergs (continues only if Bergs > 0)
-	Validates wallet for wrong input 
-	Prevents the user to continue if the node wallet  is the same as the result wallet
-	Restarts the system automatically after the system and kernel is updated

### 1. Clone the repository to the home folder and run it
```
$ cd && git clone https://github.com/ethernity-cloud/mvp-pox-node.git
$ cd mvp-pox-node
$ ./etny-node-installer.sh
```

### 2. Run the script again after system restart
```
$ cd mvp-pox-node
$ ./etny-node-installer.sh
```

## Maual Installation

### 1. Install ansible

```bash
$ sudo apt update
$ sudo apt -y install software-properties-common
$ sudo apt-add-repository --yes --update ppa:ansible/ansible
$ sudo apt -y install ansible
```


### 2. Clone the repository

```
$ git clone https://github.com/ethernity-cloud/mvp-pox-node.git
```


### 3. Install the kernel with SGX support

```bash
$ cd mvp-pox-node
$ sudo ansible-playbook -i localhost, playbook.yml \
  -e "ansible_python_interpreter=/usr/bin/python3"
```

After the first run of the script, the new kernel(with SGX support) is installed and the following message will be displayed:

```
ok: [localhost] => {
    "msg": "The kernel has been updated, a reboot is required"
}
```

Reboot the system as requested.


### 4. Create config file (please use your own wallets):

```bash
$ cd mvp-pox-node
$ cat << EOF > config
ADDRESS=0xf17f52151EbEF6C7334FAD080c5704D77216b732
PRIVATE_KEY=AE6AE8E5CCBFB04590405997EE2D52D2B330726137B875053C36D94E974D162F
RESULT_ADDRESS=0xC5fdf4076b8F3A5357c5E395ab970B5B54098Fef
RESULT_PRIVATE_KEY=0DBBE8E4AE425A6D2687F1A7E3BA17BC98C673636790F1B8AD91193C05875EF1
CONTAINER_ENGINE=docker
EOF
$
```

`CONTAINER_ENGINE` is the engine the node VM runs its containers with:
`docker` (Docker Engine, which the file means when the key is absent) or
`kiri` (the Kiri engine; see "Container engine" below).


### 5. Start the node

```bash
$ cd mvp-pox-node
$ sudo ansible-playbook -i localhost, playbook.yml \
  -e "ansible_python_interpreter=/usr/bin/python3"
```

After the second run of the script the node should be successfully installed and the following message will be seen on the screen:

```
ok: [localhost] => {
    "msg": "Ethernity NODE installation successful"
}
```

### 6. Check if the service is running correctly.

Service status can be seen by running the below command.

```
systemctl status etny-vagrant.service
```

## Ugrading

To upgrade to the latest version, please use the automated installer by running the following commands:
```
$ cd && cd mvp-pox-node
$ git pull 
$ ./etny-node-installer.sh
```

## Networks and platforms

`-n`/`--network` names the networks the agent runs (`node/config.py`): `bloxberg_mainnet`, `bloxberg_testnet`, `polygon_mainnet`, `polygon_amoy`, `iotex_testnet`, `ethereum_sepolia`, `litvm_liteforge`, and the `-unsafe` networks `bloxberg_testnet_unsafe`, `litvm_liteforge_unsafe` and `ethereum_sepolia_unsafe`. Without it every network is loaded except the `-unsafe` ones, which are loaded only when named.

A testnet comes in two kinds. `bloxberg_testnet`, `litvm_liteforge` and `ethereum_sepolia` run trustedzones provisioned by that chain's ethernity-cas validator set, which admits an enclave on a DCAP quote: the platform must produce one (requirement `dcap`, the default for a name in `<NETWORK>_TRUSTEDZONE_IMAGES`), and the set validates their orders in full, as on mainnet. `bloxberg_testnet_unsafe`, `litvm_liteforge_unsafe` and `ethereum_sepolia_unsafe` have the chain and contracts of the network they are named after and run only that network's `-unsafe` trustedzones (`etny-pynithy-testnet-unsafe`, `etny-nodenithy-testnet-unsafe`; `ecld-pynithy-litvm-testnet-unsafe`, `ecld-nodenithy-litvm-testnet-unsafe`; `ecld-pynithy-ethereum-sepolia-unsafe`, `ecld-nodenithy-ethereum-sepolia-unsafe`), which are debug-signed, self-sign their certificate from their measurement and contact no CAS: any platform with hardware SGX runs them (requirement `sgx`, written `name:sgx`), EPID-only and SGX1 with or without FLC included, and the validators judge their orders with the derived-signer check waived. Named together with the network it is named after, an `-unsafe` network joins it as one handle on their shared contract.

The agent runs a trustedzone only after an integration test has passed on this platform: the trustedzone's own (`<NETWORK>_INTEGRATION_TEST_IMAGES`), or one that covers its requirement. A DCAP platform therefore runs both kinds; a platform whose CAS-provisioned test fails and whose `-unsafe` test passes runs the `-unsafe` trustedzones alone. The names it runs are logged at start (`Platform meets dcap and sgx: runs ...`), advertised in its DP request's `metadata4` (`trustedzones=<names>;ipfs=<peer id>`), and a DO request whose image metadata names another trustedzone (field 2) is not taken, with the reason logged. Mainnet has no `-unsafe` network.

## Container engine

`CONTAINER_ENGINE` in `config` selects the engine the node VM runs its
containers with: `docker` (Docker Engine, the value the file means when the
key is absent) or `kiri`. `roles/validate_config_file` refuses any other
value. The agent drives the engine only through the `docker` and
`docker-compose` commands, which reach the engine named by `DOCKER_HOST` in
its environment, so the setting is consumed by the VM's provisioning: with
`kiri`, `vagrant/qemu-3.1`, `qemu-6.2` and `qemu-8.2` copy
`ubuntu/etny-node-provision-kiri.sh` into the VM and run it after
`etny-node-provision-docker.sh`. The agent logs the engine it reached when it
logs its environment at start (`Container engine: Kiri 0.1.18 at
unix:///run/kiri/docker.sock`; `Container engine: none answers at ...` when
no engine does, and the start goes on).

`kiri-lite` is the Kiri container engine (https://runkiri.com,
https://github.com/libracoder/kiri) packaged without a docker CLI: on a Linux
host it runs containers on the host's kernel through its own init,
`kiri-init`, and serves the Docker API (version 1.47) on
`unix:///run/kiri/docker.sock` to the kiri group and root. The docker-ce CLI
and the Compose plugin the docker provisioning installs stay the node's
clients. It needs cgroup v2, overlayfs, nftables, veth and bridge devices, all
in the box's kernel. A privileged container (every enclave container is one)
gets a tmpfs `/dev` holding copies of the host's device nodes,
`/dev/sgx_enclave` and `/dev/sgx_provision` among them, from kiri 0.1.18 on;
0.1.17 mounts the host's devtmpfs into it instead, so the script refuses a
package older than `0.1.18~`. kiri-init, kiri's agent, builds its nftables
table `ip kiri` (the bridges' masquerade, the forward chain accepting their
traffic, the published ports' DNAT) when it starts; kiri-init of 0.1.18 and
of 0.1.18~etny1 rebuilds it with `NFT_MSG_DESTROYTABLE`, a message a kernel
before 6.3 (the box's Ubuntu 22.04 kernel is 5.15) refuses with EINVAL, so on
such a kernel those builds have no table: no masquerade, no kernel port
forwarding, and the enclaves reach nothing outside the VM. kiri-lite
`0.1.18~etny2` (kiri commit f05d33d) falls back to `NFT_MSG_DELTABLE`. The
script checks for the table after starting kiri-init and refuses without it,
naming the kernel; `dpkg --compare-versions` cannot tell the builds apart,
since upstream `0.1.18` sorts above `0.1.18~etny2`.

`etny-node-provision-kiri.sh` runs as root inside the VM, is idempotent, and:

1. installs the kiri-lite package: the config file's `KIRI_LITE_DEB_URL`,
   checked against `KIRI_LITE_DEB_SHA256`, when both are set (a package built
   from a fork or served from a mirror); otherwise the kiri-lite package for
   the VM's architecture that `SHA256SUMS-linux` of download.runkiri.com
   names, after verifying that file's detached signature with the Kiri
   Releases key, whose fingerprint
   `900CB63F75328B627AF5549353263AE758141A6B` the script carries;
2. writes `/etc/systemd/system/kiri.service.d/etny.conf` (`KIRI_IDLE_SECS=0`:
   the daemon never powers off kiri-init; its default powers it off after 60 s
   with no running container or exec and no API request, keeping the daemon,
   its socket and the container records, and boots it again on the next
   request; `KIRI_HTTP_PORT=18080` and `KIRI_HTTPS_PORT=18443`: its
   container-domain routers, which have no off switch, leave ports 80 and
   443), enables and starts `kiri.service`, waits for `/run/kiri/docker.sock`,
   starts kiri-init (`POST /kiri/wake` on that socket) and checks that
   `nft list table ip kiri` finds its table (above; `nft` is installed when
   absent);
3. adds `ufw route allow in on kiri0`, `out on kiri0`, and the same for
   `kiri1`: the VM's default routed policy is deny; dockerd inserts its own
   accept rules into the host's forward chain, kiri accepts its bridges'
   traffic in the forward chain of its own table and adds nothing to the
   host's, so without the rules its containers reach nothing outside the VM.
   `kiri0` is kiri's default network, `kiri1` its first user network, the
   compose network `ethernity`;
4. while `docker.service` is active, refuses if a container other than the
   registry (`registry`) and the object store (`etny-swift-stream`) is
   running, since stopping dockerd stops every container with it, and stops
   `etny-node.service`; then stops and disables `docker.service`,
   `docker.socket` and `containerd.service` (they stay installed for the
   rollback);
5. writes `/etc/systemd/system/etny-node.service.d/container-engine.conf` with
   `Environment=DOCKER_HOST=unix:///run/kiri/docker.sock` and reloads systemd;
6. pulls `registry:2` through kiri;
7. starts `etny-node.service` again when step 4 stopped it.

It needs network access to download.runkiri.com (or to `KIRI_LITE_DEB_URL`)
and to Docker Hub, and `curl`, `gpg`, `dpkg` and `nft` in the VM. Run by hand
on a VM provisioned with docker, it moves that VM to kiri between orders: the
agent is stopped before dockerd and started again on kiri once the drop-in is
in place, and it pulls the enclave images again, since what dockerd holds in
`/var/lib/docker` is not visible to kiri.

Disk: kiri keeps each image layer twice, the compressed blob in its store
(`/var/lib/kiri-daemon`) and the unpacked layer under `/var/lib/kiri`, where
Docker keeps the unpacked layer alone. `docker rmi` and `docker image prune`,
which the agent runs when it drops the image trees beyond
`ENCLAVE_IMAGE_TREES_KEPT`, free the blobs; the unpacked layers are freed when
the engine restarts (`systemctl restart kiri`), which stops every container:
the registry and the object store come back by their restart policies, an
order's enclaves (`restart: on-failure`) do not. A tree's images take up to
some 8 GB, so the VM's disk holds the kept trees twice over between engine
restarts.

Rollback to dockerd (the script prints it too):

```
systemctl stop etny-node
systemctl disable --now kiri
rm -f /etc/systemd/system/etny-node.service.d/container-engine.conf
systemctl daemon-reload
systemctl enable --now containerd.service docker.socket docker.service
systemctl start etny-node
ufw route delete allow in on kiri0; ufw route delete allow out on kiri0
ufw route delete allow in on kiri1; ufw route delete allow out on kiri1
rm -f /etc/systemd/system/kiri.service.d/etny.conf; systemctl daemon-reload
apt-get remove kiri-lite    # apt-get purge also deletes /var/lib/kiri and /var/lib/kiri-daemon
```

dockerd's containers and images are untouched by the switch, so the registry
and the object store it ran come back as they were; set `CONTAINER_ENGINE`
back to `docker` in `config` so that a reprovisioning does not install kiri
again.

Known limits of kiri as the engine:

- unpacked layers are freed only when the engine starts: `docker rmi` and
  `docker image prune` delete the compressed blobs alone (above);
- `docker ps --filter ancestor=<image>` matches a container by the image
  string of its create request, not by the image id that string resolves to,
  as Docker does: the agent's filter, `ancestor=localhost:5000/etny-securelock`
  (a registry:port reference without a tag), matches only a container created
  from that identical string (kiri also matches an image id or id prefix, a
  `*` glob, and a registry-less name with or without `:latest`); the agent's
  start-up cleanup removes the enclave containers by name first, so the
  ancestor sweep after it finds nothing to do in the normal case;
- kiri adds no accept rules to the host's forward chain, so the `ufw route`
  rules above are what gives its containers egress through a firewall whose
  routed policy is deny.

## Mirror mode (REPLICATION_ONLY)

`REPLICATION_ONLY=True` runs the agent as a mirror: no SGX, no DP requests, no
task execution. For every configured network it pins recent results, ESR
state and CAS session bodies from chain into the Kubo at `IPFS_CONNECT_URL`,
keeps that Kubo peered with the validators' IPFS nodes published on chain and
with the peers in `IPFS_SWARM`, and pins every image the network's image
registry records as registered (`ImageRegistered` and
`TrustedZoneImageRegistered`; ECImageRegistryV2 and later): while the fetch
runs it connects to and peers with the registrant's `ipfsPeer`, a multiaddr
or the bare `/p2p/<id>` a publisher behind NAT registers, checks that the
tree is an enclave image the SDK builds (`scone_image`), then pins the tree
and its compose and announces both. The registrant is dialled up to six
times, ten seconds apart (a publisher behind NAT is reached only over the
connection its node keeps to this one, which comes and goes in the minutes
after a registration); one that cannot be reached is replaced by a provider
the routing system names and that takes a connection;
without one the attempt is counted and the image tried later, nothing
fetched, so a registration whose node is gone costs a lookup, not a fetch
run to its bounds. `IPFS_INTAKE_BIND=host:port` adds the
payload intake (`ipfs_intake.py`). A private key is still required for the
chain reads' account context; the mirror sends no transaction.

Settings, in the environment: `ESR_REPLICATION_INTERVAL_SECONDS` (300) is
the cadence of the replication round, which scans the registry for new
registrations and handles one image per round, the most recently registered
of those with the fewest attempts first; `ESR_MIN_FREE_STORAGE_GB`
(10) stops pinning below that free disk; `IMAGE_REGISTRY_SCAN_BLOCKS`
(200000) is how far back the first scan looks;
`REGISTERED_IMAGE_MAX_BYTES` (3 GiB), `REGISTERED_COMPOSE_MAX_BYTES` (1 MiB),
`REGISTERED_IMAGE_SCAN_MAX_BYTES` (1 GiB),
`REGISTERED_IMAGE_INFLATE_MAX_BYTES` (2 GiB),
`REGISTERED_IMAGE_PIN_TIMEOUT_SECONDS` (3600),
`REGISTERED_IMAGE_PIN_STALL_SECONDS` (300: a pin that fetches no block for
that long is ended and the image tried later) and
`REGISTERED_IMAGE_VERIFY_TIMEOUT_SECONDS` (900) bound one image's pin.

The bootnode (ipfs.ethernity.cloud) runs it as the docker container
`etny-mirror`: image `etny-mirror:latest` (python:3.10-slim with psutil,
python-dotenv, minio, web3 7.6.1, bs4 and requests), this repository
bind-mounted at `/app`, working directory `/app/node`, command
`python etny-node.py -k <key> -n bloxberg_testnet litvm_liteforge ethereum_sepolia`
(one handle per network it mirrors and serves the intake for; each `-unsafe`
twin follows its network), environment
`REPLICATION_ONLY=True`, `IPFS_CONNECT_URL=/ip4/<kubo>/tcp/5001/http`,
`IPFS_SWARM=<the validators' multiaddrs>`, `IPFS_INTAKE_BIND=0.0.0.0:8765`,
`ESR_REPLICATION_INTERVAL_SECONDS=60` and `LOG_LEVEL=info`, on the docker
network haproxy routes `/payload` to. It logs to `/var/log/etny-node.log` in
the container. An upgrade is `git pull --ff-only origin master` in the
checkout and a recreation of the container with the same arguments.

## Troubleshooting

### Failed installation
Backup the `~/mvp-pox-node/config` file

```
$ cd && cd mvp-pox-node
$ git pull
$ rm -rf config
$ ./etny-node-installer.sh
```

Follow through the prompts and use the same keys and addresses from your backup

### Node VM DNS
`ubuntu/etny-node-provision-dns.sh` installs the root zone's KSK-2017 and
KSK-2024 as DNSSEC trust anchors and sets the VM's resolvers to 1.1.1.1,
8.8.8.8, 1.0.0.1 and 8.8.4.4. VMs get it at provisioning. On a VM that logs
`DNSSEC validation failed: missing-key`, `NameResolutionError` or
`SSLV3_ALERT_HANDSHAKE_FAILURE`, run it from the host:

```
$ cd && cd mvp-pox-node
$ git pull
$ vagrant upload ubuntu/etny-node-provision-dns.sh /tmp/etny-node-provision-dns.sh
$ vagrant ssh -c 'sudo bash /tmp/etny-node-provision-dns.sh && resolvectl status | grep "DNS Servers"'
$ sudo systemctl restart etny-vagrant
```

