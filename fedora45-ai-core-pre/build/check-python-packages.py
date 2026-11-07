#!/usr/bin/python3
"""Reject duplicate distributions visible to the shared system interpreter."""
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import sys

assert sys.prefix == sys.base_prefix, "Run this check with the shared system Python"
packages = {}
for distribution in metadata.distributions():
    name = re.sub(r"[-_.]+", "-", distribution.metadata.get("Name", "")).lower()
    if not name:
        continue
    location = str(Path(distribution.locate_file("")).resolve())
    packages.setdefault(name, {})[location] = distribution.version
duplicates = {name: locations for name, locations in packages.items() if len(locations) > 1}
if duplicates:
    raise SystemExit("Duplicate system Python distributions:\n" + json.dumps(duplicates, indent=2))
print(f"System Python: {len(packages)} distributions, no duplicate installation paths")
