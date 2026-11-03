#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 2 ]; then
    echo "Usage: resolve-build-inputs.sh OUTPUT OPENCLAW_VERSION" >&2
    exit 2
fi

OUTPUT="$1"
OPENCLAW_REQUESTED="$2"

for command in curl jq sha256sum python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing build resolver dependency: $command" >&2
        exit 1
    }
done

CURL_RETRY=(
    --fail --silent --show-error --location
    --retry 10 --retry-delay 5 --retry-all-errors
    --connect-timeout 30 --max-time 300
)

case "${TARGETARCH:-amd64}" in
    amd64) fedora_arch=x86_64 ;;
    arm64) fedora_arch=aarch64 ;;
    *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;;
esac

fedora_repomd_hash() {
    local repo="$1"
    curl "${CURL_RETRY[@]}" \
        "https://mirrors.fedoraproject.org/metalink?repo=${repo}&arch=${fedora_arch}" \
        | sed -n 's#.*<hash type="sha256">\([^<]*\)</hash>.*#\1#p' \
        | head -n 1
}

require_match() {
    local name="$1" value="$2" pattern="$3"
    [[ "$value" =~ $pattern ]] || {
        echo "Invalid resolved value for $name: $value" >&2
        exit 1
    }
}

FEDORA_BASE_REPOMD="$(fedora_repomd_hash fedora-45)"
FEDORA_UPDATES_REPOMD="$(fedora_repomd_hash updates-released-f45)"
require_match FEDORA_BASE_REPOMD "$FEDORA_BASE_REPOMD" '^[0-9a-f]{64}$'
require_match FEDORA_UPDATES_REPOMD "$FEDORA_UPDATES_REPOMD" '^[0-9a-f]{64}$'
FEDORA_REPOMD_KEY="$(printf '%s\n%s\n' "$FEDORA_BASE_REPOMD" "$FEDORA_UPDATES_REPOMD" \
    | sha256sum | cut -d' ' -f1)"

OPENCLAW_VERSION="$OPENCLAW_REQUESTED"

encoded_openclaw="$(jq -nr --arg package openclaw '$package | @uri')"
registry_openclaw_document="$(curl "${CURL_RETRY[@]}" \
    "https://registry.npmjs.org/${encoded_openclaw}/${OPENCLAW_VERSION}")"
registry_openclaw="$(jq -er '.version' <<<"$registry_openclaw_document")"
[ "$registry_openclaw" = "$OPENCLAW_VERSION" ] || {
    echo "Requested OpenClaw $OPENCLAW_VERSION, npm returned $registry_openclaw" >&2
    exit 1
}

require_match FEDORA_REPOMD_KEY "$FEDORA_REPOMD_KEY" '^[0-9a-f]{64}$'
temporary="${OUTPUT}.tmp"
{
    printf 'FEDORA_REPOMD_KEY=%s\n' "$FEDORA_REPOMD_KEY"
    python3 "$(dirname -- "${BASH_SOURCE[0]}")/../../upgrade-loop/build_dependencies.py" --emit
    python3 - "$(dirname -- "${BASH_SOURCE[0]}")/architecture-inputs.json" "${TARGETARCH:-amd64}" <<'ARCH'
import json, sys
for name, value in json.load(open(sys.argv[1]))['architectures'][sys.argv[2]].items():
    print(f'{name}={value}')
ARCH
    printf 'OPENCLAW_VERSION=%s\n' "$OPENCLAW_VERSION"
} > "$temporary"
mv -f "$temporary" "$OUTPUT"
printf 'Resolved immutable build inputs -> %s\n' "$OUTPUT"
