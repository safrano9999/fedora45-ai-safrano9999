#!/usr/bin/python3
"""Check global dependencies, honoring exact overrides in Hermes's pinned lock."""
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

# Use the installer's complete runtime path, including Fedora RPM packages in
# /usr/lib64. uv's environment inventory can omit these installed dependencies.
result = subprocess.run(
    [sys.executable, "-m", "pip", "check"],
    capture_output=True, text=True,
)
report = result.stdout + result.stderr
sys.stderr.write(report)
if result.returncode == 0:
    raise SystemExit(0)
if result.returncode != 1:
    raise SystemExit(result.returncode)

# The upstream policy reads uv reports. Translate only complete pip version
# conflicts; missing packages, invalid metadata and unknown output still fail.
conflicts = []
for line in report.splitlines():
    match = re.fullmatch(r"(\S+) \S+ has requirement (.+), but you have \S+ (\S+)\.", line)
    if match is None:
        raise SystemExit(1)
    package, requirement, version = match.groups()
    conflicts.append(f"The package `{package}` requires `{requirement}`, but `{version}` is installed")
report = f"Found {len(conflicts)} incompatibilities\n" + "\n".join(conflicts)

root = Path("/usr/local/lib/hermes-agent")
spec = importlib.util.spec_from_file_location("hermes_pip_check", root / "pm/pip_check.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
unexpected = module.unexpected_incompatibilities(report, root / "uv.lock")
if unexpected is None or unexpected:
    raise SystemExit(1)
print("All reported mismatches match exact dependency overrides in Hermes's pinned lock.")
