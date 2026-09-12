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

"$BUILD_DIR/prepare-build-context.sh"
set -a
# shellcheck source=/dev/null
. "$ROOT/build.conf"
set +a

for name in HERMES_VERSION ELECTRUM_VERSION \
    LND_VERSION GETH_VERSION GETH_COMMIT WEBHOOK_VERSION VDITOR_VERSION \
    OPENCLAW_VERSION FEDORA45_AI_CORE_PRE_IMAGE PERSISTAINER_REPOSITORY \
    PERSISTAINER_REF; do
    [ -n "${!name:-}" ] || {
        echo "Missing $name in build.conf" >&2
        exit 1
    }
done
[ -s "$ROOT/.resolved-build.env" ] || {
    echo "Missing .resolved-build.env" >&2
    exit 1
}

BUILD_ARGS=(
    --build-arg "HERMES_VERSION=$HERMES_VERSION"
    --build-arg "ELECTRUM_VERSION=$ELECTRUM_VERSION"
    --build-arg "LND_VERSION=$LND_VERSION"
    --build-arg "GETH_VERSION=$GETH_VERSION"
    --build-arg "GETH_COMMIT=$GETH_COMMIT"
    --build-arg "WEBHOOK_VERSION=$WEBHOOK_VERSION"
    --build-arg "VDITOR_VERSION=$VDITOR_VERSION"
)
while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || {
        echo "Invalid resolved build argument: $key" >&2
        exit 1
    }
    BUILD_ARGS+=(--build-arg "$key=$value")
done < "$ROOT/.resolved-build.env"
$NO_CACHE && BUILD_ARGS+=(--no-cache)

IMAGE="$FEDORA45_AI_CORE_PRE_IMAGE"
echo "  Building $IMAGE from $ROOT ..."
podman build --pull=always "${BUILD_ARGS[@]}" \
    --platform linux/amd64 \
    --target ai-core-pre \
    -t "$IMAGE" \
    -f "$ROOT/Containerfile" \
    "$ROOT"
echo "  Done. Image ready: $IMAGE"
