#!/bin/bash
# The generic/ubuntu2204 box runs systemd-resolved with DNSSEC=yes, and
# systemd 249 trusts only the root zone's KSK-2017 (key tag 20326). The root
# zone's DNSKEY set has been signed by KSK-2024 (key tag 38696) alone since the
# rollover of 2026-10-11, so without this anchor every lookup in the VM fails
# with "DNSSEC validation failed: missing-key". A positive trust anchor file
# replaces the built-in anchors, so it lists both keys (DS records as IANA
# publishes them in root-anchors.xml).
set -e

mkdir -p /etc/dnssec-trust-anchors.d
cat > /etc/dnssec-trust-anchors.d/root.positive <<'EOF'
. IN DS 20326 8 2 E06D44B80B8F1D39A95C0B0D7C65D08458E880409BBC683457104237C7F8EC8D
. IN DS 38696 8 2 683D2D0ACB8C9B712A1948B27F741219298D0A450D612C483AF444A4C0FB2B16
EOF
systemctl restart systemd-resolved
