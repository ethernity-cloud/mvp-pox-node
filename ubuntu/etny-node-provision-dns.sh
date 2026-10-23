#!/bin/bash
# The generic/ubuntu2204 box runs systemd-resolved with DNSSEC=yes, and
# systemd 249 trusts only the root zone's KSK-2017 (key tag 20326). The root
# zone's DNSKEY set has been signed by KSK-2024 (key tag 38696) alone since the
# rollover of 2026-10-11, so without this anchor every lookup in the VM fails
# with "DNSSEC validation failed: missing-key". A positive trust anchor file
# replaces the built-in anchors, so it lists both keys (DS records as IANA
# publishes them in root-anchors.xml).
#
# The box also names 4.2.2.1, 4.2.2.2 and OpenDNS 208.67.220.220 as resolvers,
# globally (resolved.conf) and per link (its netplan 01-netcfg.yaml). OpenDNS
# answers names it filters, sepolia.gateway.tenderly.co among them, with the
# address of its block page, so every TLS connection to them fails. Both lists
# are replaced by Cloudflare's and Google's resolvers: a drop-in for resolved,
# and a drop-in for every link netplan configures, whose empty DNS= clears the
# link's list before setting the new one.
set -e

RESOLVERS="1.1.1.1 8.8.8.8 1.0.0.1 8.8.4.4"

mkdir -p /etc/dnssec-trust-anchors.d
cat > /etc/dnssec-trust-anchors.d/root.positive <<'EOF'
. IN DS 20326 8 2 E06D44B80B8F1D39A95C0B0D7C65D08458E880409BBC683457104237C7F8EC8D
. IN DS 38696 8 2 683D2D0ACB8C9B712A1948B27F741219298D0A450D612C483AF444A4C0FB2B16
EOF

mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/etny-dns.conf <<EOF
[Resolve]
DNS=
DNS=${RESOLVERS}
FallbackDNS=
EOF

for unit in /run/systemd/network/*netplan*.network; do
  [ -e "${unit}" ] || continue
  dropin="/etc/systemd/network/$(basename "${unit}").d"
  mkdir -p "${dropin}"
  cat > "${dropin}/etny-dns.conf" <<EOF
[Network]
DNS=
DNS=${RESOLVERS}
EOF
done

networkctl reload
systemctl restart systemd-resolved
