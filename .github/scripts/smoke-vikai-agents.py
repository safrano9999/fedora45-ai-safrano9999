#!/usr/bin/env python3
"""Check the installed VikAI bootstrap against the installed OpenClaw schema."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

bootstrap = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/bin/vikai-bootstrap-openclaw-agents"
with tempfile.TemporaryDirectory(prefix="vikai-schema-") as temporary:
    state = Path(temporary)
    config = state / "openclaw.json"
    config.write_text(json.dumps({
        "gateway": {"mode": "local"},
        "agents": {"ownership": "explicit", "entries": {"main": {"name": "main"}}},
    }))
    environment = {
        **os.environ,
        "OPENCLAW_CONFIG": str(config),
        "OPENCLAW_CONFIG_PATH": str(config),
        "OPENCLAW_STATE_DIR": str(state),
        "TOKEN_WORKER": "ci-worker",
        "TOKEN_ARCHITECT": "ci-architect",
        "TOKEN_QC": "ci-qc",
        "VIKUNJA_URL": "http://127.0.0.1:1",
        "VIKAI_HEARTBEAT_EVERY": "0m",
        "VIKAI_OPENCLAW_LLM": "",
    }
    subprocess.run(["python3", bootstrap], env=environment, check=True)
    generated = config.read_bytes()
    agents = json.loads(generated)["agents"]
    assert agents["ownership"] == "explicit" and "list" not in agents
    assert set(agents["entries"]) == {"main", "worker", "architect", "qc"}
    for name in ("worker", "architect", "qc"):
        entry = agents["entries"][name]
        assert Path(entry["workspace"]).is_dir() and Path(entry["agentDir"]).is_dir()
    subprocess.run(["openclaw", "config", "validate"], env=environment, check=True)
    subprocess.run(["python3", bootstrap], env=environment, check=True)
    assert config.read_bytes() == generated
    assert not list(state.rglob("workspace-state.json"))
    assert not list(state.rglob("openclaw-workspace-state.json"))
print("VikAI: valid canonical agent entries, preserved main, idempotent bootstrap and no retired workspace state")
