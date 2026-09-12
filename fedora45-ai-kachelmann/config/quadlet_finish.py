#!/usr/bin/env python3
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("compose", type=Path)
parser.add_argument("quadlet", type=Path)
parser.add_argument("name")
args = parser.parse_args()
systemd = Path.home() / ".config/containers/systemd" / args.quadlet.name
print(f"Start: podman-compose -f {args.compose} up -d")
print(f"Link:  mkdir -p {systemd.parent} && ln -sf {args.quadlet} {systemd}")
print(f"Run:   systemctl --user daemon-reload && systemctl --user restart {args.name}.service")
