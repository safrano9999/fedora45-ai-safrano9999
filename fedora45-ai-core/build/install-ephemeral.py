#!/usr/bin/env python3
"""Install the independently staged Ephemeral runtime without touching OpenClaw."""

import argparse
from pathlib import Path
import shutil


def install(source: Path, destination: Path) -> None:
    library = destination / "usr/local/lib/openclaw-ephemeral/openclaw_ephemeral"
    shutil.copytree(source / "openclaw_ephemeral", library, dirs_exist_ok=True)
    for relative, name in (
        ("openclaw-ephemeral.py", "openclaw-ephemeral.py"),
        ("runtime/yolo.sh", "openclaw-ephemeral-yolo"),
    ):
        target = destination / "usr/local/bin" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        target.chmod(0o755)
    shutil.copytree(source / "image/runtime", destination, dirs_exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", nargs="?", type=Path, default=Path("/"))
    args = parser.parse_args()
    install(args.source, args.destination)
