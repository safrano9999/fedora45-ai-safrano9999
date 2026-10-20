#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
LAYER="${ROOT##*/}"
[[ "$LAYER" =~ ^fedora([0-9]+)-ai- ]] || { echo "Invalid Fedora layer: $LAYER" >&2; exit 2; }
FEDORA_VERSION="${BASH_REMATCH[1]}"
BUILD_CONFIG="$ROOT/build.conf"
SOURCE_DIR="$ROOT/safrano9999"
IMAGE_RUNTIME_STAGE="$ROOT/.fedora${FEDORA_VERSION}-image-runtime"
BUILDTIME_STAGE="$ROOT/.fedora${FEDORA_VERSION}-image-buildtime"
RUNTIME_STAGE="$ROOT/.fedora${FEDORA_VERSION}-runtime"
REPOSITORY_LIST="$ROOT/.fedora${FEDORA_VERSION}-ai-repositories.list"
IMAGE_RUNTIME_TEMPORARY=""
BUILDTIME_TEMPORARY=""
RUNTIME_TEMPORARY=""

cleanup() {
    [ -z "$IMAGE_RUNTIME_TEMPORARY" ] || rm -rf -- "$IMAGE_RUNTIME_TEMPORARY"
    [ -z "$BUILDTIME_TEMPORARY" ] || rm -rf -- "$BUILDTIME_TEMPORARY"
    [ -z "$RUNTIME_TEMPORARY" ] || rm -rf -- "$RUNTIME_TEMPORARY"
}
trap cleanup EXIT

case "$LAYER" in
    fedora[0-9]*-ai-base)
        SOURCE_MANIFEST="$ROOT/.fedora${FEDORA_VERSION}-ai-base-source-tags.tsv"
        REQUIREMENTS="$ROOT/requirements.base.txt"
        READY_NAME=Base
        ;;
    fedora[0-9]*-ai-safrano9999)
        SOURCE_MANIFEST="$ROOT/.safrano9999-source-tags.tsv"
        REQUIREMENTS="$ROOT/requirements.safrano9999.txt"
        READY_NAME=Safrano
        ;;
    fedora[0-9]*-ai-safrano9999-full)
        SOURCE_MANIFEST="$ROOT/.safrano9999-full-source-tags.tsv"
        REQUIREMENTS="$ROOT/requirements.safrano9999-full.txt"
        READY_NAME="Safrano Full"
        ;;
    fedora[0-9]*-ai-kachelmann)
        SOURCE_MANIFEST="$ROOT/.kachelmann-source-tags.tsv"
        REQUIREMENTS="$ROOT/requirements.kachelmann.txt"
        READY_NAME=KACHELMANN
        ;;
    *)
        echo "Unsupported Fedora layer directory: $LAYER" >&2
        exit 2
        ;;
esac

[ -f "$BUILD_CONFIG" ] && [ ! -L "$BUILD_CONFIG" ] || {
    echo "Missing or unsafe build configuration: $BUILD_CONFIG" >&2
    exit 1
}

for command in git python3 sha256sum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing build preparation dependency: $command" >&2
        exit 1
    }
done
unset EXTENSIONS STANDALONE
set -a
# shellcheck source=/dev/null
. "$BUILD_CONFIG"
set +a
[ "${EXTENSIONS+x}" = x ] && [ "${STANDALONE+x}" = x ] || {
    echo "build.conf must define both EXTENSIONS and STANDALONE" >&2
    exit 2
}

trim() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

valid_repository_specification() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}

parse_csv() {
    local list_name="$1" value="$2" entry
    local -n output="$3"
    local -a entries=()

    [ -z "$value" ] && return 0
    [[ "$value" != ,* && "$value" != *, && "$value" != *,,* &&
       "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
        echo "Invalid $list_name CSV list" >&2
        return 2
    }
    IFS=',' read -r -a entries <<< "$value"
    for entry in "${entries[@]}"; do
        entry="$(trim "$entry")"
        [ -n "$entry" ] && valid_repository_specification "$entry" || {
            echo "Invalid repository specification in $list_name: $entry" >&2
            return 2
        }
        output+=("$entry")
    done
}

declare -a extension_repositories=() standalone_repositories=() repositories=()
parse_csv EXTENSIONS "$EXTENSIONS" extension_repositories
parse_csv STANDALONE "$STANDALONE" standalone_repositories
repositories=("${extension_repositories[@]}" "${standalone_repositories[@]}")
[ "${#repositories[@]}" -gt 0 ] || {
    echo "EXTENSIONS and STANDALONE cannot both be empty" >&2
    exit 2
}

repository_name() {
    printf '%s\n' "$1"
}

declare -A selected_repositories=()
for specification in "${repositories[@]}"; do
    repository="$(repository_name "$specification")"
    [ -z "${selected_repositories[$repository]+x}" ] || {
        echo "Repository occurs more than once in build.conf: $repository" >&2
        exit 2
    }
    selected_repositories["$repository"]=1
done

write_repository_list() {
    local temporary specification
    temporary="$(mktemp "$ROOT/.fedora${FEDORA_VERSION}-ai-repositories.list.XXXXXX")"
    for specification in "${repositories[@]}"; do
        repository_name "$specification"
    done > "$temporary"
    mv -f -- "$temporary" "$REPOSITORY_LIST"
}

prune_unselected_sources() {
    local path name

    [ ! -L "$SOURCE_DIR" ] || {
        echo "Source directory must not be a symlink: $SOURCE_DIR" >&2
        return 1
    }
    mkdir -p -- "$SOURCE_DIR"
    while IFS= read -r -d '' path; do
        name="${path##*/}"
        [ -n "${selected_repositories[$name]+x}" ] || {
            rm -rf -- "$path"
            printf '  [%s] pruned stale source\n' "$name"
        }
    done < <(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -print0)
}

validate_repository() {
    local repository path
    repository="$(repository_name "$1")"
    path="$SOURCE_DIR/$repository"
    [ -d "$path" ] && [ ! -L "$path" ] && [ -d "$path/.git" ] || {
        echo "Missing Containerfile Git source: $repository" >&2
        return 1
    }
    git -C "$path" rev-parse --verify HEAD >/dev/null
    [ -z "$(git -C "$path" status --porcelain=v1 --untracked-files=all)" ] || {
        echo "Dirty Containerfile Git source: $repository" >&2
        return 1
    }
}

validate_repository_roles() {
    local specification repository manifest

    for specification in "${extension_repositories[@]}"; do
        repository="$(repository_name "$specification")"
        manifest="$SOURCE_DIR/$repository/openclaw.plugin.json"
        [ -s "$manifest" ] && [ ! -L "$manifest" ] || {
            echo "EXTENSIONS repository has no safe OpenClaw manifest: $repository" >&2
            return 1
        }
    done
    for specification in "${standalone_repositories[@]}"; do
        repository="$(repository_name "$specification")"
        manifest="$SOURCE_DIR/$repository/openclaw.plugin.json"
        [ ! -e "$manifest" ] && [ ! -L "$manifest" ] || {
            echo "STANDALONE repository contains an OpenClaw manifest: $repository" >&2
            return 1
        }
    done
}

write_source_manifest() {
    local temporary specification repository path refs version
    local version_commit staged_commit

    temporary="$(mktemp "$ROOT/.source-tags.tsv.XXXXXX")"
    printf 'repository\tversion_tag\tversion_commit\tstaged_commit\n' > "$temporary"
    for specification in "${repositories[@]}"; do
        repository="$(repository_name "$specification")"
        path="$SOURCE_DIR/$repository"
        [ -d "$path/.git" ] || {
            echo "Source is not a Git checkout: $path" >&2
            return 1
        }
        refs="$(git -C "$path" show-ref --tags 2>/dev/null || true)"
        version="$(awk '
            $2 ~ /^refs\/tags\/20[0-9][0-9][.][0-9]+[.][0-9]+$/ {
                sub(/^refs\/tags\//, "", $2)
                print $2
            }
        ' <<< "$refs" | sort -V | tail -n 1)"
        version_commit="$(awk -v ref="refs/tags/$version" \
            '$2 == ref {print $1; exit}' <<< "$refs")"
        staged_commit="$(git -C "$path" rev-parse HEAD)"
        printf '%s\t%s\t%s\t%s\n' \
            "$repository" "${version:-untagged}" "${version_commit:--}" "$staged_commit" \
            >> "$temporary"
    done
    mv -f -- "$temporary" "$SOURCE_MANIFEST"
}

write_requirements() {
    local specification repository source
    local -a requirement_files=()

    for specification in "${repositories[@]}"; do
        repository="$(repository_name "$specification")"
        source="$SOURCE_DIR/$repository/requirements.txt"
        [ ! -f "$source" ] || requirement_files+=("$source")
    done
    if [ "${#requirement_files[@]}" -eq 0 ]; then
        : > "$REQUIREMENTS"
        return 0
    fi
    awk '
    {
        stripped = $0
        sub(/^[[:space:]]+/, "", stripped)
        if (stripped == "" || substr(stripped, 1, 1) == "#") { print; next }
        match(stripped, /^[A-Za-z0-9._-]+/)
        if (RSTART == 0) { print; next }
        key = tolower(substr(stripped, RSTART, RLENGTH))
        if (!(key in seen)) { seen[key] = 1; print }
    }
    ' "${requirement_files[@]}" > "$REQUIREMENTS"
}


stage_buildtime() {
    local suffix source target repository hook artifact_directory
    local source_commit source_state

    BUILDTIME_TEMPORARY="$(mktemp -d "$ROOT/.fedora${FEDORA_VERSION}-image-buildtime.XXXXXX")"
    mkdir -p -- \
        "$BUILDTIME_TEMPORARY/examples" \
        "$BUILDTIME_TEMPORARY/artifacts"

    for suffix in env_example config.conf_example container_example; do
        source="$ROOT/$LAYER.$suffix"
        target="$BUILDTIME_TEMPORARY/examples/${source##*/}"
        [ -f "$source" ] && [ ! -L "$source" ] || {
            echo "Missing or unsafe cumulative buildtime example: $source" >&2
            return 1
        }
        cp -- "$source" "$target"
    done

    python3 - "$SOURCE_DIR" "$BUILDTIME_TEMPORARY/host-hooks.tsv" \
        "${repositories[@]}" <<'PY'
from __future__ import annotations

import stat
import sys
from pathlib import Path

source_root = Path(sys.argv[1])
output = Path(sys.argv[2])
repositories = [specification.split("@", 1)[0] for specification in sys.argv[3:]]
unsafe_characters = {"\t", "\r", "\n", "\\"}
maximum_entries = 16384
entry_count = 0
host_hooks: list[tuple[str, Path]] = []


def validate_relative(path: Path, repository: str) -> None:
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
        or any(
            character in unsafe_characters
            for part in path.parts
            for character in part
        )
    ):
        raise ValueError(f"unsafe buildtime path in {repository}: {path}")


def validate_run(path: Path, repository: str, phase: str) -> None:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            f"image/buildtime/{phase}/run must be a real file in {repository}"
        )
    if not mode & 0o111:
        raise ValueError(
            f"image/buildtime/{phase}/run is not executable in {repository}"
        )
    with path.open("rb") as handle:
        shebang = handle.readline(256)
    if (
        not shebang.startswith(b"#!")
        or b"\x00" in shebang
        or b"\r" in shebang
        or not shebang[2:].lstrip().startswith(b"/")
    ):
        raise ValueError(
            f"image/buildtime/{phase}/run has no absolute executable shebang "
            f"in {repository}"
        )


try:
    for repository in repositories:
        image_root = source_root / repository / "image"
        if not image_root.exists() and not image_root.is_symlink():
            continue
        if image_root.is_symlink() or not image_root.is_dir():
            raise ValueError(
                f"image must be a real directory in {repository}: {image_root}"
            )
        buildtime = image_root / "buildtime"
        if not buildtime.exists() and not buildtime.is_symlink():
            continue
        if buildtime.is_symlink() or not buildtime.is_dir():
            raise ValueError(
                f"image/buildtime must be a real directory in {repository}"
            )
        for child in sorted(buildtime.iterdir()):
            if child.name not in {"host", "container"}:
                raise ValueError(
                    f"unsupported image/buildtime entry in {repository}: "
                    f"{child.name}"
                )
            if child.is_symlink() or not child.is_dir():
                raise ValueError(
                    f"image/buildtime/{child.name} must be a real directory "
                    f"in {repository}"
                )
        for phase in ("host", "container"):
            phase_root = buildtime / phase
            if not phase_root.exists() and not phase_root.is_symlink():
                continue
            if phase_root.is_symlink() or not phase_root.is_dir():
                raise ValueError(
                    f"image/buildtime/{phase} must be a real directory "
                    f"in {repository}"
                )
            for entry in sorted(phase_root.rglob("*")):
                relative = entry.relative_to(phase_root)
                validate_relative(relative, repository)
                metadata = entry.lstat()
                mode = stat.S_IMODE(metadata.st_mode)
                if mode & 0o7000:
                    raise ValueError(
                        f"unsafe buildtime permissions in {repository}: "
                        f"{phase}/{relative}"
                    )
                if stat.S_ISLNK(metadata.st_mode) or not (
                    stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISREG(metadata.st_mode)
                ):
                    raise ValueError(
                        f"unsafe buildtime entry in {repository}: "
                        f"{phase}/{relative}"
                    )
                entry_count += 1
                if entry_count > maximum_entries:
                    raise ValueError("buildtime metadata contains too many entries")
            run = phase_root / "run"
            if not run.exists() and not run.is_symlink():
                continue
            validate_run(run, repository, phase)
            if phase == "host":
                host_hooks.append((repository, run))

    with output.open("w", encoding="utf-8") as handle:
        for repository, hook in host_hooks:
            handle.write(f"{repository}\t{hook}\n")
except (OSError, ValueError) as error:
    raise SystemExit(f"Invalid image/buildtime contract: {error}") from error
PY

    while IFS=$'\t' read -r repository hook; do
        [ -n "$repository" ] || continue
        artifact_directory="$BUILDTIME_TEMPORARY/artifacts/$repository"
        mkdir -p -- "$artifact_directory"
        printf '  [%s] running image/buildtime/host/run\n' "$repository"
        source_commit="$(git -C "$SOURCE_DIR/$repository" rev-parse HEAD)"
        (
            cd -- "$SOURCE_DIR/$repository"
            export FEDORA_BUILDTIME_PHASE=host
            export FEDORA_BUILDTIME_LAYER="$LAYER"
            export FEDORA_BUILDTIME_REPOSITORY="$repository"
            export FEDORA_BUILDTIME_REPOSITORY_DIR="$SOURCE_DIR/$repository"
            export FEDORA_BUILDTIME_EXAMPLES_DIR="$BUILDTIME_TEMPORARY/examples"
            export FEDORA_BUILDTIME_ARTIFACTS_DIR="$artifact_directory"
            for field in PHASE LAYER REPOSITORY REPOSITORY_DIR EXAMPLES_DIR ARTIFACTS_DIR; do
                variable="FEDORA_BUILDTIME_$field"
                export "FEDORA${FEDORA_VERSION}_BUILDTIME_$field=${!variable}"
            done
            "$hook"
        )
        [ "$(git -C "$SOURCE_DIR/$repository" rev-parse HEAD)" = "$source_commit" ] || {
            echo "Host buildtime hook changed repository HEAD: $repository" >&2
            return 1
        }
        source_state="$(
            git -C "$SOURCE_DIR/$repository" status \
                --porcelain=v1 --untracked-files=all --ignored=matching
        )"
        [ -z "$source_state" ] || {
            echo "Host buildtime hook wrote outside its artifact directory: $repository" >&2
            return 1
        }
    done < "$BUILDTIME_TEMPORARY/host-hooks.tsv"

    python3 - "$BUILDTIME_TEMPORARY" "$LAYER" \
        "${repositories[@]}" <<'PY'
from __future__ import annotations

import hashlib
import stat
import sys
from pathlib import Path

stage = Path(sys.argv[1])
layer = sys.argv[2]
repositories = [specification.split("@", 1)[0] for specification in sys.argv[3:]]
repository_set = set(repositories)
examples = stage / "examples"
artifacts = stage / "artifacts"
manifest = stage / "manifest.tsv"
unsafe_characters = {"\t", "\r", "\n", "\\"}
maximum_entries = 65536
maximum_size = 2 * 1024 * 1024 * 1024
entry_count = 0
total_size = 0
rows: list[tuple[str, str, str, str, str]] = []


def validate_relative(path: Path, context: str) -> None:
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
        or any(
            character in unsafe_characters
            for part in path.parts
            for character in part
        )
    ):
        raise ValueError(f"unsafe {context} path: {path}")


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def add_file(kind: str, repository: str, path: Path, relative: Path) -> None:
    global entry_count, total_size
    validate_relative(relative, kind)
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o7000:
        raise ValueError(f"unsafe {kind} permissions: {relative}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"unsafe {kind} file: {relative}")
    entry_count += 1
    total_size += metadata.st_size
    if entry_count > maximum_entries:
        raise ValueError("buildtime stage contains too many files")
    if total_size > maximum_size:
        raise ValueError("buildtime stage exceeds the size limit")
    rows.append((kind, repository, relative.as_posix(), f"{mode:04o}", digest(path)))


try:
    if examples.is_symlink() or not examples.is_dir():
        raise ValueError("examples is not a real directory")
    expected_examples = {
        f"{layer}.env_example",
        f"{layer}.config.conf_example",
        f"{layer}.container_example",
    }
    actual_examples = {entry.name for entry in examples.iterdir()}
    if actual_examples != expected_examples:
        raise ValueError(
            "cumulative example set differs from the exact layer triple: "
            f"expected={sorted(expected_examples)}, actual={sorted(actual_examples)}"
        )
    for name in sorted(expected_examples):
        add_file("example", "-", examples / name, Path("examples") / name)

    if artifacts.is_symlink() or not artifacts.is_dir():
        raise ValueError("artifacts is not a real directory")
    for repository_root in sorted(artifacts.iterdir()):
        repository = repository_root.name
        if repository not in repository_set:
            raise ValueError(f"artifact directory has no selected owner: {repository}")
        if repository_root.is_symlink() or not repository_root.is_dir():
            raise ValueError(
                f"artifact owner root must be a real directory: {repository}"
            )
        for entry in sorted(repository_root.rglob("*")):
            relative = entry.relative_to(repository_root)
            validate_relative(relative, f"artifact/{repository}")
            metadata = entry.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if mode & 0o7000:
                raise ValueError(
                    f"unsafe artifact permissions in {repository}: {relative}"
                )
            if stat.S_ISLNK(metadata.st_mode) or not (
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
            ):
                raise ValueError(
                    f"unsafe artifact entry in {repository}: {relative}"
                )
            if stat.S_ISREG(metadata.st_mode):
                add_file(
                    "artifact",
                    repository,
                    entry,
                    Path("artifacts") / repository / relative,
                )

    with manifest.open("w", encoding="utf-8") as handle:
        handle.write("kind\trepository\tpath\tmode\tsha256\n")
        for row in rows:
            handle.write("\t".join(row) + "\n")
except (OSError, ValueError) as error:
    raise SystemExit(f"Invalid buildtime stage: {error}") from error
PY

    rm -f -- "$BUILDTIME_TEMPORARY/host-hooks.tsv"
    rm -rf -- "$BUILDTIME_STAGE"
    mv -- "$BUILDTIME_TEMPORARY" "$BUILDTIME_STAGE"
    BUILDTIME_TEMPORARY=""
}

stage_image_runtime() {
    IMAGE_RUNTIME_TEMPORARY="$(mktemp -d "$ROOT/.fedora${FEDORA_VERSION}-image-runtime.XXXXXX")"
    mkdir -p -- "$IMAGE_RUNTIME_TEMPORARY/rootfs"

    python3 - "$SOURCE_DIR" "$IMAGE_RUNTIME_TEMPORARY/rootfs" \
        "$IMAGE_RUNTIME_TEMPORARY/manifest.tsv" "${repositories[@]}" <<'PY'
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
from pathlib import Path

source_root = Path(sys.argv[1])
destination = Path(sys.argv[2])
manifest = Path(sys.argv[3])
repositories = [specification.split("@", 1)[0] for specification in sys.argv[4:]]
maximum_entries = 65536
maximum_size = 2 * 1024 * 1024 * 1024
unsafe_characters = {"\t", "\r", "\n", "\\"}
declared_directories: dict[Path, tuple[str, int]] = {}
installed_files: dict[Path, str] = {}
manifest_rows: list[tuple[str, str, str, str]] = []
entry_count = 0
total_size = 0


def validate_relative(path: Path, repository: str) -> None:
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
        or any(
            character in unsafe_characters
            for part in path.parts
            for character in part
        )
    ):
        raise ValueError(f"unsafe runtime path in {repository}: {path}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


try:
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o755)
    for repository in repositories:
        repository_root = source_root / repository
        runtime = repository_root / "image" / "runtime"
        if not runtime.exists() and not runtime.is_symlink():
            print(f"  [{repository}] no image/runtime overlay; skipped")
            continue
        if runtime.is_symlink() or not runtime.is_dir():
            raise ValueError(
                f"image/runtime must be a real directory in {repository}: {runtime}"
            )

        entries = sorted(
            runtime.rglob("*"),
            key=lambda entry: (
                len(entry.relative_to(runtime).parts),
                entry.relative_to(runtime).as_posix(),
            ),
        )
        repository_files = 0
        for entry in entries:
            relative = entry.relative_to(runtime)
            validate_relative(relative, repository)
            metadata = entry.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if mode & 0o7000:
                raise ValueError(
                    f"unsafe runtime permissions in {repository}: {relative}"
                )
            if stat.S_ISLNK(metadata.st_mode) or not (
                stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
            ):
                raise ValueError(f"unsafe runtime entry in {repository}: {relative}")
            entry_count += 1
            if entry_count > maximum_entries:
                raise ValueError("runtime overlays contain too many entries")

            target = destination / relative
            installed_path = Path("/") / relative
            if stat.S_ISDIR(metadata.st_mode):
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    owner = installed_files.get(relative, "another repository")
                    raise ValueError(
                        f"runtime type collision at {installed_path}: "
                        f"{repository} conflicts with {owner}"
                    )
                previous = declared_directories.get(relative)
                if previous is not None and previous[1] != mode:
                    raise ValueError(
                        f"runtime directory mode collision at {installed_path}: "
                        f"{repository}={mode:04o}, {previous[0]}={previous[1]:04o}"
                    )
                if not target.exists():
                    target.mkdir(parents=True)
                    os.chmod(target, mode)
                declared_directories.setdefault(relative, (repository, mode))
                continue

            total_size += metadata.st_size
            if total_size > maximum_size:
                raise ValueError("runtime overlays exceed the size limit")
            if target.exists() or target.is_symlink():
                owner = installed_files.get(relative)
                if owner is None:
                    owner = declared_directories.get(
                        relative, ("another repository", 0)
                    )[0]
                raise ValueError(
                    f"runtime file collision at {installed_path}: "
                    f"{repository} conflicts with {owner}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with entry.open("rb") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            os.chmod(target, mode)
            digest = sha256(target)
            installed_files[relative] = repository
            manifest_rows.append(
                (repository, installed_path.as_posix(), f"{mode:04o}", digest)
            )
            repository_files += 1
        print(f"  [{repository}] staged {repository_files} image/runtime files")

    with manifest.open("w", encoding="utf-8") as handle:
        handle.write("repository\tinstalled_path\tmode\tsha256\n")
        for row in manifest_rows:
            handle.write("\t".join(row) + "\n")
except (OSError, ValueError) as error:
    raise SystemExit(f"Invalid owner image metadata: {error}") from error
PY

    write_enable_list \
        "$IMAGE_RUNTIME_TEMPORARY/rootfs" \
        "$IMAGE_RUNTIME_TEMPORARY/systemd-enable.list"
    rm -rf -- "$IMAGE_RUNTIME_STAGE"
    mv -- "$IMAGE_RUNTIME_TEMPORARY" "$IMAGE_RUNTIME_STAGE"
    IMAGE_RUNTIME_TEMPORARY=""
}

validate_nextcloud_runtime_archive() {
    python3 - "$1" <<'PY'
import stat
import sys
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

archive_path = sys.argv[1]
required = "runtime/opt/nextcloudcmd/bin/nextcloudcmd"
maximum_entries = 32768
maximum_size = 2 * 1024 * 1024 * 1024

try:
    with ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if not entries:
            raise ValueError("archive is empty")
        if len(entries) > maximum_entries:
            raise ValueError("archive has too many entries")
        seen: set[str] = set()
        total = 0
        runtime_binary = None
        for entry in entries:
            path = PurePosixPath(entry.filename)
            parts = path.parts
            if (
                not entry.filename
                or "\\" in entry.filename
                or path.is_absolute()
                or not parts
                or any(part in {"", ".", ".."} for part in parts)
                or ".git" in parts
            ):
                raise ValueError(f"unsafe archive path: {entry.filename!r}")
            normalized = "/".join(parts)
            if normalized in seen:
                raise ValueError(f"duplicate archive path: {entry.filename!r}")
            seen.add(normalized)
            mode = entry.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError(f"unsupported archive entry: {entry.filename!r}")
            if mode & 0o7000:
                raise ValueError(f"unsafe archive permissions: {entry.filename!r}")
            if entry.flag_bits & 0x1:
                raise ValueError(f"encrypted archive entry: {entry.filename!r}")
            total += entry.file_size
            if total > maximum_size:
                raise ValueError("archive exceeds extraction limit")
            if normalized == required:
                runtime_binary = entry
        if runtime_binary is None or runtime_binary.is_dir():
            raise ValueError(f"required runtime binary is missing: {required}")
        if not (runtime_binary.external_attr >> 16) & 0o111:
            raise ValueError(f"required runtime binary is not executable: {required}")
except (BadZipFile, OSError, ValueError) as error:
    raise SystemExit(f"Invalid NEXTCLOUD runtime archive: {error}") from error
PY
}

stage_runtime_assets() {
    local repository=NEXTCLOUD
    local asset=nextcloud-fedora64-plugin-latest.zip
    local expected actual
    mkdir -p "$RUNTIME_STAGE"
    printf 'repository\tasset\tsha256\tstatus\n' > "$RUNTIME_STAGE/manifest.tsv"
    [ -n "${selected_repositories[$repository]+x}" ] || return 0
    expected="$(read_asset_checksum "$RUNTIME_STAGE/$asset.sha256" "$asset")"
    actual="$(sha256sum "$RUNTIME_STAGE/$asset" | cut -d' ' -f1)"
    [ "$actual" = "$expected" ] || {
        echo "NEXTCLOUD runtime checksum mismatch" >&2
        return 1
    }
    validate_nextcloud_runtime_archive "$RUNTIME_STAGE/$asset"
    printf '%s  %s\n' "$expected" "$asset" > "$RUNTIME_STAGE/$asset.sha256"
    printf '%s\t%s\t%s\tcomplete\n' "$repository" "$asset" "$expected" >> "$RUNTIME_STAGE/manifest.tsv"
}

write_enable_list() {
    python3 - "$1" "$2" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]) / "etc/systemd/system"
output = Path(sys.argv[2])
unit_name = re.compile(
    r"^[A-Za-z0-9@_.:+-]+\.(?:automount|device|mount|path|scope|service|slice|socket|swap|target|timer)$"
)
enabled: list[str] = []
if root.is_dir():
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.is_symlink() or not unit_name.fullmatch(path.name):
            continue
        in_install = False
        directives: dict[str, list[str]] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(('#', ';')):
                continue
            if line.startswith('[') and line.endswith(']'):
                in_install = line == '[Install]'
                continue
            if not in_install or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key not in {'WantedBy', 'RequiredBy', 'UpheldBy', 'Alias', 'Also'}:
                continue
            if not value.strip():
                directives[key] = []
            else:
                directives.setdefault(key, []).extend(value.split())
        if any(directives.values()):
            enabled.append(path.name)
output.write_text("".join(f"{name}\n" for name in enabled), encoding="utf-8")
PY
}

read_asset_checksum() {
    python3 - "$1" "$2" <<'PY'
import re
import sys
from pathlib import Path

checksum_path = Path(sys.argv[1])
asset = sys.argv[2]
try:
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
except (OSError, UnicodeError) as error:
    raise SystemExit(f"Invalid checksum file {checksum_path}: {error}") from error
if len(lines) != 1:
    raise SystemExit(f"Invalid checksum file {checksum_path}: expected one line")
match = re.fullmatch(r"([0-9A-Fa-f]{64})(?:[ \t]+\*?([^\s]+))?", lines[0])
if match is None or (match.group(2) is not None and match.group(2) != asset):
    raise SystemExit(f"Invalid checksum file {checksum_path}")
print(match.group(1).lower())
PY
}


write_repository_list
prune_unselected_sources
for specification in "${repositories[@]}"; do
    validate_repository "$specification"
done
validate_repository_roles
write_source_manifest
write_requirements
stage_buildtime
stage_runtime_assets
stage_image_runtime

printf '%s build context ready: %s\n' "$READY_NAME" "$ROOT"
