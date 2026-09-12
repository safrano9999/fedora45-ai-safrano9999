"""Probe configured agent models in isolated sessions without channel delivery.

Run inside the selected container. Only status/model metadata reaches stdout.
"""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--agents", help="CSV; omitted means every configured OpenClaw agent")
parser.add_argument("--engines", default="openclaw,hermes")
args = parser.parse_args()
marker = "FEDORA_MODEL_PROBE_OK"
message = f"Infrastructure model check. Reply with exactly {marker}. Do not use tools or send any messages."


def check(task):
    engine, agent = task
    result = {"engine": engine, "agent": agent, "timestamp": datetime.now(timezone.utc).isoformat()}
    if engine == "openclaw":
        command = ["openclaw", "agent", "--agent", agent, "--session-key",
                   f"agent:{agent}:upgrade-probe-{uuid.uuid4()}", "--message", message,
                   "--timeout", "60", "--json"]
    else:
        command = ["hermes", "chat", "-q", message, "--oneshot", "-Q",
                   "--max-turns", "1", "--run-budget", "60", "--source", "tool"]
    try:
        completed = subprocess.run(command, cwd="/tmp", capture_output=True, text=True, timeout=90)
        result["exit_code"] = completed.returncode
        if completed.returncode:
            result["status"] = "FAIL"
            return result
        if engine == "openclaw":
            response = json.loads(completed.stdout)
            payload = response.get("result", {})
            meta = payload.get("meta", {})
            result.update({key: value for key, value in meta.get("agentMeta", {}).items()
                           if key in ("provider", "model")})
            answer = "\n".join(item.get("text", "") for item in payload.get("payloads", []))
            passed = response.get("status") == "ok" and not meta.get("aborted") and answer.strip() == marker
        else:
            passed = completed.stdout.strip() == marker
        result["status"] = "PASS" if passed else "FAIL"
    except (subprocess.TimeoutExpired, ValueError, OSError) as error:
        result.update(status="FAIL", error=type(error).__name__)
    return result


tasks = []
if "openclaw" in args.engines.split(","):
    config = json.loads(Path("/root/.openclaw/openclaw.json").read_text())
    entries = config["agents"]["entries"]
    agents = list(entries) if isinstance(entries, dict) else [entry["id"] for entry in entries]
    if args.agents:
        selected = args.agents.split(",")
        if not set(selected).issubset(agents):
            parser.error("Unknown OpenClaw agent")
        agents = selected
    tasks.extend(("openclaw", agent) for agent in agents)
if "hermes" in args.engines.split(","):
    tasks.append(("hermes", "default"))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(check, tasks))
passed = bool(results) and all(result["status"] == "PASS" for result in results)
print(json.dumps({"status": "PASS" if passed else "FAIL", "results": results}, indent=2))
raise SystemExit(0 if passed else 1)
