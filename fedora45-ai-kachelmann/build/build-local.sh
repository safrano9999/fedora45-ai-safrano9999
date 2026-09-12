#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

BUILD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$BUILD_DIR/.." && pwd -P)"
LAYER="${ROOT##*/}"
NO_CACHE=false
OUTPUT_KEY="$(printf '%s' "$LAYER" | tr '[:lower:]-' '[:upper:]_')_IMAGE"

case "$LAYER" in
    fedora44-ai-base|fedora45-ai-base)
        PARENT_KEY=AI_CORE_IMAGE
        PARENT_LABEL=Core
        BUILD_TARGET=ai-base
        ;;
    fedora44-ai-safrano9999|fedora45-ai-safrano9999)
        PARENT_KEY=AI_KACHELMANN_IMAGE
        PARENT_LABEL=KACHELMANN
        BUILD_TARGET=ai-safrano9999
        ;;
    fedora44-ai-safrano9999-full|fedora45-ai-safrano9999-full)
        PARENT_KEY=AI_SAFRANO9999_IMAGE
        PARENT_LABEL=Safrano
        BUILD_TARGET=ai-safrano9999-full
        ;;
    fedora44-ai-kachelmann|fedora45-ai-kachelmann)
        PARENT_KEY=AI_BASE_IMAGE
        PARENT_LABEL=Base
        BUILD_TARGET=ai-kachelmann
        ;;
    *)
        echo "Unsupported Fedora layer directory: $LAYER" >&2
        exit 2
        ;;
esac

case "${1:-}" in
    "") ;;
    --no-cache) NO_CACHE=true ;;
    --help|-h)
        echo "Usage: ./build/build-local.sh [--no-cache]"
        exit 0
        ;;
    *) echo "Usage: ./build/build-local.sh [--no-cache]" >&2; exit 2 ;;
esac

command -v podman >/dev/null 2>&1 || {
    echo "Missing local build dependency: podman" >&2
    exit 1
}

ensure_ghcr_login() {
    local username
    podman login --get-login ghcr.io >/dev/null 2>&1 && return 0
    command -v gh >/dev/null 2>&1 || {
        echo "gh is required to authenticate to the private $PARENT_LABEL image" >&2
        return 1
    }
    username="$(gh api user --jq .login)"
    gh auth token |
        podman login ghcr.io --username "$username" --password-stdin
}

prepare_arguments=()
$NO_CACHE && prepare_arguments+=(--no-cache)
"$BUILD_DIR/prepare-build-context.sh" "${prepare_arguments[@]}"

set -a
# shellcheck source=/dev/null
. "$ROOT/build.conf"
set +a

for name in "$PARENT_KEY" "$OUTPUT_KEY"; do
    [ -n "${!name:-}" ] || {
        echo "Missing $name in build.conf" >&2
        exit 1
    }
done
PARENT_IMAGE="${!PARENT_KEY}"
IMAGE="${!OUTPUT_KEY}"
[ -s "$ROOT/.resolved-build.env" ] || {
    echo "Missing .resolved-build.env" >&2
    exit 1
}
PULL_POLICY=always
[[ "$PARENT_IMAGE" != localhost/* ]] || PULL_POLICY=missing
[[ "$PARENT_IMAGE" != ghcr.io/* ]] || ensure_ghcr_login

BUILD_ARGS=(--build-arg "$PARENT_KEY=$PARENT_IMAGE")
while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || {
        echo "Invalid resolved build argument: $key" >&2
        exit 1
    }
    BUILD_ARGS+=(--build-arg "$key=$value")
done < "$ROOT/.resolved-build.env"
$NO_CACHE && BUILD_ARGS+=(--no-cache)

echo "  Building $IMAGE from $ROOT ..."
podman build --pull="$PULL_POLICY" "${BUILD_ARGS[@]}" \
    --target "$BUILD_TARGET" \
    -t "$IMAGE" \
    -f "$ROOT/Containerfile" \
    "$ROOT"
echo "  Done. Image ready: $IMAGE"
