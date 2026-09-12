#!/usr/bin/env python3
"""Select and link one Fedora container instance from local cumulative examples."""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from pathlib import Path


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class SetupError(RuntimeError):
    """A safe, user-facing setup error."""


def log(message: str) -> None:
    print(message, file=sys.stderr)


def ask(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def read_nr(path: Path) -> str:
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line.startswith("CONTAINER_NR="):
                return line.split("=", 1)[1].strip()
    return ""


def port_mode(path: Path) -> str | int | None:
    value = read_nr(path)
    if value.lower() in {"", "blank", "manual"}:
        return None
    if value.upper() == "TUN":
        return "TUN"
    if value.isdigit() and 2 <= int(value) <= 5:
        return int(value)
    raise SetupError(f"Invalid CONTAINER_NR={value!r} in {path}")


def write_nr(path: Path, value: str | int | None) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replacement = f"CONTAINER_NR={value or ''}"
    output: list[str] = []
    replaced = False
    for line in lines:
        if line.split("=", 1)[0].strip() == "CONTAINER_NR":
            if not replaced:
                output.append(replacement)
                replaced = True
        else:
            output.append(line)
    if not replaced:
        if output and output[-1]:
            output.append("")
        output.append(replacement)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.nr-{uuid.uuid4().hex}")
    try:
        temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def mode_label(value: str | int | None) -> str:
    if isinstance(value, int):
        return f"Portrange {value * 10000} - {(value + 1) * 10000 - 1}"
    return value or "manual"


def choose_instance(instances: Path, requested: str, default_name: str) -> tuple[str, bool]:
    names = sorted(
        path.name
        for path in instances.iterdir()
        if path.is_dir() and not path.is_symlink()
    )
    if requested:
        name = requested
        is_new = name not in names
    elif not sys.stdin.isatty():
        if names:
            name, is_new = names[0], False
        elif default_name:
            name, is_new = default_name, True
        else:
            raise SetupError("No container exists; pass INSTANCE")
    elif not names:
        prompt = "  New container name"
        if default_name:
            prompt += f" [{default_name}]"
        name = ask(f"{prompt}: ") or default_name
        is_new = True
    else:
        new_index = len(names) + 1
        log("\n  Container:")
        for index, candidate in enumerate(names, 1):
            log(f"    ({index}) {candidate}")
        log(f"    ({new_index}) new\n")
        choice = ask(f"  Choose [1-{new_index}] (default: 1): ") or "1"
        if choice == str(new_index):
            prompt = "  New container name"
            if default_name and default_name not in names:
                prompt += f" [{default_name}]"
            name = ask(f"{prompt}: ") or (default_name if default_name not in names else "")
            if name in names:
                raise SetupError(f"Container already exists: {name}")
            is_new = True
        elif choice.isdigit() and 1 <= int(choice) <= len(names):
            name, is_new = names[int(choice) - 1], False
        else:
            raise SetupError(f"Invalid container choice: {choice}")
    if not NAME_RE.fullmatch(name):
        raise SetupError(f"Invalid container name: {name}")
    return name, is_new


def choose_port_mode(instances: Path, name: str, is_new: bool) -> None:
    config = instances / name / f"{name}_container.conf"
    if not is_new:
        port_mode(config)
        return
    modes = {
        path.name: port_mode(path / f"{path.name}_container.conf")
        for path in instances.iterdir()
        if path.is_dir() and not path.is_symlink() and path.name != name
    }
    highest = max((value for value in modes.values() if isinstance(value, int)), default=1)
    next_nr = highest + 1 if highest < 5 else None
    selected: str | int | None = "TUN"
    if sys.stdin.isatty():
        used = {
            value: candidate
            for candidate, value in modes.items()
            if isinstance(value, int)
        }
        options: list[str | int | None] = ["TUN"]
        if next_nr is not None:
            options.append(next_nr)
        options.extend(number for number in range(2, 6) if number != next_nr)
        options.append(None)
        log("\n  Publish ports:")
        for index, option in enumerate(options, 1):
            suffix = " (default)" if option == "TUN" else ""
            if option == next_nr:
                suffix = " (next: +1)"
            elif option in used:
                suffix = f" (used: {used[option]})"
            log(f"    ({index}) {mode_label(option)}{suffix}")
        choice = ask(f"\n  Choose [1-{len(options)}] (default: 1): ") or "1"
        if not choice.isdigit() or not 1 <= int(choice) <= len(options):
            raise SetupError(f"Invalid publish-port choice: {choice}")
        selected = options[int(choice) - 1]
        if selected in used:
            raise SetupError(f"CONTAINER_NR={selected} is already used by {used[selected]}")
    config.parent.mkdir(parents=True, exist_ok=True)
    write_nr(config, selected)


def hardlink(source: Path, target: Path) -> None:
    try:
        if not target.is_symlink() and os.path.samefile(source, target):
            return
    except FileNotFoundError:
        pass
    if target.exists() and target.is_dir():
        raise SetupError(f"Hardlink target is a directory: {target}")
    temporary = target.with_name(f".{target.name}.link-{uuid.uuid4().hex}")
    try:
        os.link(source, temporary)
        os.replace(temporary, target)
    except OSError as error:
        raise SetupError(f"Cannot hardlink {source} -> {target}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def symlink(source: Path, target: Path) -> None:
    relative = os.path.relpath(source, target.parent)
    if target.is_symlink() and os.readlink(target) == relative:
        return
    if target.exists() and target.is_dir():
        raise SetupError(f"Symlink target is a directory: {target}")
    temporary = target.with_name(f".{target.name}.link-{uuid.uuid4().hex}")
    try:
        temporary.symlink_to(relative)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def required_layer_files(repo: Path) -> list[Path]:
    layer = repo.name.removesuffix("-latest")
    examples = [
        repo / f"{layer}.env_example",
        repo / f"{layer}.config.conf_example",
        repo / f"{layer}.container_example",
    ]
    missing = [path for path in examples if not path.is_file() or path.is_symlink()]
    if missing:
        raise SetupError("Missing cumulative example file(s): " + ", ".join(map(str, missing)))
    return examples


def link_instance(repo: Path, config: Path, instance: Path) -> None:
    examples = required_layer_files(repo)
    instance.mkdir(parents=True, exist_ok=True)
    stale_build = instance / f"{instance.name}_build.conf"
    if stale_build.is_symlink() or stale_build.is_file():
        stale_build.unlink()
    elif stale_build.exists():
        raise SetupError(f"Build config path is not a file or symlink: {stale_build}")
    desired_names = {source.name for source in examples}
    for target in instance.iterdir():
        if not target.is_symlink():
            continue
        if "example" in target.name and target.name not in desired_names:
            target.unlink()
    for source in examples:
        symlink(source, instance / source.name)
    for source in (
        config,
        config.parent / "optional_persistence.sh",
    ):
        if source.is_file():
            hardlink(source, instance / source.name)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--default-name", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    repo = args.repo.resolve()
    if not repo.is_dir():
        raise SetupError(f"Repository directory not found: {repo}")
    config = args.config if args.config.is_absolute() else repo / args.config
    config = config.resolve()
    if not config.is_file():
        raise SetupError(f"config.sh not found: {config}")
    if args.default_name and not NAME_RE.fullmatch(args.default_name):
        raise SetupError(f"Invalid default container name: {args.default_name}")

    required_layer_files(repo)
    instances = repo / "CONTAINER"
    instances.mkdir(parents=True, exist_ok=True)
    for instance in sorted(
        path for path in instances.iterdir() if path.is_dir() and not path.is_symlink()
    ):
        link_instance(repo, config, instance)

    name, is_new = choose_instance(instances, args.name, args.default_name)
    instance = instances / name
    instance.mkdir(parents=True, exist_ok=True)
    choose_port_mode(instances, name, is_new)
    link_instance(repo, config, instance)
    print(instance)


if __name__ == "__main__":
    try:
        main()
    except (OSError, SetupError) as error:
        print(f"container-instance-setup: {error}", file=sys.stderr)
        raise SystemExit(1) from None
