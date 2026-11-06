# The node's enclave containers on kiri: trial on etnyvm13 (2026-11-06)

Node: etnyvm13 on dprocessor, Ubuntu 22.04.5, kernel 5.15.0-198, 2 vCPU, 7.9 GB, EPC 1 GB,
network polygon_amoy, operator 0x4F75D3b3453eB8AC085130b67af26210f0a30dEA, node 3.7.17 (bf18536).
Engines: Docker 29.9.2 + Compose v5.6.1 (the client CLIs stay under kiri too);
kiri 0.1.18~etny2 (upstream v0.1.18 = 82e13b5 plus the fork's nftables fallback, below).
Switch: dockerd disabled, kiri-lite installed, `DOCKER_HOST=unix:///run/kiri/docker.sock` in a
drop-in of etny-node.service, ufw route rules for kiri0/kiri1, KIRI_IDLE_SECS=0.

## Findings

1. **Upstream kiri does not work on the node VMs' kernel without a fix.** kiri-init rebuilds its
   nftables table with `NFT_MSG_DESTROYTABLE`, a message type Linux added in 6.3; the 5.15 kernel
   refuses the whole batch (EINVAL). Symptoms: `kiri-init: no uplink: Invalid argument` at start,
   no `table ip kiri`, every published port "relaying in user space", containers without egress;
   the trustedzone exited with code 1 four times (it cannot reach the CAS) and the integration
   test failed. Fixed in the fork (C:/Users/Iosif/kiri branch etny, f05d33d): the table removal
   falls back to `NFT_MSG_DELTABLE`, and to none when no table exists yet. With 0.1.18~etny2 the
   table appears (masquerade on eth0, the forward chain, kernel DNAT of the registry's 5000).
2. **SGX works under kiri.** A privileged container lists /dev/sgx_enclave (10,125) and
   /dev/sgx_provision; `--device` works too. The pynithy trustedzone ran in its enclave, the LAS
   answered its quote requests, the CAS attestation and the CAS challenge passed, and the
   integration test passed four times in a row (one failure before the NAT fix).
3. **Tasks run.** Three hello tasks pinned to node13 under kiri (orders 86, 87, 88) completed with
   the right results; the Docker baseline of the same morning is order 85.
4. The published 0.1.17 package mounts devtmpfs into privileged containers (upstream fixed it in
   ffe980f); the node's enclave containers are all privileged, so 0.1.17 was never an option.

## Measurements

Integration test of ecld-pynithy-amoy (images already in the engine's store; node log stamps).
"compose up" = "Enclave images ... are loaded" to "Checking if object context_test.etny";
"enclave phase" = that line to "Enclave execution finished".

| run | engine | compose up | enclave phase | start to passed |
|---|---|---|---|---|
| 2026-10-29 00:19 | Docker | (not logged) | 21.3 s | 2 m 21 s (with pull) |
| 2026-10-29 05:29 | Docker | 1.49 s | 19.1 s | 30.7 s |
| 10:59 | kiri etny1 + hand-made masquerade | 0.27 s | 25.3 s | 26.5 s |
| 11:04 | kiri etny2 | 0.24 s | 23.2 s | 34.2 s (registry restarted) |
| 11:12 | kiri etny2 | 0.31 s | 23.2 s | 25.3 s |
| 11:12 | kiri etny2 | 0.23 s | 21.2 s | 22.8 s |
| 11:14 (after the rollback) | Docker | 0.90 s | 22.3 s | 25.0 s |
| 11:14 | Docker | 1.03 s | 23.3 s | 25.8 s |

Same-day means: compose up kiri 0.26 s against Docker 0.97 s; enclave phase kiri 23.2 s against
Docker 22.8 s; a whole run 22.8-26.5 s against 25.0-25.8 s. The integration test is not faster in
any way a node operator would notice: the engine's share is under a second of a 25 s run.

Orders (nodenithy JS trustedzone; "preloaded -> ready" = "Task preloaded" to "Docker environment
ready"; the enclave creation figure is the node's "Trustedzone enclave created after N s").

| order | engine | preloaded -> ready | enclave created after | secured -> completed | client total |
|---|---|---|---|---|---|
| 85 | Docker | 13.1 s | 8 s | 91.3 s | 87 s |
| 86 | kiri (first run: 8 GB tree pulled into kiri's store) | 32.0 s | 9 s | 108.6 s | 107 s |
| 87 | kiri | 10.5 s | 8 s | 90.2 s | 87 s |
| 88 | kiri | 8.2 s | 6 s | 91.6 s | 91 s |

Engine events (create to start, from `docker events`):

| container | Docker (order 85) | kiri (orders 87, 88) |
|---|---|---|
| las | 387 ms | 96 ms, 54 ms |
| etny-trustedzone | 553 ms | 109 ms, 70 ms |
| etny-securelock | 227 ms | 21 ms, 26 ms |
| both LAS and trustedzone up, from the first create | 620 ms | 114 ms, 74 ms |

Memory (ps RSS, the node idle with its registry and MinIO containers running, measured at 11:11
under kiri and at 11:20 under Docker on the same VM):

| | Docker | kiri |
|---|---|---|
| engine processes | dockerd 101 MB, containerd 50 MB, two shims 10 MB each, two docker-proxy 4-5 MB each: about 180 MB | kiri 7.0 MB, kiri-init 1.4 MB: about 8 MB |
| service cgroups (memory.current, page cache included) | docker.service 141 MB + containerd.service 71 MB | not measured |
| per extra running container | one shim (about 10 MB) and a docker-proxy per published port | nothing |

About 170-200 MB saved on a 7.9 GB VM (2-3 %); each enclave container holds a SCONE heap of
256 MB to 1 GB besides. Disk: /var/lib/kiri 5.2 GB + /var/lib/kiri-daemon 1.8 GB after two image
trees (pynithy and the small nodenithy tree).

## Reading
- The engine's own work (create, start, compose up) is 5 to 7 times faster under kiri, which
  saves half a second to a few seconds per order; the "preloaded -> ready" phase went from 13.1 s
  to 8-10 s.
- The enclave's creation (6-9 s for the nodenithy image, 20-25 s for the pynithy one) is SCONE's
  and the same under both engines within the noise of four kiri and two Docker samples; the kiri
  pynithy samples (21-25 s) sit above the two Docker ones (19-21 s), not a settled difference.
- A task's wall time is dominated by the chain (placing, result transactions) and the enclave:
  87-91 s either way.

## Kiri-side items for the fork (upstream candidates)
1. The nftables fallback above (done, f05d33d).
2. Unpacked layers are freed only when the agent starts (RetainLayers sent from the boot path
   alone); rmi / image prune should send it, or /var/lib/kiri/layers grows across the node's
   tree pruning.
3. `ps --filter ancestor=localhost:5000/<image>` matches the exact create string only.
4. kiri adds no accept to the host's filter FORWARD chain; ufw's `deny routed` needs the route
   rules (or a kiri change inserting accepts as dockerd does).

## State of etnyvm13 at the end
Rolled back to dockerd at 11:14 UTC (/tmp/rollback-to-docker.sh): docker.service, docker.socket
and containerd enabled and active, kiri.service disabled and inactive (kiri-lite 0.1.18~etny2
stays installed, its store under /var/lib/kiri), the etny-node DOCKER_HOST drop-in removed, the
ufw route rules deleted, the registry and etny-swift-stream containers of dockerd started, the
node active and its integration test passed twice under Docker. The switch-over script
(/tmp/switch-to-kiri.sh on the VM, scratchpad/kiri-trial/switch-to-kiri.sh here) puts it back
on kiri in under a minute.
Logs: /home/vagrant/kiri-trial-logs on the VM.

## Decision and what this branch holds
The nodes stay on Docker (2026-11-06). The gain measured here is the engine's share of a task,
under a second, and about 170-200 MB of memory; it matters on a node with 2 GB of RAM running the
three enclave containers, where the engine is a tenth of the machine, and not on the 8 GB VMs of
the rig. This branch keeps the work for that case:
- the `CONTAINER_ENGINE` setting with `ubuntu/etny-node-provision-kiri.sh`, the templates, the
  validation, the start-up log line and their tests (README "Container engine");
- this report;
- `kiri-0.1.18-nftables-deltable-fallback.patch`, the fix kiri needs on a kernel before 6.3,
  as a patch on upstream v0.1.18 (82e13b5); `kiri-lite 0.1.18~etny2` is that build. The
  provisioning script refuses a build without it (no `table ip kiri` after the engine starts).

To pick it up: apply the patch to a checkout of libracoder/kiri at v0.1.18 or later (or propose it
upstream), build with `cargo xtask linux --arch amd64` and `packaging/linux/build.sh`, host the
.deb and name it with `KIRI_LITE_DEB_URL` / `KIRI_LITE_DEB_SHA256` in the node's config (or wait
for an upstream release that carries the fix), set `CONTAINER_ENGINE=kiri`, and test on a VM
sized like the target node: the measurements above come from an 8 GB VM; under 2 GB the figures
that decide are the enclave containers' resident memory next to the engine's, and whether an
order completes without swapping. Two kiri-side items remain open (above): the unpacked layers
freed only when the agent restarts, and the ancestor filter.
