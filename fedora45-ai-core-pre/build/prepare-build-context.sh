#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

CONTEXT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILD="$CONTEXT/build"

set -a
# shellcheck source=/dev/null
. "$CONTEXT/build.conf"
set +a

for command in curl git jq sha256sum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing build preparation dependency: $command" >&2
        exit 1
    }
done

stage_persistainer() {
    local repository="${PERSISTAINER_REPOSITORY:-}"
    local ref="${PERSISTAINER_REF:-}"
    local target="$BUILD/vendor/persistainer"
    local stage checkout runtime unexpected commit fetch_ref="refs/heads/${ref}"

    if [ -n "${SAFRANO_SOURCE_MANIFEST:-}" ]; then
        fetch_ref="$(python3 "${SAFRANO_SOURCE_MANIFEST%/*}/source_snapshot.py" \
            --manifest "$SAFRANO_SOURCE_MANIFEST" --repository "$repository")"
    fi

    [[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
        echo "Invalid PERSISTAINER_REPOSITORY: $repository" >&2
        exit 1
    }
    [ -n "$ref" ] && git check-ref-format --branch "$ref" >/dev/null 2>&1 || {
        echo "Invalid PERSISTAINER_REF: $ref" >&2
        exit 1
    }

    # A failed private-repository fetch must never leave a stale usable payload.
    rm -rf -- "$target"
    mkdir -p "$BUILD/vendor"
    stage="$(mktemp -d "$BUILD/.persistainer.XXXXXX")"
    checkout="$stage/checkout"
    cleanup_persistainer_stage() {
        rm -rf -- "$stage"
    }
    trap cleanup_persistainer_stage EXIT

    git init -q "$checkout"
    git -C "$checkout" remote add origin "https://github.com/${repository}.git"
    GIT_TERMINAL_PROMPT=0 git -C "$checkout" fetch -q --no-tags --depth=1 \
        origin "$fetch_ref"
    git -C "$checkout" checkout -q --detach FETCH_HEAD
    commit="$(git -C "$checkout" rev-parse --verify HEAD)"
    if [ -n "${SAFRANO_SOURCE_MANIFEST:-}" ] && [ "$commit" != "$fetch_ref" ]; then
        echo "persistainer differs from prepared source snapshot" >&2
        return 1
    fi
    runtime="$checkout/image/runtime"

    [ -d "$runtime" ] && [ ! -L "$runtime" ] || {
        echo "persistainer has no real image/runtime directory at $commit" >&2
        exit 1
    }
    for required in \
        etc/systemd/system/persistainer.service \
        usr/local/bin/persistainer; do
        [ -f "$runtime/$required" ] && [ ! -L "$runtime/$required" ] || {
            echo "Missing persistainer runtime file at $commit: $required" >&2
            exit 1
        }
    done
    unexpected="$(find "$runtime" -mindepth 1 ! -type d ! -type f -print -quit)"
    [ -z "$unexpected" ] || {
        echo "Unsupported persistainer runtime entry at $commit: $unexpected" >&2
        exit 1
    }
    bash -n "$runtime/usr/local/bin/persistainer"

    mkdir -p "$stage/payload/image"
    cp -a -- "$runtime" "$stage/payload/image/runtime"
    mv -- "$stage/payload" "$target"
    printf '  Staged %s@%s (%s)\n' "$repository" "$ref" "$commit"

    trap - EXIT
    cleanup_persistainer_stage
}

stage_persistainer
"$BUILD/resolve-build-inputs.sh" \
    "$CONTEXT/.resolved-build.env" \
    "$OPENCLAW_VERSION"

printf 'Core-pre build context ready: %s\n' "$CONTEXT"
