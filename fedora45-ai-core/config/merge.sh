#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

ROOT="$(pwd -P)"
MERGE_WILDCARDS=(
    'env|env_example|*env*example'
    'config|config.conf_example|*config.conf*example'
    'container|container_example|*container*example'
)

show_help() {
    cat <<EOF
Usage: merge.sh OUTPUT_BASE

Offline fallback for a cumulative Fedora example triple. OUTPUT_BASE names all
three outputs, for example fedora44-ai-base. All matching example files found
directly in ./ are merged, including an already existing output triple.

No repository list is read and no network request is made.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    show_help
    exit 0
fi
[ "$#" -eq 1 ] || {
    show_help >&2
    exit 2
}
OUTPUT_BASE="$1"
[[ "$OUTPUT_BASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    echo "Invalid output base: $OUTPUT_BASE" >&2
    exit 2
}

merge_kind() {
    local kind="$1" suffix="$2" pattern="$3" output source
    local -a sources=()

    shopt -s nullglob
    for source in "$ROOT"/$pattern; do
        [ -f "$source" ] && [ ! -L "$source" ] || continue
        sources+=("$source")
    done
    shopt -u nullglob

    [ "${#sources[@]}" -gt 0 ] || {
        echo "No $kind example files found in $ROOT" >&2
        exit 1
    }

    output="$ROOT/$OUTPUT_BASE.$suffix"
    python3 - "$output" "${sources[@]}" <<'PY'
from pathlib import Path
import re
import sys

assignment = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
commented_volume = re.compile(
    r"^[ \t]*#[ \t]*(?:export[ \t]+)?"
    r"([A-Za-z_][A-Za-z0-9_]*_VOLUMES)=(.*)$"
)
output = Path(sys.argv[1])
sources = [Path(value) for value in sys.argv[2:]]
seen: set[str] = set()
result: list[str] = []


def append_entry(pending: list[str], entry: str) -> None:
    normalized: list[str] = []
    for line in pending:
        if not line.strip() and (not normalized or not normalized[-1].strip()):
            continue
        normalized.append(line)
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    while result and not result[-1].strip():
        result.pop()
    if result:
        result.append("")
    result.extend(normalized)
    result.extend((entry, ""))


for path in sources:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError as error:
        raise SystemExit(f"Example file is not valid UTF-8: {path}") from error
    pending: list[str] = []
    for line in lines:
        match = commented_volume.match(line)
        if match:
            key, value = match.groups()
            if key not in seen:
                seen.add(key)
                append_entry(pending, f"# {key}={value}")
            pending.clear()
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            pending.append(line)
            continue
        match = assignment.match(line)
        if match:
            key, value = match.groups()
            if key not in seen:
                seen.add(key)
                append_entry(pending, f"{key}={value}")
            pending.clear()
            continue
        pending.clear()

payload = "\n".join(result) + "\n" if result else ""
output.write_text(payload, encoding="utf-8")
PY
    printf 'Merged %s: %d source(s) -> %s\n' \
        "$kind" "${#sources[@]}" "$output"
}

for rule in "${MERGE_WILDCARDS[@]}"; do
    IFS='|' read -r kind suffix pattern <<< "$rule"
    merge_kind "$kind" "$suffix" "$pattern"
done
