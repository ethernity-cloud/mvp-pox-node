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

`-n`/`--network` names the networks the agent runs (`node/config.py`): `bloxberg_mainnet`, `bloxberg_testnet`, `polygon_mainnet`, `polygon_amoy`, `iotex_testnet`, `ethereum_sepolia`, `litvm_liteforge`, the `-unsafe` networks `bloxberg_testnet_unsafe`, `polygon_amoy_unsafe`, `litvm_liteforge_unsafe` and `ethereum_sepolia_unsafe`, and the testnet copies of PoX 1.0 `bloxberg_testnet_pox10` and `polygon_amoy_pox10`. Without it, or with `all`, every network is loaded except the `-unsafe` ones, which are loaded only when named. `auto` (the installer's AUTO) also leaves out the testnet copies of PoX 1.0, which are loaded when named or under `all`.

A network whose PoX is 1.0 or 2.0 is also named by a word that ends in that generation, `_pox10` or `_pox20` (`config.GENERATION_WORDS`). The word selects the network its name selects, in any letter case. A word adds nothing when the selection also gives its network's name or an earlier word for it, so a network named by both is run once; a name is run at each place it is given:

| Network | Name | Generation word |
|---|---|---|
| bloxberg mainnet, PoX 1.0 | `bloxberg_mainnet` | `bloxberg_pox10` |
| Polygon mainnet, PoX 1.0 | `polygon_mainnet` | `polygon_pox10` |
| bloxberg testnet, PoX 2.0 | `bloxberg_testnet` | `bloxberg_testnet_pox20` |
| bloxberg testnet, PoX 2.0, `-unsafe` | `bloxberg_testnet_unsafe` | `bloxberg_testnet_unsafe_pox20` |
| Polygon Amoy, PoX 2.0 | `polygon_amoy` | `polygon_amoy_pox20` |
| Polygon Amoy, PoX 2.0, `-unsafe` | `polygon_amoy_unsafe` | `polygon_amoy_unsafe_pox20` |
| bloxberg testnet's copy of PoX 1.0 | `bloxberg_testnet_pox10` | the name |
| Polygon Amoy's copy of PoX 1.0 | `polygon_amoy_pox10` | the name |
| IoTeX testnet | `iotex_testnet` | none |
| Ethereum Sepolia | `ethereum_sepolia`, `ethereum_sepolia_unsafe` | none |
| LitVM LiteForge | `litvm_liteforge`, `litvm_liteforge_unsafe` | none |

A network's settings and files are kept under its name, whichever word selects it: `<NAME>_<FIELD>` in `node/.env` and in the operator's `config`, the argument `--<name>_<field>`, the directory `node/<name>/`, the intake route `/payload/<name>/` and the network of `--handover --network`, which takes either word. A variable or an argument under a generation word that is not a name sets nothing. The installer's list (`networks.ini`) ends each network's line with its generation word and writes the names into the `NETWORK` line of the node's `config`.

A testnet comes in two kinds. `bloxberg_testnet`, `litvm_liteforge` and `ethereum_sepolia` run trustedzones provisioned by that chain's ethernity-cas validator set, which admits an enclave on a DCAP quote: the platform must produce one (requirement `dcap`, the default for a name in `<NETWORK>_TRUSTEDZONE_IMAGES`), and the set validates their orders in full, as on mainnet. `bloxberg_testnet_unsafe`, `litvm_liteforge_unsafe` and `ethereum_sepolia_unsafe` have the chain and contracts of the network they are named after and run only that network's `-unsafe` trustedzones (`etny-pynithy-testnet-unsafe`, `etny-nodenithy-testnet-unsafe`; `ecld-pynithy-litvm-testnet-unsafe`, `ecld-nodenithy-litvm-testnet-unsafe`; `ecld-pynithy-ethereum-sepolia-unsafe`, `ecld-nodenithy-ethereum-sepolia-unsafe`), which are debug-signed, self-sign their certificate from their measurement and contact no CAS: any platform with hardware SGX runs them (requirement `sgx`, written `name:sgx`), EPID-only and SGX1 with or without FLC included, and the validators judge their orders with the derived-signer check waived. Named together with the network it is named after, an `-unsafe` network joins it as one handle on their shared contract.

The agent runs a trustedzone only after an integration test has passed on this platform: the trustedzone's own (`<NETWORK>_INTEGRATION_TEST_IMAGES`), or one that covers its requirement. A DCAP platform therefore runs both kinds; a platform whose CAS-provisioned test fails and whose `-unsafe` test passes runs the `-unsafe` trustedzones alone. The names it runs are logged at start (`Platform meets dcap and sgx: runs ...`), advertised in its DP request's `metadata4` (`trustedzones=<names>;ipfs=<peer id>`), and a DO request whose image metadata names another trustedzone (field 2) is not taken, with the reason logged. Mainnet has no `-unsafe` network. An order's enclaves run with the LAS of a trustedzone tree that passed the integration test, not the LAS bundled in the dApp's own tree: the pass keeps the tree's las service block in `node/<network>/las-tested-<trustedzone>.yml` and its image under the docker tag `localhost:5000/etny-las:tested-<trustedzone>`; the order's compose takes that block's entrypoint and command and its LAS image name is pointed at the tested image (logged as `The LAS of <trustedzone>'s tested tree runs this order's enclaves`). A trustedzone without a test of its own takes the tested LAS of one with the same platform requirement; a recorded pass loaded at start without a kept LAS runs its test again; until a tested LAS is held the order runs its tree's own. The LAS is in no enclave's measurement.

## The testnet copies of PoX 1.0 (release 3.8.19)

`bloxberg_testnet_pox10` and `polygon_amoy_pox10` are testnet copies of the
mainnets' PoX 1.0. `bloxberg_testnet_pox10` is bloxberg mainnet's 1.0
contract code on chain 8995: the PoX
`0x69A7bEB5Da2d3dA711d52E491D71e51296b08745`, a proxy and its own token
(symbol tE10), the V1 image registry
`0x7dBC1b591cc3b7136D863bc7430C6Db50E929665` and the heartbeat
`0x4426C87484038F9B720a6012A1191DA73bDa0ad3`. `polygon_amoy_pox10` is Polygon
mainnet's on Amoy, chain 80002: the PoX
`0x9Aad82557c84c11C4031382d8Bf2cacF8f256a02`, whose token is Amoy's test ECLD
(symbol tECLD), the V1 image registry
`0xa2EE2a9118d0011AF05a6196c862D37d8c29F8eC` and the heartbeat
`0x937F74419E31c1200e86B6D2439D9f12b3Fa12c9`. Their trustedzones
(`etny-pynithy-testnet-pox10`, `etny-nodenithy-testnet-pox10`;
`ecld-pynithy-amoy-pox10`, `ecld-nodenithy-amoy-pox10`) are signed
`--production` and provisioned by Scontain's CAS, as the mainnets' 1.0
trustedzones are: requirement `dcap`, no validator set, no CAS resolved from
the chain.

The agent runs a copy as it runs the copy's mainnet. The contract answers no
`pricingVersion()` (`pox=legacy`). `NETWORK_TYPE` is `MAINNET`: a DO
request's placement waits for the node's turn in a cycle of
`max(1, nodes // 25)` blocks, the nodes being those the copy's heartbeat
counts, and the payout line is logged after an order. Every setting of
`node/.env` but the addresses, the token name, the trustedzones and, on Amoy,
the RPC and the chain id is the mainnet's: on `polygon_amoy_pox10` the fee
ceiling is 500 gwei over a priority fee of 32. `bloxberg_testnet_pox10`
names bloxberg testnet's Enclave State Registry, which its trustedzones bake
(`BLOXBERG_TESTNET_POX10_ESR_CONTRACT_ADDRESS` sets another);
`polygon_amoy_pox10` names none.

A copy is run when `-n` names it or is `all`; `auto`, `openbeta` and the
legacy names leave it out, and a selection that holds `all`, `auto` or
`openbeta` beside network names takes the word's networks alone. It runs
from the node's wallet, on the gas the wallet holds on the copy's chain, in
its own directory `node/<network>/`. The installer lists the copies as
choices 11 and 12 (`networks.ini`), which its AUTO leaves out.

## DO request age (release 3.8.18)

The agent places no order on a DO request created more than
`DO_REQUEST_MAX_AGE_SECONDS` (86400 in `node/.env`) before the timestamp of
the chain's latest block, on every network and whether the request is pinned
to the node or not; `<NETWORK>_DO_REQUEST_MAX_AGE_SECONDS` or the argument
`--<network>_do_request_max_age_seconds` sets one network's. A request's
`createdAt` is read from the PoX's storage with `eth_getStorageAt`
(`node/do_request_age.py`): from the storage `ethStorageAddress()` names
(layout `cloud`, every network but `bloxberg_mainnet` and
`bloxberg_testnet_pox10`) or from the PoX itself (layout `v05`, those two),
logged at the first pass as `DO request
ages: createdAt is read from <address> (layout <name>)`. Each pass with a
request to judge establishes the age boundary, the lowest id created within
the limit, by a binary search from the boundary before; the pass never reads
a request below it (`DO requests below <boundary> ... none of them is judged
or placed`, logged when it moves). A pass whose boundary is not established
places no order: an implausible storage is logged at ERROR (`DO request ages
are not established`), an RPC failure at WARNING (`DO request ages were not
read`), each at most once an hour.

## The 2.0 PoX (release 3.8.0)

At start the agent probes each network's protocol contract for
`pricingVersion()`. A contract that answers is a 2.0 PoX
(pox-smart-contract-litvm): the agent binds the reader, the pricing, the
reservation broker and the ECLD token the contract names and logs
`pox=2.0 reader=... pricing=...`; one that reverts is a legacy contract and
runs as before (`pox=legacy`). The 3.8 releases log `pox=1.2` for a contract
that answers. `bloxberg_testnet` and `bloxberg_testnet_unsafe` bind the 2.0
implementation
`0x830c781F0352050343211B6BFA83600B3e77647C`, `polygon_amoy` and
`polygon_amoy_unsafe` the 2.0 implementation
`0xd1182deA61CDa785C320cdBE5888EaC4178cAAF2`.

On a 2.0 PoX the node's offer is `addDPRequestV2`, carrying the GFLOPS of its
capability record (`gflops`, cuv 4; one CU is 100 GFLOPS) and a tip per
CU-hour, and is refreshed an hour before its expiry (`refreshDPRequest`; the
pricing's `dpRequestTtl` sets the expiry). The record comes from the
trustedzone's integration test, which waits `INTEGRATION_TEST_SECONDS`
(900 s): the test writes `capability.time.etny` and `capability.etny`, the
agent admits the record (`record admitted` in the log) at cuv 4
(`CU_VERSION_MIN`) and above with `gflops` at most `CU_MAX_GFLOPS` (6,400),
and keeps it in `node/<network>/capability.json` for seven days, re-measuring
a day before; a stored record below cuv 4 or without `gflops` is dropped when
the store loads, and its integration test runs again. A V2 DO request is
judged on its recorded terms against the reader's quote plus the tip; an
order's relays are paid from the order's own chain fee at its gas price cap,
and the agent waits while the chain's gas price is above the cap (`gas wait`
in the log). The result of a recorded order is posted with
`_addResultToOrderV2`.

Where a network's validators vote on offers, they cancel an offer whose
record fails a check or whose node's probes deliver below its attested
GFLOPS, with the reason in `DPRequestCancelled` (1 to 8,
`node/tests/spec/metadata-keys.md`; the agent logs it by name). The agent
reads these cancels for every request of its own, before each add and at
every idle tick, from a cursor kept in `node/<network>/broker_cursor.json`,
so a cancel that landed while it was stopped is seen at its start. A reason
that faults the record drops the record the cancelled request carried, and
the integration test runs again before the next request. An offer without a
record is cancelled with reason 9 (`RecordMissing`) unless its
`trustedzones=` list names at least one trustedzone, every one ending in
`-unsafe`, and the network admits them: an `-unsafe` trustedzone contacts no
CAS, so its node never carries a record. After a reason-9 cancel the agent
posts no DP request without a record on that network, unless the request
names only `-unsafe` trustedzones, until an integration test produces one.
The hold is logged, kept in `node/<network>/broker_cursor.json` across
restarts, and the tests able to produce a record (requirement `dcap`) run
again at once, then on the failure backoff while they pass without one;
meanwhile the heartbeat, the IPFS upkeep, an order approved late and the
broker's tick go on; the first advertised record clears it. On a network
that has not cancelled its offer with reason 9, an agent holding no record
posts its offer without one.

Settings, per network in `node/.env` or the operator's `config` (every one
optional; `node/.env.example` documents them all):

- `<NETWORK>_TIP_PER_CU_HOUR_WEI` (0): the operator's tip in token wei per
  CU-hour, added to the gas the node prices into its offer.
- `<NETWORK>_ACCEPT_RESERVED` (true): whether the offer accepts the reserved
  orders the reservation broker places; `0` keeps the node on spot orders.
- `<NETWORK>_FARM_INVITE` (empty): a farm owner's invitation
  `<farm>:<expiresAt>:<signature>`, joined at start with `joinFarm`; a refused
  invitation is logged once and the node stays farmless until a fresh one.
- `<NETWORK>_FARM_ACCEPT` (true): whether the node accepts the handovers its
  farm's members announce.
- `<NETWORK>_LOGS_CHUNK_BLOCKS` (9000; 3000 on `litvm_liteforge` and
  `litvm_liteforge_unsafe`): the blocks one `eth_getLogs` call reads, at most
  what the network's RPC serves in one call.

The broker items run only on a network whose contract names a broker (none is
deployed yet). There, `etny-node.py --handover --network <name>
[--appoint <node>]` asks the running agent to announce the handover of the
reserved order it runs and prints its answer; the agent serves the order to
the announced window, posts the result and adds no next DP request. The
systemd unit sends SIGHUP on a stop (`KillSignal=SIGHUP`): while a reserved
order runs the agent does the same, otherwise it stops as before;
`TimeoutStopSec=21600` lets a stop wait for the window, and
`ubuntu/etny-node-start.sh` installs the repository's unit whenever it differs
from the installed one, so the unit travels with `git pull`. The handover's
gas deposit is pulled in ECLD, so the node wallet needs an ECLD balance for
it beside its gas.

The agent's Python dependencies gain `cryptography` 3.4.8, which
`dependency_manager` installs at start as it does the others.

## PoX 2.0 matching (branch feature/matching)

On a 2.0 PoX whose pricing answers a matching contract (`matching()`) whose
`reader()` names its PoxMatchReader, the agent serves the network as
MATCHING-DESIGN.md section 7 states (`matching=... match_reader=...` in the
log). A pricing with no `matching()`, the zero address, and a matching
contract whose `reader()` is the zero address (not written by its owner yet)
leave the network as before, a PoX 1.0 network keeping its rotation.

- It keeps a copy of the roll in memory only (`matching.py`), rebuilt at
  every start by one paged scan and fed from PoxMatching's `Beat`,
  `Announced`, `Left` and `Skipped` logs, and ranks itself on it with the rule
  of `spec/matching.md` (copies in `node/tests/spec/matching/`).
- It sends one heartbeat per slot of PoxMatching's `slotSeconds` (one an hour
  on a testnet, two a day on a mainnet) at an offset its address gives, to
  PoxMatching alone; the offer's refresh comes first when it has expired, an
  announce is the slot's heartbeat when the node has no open period, and none
  is sent between an order's placement and its `etny-sw` row or while the node
  is banned. The heartbeat runs through an order's enclave wait too.
- It announces its offer after an add and whenever it has no open period, and
  again after a refresh that changed its tip; the refresh keeps the period's
  tip while a claim can be approved or a mandate is unanswered. It pauses
  while its task slot is taken on another network, its acceptance of a
  hand-over stands, the fee it would offer reaches the ceiling's
  `<NETWORK>_FEE_CEILING_PAUSE_PERCENT`, or the runners' default minimum is on
  and its score is below 0.50.
- On a request served by claims it sends `claim` with the round's stamp and
  its step in every round, on a settled read the copy has read the logs to,
  never placing the order itself; a mandated node sends its claim
  `<NETWORK>_CLAIM_AHEAD_BLOCKS` plus one blocks before its step, one that is
  not mandated after the mandates, a step per `<NETWORK>_CLAIM_SPREAD_NODES`
  nodes ahead and while fewer than `<NETWORK>_CLAIM_STAND_LIMIT` approvable
  claims ahead stand. A request takes the parameter set `requestInfo`'s
  `parameterSet` names, and none is claimed while that is 256 (no set in force
  for it). A request not served by claims (`claims` off in its
  parameter set) is placed as before. An approved claim's order is run when
  it appears on the offer; no integration test starts while a claim can be
  approved, and an approval ends a running one.
- An order's objects are fetched for `fetchTrySeconds` (600) after its
  approval before the order is closed as not fetchable, the validators' IPFS
  nodes read and dialled again between attempts.
- After each heartbeat it reads the late claims the heartbeat charged and its
  failed orders, tests their windows' block headers, and asks for the
  correction of full ones at most once an hour (`askCorrection`).
- It keeps a local list of the images whose orders ended in 59 or 63, or in
  20, 62 or a 33 that PoxMatching's `early33` judges a timeout before its time
  on the paid classes (`node/<network>/image_failures.json`), and skips an
  image for 7 days after 3 such ends (`skipImage`); it proves three nodes for
  an end in 20, 33, 59, 62 or 63 (`proveWide`).
- `<NETWORK>_PAYOUT_ADDRESS` names the rewards wallet once, before the next
  offer, while none is named; a wallet named otherwise is left to its own
  `changePayoutAddress`. The farm step names no payout: the join sets it, and
  a node with a named wallet joins once that wallet's `approveFarm` names the
  farm.
- The offer's memory is the host's total less the larger of
  `OFFER_MEMORY_RESERVE_GIB` (1) and the memory in use, MemAvailable; its
  storage the total of the node's working filesystem less the larger of
  `OFFER_STORAGE_RESERVE_GIB` (20) and the space in use; each at most the
  reading of the signed bench record the DP request carries in its metadata4
  (`encmem=`, the memory the bench allocated, and `custorage=`, the storage it
  read). Below 1 GB of either no DP request is posted.
- The offer states the node's enclave memory above its GFLOPS: the largest
  listed size at or below its memory figure whose bench record passed, the
  record's `encmem=`, at most `<NETWORK>_ENCLAVE_MEMORY_GIB` when set. A cancel
  with reason 10 (`EnclaveMemoryAboveRecord`) or 11 (`StorageAboveRecord`) is
  never followed by an offer stating the same figure on that record; the next
  states the record's reading below it, or the record's integration test, the
  bench, runs again.

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

Of the 2.0 PoX the mirror loads the configuration only: it probes each
contract's generation and binds the broker the contract names (reading no fee
percents, running no integration test and sending nothing), and on a network
with a broker it pins the artefacts every reservation's template names
(`templateOf`: the enclave images and compose, the payload, the challenge and
the session request) for the reservation's term, walking the reservations
from a cursor kept in `esr_progress.json`. The intake takes a payload lodged
on the signature of the request's owner, and when that owner is a contract
answering ERC-165 for `IReservationResolver` (the broker), on the signature
of the holder its `holderOf` names; a request for which the resolver finds no
holder is refused.

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

