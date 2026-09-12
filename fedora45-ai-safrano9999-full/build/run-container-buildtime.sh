#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

if [ "$#" -ne 3 ]; then
    echo "Usage: run-container-buildtime.sh STAGE_DIR LAYER REPOSITORIES_DIR" >&2
    exit 2
fi

[ -d "$1" ] && [ ! -L "$1" ] || {
    echo "Missing or unsafe buildtime stage: $1" >&2
    exit 1
}
STAGE_DIR="$(cd -- "$1" && pwd -P)"
LAYER="$2"
[ -d "$3" ] && [ ! -L "$3" ] || {
    echo "Missing or unsafe installed repository root: $3" >&2
    exit 1
}
REPOSITORIES_DIR="$(cd -- "$3" && pwd -P)"
EXAMPLES_DIR="$STAGE_DIR/examples"
ARTIFACTS_ROOT="$STAGE_DIR/artifacts"

[[ "$LAYER" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
    echo "Invalid buildtime layer name: $LAYER" >&2
    exit 2
}
[ -d "$REPOSITORIES_DIR" ] && [ ! -L "$REPOSITORIES_DIR" ] || {
    echo "Missing or unsafe installed repository root: $REPOSITORIES_DIR" >&2
    exit 1
}
[ -d "$EXAMPLES_DIR" ] && [ ! -L "$EXAMPLES_DIR" ] || {
    echo "Missing or unsafe buildtime examples: $EXAMPLES_DIR" >&2
    exit 1
}
[ -d "$ARTIFACTS_ROOT" ] && [ ! -L "$ARTIFACTS_ROOT" ] || {
    echo "Missing or unsafe buildtime artifacts: $ARTIFACTS_ROOT" >&2
    exit 1
}

validate_support_tree() {
    local repository="$1" phase_root="$2" entry relative mode permissions

    while IFS= read -r -d '' entry; do
        relative="${entry#"$phase_root"/}"
        [[ "$relative" != "$entry" && "$relative" != *$'\t'* &&
           "$relative" != *$'\r'* && "$relative" != *$'\n'* &&
           "$relative" != *'\\'* && "/$relative/" != */../* &&
           "/$relative/" != */./* && "/$relative/" != */.git/* ]] || {
            echo "Unsafe container buildtime path in $repository: $relative" >&2
            return 1
        }
        if [ -L "$entry" ] || { [ ! -d "$entry" ] && [ ! -f "$entry" ]; }; then
            echo "Unsafe container buildtime entry in $repository: $relative" >&2
            return 1
        fi
        mode="$(stat -c '%a' -- "$entry")"
        [[ "$mode" =~ ^[0-7]+$ ]] || {
            echo "Invalid container buildtime mode in $repository: $relative" >&2
            return 1
        }
        permissions=$((8#$mode))
        (( (permissions & 07000) == 0 )) || {
            echo "Unsafe container buildtime permissions in $repository: $relative" >&2
            return 1
        }
    done < <(find -P "$phase_root" -mindepth 1 -print0 | sort -z)
}

validate_run() {
    local repository="$1" run="$2" first_line interpreter

    [ -f "$run" ] && [ ! -L "$run" ] || {
        echo "image/buildtime/container/run must be a real file in $repository" >&2
        return 1
    }
    [ -x "$run" ] || {
        echo "image/buildtime/container/run is not executable in $repository" >&2
        return 1
    }
    IFS= read -r first_line < "$run" || true
    [[ "$first_line" == '#!'* && "$first_line" != *$'\r'* ]] || {
        echo "image/buildtime/container/run has no executable shebang in $repository" >&2
        return 1
    }
    interpreter="${first_line#\#!}"
    interpreter="${interpreter#"${interpreter%%[![:space:]]*}"}"
    [[ "$interpreter" == /* ]] || {
        echo "image/buildtime/container/run has no absolute shebang in $repository" >&2
        return 1
    }
}

invalid="$(
    find -P "$REPOSITORIES_DIR" -mindepth 1 -maxdepth 1 \
        \( -type l -o \( ! -type d ! -type f \) \) -print -quit
)"
[ -z "$invalid" ] || {
    echo "Unsafe installed repository-root entry: $invalid" >&2
    exit 1
}

while IFS= read -r -d '' repository_dir; do
    repository="${repository_dir##*/}"
    [[ "$repository" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
        echo "Unsafe installed repository name: $repository" >&2
        exit 1
    }
    image_root="$repository_dir/image"
    if [ ! -e "$image_root" ] && [ ! -L "$image_root" ]; then
        continue
    fi
    [ -d "$image_root" ] && [ ! -L "$image_root" ] || {
        echo "Unsafe installed image directory: $image_root" >&2
        exit 1
    }
    buildtime_root="$image_root/buildtime"
    if [ ! -e "$buildtime_root" ] && [ ! -L "$buildtime_root" ]; then
        continue
    fi
    [ -d "$buildtime_root" ] && [ ! -L "$buildtime_root" ] || {
        echo "Unsafe installed buildtime directory: $buildtime_root" >&2
        exit 1
    }
    container_root="$buildtime_root/container"
    if [ ! -e "$container_root" ] && [ ! -L "$container_root" ]; then
        continue
    fi
    [ -d "$container_root" ] && [ ! -L "$container_root" ] || {
        echo "Unsafe installed container buildtime directory: $container_root" >&2
        exit 1
    }

    validate_support_tree "$repository" "$container_root"
    run="$container_root/run"
    if [ ! -e "$run" ] && [ ! -L "$run" ]; then
        continue
    fi
    validate_run "$repository" "$run"

    artifacts_dir="$ARTIFACTS_ROOT/$repository"
    if [ -e "$artifacts_dir" ] || [ -L "$artifacts_dir" ]; then
        [ -d "$artifacts_dir" ] && [ ! -L "$artifacts_dir" ] || {
            echo "Unsafe buildtime artifact directory: $artifacts_dir" >&2
            exit 1
        }
    else
        mkdir -- "$artifacts_dir"
    fi

    printf '  [%s] running image/buildtime/container/run\n' "$repository"
    (
        cd -- "$repository_dir"
        export FEDORA44_BUILDTIME_PHASE=container
        export FEDORA44_BUILDTIME_LAYER="$LAYER"
        export FEDORA44_BUILDTIME_REPOSITORY="$repository"
        export FEDORA44_BUILDTIME_REPOSITORY_DIR="$repository_dir"
        export FEDORA44_BUILDTIME_EXAMPLES_DIR="$EXAMPLES_DIR"
        export FEDORA44_BUILDTIME_ARTIFACTS_DIR="$artifacts_dir"
        "$run"
    )
done < <(
    find -P "$REPOSITORIES_DIR" -mindepth 1 -maxdepth 1 -type d -print0 |
        sort -z
)
