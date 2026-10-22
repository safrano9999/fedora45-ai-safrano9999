#!/usr/bin/python3
"""Check global dependencies, honoring exact overrides in Hermes's pinned lock."""
import importlib.util
from pathlib import Path
import subprocess
import sys

result = subprocess.run(
    ["uv", "pip", "check", "--system", "--python", "/usr/bin/python3"],
    capture_output=True, text=True,
)
report = result.stdout + result.stderr
sys.stderr.write(report)
if result.returncode == 0:
    raise SystemExit(0)
if result.returncode != 1:
    raise SystemExit(result.returncode)

root = Path("/usr/local/lib/hermes-agent")
spec = importlib.util.spec_from_file_location("hermes_pip_check", root / "pm/pip_check.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
unexpected = module.unexpected_incompatibilities(report, root / "uv.lock")
if unexpected is None or unexpected:
    raise SystemExit(1)
print("All reported mismatches match exact dependency overrides in Hermes's pinned lock.")
