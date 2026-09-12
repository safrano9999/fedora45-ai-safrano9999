#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

CONTEXT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILD="$CONTEXT/build"

set -a
# shellcheck source=/dev/null
. "$CONTEXT/build.conf"
set +a

OPENCLAW_DETERMINISTIC_REPOSITORY=safrano9999/openclaw-deterministic-latest
: "${OPENCLAW_DETERMINISTIC_TAG:?Missing deterministic release tag in build.conf}"
OPENCLAW_DETERMINISTIC_ASSET="openclaw-${OPENCLAW_VERSION}-deterministic.tar.gz"
OPENCLAW_EPHEMERAL_REPOSITORY=safrano9999/openclaw-ephemeral
HERMES_EPHEMERAL_REPOSITORY=safrano9999/hermes-ephemeral

for command in curl git python3 sha256sum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing build preparation dependency: $command" >&2
        exit 1
    }
done

vendor_stage="$(mktemp -d "$BUILD/.vendor.XXXXXX")"
rm -rf -- "$BUILD/vendor"
cleanup_vendor_stage() {
    rm -rf -- "$vendor_stage"
}
trap cleanup_vendor_stage EXIT

stage_release_asset() {
    local repository="$1" tag="$2" asset="$3" expected_sha256="$4" destination="$5"
    mkdir -p "$(dirname "$destination")"
    curl -fsSL --retry 3 --connect-timeout 15 \
        "https://github.com/${repository}/releases/download/${tag}/${asset}" \
        -o "$destination"
    if [ -n "$expected_sha256" ]; then
        printf '%s  %s\n' "$expected_sha256" "$destination" | sha256sum -c -
    fi
}

checkout_source() {
    local repository="$1" destination="$2" commit="${3:-}" ref="${3:-main}"
    [[ -z "$commit" || "$commit" =~ ^[0-9a-f]{40}$ ]] || {
        echo "Expected a full source commit for $repository" >&2
        return 1
    }

    git init -q "$destination"
    git -C "$destination" remote add origin \
        "https://github.com/${repository}.git"
    GIT_TERMINAL_PROMPT=0 git -C "$destination" fetch -q --no-tags \
        --depth=1 origin "$ref"
    git -C "$destination" checkout -q --detach FETCH_HEAD
    [ "$(git -C "$destination" rev-parse HEAD)" = \
      "${commit:-$(git -C "$destination" rev-parse FETCH_HEAD)}" ] || {
        echo "Checkout did not resolve the requested source exactly: $repository" >&2
        return 1
    }
    printf '  Staged %s@%s (%s)\n' \
        "$repository" "$ref" "$(git -C "$destination" rev-parse HEAD)"
}

stage_runtime_overlay() {
    local source="$1" destination="$2" repository="$3"
    python3 - "$source" "$destination" "$repository" <<'PY'
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
repository = sys.argv[3]

if source.is_symlink() or not source.is_dir():
    raise SystemExit(f"{repository} has no safe image/runtime directory")

count = 0
total = 0
for entry in sorted(
    source.rglob("*"),
    key=lambda path: (len(path.relative_to(source).parts), path.as_posix()),
):
    relative = entry.relative_to(source)
    if entry.is_symlink():
        raise SystemExit(f"unsafe runtime symlink in {repository}: {relative}")
    info = entry.stat(follow_symlinks=False)
    if info.st_mode & 0o7000:
        raise SystemExit(f"unsafe runtime mode in {repository}: {relative}")
    target = destination / relative
    if stat.S_ISDIR(info.st_mode):
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, info.st_mode & 0o777)
        continue
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"unsafe runtime entry in {repository}: {relative}")
    count += 1
    total += info.st_size
    if count > 256 or total > 32 * 1024 * 1024:
        raise SystemExit(f"runtime overlay limits exceeded in {repository}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with entry.open("rb") as input_file, target.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
    os.chmod(target, info.st_mode & 0o777)

if count == 0:
    raise SystemExit(f"empty image/runtime directory in {repository}")
print(f"  [{repository}] staged {count} runtime file(s)")
PY
}

stage_release_asset \
    "$OPENCLAW_DETERMINISTIC_REPOSITORY" \
    "$OPENCLAW_DETERMINISTIC_TAG" \
    "$OPENCLAW_DETERMINISTIC_ASSET" \
    "$OPENCLAW_DETERMINISTIC_SHA256" \
    "$vendor_stage/openclaw-deterministic/openclaw-deterministic.tar.gz"

stage_release_asset \
    "$NOTE_REPOSITORY" \
    "$NOTE_RELEASE_TAG" \
    "$NOTE_RELEASE_ASSET" \
    "$NOTE_RELEASE_SHA256" \
    "$vendor_stage/note/$NOTE_RELEASE_ASSET"

ephemeral_checkout="$vendor_stage/.openclaw-ephemeral-checkout"
checkout_source "$OPENCLAW_EPHEMERAL_REPOSITORY" "$ephemeral_checkout" "${OPENCLAW_EPHEMERAL_COMMIT:-}"

ephemeral_files=(
    openclaw-ephemeral.py
    openclaw_ephemeral/__init__.py
    openclaw_ephemeral/cli.py
    openclaw_ephemeral/configuration.py
    openclaw_ephemeral/environment.py
    openclaw_ephemeral/filesystem.py
    openclaw_ephemeral/plugins.py
    openclaw_ephemeral/providers.py
    openclaw_ephemeral/scheduling.py
)
for relative in "${ephemeral_files[@]}"; do
    [ -f "$ephemeral_checkout/$relative" ] || {
        echo "Missing Ephemeral runtime file on main: $relative" >&2
        exit 1
    }
    install -D -m 0644 \
        "$ephemeral_checkout/$relative" \
        "$vendor_stage/openclaw-ephemeral/$relative"
done
install -D -m 0644 \
    "$ephemeral_checkout/container/runtime/yolo.sh" \
    "$vendor_stage/openclaw-ephemeral/runtime/yolo.sh"
stage_runtime_overlay \
    "$ephemeral_checkout/image/runtime" \
    "$vendor_stage/openclaw-ephemeral/image/runtime" \
    "$OPENCLAW_EPHEMERAL_REPOSITORY"
rm -rf -- "$ephemeral_checkout"

[ "$(find "$vendor_stage/openclaw-ephemeral" -type f \
    ! -path '*/image/runtime/*' | wc -l)" -eq 10 ] || {
    echo "OpenClaw Ephemeral payload must contain exactly ten code files" >&2
    exit 1
}

hermes_checkout="$vendor_stage/.hermes-ephemeral-checkout"
checkout_source "$HERMES_EPHEMERAL_REPOSITORY" "$hermes_checkout" "${HERMES_EPHEMERAL_COMMIT:-}"
stage_runtime_overlay \
    "$hermes_checkout/image/runtime" \
    "$vendor_stage/hermes-ephemeral/image/runtime" \
    "$HERMES_EPHEMERAL_REPOSITORY"
rm -rf -- "$hermes_checkout"

rm -rf -- "$BUILD/vendor"
mv -- "$vendor_stage" "$BUILD/vendor"
trap - EXIT
printf 'Build context ready: %s\n' "$CONTEXT"
