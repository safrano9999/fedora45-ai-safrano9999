#!/usr/bin/env bash

# Shared architecture policy for the Fedora45 layer setup scripts. The host
# architecture is detected by default, while IMAGE_ARCH/--arch can override it
# for preparing a deployment on another machine.

fedora_normalize_arch() {
    case "${1:-}" in
        amd64|x86_64|x86) printf '%s\n' amd64 ;;
        arm64|aarch64) printf '%s\n' arm64 ;;
        multi|all) printf '%s\n' multi ;;
        *)
            echo "Unsupported image architecture: ${1:-<empty>} (use amd64, arm64, or multi)" >&2
            return 2
            ;;
    esac
}

fedora_detect_arch() {
    local requested="${1:-${IMAGE_ARCH:-${FEDORA_IMAGE_ARCH:-}}}"
    requested="${requested:-$(uname -m)}"
    FEDORA_IMAGE_ARCH="$(fedora_normalize_arch "$requested")"
    export FEDORA_IMAGE_ARCH
}

fedora_arch_image_name() {
    local image_name="$1"
    case "${FEDORA_IMAGE_ARCH:?fedora_detect_arch must run first}" in
        amd64|multi)
            printf '%s\n' "$image_name"
            ;;
        arm64)
            case "$image_name" in
                fedora45-ai-safrano9999-full)
                    printf '%s\n' fedora45-ai-arm64-full
                    ;;
                fedora45-ai-*)
                    printf 'fedora45-ai-arm64-%s\n' "${image_name#fedora45-ai-}"
                    ;;
                *)
                    echo "Cannot map Fedora image to ARM64: $image_name" >&2
                    return 2
                    ;;
            esac
            ;;
    esac
}

fedora_registry_image() {
    local image_name="$1"
    local tag="${2:-${IMAGE_TAG:-latest}}"
    printf 'ghcr.io/safrano9999/%s:%s\n' \
        "$(fedora_arch_image_name "$image_name")" "$tag"
}
