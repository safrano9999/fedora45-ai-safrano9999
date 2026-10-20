#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

CONTEXT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILD="$CONTEXT/build"

set -a
# shellcheck source=/dev/null
. "$CONTEXT/build.conf"
set +a

"$BUILD/resolve-build-inputs.sh" \
    "$CONTEXT/.resolved-build.env" \
    "$OPENCLAW_VERSION"

printf 'Core-pre build context ready: %s\n' "$CONTEXT"
