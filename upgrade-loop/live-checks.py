#!/usr/bin/env python3
"""Bounded checks of the actual deployed instance; no copied volumes or LLM grading."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import io
import os
from pathlib import Path
import pwd
import subprocess
import tarfile
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT / "live-flow-policy.json").read_text())


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Checks:
    def __init__(self, owner, container, directory):
        self.container = container
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.prefix = ["sudo", "-n", "-u", owner, "env",
                       f"XDG_RUNTIME_DIR=/run/user/{pwd.getpwnam(owner).pw_uid}", "podman"]
        self.remote = "/tmp/fedora45-upgrade-probes"

    def command(self, *args, timeout=30):
        return subprocess.run(self.prefix + list(args), text=True, capture_output=True,
                              timeout=timeout, check=True).stdout

    def inside(self, *args, timeout=30):
        return self.command("exec", self.container, *args, timeout=timeout)

    def identity(self):
        data = json.loads(self.command("inspect", self.container))[0]
        return {"id": data["Id"], "image": data["Image"], "started": data["State"]["StartedAt"],
                "running": data["State"]["Running"], "mounts": [
                    {k: v for k, v in mount.items() if k in ("Type", "Name", "Source", "Destination", "RW")}
                    for mount in data["Mounts"]]}

    def inventory(self):
        # Read configuration only. Tool contents, prompts, keys and headers never leave the instance.
        source = '''import json,pathlib,yaml
c=json.loads(pathlib.Path('/root/.openclaw/openclaw.json').read_text())
h=yaml.safe_load(pathlib.Path('/root/.hermes/config.yaml').read_text())
e=c['agents']['entries']; e=e if isinstance(e,dict) else {v['id']:v for v in e}
s=c.get('mcp',{}).get('servers',{})
a={name:{'model':v.get('model',c['agents'].get('defaults',{}).get('model')),'mcp':sorted(k for k,v in s.items() if v.get('enabled',True) and (not v.get('codex',{}).get('agents') or name in v['codex']['agents'] or '*' in v['codex']['agents']))} for name,v in e.items()}
print(json.dumps({'openclaw_agents':a,'hermes_mcp':sorted(k for k,v in h.get('mcp_servers',{}).items() if v.get('enabled',True)),'hermes_model':h['model'].get('default'),'disabled_mcp':sorted(k for k,v in s.items() if not v.get('enabled',True))}))
'''
        return json.loads(self.inside("python3", "-c", source))

    def baseline(self):
        result = {"identity": self.identity(), "inventory": self.inventory()}
        self.save("baseline", result)
        return {"status": "PASS", "data": result}

    def save(self, name, value):
        path = self.directory / f"{name}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)

    def wait(self):
        identity = self.identity()
        if not identity["running"]:
            raise RuntimeError("Container is not running")
        started = datetime.fromisoformat(identity["started"].replace("Z", "+00:00"))
        remaining = max(0, POLICY["boot_wait_seconds"] - (datetime.now(timezone.utc) - started).total_seconds())
        time.sleep(remaining)
        current = self.identity()
        if (current["id"], current["started"]) != (identity["id"], identity["started"]):
            raise RuntimeError("Container restarted during boot wait")
        previous = self.directory / "test-identity.json"
        already_waited = previous.exists() and json.loads(previous.read_text()) == current
        self.save("test-identity", current)
        if not already_waited:
            self.save("test-budget", {"deadline": time.time() + POLICY["test_budget_seconds"]})
        return {"status": "PASS", "started": identity["started"], "completed": stamp(),
                "waited_seconds": round(remaining, 2)}

    def ready(self):
        expected = json.loads((self.directory / "test-identity.json").read_text())
        current = self.identity()
        if not current["running"] or any(current[k] != expected[k] for k in ("id", "started", "image")):
            raise RuntimeError("Test identity changed")
        deadline = json.loads((self.directory / "test-budget.json").read_text())["deadline"]
        remaining = int(deadline - time.time())
        if remaining < 1:
            raise TimeoutError("Live test budget exhausted")
        return remaining

    def stage(self, name):
        remaining = self.ready()
        cached = self.directory / f"check-{name}.json"
        if cached.exists():
            result = json.loads(cached.read_text())
            if result.get("container_start") == json.loads((self.directory / "test-identity.json").read_text())["started"]:
                return result  # Exactly one attempt per check for this start, including failures.
        result = {"step": name, "timestamp": stamp(), "status": "FAIL"}
        try:
            result.update(self.perform(name, remaining))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result["error"] = type(error).__name__
        result["completed"] = stamp()
        result["container_start"] = json.loads((self.directory / "test-identity.json").read_text())["started"]
        self.save(f"check-{name}", result)
        return result

    def perform(self, name, remaining):
        if name == "inventory":
            baseline = json.loads((self.directory / "baseline.json").read_text())
            inventory = self.inventory()
            same = inventory == baseline["inventory"] and self.identity()["mounts"] == baseline["identity"]["mounts"]
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as output:
                output.add(ROOT / "probes", arcname=".")
            path = self.directory / "expected-inventory.json"
            path.write_text(json.dumps(baseline["inventory"]))
            path.chmod(0o644)  # Only model/agent names and assignments, for the owning Podman user.
            self.inside("mkdir", "-p", self.remote)
            subprocess.run(self.prefix + ["exec", "-i", self.container, "tar", "-xf", "-", "-C", self.remote],
                           input=archive.getvalue(), capture_output=True, check=True, timeout=min(30, remaining))
            # stdin avoids granting the owning user access to the private run directory.
            subprocess.run(self.prefix + ["exec", "-i", self.container, "sh", "-c",
                           f"cat > {self.remote}/baseline.json"], input=path.read_text(), text=True,
                           capture_output=True, check=True, timeout=10)
            return {"status": "PASS" if same else "FAIL", "inventory": inventory, "original_mounts": same}
        if name in ("services", "health"):
            services = ["openclaw", "hermes", "citadel", "tailscaled"]
            values = self.inside("systemctl", "is-active", *services).splitlines()
            version = self.inside("sh", "-c", '. /etc/os-release; printf "%s %s\\n" "$ID" "$VERSION_ID"').strip()
            node = self.inside("node", "--version").strip()
            return {"status": "PASS" if version == "fedora 45" and values == ["active"] * len(services) else "FAIL",
                    "services": dict(zip(services, values)), "os": version, "node": node}
        if name == "openclaw-mcp":
            package = self.inside("sh", "-c", 'printf "%s/openclaw" "$(npm root -g)"').strip()
            return self.probe(["node", f"{self.remote}/runtime-mcp-calls.mjs", package,
                               f"{self.remote}/baseline.json"], min(240, remaining))
        if name == "hermes-mcp":
            return self.probe(["/usr/local/lib/hermes-agent/venv/bin/python",
                               f"{self.remote}/runtime-hermes-mcp.py"], min(120, remaining))
        if name in ("openclaw-models", "hermes-models"):
            return self.probe(["python3", f"{self.remote}/runtime-models.py", "--engines",
                               name.split("-")[0], "--tool-call"], min(360, remaining))
        if name == "links":
            # Link requests are ordinary HTTP checks, with bounded parallelism and no LLM.
            source = (ROOT / "probes/runtime-links.py").read_text()
            return json.loads(self.inside("python3", "-c", source, timeout=min(90, remaining)))
        if name == "tailscale":
            links = json.loads((self.directory / "check-links.json").read_text()).get("results", [])
            selected = [r["url"] for r in links if (urlsplit(r["url"]).hostname or "").endswith(".ts.net")]
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(check_url, selected))
            return {"status": "PASS" if results and all(r["status"] == "PASS" for r in results) else "FAIL",
                    "context": "owning host tailnet", "results": results}
        raise ValueError("Unknown live test stage")

    def probe(self, command, timeout):
        done = subprocess.run(self.prefix + ["exec", self.container] + command,
                              text=True, capture_output=True, timeout=timeout)
        # Some libraries write startup messages before their final structured result.
        decoder = json.JSONDecoder()
        for line in range(len(done.stdout)):
            if done.stdout[line] != "{":
                continue
            try:
                result, end = decoder.raw_decode(done.stdout[line:])
                if isinstance(result, dict) and "status" in result and not done.stdout[line + end:].strip():
                    if done.returncode:
                        result["status"] = "FAIL"
                    return result
            except ValueError:
                pass
        return {"status": "FAIL", "error": "MissingStructuredProbeResult", "exit_code": done.returncode}


def check_url(url):
    parsed = urlsplit(url)
    safe = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    try:
        with urlopen(url, timeout=10) as response:
            response.read(1024)
            return {"url": safe, "http_status": response.status, "status": "PASS" if response.status == 200 else "FAIL"}
    except Exception as error:
        return {"url": safe, "status": "FAIL", "error": type(error).__name__}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    checks = Checks(args.owner, args.container, args.run_dir)
    try:
        result = checks.baseline() if args.stage == "baseline" else checks.wait() if args.stage == "wait" else checks.stage(args.stage)
    except Exception as error:
        result = {"status": "FAIL", "error": type(error).__name__, "timestamp": stamp()}
    print(json.dumps(result))
