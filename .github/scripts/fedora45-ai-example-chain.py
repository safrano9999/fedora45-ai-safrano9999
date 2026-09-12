#!/usr/bin/env python3
# Source of truth: SCRIPTS/githubactions. Generated copies are overwritten.
"""Build the deterministic Fedora example-file chain.

The ``merge`` command is deliberately offline.  It only reads the explicitly
provided source directories and is also the merge kernel used by ``chain``.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


KINDS: tuple[tuple[str, str], ...] = (
    ("env", "env.example"),
    ("config", "config.conf_example"),
    ("container", "container.example"),
)
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
ASSIGNMENT = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
COMMENTED_VOLUME = re.compile(
    r"^[ \t]*#[ \t]*(?:export[ \t]+)?"
    r"([A-Za-z_][A-Za-z0-9_]*_VOLUMES)=(.*)$"
)

LAYERS: tuple[tuple[str, str | None], ...] = (
    ("fedora45-ai-core", None),
    ("fedora45-ai-base", "fedora45-ai-core"),
    ("fedora45-ai-kachelmann", "fedora45-ai-base"),
    ("fedora45-ai-safrano9999", "fedora45-ai-kachelmann"),
    ("fedora45-ai-safrano9999-full", "fedora45-ai-safrano9999"),
)


class ExampleChainError(RuntimeError):
    """A safe, user-facing example-chain failure."""


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, check=True, env=env)


def output_filename(layer: str, kind: str) -> str:
    suffix = {
        "env": "env_example",
        "config": "config.conf_example",
        "container": "container_example",
    }[kind]
    return f"{layer}.{suffix}"


def additional_filename(layer: str, kind: str) -> str:
    suffix = {
        "env": "env_example",
        "config": "config.conf_example",
        "container": "container_example",
    }[kind]
    return f"{layer}-additional.{suffix}"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise ExampleChainError(f"Example file is not valid UTF-8: {path}") from error


def merge_keyed(paths: list[Path]) -> bytes:
    """Merge config-style files deterministically with first-key-wins semantics."""

    seen: set[str] = set()
    result: list[str] = []

    def append_entry(pending: list[str], entry: str) -> None:
        normalized: list[str] = []
        for pending_line in pending:
            if not pending_line.strip() and (not normalized or not normalized[-1].strip()):
                continue
            normalized.append(pending_line)
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

    for path in paths:
        pending: list[str] = []
        for line in read_text(path).splitlines():
            commented_volume = COMMENTED_VOLUME.match(line)
            if commented_volume:
                key, value = commented_volume.groups()
                if key not in seen:
                    seen.add(key)
                    append_entry(pending, f"# {key}={value}")
                pending.clear()
                continue

            if not line.strip() or line.lstrip().startswith("#"):
                pending.append(line)
                continue

            assignment = ASSIGNMENT.match(line)
            if assignment:
                key, value = assignment.groups()
                if key not in seen:
                    seen.add(key)
                    append_entry(pending, f"{key}={value}")
                pending.clear()
                continue

            pending.clear()

    if not result:
        return b""
    return ("\n".join(result) + "\n").encode("utf-8")


def merge_sources(
    source_directories: list[Path], output_directory: Path, output_prefix: str
) -> list[Path]:
    """Merge generic example triples from ordered, already-local directories."""

    if not source_directories:
        raise ExampleChainError("At least one local source directory is required")
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    for kind, generic_name in KINDS:
        sources = [
            directory / generic_name
            for directory in source_directories
            if (directory / generic_name).is_file()
            and not (directory / generic_name).is_symlink()
        ]
        output = output_directory / output_filename(output_prefix, kind)
        output.write_bytes(merge_keyed(sources))
        outputs.append(output)
        print(
            f"Merged {output_prefix} {kind}: {len(sources)} source(s) -> {output}"
        )

    return outputs


def parse_repositories(path: Path) -> list[str]:
    if not path.is_file() or path.is_symlink():
        raise ExampleChainError(f"Missing repository list: {path}")

    repositories: list[str] = []
    seen: set[str] = set()
    for number, raw_line in enumerate(read_text(path).splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if not REPOSITORY_NAME.fullmatch(value) or value in {".", ".."}:
            raise ExampleChainError(
                f"Invalid repository name in {path}:{number}: {value!r}"
            )
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        repositories.append(value)
    return repositories


def safe_extract(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[PurePosixPath] = set()

    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise ExampleChainError(f"Invalid ZIP archive: {archive_path}") from error

    with archive:
        for info in archive.infolist():
            if info.flag_bits & 0x1:
                raise ExampleChainError(
                    f"Encrypted ZIP member is not allowed: {archive_path}:{info.filename}"
                )
            if "\\" in info.filename:
                raise ExampleChainError(
                    f"Unsafe ZIP member: {archive_path}:{info.filename}"
                )
            relative = PurePosixPath(info.filename)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ExampleChainError(
                    f"Unsafe ZIP member: {archive_path}:{info.filename}"
                )
            if relative in seen:
                raise ExampleChainError(
                    f"Duplicate ZIP member: {archive_path}:{info.filename}"
                )
            seen.add(relative)

            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise ExampleChainError(
                    f"ZIP symlink is not allowed: {archive_path}:{info.filename}"
                )

            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def stage_named_triple(
    destination: Path,
    source_directory: Path,
    prefix: str,
    *,
    additional: bool,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for kind, generic_name in KINDS:
        name = (
            additional_filename(prefix, kind)
            if additional
            else output_filename(prefix, kind)
        )
        source = source_directory / name
        if not source.is_file() or source.is_symlink():
            raise ExampleChainError(f"Missing required example source: {source}")
        shutil.copyfile(source, destination / generic_name)


def download_repository(
    owner: str, repository: str, downloads: Path, extracted: Path
) -> Path:
    asset_name = f"{repository}-examplefiles.zip"
    repository_downloads = downloads / repository
    repository_downloads.mkdir(parents=True, exist_ok=False)
    print(f"Downloading {owner}/{repository} release latest asset {asset_name}")
    try:
        run(
            [
                "gh",
                "release",
                "download",
                "latest",
                "--repo",
                f"{owner}/{repository}",
                "--pattern",
                asset_name,
                "--dir",
                str(repository_downloads),
            ]
        )
    except subprocess.CalledProcessError as error:
        raise ExampleChainError(
            f"Cannot download latest asset {owner}/{repository}/{asset_name}"
        ) from error

    archive_path = repository_downloads / asset_name
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ExampleChainError(
            f"Latest release asset was not downloaded: "
            f"{owner}/{repository}/{asset_name}"
        )

    repository_extract = extracted / repository
    safe_extract(archive_path, repository_extract)
    if not any((repository_extract / name).is_file() for _, name in KINDS):
        raise ExampleChainError(
            f"Latest asset has no root example files: "
            f"{owner}/{repository}/{asset_name}"
        )
    return repository_extract


def atomic_install(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ExampleChainError(f"Generated output target is not a regular file: {target}")
    if target.is_file() and target.read_bytes() == source.read_bytes():
        return
    temporary = target.with_name(f".{target.name}.example-chain-{os.getpid()}")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def build_chain(repository_root: Path, owner: str) -> None:
    repository_root = repository_root.resolve()
    if not (repository_root / ".git").exists():
        raise ExampleChainError(f"Not a repository root: {repository_root}")
    if not REPOSITORY_NAME.fullmatch(owner):
        raise ExampleChainError(f"Invalid GitHub repository owner: {owner!r}")

    def layer_path(layer: str) -> Path:
        return repository_root / layer

    layers = LAYERS
    layer_repositories: dict[str, list[str]] = {}
    unique_repositories: list[str] = []
    repository_spellings: dict[str, str] = {}
    for layer, _ in layers:
        layer_directory = layer_path(layer)
        if not layer_directory.is_dir() or layer_directory.is_symlink():
            raise ExampleChainError(f"Missing Fedora layer directory: {layer_directory}")
        repositories = parse_repositories(
            layer_directory / f"{layer}-additional.repos"
        )
        layer_repositories[layer] = repositories
        for repository in repositories:
            folded = repository.casefold()
            existing = repository_spellings.get(folded)
            if existing is not None and existing != repository:
                raise ExampleChainError(
                    "Repository names differ only by case: "
                    f"{existing!r} and {repository!r}"
                )
            if existing is None:
                repository_spellings[folded] = repository
                unique_repositories.append(repository)

    runner_temp = os.environ.get("RUNNER_TEMP")
    temporary_parent = Path(runner_temp) if runner_temp else None
    with tempfile.TemporaryDirectory(
        prefix="fedora45-ai-example-chain-", dir=temporary_parent
    ) as temporary_name:
        workspace = Path(temporary_name)
        downloads = workspace / "downloads"
        extracted = workspace / "extracted"
        generated = workspace / "generated"
        staged = workspace / "staged"
        downloads.mkdir()
        extracted.mkdir()
        generated.mkdir()
        staged.mkdir()

        repository_sources = {
            repository: download_repository(
                owner, repository, downloads, extracted
            )
            for repository in unique_repositories
        }

        generated_directories: dict[str, Path] = {}
        for layer, parent in layers:
            layer_directory = layer_path(layer)
            source_directories: list[Path] = []

            additional_stage = staged / layer / "00-additional"
            stage_named_triple(
                additional_stage,
                layer_directory,
                layer,
                additional=True,
            )
            source_directories.append(additional_stage)

            if parent is not None:
                parent_stage = staged / layer / "01-parent"
                stage_named_triple(
                    parent_stage,
                    generated_directories[parent],
                    parent,
                    additional=False,
                )
                source_directories.append(parent_stage)

            source_directories.extend(
                repository_sources[repository]
                for repository in layer_repositories[layer]
            )
            layer_generated = generated / layer
            merge_sources(source_directories, layer_generated, layer)
            generated_directories[layer] = layer_generated

        for layer, _ in layers:
            for kind, _ in KINDS:
                filename = output_filename(layer, kind)
                atomic_install(
                    generated_directories[layer] / filename,
                    layer_path(layer) / filename,
                )


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    subparsers = argument_parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser(
        "merge", help="Offline merge of explicitly supplied local source directories"
    )
    merge_parser.add_argument("--output-dir", type=Path, required=True)
    merge_parser.add_argument("--output-prefix", required=True)
    merge_parser.add_argument(
        "--source-dir", type=Path, action="append", required=True
    )

    chain_parser = subparsers.add_parser(
        "chain", help="Download latest assets and generate the complete Fedora chain"
    )
    chain_parser.add_argument("--repository-root", type=Path, required=True)
    chain_parser.add_argument("--owner", required=True)
    return argument_parser


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "merge":
        sources = [path.resolve() for path in arguments.source_dir]
        for source in sources:
            if not source.is_dir() or source.is_symlink():
                raise ExampleChainError(f"Invalid local source directory: {source}")
        output_directory = arguments.output_dir.resolve()
        with tempfile.TemporaryDirectory(
            prefix="fedora45-ai-example-merge-"
        ) as temporary_name:
            generated = merge_sources(
                sources,
                Path(temporary_name),
                arguments.output_prefix,
            )
            for source in generated:
                atomic_install(source, output_directory / source.name)
        return
    build_chain(arguments.repository_root, arguments.owner)


if __name__ == "__main__":
    try:
        main()
    except (
        ExampleChainError,
        KeyboardInterrupt,
        OSError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as error:
        print(f"fedora45-ai-example-chain: {error or 'Interrupted'}", file=sys.stderr)
        raise SystemExit(1) from None
