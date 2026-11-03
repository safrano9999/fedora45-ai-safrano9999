#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(uname -m)" != aarch64 ] && [ "$(uname -m)" != arm64 ]; then
    exit 0
fi

: "${ARM64_TOOLCHAINS_RELEASE:?ARM64_TOOLCHAINS_RELEASE is required on ARM64}"
: "${ARM64_ELECTRUM_VERSION:?ARM64_ELECTRUM_VERSION is required on ARM64}"
: "${ARM64_AGAVE_VERSION:?ARM64_AGAVE_VERSION is required on ARM64}"
: "${ARM64_ELECTRUM_SHA256:?ARM64_ELECTRUM_SHA256 is required on ARM64}"
: "${ARM64_AGAVE_SHA256:?ARM64_AGAVE_SHA256 is required on ARM64}"

command -v gh >/dev/null
command -v sha256sum >/dev/null
command -v tar >/dev/null

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

electrum_archive="electrum-${ARM64_ELECTRUM_VERSION}-linux-arm64.tar.gz"
agave_archive="agave-${ARM64_AGAVE_VERSION#v}-linux-arm64.tar.gz"

gh release download "$ARM64_TOOLCHAINS_RELEASE" \
    --repo safrano9999/DIESDAS- \
    --pattern "$electrum_archive" \
    --pattern "$agave_archive" \
    --dir "$work_dir"

printf '%s  %s\n' "$ARM64_ELECTRUM_SHA256" "$work_dir/$electrum_archive" \
    | sha256sum --check --strict -
printf '%s  %s\n' "$ARM64_AGAVE_SHA256" "$work_dir/$agave_archive" \
    | sha256sum --check --strict -

tar -xzf "$work_dir/$electrum_archive" -C /
tar -xzf "$work_dir/$agave_archive" -C /opt

test -x /opt/electrum/electrum
test -x /opt/solana-release/bin/solana
test -x /opt/solana-release/bin/agave-validator

ln -sfn /opt/electrum/electrum /usr/local/bin/electrum
for binary in /opt/solana-release/bin/*; do
    [ -x "$binary" ] || continue
    ln -sfn "$binary" "/usr/local/bin/${binary##*/}"
done

mkdir -p /README
printf '{"architecture":"linux/arm64","electrum":"%s","agave":"%s","release":"%s"}\n' \
    "$ARM64_ELECTRUM_VERSION" "$ARM64_AGAVE_VERSION" "$ARM64_TOOLCHAINS_RELEASE" \
    > /README/fedora45-ai-arm64-toolchains.json

electrum --offline --version
solana --version
