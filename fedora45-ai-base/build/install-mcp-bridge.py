#!/usr/bin/env python3
"""Refresh the image-owned bridge and its public install record in Base."""

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


archive, version = sys.argv[1:]
subprocess.run(
    ["openclaw", "plugins", "install", "--force", "--accept-capabilities", archive],
    check=True,
)
plugin_id = "openclaw-mcp-server-bridge"
root = Path("/root/.openclaw/extensions") / plugin_id
assert json.loads((root / "package.json").read_text())["version"] == version
assert (root / "dist/index.js").stat().st_size > 0
assert (root / "assets/favicon.jpeg").is_file()
initializer = Path("/usr/local/libexec/openclaw-mcp-bridge-init.py")
shutil.copyfile(root / "image/init/20-openclaw-mcp-bridge.py", initializer)
initializer.chmod(0o755)

with sqlite3.connect("file:/root/.openclaw/state/openclaw.sqlite?mode=ro", uri=True) as db:
    row = db.execute(
        "SELECT value_json FROM config_machine_state WHERE state_key = ?",
        ("plugins.installedIndex",),
    ).fetchone()
record = json.loads(row[0])["index"]["installRecords"][plugin_id]
assert record["version"] == version and Path(record["installPath"]) == root
seed_path = Path("/usr/local/share/openclaw/image-plugin-installs.json")
seed = json.loads(seed_path.read_text())
seed["installRecords"][plugin_id] = record
seed_path.write_text(json.dumps(seed) + "\n")
Path(archive).unlink()
