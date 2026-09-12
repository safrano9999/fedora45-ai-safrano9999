#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 2 ]; then
    echo "Usage: resolve-build-inputs.sh OUTPUT OPENCLAW_VERSION" >&2
    exit 2
fi

OUTPUT="$1"
OPENCLAW_REQUESTED="$2"

for command in curl git jq sha256sum; do
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

GH_AUTH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -z "$GH_AUTH_TOKEN" ] && command -v gh >/dev/null 2>&1 \
    && gh auth status --hostname github.com >/dev/null 2>&1; then
    GH_AUTH_TOKEN="$(gh auth token)"
fi

github_api() {
    local endpoint="$1"
    local -a headers=()
    [ -z "$GH_AUTH_TOKEN" ] || headers=(-H "Authorization: Bearer $GH_AUTH_TOKEN")
    curl "${CURL_RETRY[@]}" \
        -H 'Accept: application/vnd.github+json' \
        -H 'X-GitHub-Api-Version: 2022-11-28' \
        "${headers[@]}" "https://api.github.com${endpoint}"
}

npm_latest() {
    local package="$1" encoded
    encoded="$(jq -nr --arg package "$package" '$package | @uri')"
    curl "${CURL_RETRY[@]}" \
        "https://registry.npmjs.org/${encoded}/latest" | jq -er '.version'
}

fedora_repomd_hash() {
    local repo="$1"
    curl "${CURL_RETRY[@]}" \
        "https://mirrors.fedoraproject.org/metalink?repo=${repo}&arch=x86_64" \
        | sed -n 's#.*<hash type="sha256">\([^<]*\)</hash>.*#\1#p' \
        | head -n 1
}

git_remote_commit() {
    local repository="$1" reference="$2" attempt commit=""
    for attempt in {1..10}; do
        commit="$(git ls-remote "$repository" "$reference" | awk 'NR == 1 {print $1}')" || true
        if [[ "$commit" =~ ^[0-9a-f]{40}$ ]]; then
            printf '%s\n' "$commit"
            return 0
        fi
        [ "$attempt" -eq 10 ] || sleep 5
    done
    echo "Cannot resolve $repository $reference after 10 attempts" >&2
    return 1
}

require_match() {
    local name="$1" value="$2" pattern="$3"
    [[ "$value" =~ $pattern ]] || {
        echo "Invalid resolved value for $name: $value" >&2
        exit 1
    }
}

UV_RELEASE="$(github_api /repos/astral-sh/uv/releases/latest)"
UV_VERSION="$(jq -er '.tag_name | ltrimstr("v")' <<<"$UV_RELEASE")"
UV_INSTALLER_SHA256="$(curl "${CURL_RETRY[@]}" \
    "https://astral.sh/uv/${UV_VERSION}/install.sh" | sha256sum | cut -d' ' -f1)"

FEDORA_BASE_REPOMD="$(fedora_repomd_hash fedora-45)"
FEDORA_UPDATES_REPOMD="$(fedora_repomd_hash updates-released-f45)"
require_match FEDORA_BASE_REPOMD "$FEDORA_BASE_REPOMD" '^[0-9a-f]{64}$'
require_match FEDORA_UPDATES_REPOMD "$FEDORA_UPDATES_REPOMD" '^[0-9a-f]{64}$'
FEDORA_REPOMD_KEY="$(printf '%s\n%s\n' "$FEDORA_BASE_REPOMD" "$FEDORA_UPDATES_REPOMD" \
    | sha256sum | cut -d' ' -f1)"

SOLANA_INSTALLER_URL='https://release.anza.xyz/stable/agave-install-init-x86_64-unknown-linux-gnu'
SOLANA_INSTALLER_MD5="$(curl "${CURL_RETRY[@]}" --head "$SOLANA_INSTALLER_URL" \
    | awk -F': *' 'tolower($1) == "etag" {gsub(/"/, "", $2); sub(/\r$/, "", $2); print $2; exit}')"

FUGU_COMMIT="$(git_remote_commit https://github.com/SakanaAI/fugu.git HEAD)"
ELECTRUM_KEYS_COMMIT="$(git_remote_commit https://github.com/spesmilo/electrum.git refs/heads/master)"

CLOUDFLARED_RELEASE="$(github_api /repos/cloudflare/cloudflared/releases/latest)"
CLOUDFLARED_VERSION="$(jq -er '.tag_name' <<<"$CLOUDFLARED_RELEASE")"
CLOUDFLARED_SHA256="$(jq -er '.assets[] | select(.name == "cloudflared-linux-amd64") | .digest | sub("^sha256:"; "")' <<<"$CLOUDFLARED_RELEASE")"

OPENCLAW_VERSION="$OPENCLAW_REQUESTED"
CODEX_VERSION="$(npm_latest @openai/codex)"
CLAUDE_CODE_VERSION="$(npm_latest @anthropic-ai/claude-code)"
OPENCLAW_BRAVE_PLUGIN_VERSION="${OPENCLAW_BRAVE_PLUGIN_VERSION:-$OPENCLAW_VERSION}"

encoded_openclaw="$(jq -nr --arg package openclaw '$package | @uri')"
registry_openclaw_document="$(curl "${CURL_RETRY[@]}" \
    "https://registry.npmjs.org/${encoded_openclaw}/${OPENCLAW_VERSION}")"
registry_openclaw="$(jq -er '.version' <<<"$registry_openclaw_document")"
[ "$registry_openclaw" = "$OPENCLAW_VERSION" ] || {
    echo "Requested OpenClaw $OPENCLAW_VERSION, npm returned $registry_openclaw" >&2
    exit 1
}

require_match FEDORA_REPOMD_KEY "$FEDORA_REPOMD_KEY" '^[0-9a-f]{64}$'
require_match UV_VERSION "$UV_VERSION" '^[0-9]+([.][0-9]+){2}([._+-][A-Za-z0-9.-]+)?$'
require_match UV_INSTALLER_SHA256 "$UV_INSTALLER_SHA256" '^[0-9a-f]{64}$'
require_match SOLANA_INSTALLER_MD5 "$SOLANA_INSTALLER_MD5" '^[0-9a-f]{32}$'
require_match FUGU_COMMIT "$FUGU_COMMIT" '^[0-9a-f]{40}$'
require_match ELECTRUM_KEYS_COMMIT "$ELECTRUM_KEYS_COMMIT" '^[0-9a-f]{40}$'
require_match CLOUDFLARED_VERSION "$CLOUDFLARED_VERSION" '^[A-Za-z0-9._+-]+$'
require_match CLOUDFLARED_SHA256 "$CLOUDFLARED_SHA256" '^[0-9a-f]{64}$'
require_match OPENCLAW_VERSION "$OPENCLAW_VERSION" '^[A-Za-z0-9._+-]+$'
require_match CODEX_VERSION "$CODEX_VERSION" '^[A-Za-z0-9._+-]+$'
require_match CLAUDE_CODE_VERSION "$CLAUDE_CODE_VERSION" '^[A-Za-z0-9._+-]+$'
require_match OPENCLAW_BRAVE_PLUGIN_VERSION "$OPENCLAW_BRAVE_PLUGIN_VERSION" '^[A-Za-z0-9._+-]+$'

temporary="${OUTPUT}.tmp"
{
    printf 'FEDORA_REPOMD_KEY=%s\n' "$FEDORA_REPOMD_KEY"
    printf 'UV_VERSION=%s\n' "$UV_VERSION"
    printf 'UV_INSTALLER_SHA256=%s\n' "$UV_INSTALLER_SHA256"
    printf 'SOLANA_INSTALLER_MD5=%s\n' "$SOLANA_INSTALLER_MD5"
    printf 'FUGU_COMMIT=%s\n' "$FUGU_COMMIT"
    printf 'ELECTRUM_KEYS_COMMIT=%s\n' "$ELECTRUM_KEYS_COMMIT"
    printf 'CLOUDFLARED_VERSION=%s\n' "$CLOUDFLARED_VERSION"
    printf 'CLOUDFLARED_SHA256=%s\n' "$CLOUDFLARED_SHA256"
    printf 'OPENCLAW_VERSION=%s\n' "$OPENCLAW_VERSION"
    printf 'CODEX_VERSION=%s\n' "$CODEX_VERSION"
    printf 'CLAUDE_CODE_VERSION=%s\n' "$CLAUDE_CODE_VERSION"
    printf 'OPENCLAW_BRAVE_PLUGIN_VERSION=%s\n' "$OPENCLAW_BRAVE_PLUGIN_VERSION"
} > "$temporary"
mv -f "$temporary" "$OUTPUT"
printf 'Resolved immutable build inputs -> %s\n' "$OUTPUT"
