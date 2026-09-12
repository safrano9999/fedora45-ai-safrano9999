#!/usr/bin/env bash
# Source of truth: SCRIPTS/githubactions. Generated copies are overwritten.
set -euo pipefail

image="${1:?Usage: resolve-fedora-image-tag.sh IMAGE [YY.MM.N]}"
version="${2:-}"
[[ "$image" =~ ^ghcr.io/([^/]+)/(fedora45-ai-(core-pre|core|base|kachelmann|safrano9999(-full)?))$ ]] || {
    echo "Unsupported Fedora image: $image" >&2; exit 1;
}
owner="${BASH_REMATCH[1]}"
package="${BASH_REMATCH[2]}"
month="$(date -u +%y.%m)"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
touch "$temporary/all" "$temporary/current"

# A shared monthly sequence leaves the chosen version available to every
# downstream layer. Workflow concurrency serializes publications per image.
for layer in core-pre core base kachelmann safrano9999 safrano9999-full; do
    candidate="fedora45-ai-$layer"
    if ! gh api --paginate "users/$owner/packages/container/$candidate/versions?per_page=100" \
        --jq '.[].metadata.container.tags[]' > "$temporary/tags" 2> "$temporary/error"; then
        if grep -Fq '(HTTP 404)' "$temporary/error"; then
            : > "$temporary/tags"
        else
            cat "$temporary/error" >&2; exit 1
        fi
    fi
    cat "$temporary/tags" >> "$temporary/all"
    [[ "$candidate" != "$package" ]] || cp "$temporary/tags" "$temporary/current"
done

if [[ -z "$version" ]]; then
    next="$(jq -Rsr --arg prefix "$month." \
        '[split("\n")[] | select(startswith($prefix)) | ltrimstr($prefix) | select(test("^[1-9][0-9]*$")) | tonumber] | ((max // 0) + 1)' \
        "$temporary/all")"
    version="$month.$next"
fi
[[ "$version" =~ ^[0-9]{2}\.(0[1-9]|1[0-2])\.[1-9][0-9]*$ ]] || {
    echo "Image version must use YY.MM.N: $version" >&2; exit 1;
}
if grep -Fxq "$version" "$temporary/current"; then
    echo "Refusing to overwrite existing image version: $image:$version" >&2; exit 1
fi
printf '%s\n' "$version"
