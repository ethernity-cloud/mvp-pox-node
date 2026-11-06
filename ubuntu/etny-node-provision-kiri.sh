#!/bin/bash
# Installs kiri-lite, the Kiri container engine, on the node VM and points the
# etny-node service at its Docker API socket. Run as root inside the VM, after
# etny-node-provision-docker.sh, when the config file has CONTAINER_ENGINE=kiri
# (vagrant/qemu-3.1, qemu-6.2, qemu-8.2). Every step checks what is in place
# before changing it, so a second run changes nothing.
#
# What it does:
#   1. Installs the kiri-lite package. The config file's KIRI_LITE_DEB_URL,
#      checked against KIRI_LITE_DEB_SHA256, when both are set; otherwise the
#      kiri-lite package for this architecture that SHA256SUMS-linux of
#      download.runkiri.com names, the sums file verified with the Kiri
#      Releases key (fingerprint 900CB63F75328B627AF5549353263AE758141A6B,
#      fetched from the archive and compared to that value). A package older
#      than 0.1.18~ is refused (dpkg --compare-versions): kiri 0.1.17 mounts
#      the host's devtmpfs into a privileged container, which breaks the
#      host's terminals, and every enclave container is privileged.
#   2. Writes the kiri unit drop-in /etc/systemd/system/kiri.service.d/etny.conf:
#      KIRI_IDLE_SECS=0, so the daemon never powers off kiri-init, its agent
#      (the default powers it off after 60 s with no running container or
#      exec and no API request, keeping the daemon, its socket and the
#      container records, and boots it again on the next request);
#      KIRI_HTTP_PORT and KIRI_HTTPS_PORT move its container-domain routers
#      off ports 80 and 443 (the routers have no off switch in the Linux
#      build; the *.kiri.local resolver stays on 127.0.0.1:53535). Enables
#      and starts kiri, waits for /run/kiri/docker.sock, starts kiri-init
#      (POST /kiri/wake) and checks that the nftables table `ip kiri`, which
#      kiri-init builds when it starts, exists: it holds the bridges'
#      masquerade, the forward chain accepting their traffic and the
#      published ports' DNAT. kiri-init of 0.1.18 and of 0.1.18~etny1
#      rebuilds the table with NFT_MSG_DESTROYTABLE, which a kernel before
#      6.3 (Ubuntu 22.04: 5.15) refuses with EINVAL, so on such a kernel it
#      builds no table; a missing table is refused naming the kernel and
#      kiri-lite 0.1.18~etny2 (kiri commit f05d33d), whose kiri-init falls
#      back to NFT_MSG_DELTABLE. dpkg --compare-versions cannot tell these
#      builds apart: upstream 0.1.18 sorts above 0.1.18~etny2.
#   3. ufw: route rules for kiri's bridges kiri0 (its default network) and
#      kiri1 (its first user network, the compose network `ethernity`).
#   4. While docker.service is active: refuses when a container other than
#      the order registry (`registry`) and the object store
#      (`etny-swift-stream`) is running, since stopping dockerd stops every
#      container with it, and stops etny-node.service, started again in
#      step 7. Then stops and disables docker.service, docker.socket and
#      containerd.service. docker-ce-cli and the compose plugin stay: the
#      node runs the engine through `docker` and `docker-compose`.
#   5. Writes /etc/systemd/system/etny-node.service.d/container-engine.conf
#      with DOCKER_HOST=unix:///run/kiri/docker.sock and reloads systemd.
#   6. Pulls registry:2 through kiri, as the docker provisioning pulled it
#      through dockerd, so that the first integration test needs no Docker
#      Hub pull.
#   7. Starts etny-node.service again when step 4 stopped it.
#
# Needs: systemd, ufw enabled (etny-node-provision-docker.sh), curl, gpg,
# dpkg and nft (the nftables package, installed when absent); access to
# download.runkiri.com (or to KIRI_LITE_DEB_URL) and to Docker Hub. The
# config file is read for KIRI_LITE_DEB_URL and KIRI_LITE_DEB_SHA256 only.
#
# Rollback: the commands printed at the end (README, "Container engine").
set -euo pipefail

CONFIG_FILE=/home/vagrant/etny/node/config
ARCHIVE=https://download.runkiri.com
KIRI_KEY_FINGERPRINT=900CB63F75328B627AF5549353263AE758141A6B
MIN_VERSION='0.1.18~'
KIRI_SOCKET=/run/kiri/docker.sock
KIRI_DROPIN=/etc/systemd/system/kiri.service.d/etny.conf
NODE_DROPIN=/etc/systemd/system/etny-node.service.d/container-engine.conf
KIRI_HTTP_PORT=18080
KIRI_HTTPS_PORT=18443
BRIDGES=(kiri0 kiri1)
DOCKER_UNITS=(docker.service docker.socket containerd.service)
DOCKER_SOCKET=/var/run/docker.sock
# The containers the node keeps between orders: the order registry
# (node/etny-node.py REGISTRY_CONTAINER) and the object store
# (node/docker/docker-compose-swift-stream.yml container_name). Any other
# running container is an order's or an integration test's.
REGISTRY_CONTAINER=registry
OBJECT_STORE_CONTAINER=etny-swift-stream

export DEBIAN_FRONTEND=noninteractive

# The value of key $1 in the config file (its last assignment, trimmed), or
# nothing when the file or the key is absent.
config_value() {
    [ -r "${CONFIG_FILE}" ] || return 0
    { grep -E "^[[:space:]]*$1[[:space:]]*=" "${CONFIG_FILE}" || true; } \
        | tail -n 1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

fail() {
    echo "etny-node-provision-kiri: $*" >&2
    exit 1
}

WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

ARCH=$(dpkg --print-architecture)
DEB_URL=$(config_value KIRI_LITE_DEB_URL)
DEB_SHA256=$(config_value KIRI_LITE_DEB_SHA256)

# 1. The package.
if [ -n "${DEB_URL}" ] && [ -n "${DEB_SHA256}" ]; then
    echo "Fetching ${DEB_URL} (KIRI_LITE_DEB_URL)"
    DEB="${WORK}/$(basename "${DEB_URL}")"
    curl -fsSL -o "${DEB}" "${DEB_URL}"
    echo "${DEB_SHA256}  ${DEB}" | sha256sum -c --quiet - \
        || fail "${DEB_URL} does not have the SHA-256 KIRI_LITE_DEB_SHA256 gives"
elif [ -n "${DEB_URL}${DEB_SHA256}" ]; then
    fail "KIRI_LITE_DEB_URL and KIRI_LITE_DEB_SHA256 are set together or not at all"
else
    echo "Fetching the kiri-lite package ${ARCHIVE} publishes"
    curl -fsSL -o "${WORK}/kiri-archive-key.asc" "${ARCHIVE}/kiri-archive-key.asc"
    curl -fsSL -o "${WORK}/SHA256SUMS-linux" "${ARCHIVE}/SHA256SUMS-linux"
    curl -fsSL -o "${WORK}/SHA256SUMS-linux.asc" "${ARCHIVE}/SHA256SUMS-linux.asc"
    export GNUPGHOME="${WORK}/gnupg"
    mkdir -m 700 "${GNUPGHOME}"
    gpg --batch --quiet --import "${WORK}/kiri-archive-key.asc" 2>/dev/null \
        || fail "${ARCHIVE}/kiri-archive-key.asc is not a key gpg imports"
    FINGERPRINTS=$(gpg --batch --with-colons --list-keys 2>/dev/null | awk -F: '$1 == "fpr" {print $10}')
    [ "${FINGERPRINTS}" = "${KIRI_KEY_FINGERPRINT}" ] \
        || fail "${ARCHIVE}/kiri-archive-key.asc holds the key(s) ${FINGERPRINTS//$'\n'/ }, not ${KIRI_KEY_FINGERPRINT}"
    # VALIDSIG's last field is the primary key fingerprint of the signer.
    gpg --batch --status-fd 1 --verify "${WORK}/SHA256SUMS-linux.asc" "${WORK}/SHA256SUMS-linux" 2>/dev/null \
        | awk -v fpr="${KIRI_KEY_FINGERPRINT}" '$1 == "[GNUPG:]" && $2 == "VALIDSIG" && $NF == fpr {ok = 1} END {exit !ok}' \
        || fail "SHA256SUMS-linux is not signed by the Kiri Releases key ${KIRI_KEY_FINGERPRINT}"
    DEB_NAME=''
    DEB_VERSION=''
    while read -r sum name; do
        version=${name#kiri-lite_}
        version=${version%"_${ARCH}.deb"}
        if [ -z "${DEB_NAME}" ] || dpkg --compare-versions "${version}" gt "${DEB_VERSION}"; then
            DEB_NAME=${name}
            DEB_VERSION=${version}
            DEB_SHA256=${sum}
        fi
    done < <(grep -E "^[0-9a-f]{64}  kiri-lite_[^_ ]+_${ARCH}\.deb$" "${WORK}/SHA256SUMS-linux" || true)
    [ -n "${DEB_NAME}" ] || fail "SHA256SUMS-linux of ${ARCHIVE} names no kiri-lite package for ${ARCH}"
    DEB="${WORK}/${DEB_NAME}"
    curl -fsSL -o "${DEB}" "${ARCHIVE}/${DEB_NAME}"
    echo "${DEB_SHA256}  ${DEB}" | sha256sum -c --quiet - \
        || fail "${ARCHIVE}/${DEB_NAME} does not have the SHA-256 SHA256SUMS-linux gives"
fi

DEB_PACKAGE=$(dpkg-deb -f "${DEB}" Package)
DEB_ARCH=$(dpkg-deb -f "${DEB}" Architecture)
DEB_VERSION=$(dpkg-deb -f "${DEB}" Version)
[ "${DEB_PACKAGE}" = kiri-lite ] || fail "${DEB} is the package ${DEB_PACKAGE}, not kiri-lite"
[ "${DEB_ARCH}" = "${ARCH}" ] || fail "${DEB} is built for ${DEB_ARCH}; this VM is ${ARCH}"
dpkg --compare-versions "${DEB_VERSION}" ge "${MIN_VERSION}" \
    || fail "kiri-lite ${DEB_VERSION} is older than 0.1.18: it mounts the host's devtmpfs into a privileged container, which breaks the host's terminals, and every enclave container is privileged; not installed"

INSTALLED=$(dpkg-query -W -f '${db:Status-Status} ${Version}' kiri-lite 2>/dev/null || true)
if [ "${INSTALLED}" = "installed ${DEB_VERSION}" ]; then
    echo "kiri-lite ${DEB_VERSION} is installed"
else
    dpkg -i "${DEB}"
fi

# 2. The engine's unit: kiri-init never powered off when idle; the
# container-domain routers off ports 80 and 443.
install -d -m 755 "$(dirname "${KIRI_DROPIN}")"
cat > "${KIRI_DROPIN}" <<EOF
# Written by etny-node-provision-kiri.sh (mvp-pox-node).
[Service]
# The daemon never powers off kiri-init, its agent. Its default powers
# kiri-init off after 60 s with no running container or exec and no API
# request, keeping the daemon, its socket and the container records, and
# boots it again on the next request.
Environment=KIRI_IDLE_SECS=0
# The container-domain routers (http://<name>.kiri.localhost) have no off
# switch; on these ports they take nothing a node serves, and ufw denies
# incoming connections to them.
Environment=KIRI_HTTP_PORT=${KIRI_HTTP_PORT}
Environment=KIRI_HTTPS_PORT=${KIRI_HTTPS_PORT}
EOF
systemctl daemon-reload
systemctl enable --now kiri.service
for _ in $(seq 1 60); do
    [ -S "${KIRI_SOCKET}" ] && break
    sleep 1
done
[ -S "${KIRI_SOCKET}" ] || fail "kiri did not open ${KIRI_SOCKET} within 60 s (journalctl -u kiri)"

# kiri-init builds the nftables table ip kiri (the bridges' masquerade, the
# forward chain accepting their traffic, the published ports' DNAT) when it
# starts; the daemon starts it on the first request that needs it, and
# POST /kiri/wake is one. kiri-init of 0.1.18 and of 0.1.18~etny1 sends the
# rebuild with NFT_MSG_DESTROYTABLE, which a kernel before 6.3 refuses with
# EINVAL, and then has no table.
curl -fsS -o /dev/null --unix-socket "${KIRI_SOCKET}" -X POST http://localhost/kiri/wake \
    || fail "kiri did not start kiri-init (POST /kiri/wake on ${KIRI_SOCKET} failed; journalctl -u kiri)"
if ! command -v nft >/dev/null; then
    apt-get -yq install nftables
fi
for _ in $(seq 1 20); do
    nft list table ip kiri >/dev/null 2>&1 && break
    sleep 0.5
done
nft list table ip kiri >/dev/null 2>&1 \
    || fail "kiri-init of kiri-lite ${DEB_VERSION} built no nftables table ip kiri on kernel $(uname -r): kiri-init of 0.1.18 and of 0.1.18~etny1 rebuilds the table with NFT_MSG_DESTROYTABLE, which a kernel before 6.3 refuses, leaving the bridges without masquerade and the published ports without kernel forwarding (journalctl -u kiri: 'kiri-init: no uplink'); kiri-lite 0.1.18~etny2 (kiri commit f05d33d) falls back to NFT_MSG_DELTABLE"

# 3. ufw. The VM's default routed policy is deny (etny-node-provision-docker.sh
# enables ufw; its filter FORWARD chain drops what no rule accepts). dockerd
# inserts its own accept rules into that chain; kiri accepts its bridges'
# forwarded traffic in the forward chain of its own table, ip kiri, and adds
# nothing to the host's chain, so without these rules its containers reach
# nothing outside the VM (the CAS, the RPC, IPFS, the image registry). ufw
# keeps an existing rule as it is.
for bridge in "${BRIDGES[@]}"; do
    ufw route allow in on "${bridge}"
    ufw route allow out on "${bridge}"
done

# 4. dockerd off. Stopping dockerd stops every container it runs, so while it
# is active a running container other than the registry and the object store
# (an order's or an integration test's) refuses the switch, and etny-node is
# stopped first, started again on kiri in step 7. The CLI and the compose
# plugin stay installed.
NODE_STOPPED=0
if systemctl is-active --quiet docker.service; then
    NAMES=$(docker -H "unix://${DOCKER_SOCKET}" ps --format '{{.Names}}') \
        || fail "docker ps on ${DOCKER_SOCKET} failed while docker.service is active; the running containers are unknown"
    RUNNING=$(grep -vxF -e "${REGISTRY_CONTAINER}" -e "${OBJECT_STORE_CONTAINER}" <<<"${NAMES}" || true)
    [ -z "${RUNNING}" ] \
        || fail "the containers ${RUNNING//$'\n'/ } run under dockerd, which stopping dockerd stops; run again when only ${REGISTRY_CONTAINER} and ${OBJECT_STORE_CONTAINER} run"
    if systemctl is-active --quiet etny-node.service; then
        systemctl stop etny-node.service
        NODE_STOPPED=1
    fi
fi
UNITS=()
for unit in "${DOCKER_UNITS[@]}"; do
    if [ -n "$(systemctl list-unit-files --no-legend "${unit}")" ]; then
        UNITS+=("${unit}")
    fi
done
if [ "${#UNITS[@]}" -gt 0 ]; then
    systemctl disable --now "${UNITS[@]}"
fi

# 5. The node's docker and docker-compose commands reach kiri.
install -d -m 755 "$(dirname "${NODE_DROPIN}")"
cat > "${NODE_DROPIN}" <<EOF
# Written by etny-node-provision-kiri.sh (mvp-pox-node): the node's docker and
# docker-compose commands reach the Kiri engine.
[Service]
Environment=DOCKER_HOST=unix://${KIRI_SOCKET}
EOF
systemctl daemon-reload

# 6. The order registry's image, into kiri's store.
export DOCKER_HOST="unix://${KIRI_SOCKET}"
docker pull registry:2 || echo "registry:2 was not pulled; the node pulls it when it first runs an integration test"

# 7. The agent, on kiri.
if [ "${NODE_STOPPED}" = 1 ]; then
    systemctl start etny-node.service
fi

echo "Container engine: $(docker version --format '{{.Server.Platform.Name}} {{.Server.Version}}') at ${DOCKER_HOST}"
if [ "${NODE_STOPPED}" = 1 ]; then
    echo "etny-node.service was stopped before dockerd and is started again on kiri."
elif ! systemctl is-active --quiet etny-node.service; then
    echo "etny-node.service is not running; it starts on kiri."
fi
cat <<EOF
Rollback to dockerd:
  systemctl stop etny-node
  systemctl disable --now kiri
  rm -f ${NODE_DROPIN}
  systemctl daemon-reload
  systemctl enable --now ${DOCKER_UNITS[2]} ${DOCKER_UNITS[1]} ${DOCKER_UNITS[0]}
  systemctl start etny-node
  ufw route delete allow in on ${BRIDGES[0]}; ufw route delete allow out on ${BRIDGES[0]}
  ufw route delete allow in on ${BRIDGES[1]}; ufw route delete allow out on ${BRIDGES[1]}
  rm -f ${KIRI_DROPIN}; systemctl daemon-reload
  apt-get remove kiri-lite    (apt-get purge kiri-lite also deletes /var/lib/kiri and /var/lib/kiri-daemon)
EOF
