#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

BUILD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$BUILD_DIR/.." && pwd -P)"
NO_CACHE=false

case "${1:-}" in
    "") ;;
    --no-cache) NO_CACHE=true ;;
    --help|-h)
        echo "Usage: ./build/build-local.sh [--no-cache]"
        exit 0
        ;;
    *)
        echo "Usage: ./build/build-local.sh [--no-cache]" >&2
        exit 2
        ;;
esac

command -v podman >/dev/null 2>&1 || {
    echo "Missing local build dependency: podman" >&2
    exit 1
}

ensure_ghcr_login() {
    local username
    podman login --get-login ghcr.io >/dev/null 2>&1 && return 0
    command -v gh >/dev/null 2>&1 || {
        echo "gh is required to authenticate to the private Core-pre image" >&2
        return 1
    }
    username="$(gh api user --jq .login)"
    gh auth token |
        podman login ghcr.io --username "$username" --password-stdin
}

"$BUILD_DIR/prepare-build-context.sh"
set -a
# shellcheck source=/dev/null
. "$ROOT/build.conf"
set +a

for name in AI_CORE_PRE_IMAGE FEDORA45_AI_CORE_IMAGE OPENCLAW_VERSION NOTE_REPOSITORY \
    NOTE_RELEASE_TAG NOTE_RELEASE_ASSET NOTE_RELEASE_SHA256; do
    [ -n "${!name:-}" ] || {
        echo "Missing $name in build.conf" >&2
        exit 1
    }
done

PULL_POLICY=always
[[ "$AI_CORE_PRE_IMAGE" != localhost/* ]] || PULL_POLICY=missing
[[ "$AI_CORE_PRE_IMAGE" != ghcr.io/* ]] || ensure_ghcr_login

BUILD_ARGS=(
    --build-arg "AI_CORE_PRE_IMAGE=$AI_CORE_PRE_IMAGE"
    --build-arg "OPENCLAW_VERSION=$OPENCLAW_VERSION"
    --build-arg "NOTE_REPOSITORY=$NOTE_REPOSITORY"
    --build-arg "NOTE_RELEASE_TAG=$NOTE_RELEASE_TAG"
    --build-arg "NOTE_RELEASE_ASSET=$NOTE_RELEASE_ASSET"
    --build-arg "NOTE_RELEASE_SHA256=$NOTE_RELEASE_SHA256"
)
$NO_CACHE && BUILD_ARGS+=(--no-cache)

IMAGE="$FEDORA45_AI_CORE_IMAGE"
echo "  Building $IMAGE from $ROOT ..."
podman build --pull="$PULL_POLICY" "${BUILD_ARGS[@]}" \
    --platform linux/amd64 \
    --target ai-core \
    -t "$IMAGE" \
    -f "$ROOT/Containerfile" \
    "$ROOT"
echo "  Done. Image ready: $IMAGE"
