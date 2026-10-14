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
EOF
$
```


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

