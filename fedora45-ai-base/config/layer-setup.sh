#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="${ROOT##*/}"
LAYER="$IMAGE_NAME"
[[ "$LAYER" =~ ^fedora[0-9]+-ai-[a-z0-9][a-z0-9-]*$ ]] || {
    echo "Invalid Fedora layer directory: $LAYER" >&2
    exit 2
}

REGISTRY_IMAGE="ghcr.io/safrano9999/$IMAGE_NAME:latest"
LOCAL_IMAGE="localhost/$IMAGE_NAME:latest"
DEFAULT_IMAGE_CHOICE=1
OUTPUT_IMAGE_KEY="$(printf '%s' "$LAYER" | tr '[:lower:]-' '[:upper:]_')_IMAGE"
DEFAULT_INSTANCE="$LAYER"

NO_CONFIG=false
NO_BUILD=false
NO_CACHE=false
IMG_CHOICE=""
INSTANCE="${CONFIG_CONTAINER_NAME:-}"

show_help() {
    cat <<EOF
Usage: ./setup.sh [OPTIONS] [INSTANCE]

Options:
  --config-only  Configure and render without an image operation
  --no-config    Keep the existing instance configuration unchanged
  --no-build     Configure and render without an image operation
  --pull         Pull $REGISTRY_IMAGE
  --build        Build this repository's Containerfile
  --no-cache     Disable the local image build cache
  --help         Show this help and exit

Generated files are kept below CONTAINER/INSTANCE. The cumulative example
triple already present in this layer is used directly; setup performs no
example download or merge.
EOF
}

for argument in "$@"; do
    case "$argument" in
        --help|-h) show_help; exit 0 ;;
        --config-only|--no-build) NO_BUILD=true ;;
        --no-config) NO_CONFIG=true ;;
        --pull) IMG_CHOICE=1 ;;
        --build) IMG_CHOICE=2 ;;
        --no-cache) NO_CACHE=true ;;
        --*) echo "Unknown option: $argument" >&2; exit 2 ;;
        *)
            [ -z "$INSTANCE" ] || {
                echo "Only one INSTANCE may be selected" >&2
                exit 2
            }
            INSTANCE="$argument"
            ;;
    esac
done

finish() {
    python3 "$ROOT/config/quadlet_finish.py" \
        "$INSTANCE_DIR/$COMPOSE_FILE" \
        "$INSTANCE_DIR/$QUADLET_FILE" \
        "$INSTANCE"
    printf '\n  Instance: %s\n  Compose:  %s\n  Quadlet:  %s\n' \
        "$INSTANCE_DIR" \
        "$INSTANCE_DIR/$COMPOSE_FILE" \
        "$INSTANCE_DIR/$QUADLET_FILE"
}

render_image() {
    local image="$1"
    local compose="$INSTANCE_DIR/$COMPOSE_FILE"
    local quadlet="$INSTANCE_DIR/$QUADLET_FILE"
    local temporary="${compose}.tmp"

    [ -f "$compose" ] && [ -f "$quadlet" ] || {
        echo "config.sh did not render Compose and Quadlet files" >&2
        return 1
    }
    awk '
        /^    build:[[:space:]]*$/ { skipping = 1; next }
        skipping && /^    [^[:space:]]/ { skipping = 0 }
        !skipping { print }
    ' "$compose" > "$temporary"
    mv -f -- "$temporary" "$compose"
    sed -i "s#^    image: .*#    image: $image#" "$compose"
    sed -i "s#^Image=.*#Image=$image#" "$quadlet"
}

ensure_ghcr_login() {
    local username
    podman login --get-login ghcr.io >/dev/null 2>&1 && return 0
    command -v gh >/dev/null 2>&1 || {
        echo "gh is required to authenticate to private GHCR images" >&2
        return 1
    }
    username="$(gh api user --jq .login)"
    gh auth token |
        podman login ghcr.io --username "$username" --password-stdin
}

instance_arguments=(
    "$ROOT"
    --config "$ROOT/config/config.sh"
    --default-name "$DEFAULT_INSTANCE"
)
[ -z "$INSTANCE" ] || instance_arguments+=(--name "$INSTANCE")
INSTANCE_DIR="$(python3 "$ROOT/config/container-instance-setup.py" "${instance_arguments[@]}")"
INSTANCE="${INSTANCE_DIR##*/}"
ENV_FILE="$INSTANCE.env"
CONFIG_FILE="${INSTANCE}_config.conf"
CONTAINER_FILE="${INSTANCE}_container.conf"
COMPOSE_FILE="${INSTANCE}-compose.yml"
QUADLET_FILE="${INSTANCE}.container"

if ! $NO_CONFIG; then
    echo "  Configuring instance $INSTANCE..."
    (
        cd "$INSTANCE_DIR"
        CONFIG_CONTAINER_NAME="$INSTANCE" \
        CONFIG_CONTAINER_IMAGE="$REGISTRY_IMAGE" \
            bash ./config.sh
    )
    [ ! -f "$INSTANCE_DIR/$ENV_FILE" ] || chmod 0600 "$INSTANCE_DIR/$ENV_FILE"
    render_image "$REGISTRY_IMAGE"
fi

if $NO_BUILD; then
    finish
    echo "  Configuration complete; no image operation was requested."
    exit 0
fi

if [ -z "$IMG_CHOICE" ]; then
    printf '\n  Image source:\n    (1) Pull %s\n    (2) Build %s\n' \
        "$REGISTRY_IMAGE" "$LOCAL_IMAGE"
    echo "  Build locally from this repository's context."
    read -rp "  Choose [1/2] (default: $DEFAULT_IMAGE_CHOICE): " IMG_CHOICE
    IMG_CHOICE="${IMG_CHOICE:-$DEFAULT_IMAGE_CHOICE}"
fi

case "$IMG_CHOICE" in
    1)
        command -v podman >/dev/null 2>&1 || {
            echo "podman is required to pull the image" >&2
            exit 1
        }
        case "$REGISTRY_IMAGE" in ghcr.io/*) ensure_ghcr_login ;; esac
        podman pull --retry 10 --retry-delay 5s "$REGISTRY_IMAGE"
        render_image "$REGISTRY_IMAGE"
        ;;
    2)
        arguments=()
        $NO_CACHE && arguments+=(--no-cache)
        env "$OUTPUT_IMAGE_KEY=$LOCAL_IMAGE" \
            "$ROOT/build/build-local.sh" "${arguments[@]}"
        render_image "$LOCAL_IMAGE"
        ;;
    *)
        echo "Invalid image choice: $IMG_CHOICE" >&2
        exit 2
        ;;
esac

finish
