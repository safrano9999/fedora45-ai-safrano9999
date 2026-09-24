#!/usr/bin/env python3
"""Install the independently staged OpenCode runtime configurator."""

import argparse
from pathlib import Path
import shutil


def install(source: Path, destination: Path) -> None:
    library = destination / "usr/local/lib/opencode-ephemeral/opencode_ephemeral"
    shutil.copytree(source / "opencode_ephemeral", library, dirs_exist_ok=True)

    launcher = destination / "usr/local/bin/opencode-ephemeral.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "opencode-ephemeral.py", launcher)
    launcher.chmod(0o755)

    shutil.copytree(source / "image/runtime", destination, dirs_exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", nargs="?", type=Path, default=Path("/"))
    args = parser.parse_args()
    install(args.source, args.destination)
