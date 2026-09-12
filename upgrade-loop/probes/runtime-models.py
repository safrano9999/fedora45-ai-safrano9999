"""Probe configured agent models in isolated sessions without channel delivery.

Run inside the selected container. Only status/model metadata reaches stdout.
"""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import uuid
import yaml

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--agents", help="CSV; omitted means every configured OpenClaw agent")
parser.add_argument("--engines", default="openclaw,hermes")
parser.add_argument("--tool-call", action="store_true", help="Require a harmless tool call recorded in the probe session")
args = parser.parse_args()
marker = "FEDORA_MODEL_PROBE_OK"
message = f"Infrastructure model check. Reply with exactly {marker}. Do not use tools or send any messages."


def check(task):
    engine, agent = task
    probe_id = str(uuid.uuid4())
    prompt = message
    if args.tool_call:
        instruction = ("In functions.exec, find the ALL_TOOLS entry whose name ends with session_status (it may be prefixed), call tools[entry.name]({}) and display its result; do not silently skip the call" if engine == "openclaw" else
                       "Use terminal once to run exactly: printf FEDORA_TOOL_PROBE_OK")
        prompt = f"Infrastructure probe {probe_id}. {instruction}, then reply with exactly {marker}. Do not send messages or use any other tools."
    result = {"engine": engine, "agent": agent, "timestamp": datetime.now(timezone.utc).isoformat()}
    if engine == "openclaw":
        session_key = f"agent:{agent}:upgrade-probe-{probe_id}"
        command = ["openclaw", "agent", "--agent", agent, "--session-key",
                   session_key, "--message", prompt, "--timeout", "90", "--json"]
    else:
        command = ["hermes", "chat", "-q", prompt, "--oneshot", "-Q",
                   "--max-turns", "2" if args.tool_call else "1", "--run-budget", "90", "--source", "tool"]
    try:
        completed = subprocess.run(command, cwd="/tmp", capture_output=True, text=True, timeout=120)
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
            result["expected_model"] = expected_models[agent]
            passed = passed and f"{result.get('provider')}/{result.get('model')}" == expected_models[agent]
        else:
            passed = completed.stdout.strip() == marker
        if args.tool_call and (passed or engine == "hermes"):
            if engine == "openclaw":
                history = subprocess.run(["openclaw", "gateway", "call", "chat.history", "--json",
                                          "--params", json.dumps({"sessionKey": session_key, "limit": 30})],
                                         capture_output=True, text=True, timeout=30, check=True)
                calls = [row for row in json.loads(history.stdout).get("messages", [])
                         if row.get("role") == "toolResult" and row.get("toolName") == "session_status"
                         and not row.get("isError")]
            else:
                with sqlite3.connect("file:/root/.hermes/state.db?mode=ro", uri=True) as db:
                    sessions = db.execute("SELECT DISTINCT session_id FROM messages WHERE role='user' AND instr(content,?)>0",
                                          (probe_id,)).fetchall()
                    calls = db.execute("SELECT id FROM messages WHERE session_id=? AND role='tool' AND tool_name='terminal' AND instr(content,?)>0",
                                       (sessions[0][0], "FEDORA_TOOL_PROBE_OK")).fetchall() if len(sessions) == 1 else []
                    if len(sessions) == 1:
                        model, base_url = db.execute("SELECT model,billing_base_url FROM sessions WHERE id=?", sessions[0]).fetchone()
                        reply = db.execute("SELECT content FROM messages WHERE session_id=? AND role='assistant' AND length(content)>0 ORDER BY id DESC LIMIT 1",
                                           sessions[0]).fetchone()
                        result.update(model=model, expected_model=hermes_model["default"],
                                      provider_route_matches=base_url == hermes_model["base_url"],
                                      model_reply_verified=bool(reply and reply[0].strip() == marker))
                        # The CLI can print user-configured banners and usage statistics.
                        passed = (model == hermes_model["default"] and result["provider_route_matches"]
                                  and result["model_reply_verified"])
            result["verified_tool_results"] = len(calls)
            passed = passed and bool(calls)
        result["status"] = "PASS" if passed else "FAIL"
    except (subprocess.SubprocessError, sqlite3.Error, ValueError, OSError) as error:
        result.update(status="FAIL", error=type(error).__name__)
    return result


tasks = []
expected_models = {}
if "openclaw" in args.engines.split(","):
    config = json.loads(Path("/root/.openclaw/openclaw.json").read_text())
    entries = config["agents"]["entries"]
    agents = list(entries) if isinstance(entries, dict) else [entry["id"] for entry in entries]
    for agent in agents:
        entry = entries[agent] if isinstance(entries, dict) else next(item for item in entries if item["id"] == agent)
        model = entry.get("model", config["agents"].get("defaults", {}).get("model"))
        if isinstance(model, dict) and model.get("fallbacks"):
            parser.error("Configured fallbacks need explicit model probe cases")
        expected_models[agent] = model.get("primary") if isinstance(model, dict) else model
    if args.agents:
        selected = args.agents.split(",")
        if not set(selected).issubset(agents):
            parser.error("Unknown OpenClaw agent")
        agents = selected
    tasks.extend(("openclaw", agent) for agent in agents)
if "hermes" in args.engines.split(","):
    hermes_model = yaml.safe_load(Path("/root/.hermes/config.yaml").read_text())["model"]
    tasks.append(("hermes", "default"))
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(check, tasks))
passed = bool(results) and all(result["status"] == "PASS" for result in results)
print(json.dumps({"status": "PASS" if passed else "FAIL", "results": results}, indent=2))
raise SystemExit(0 if passed else 1)
