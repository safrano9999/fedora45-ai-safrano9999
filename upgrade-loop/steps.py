#!/usr/bin/env python3
"""Host-side adapters for the n8n workflow. Every operation returns a structured receipt."""
import asyncio
import base64
from datetime import datetime, timezone
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import time
from source_snapshot import update_openclaw_source

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
STATE = Path.home() / ".local/state/fedora45-upgrade-loop"
CONFIG = Path.home() / ".config/fedora45-upgrade-loop.json"
LAYERS = ["core-pre", "core", "base", "kachelmann", "safrano9999", "safrano9999-full"]
IMAGES = ["ghcr.io/safrano9999/fedora45-ai-" + layer for layer in LAYERS]
GITHUB = "safrano9999/fedora45-ai-safrano9999"
spec = importlib.util.spec_from_file_location("live_checks", ROOT / "live-checks.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def command(args, *, cwd=ROOT, timeout=120, env=None, data=None, check=True):
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True, input=data,
                            capture_output=True, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {Path(str(args[0])).name}")
    return result


def gh(endpoint, *args, data=None):
    return json.loads(command(["gh", "api", endpoint, *args], data=data).stdout or "null")


async def mcp_call(tool, arguments):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    server = REPO.parent / "SAFRANO_MCP/server.py"
    async with stdio_client(StdioServerParameters(command="python3", args=[str(server)])) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            reply = await session.call_tool(tool, arguments)
            if reply.isError:
                raise RuntimeError(f"MCP operation failed: {tool}")
            for item in reply.content:
                if item.type == "text":
                    value = json.loads(item.text)
                    # Tool stdout can contain arbitrary logs. Only record structured metadata.
                    value.pop("output", None)
                    return value
    raise RuntimeError("Missing MCP receipt")


def mcp(tool, **arguments):
    return asyncio.run(mcp_call(tool, arguments))


class Run:
    def __init__(self, identifier):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            raise ValueError("Invalid execution identifier")
        self.directory = STATE / identifier
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "run.json"
        self.lock = (self.directory / "run.lock").open("w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {
            "run": {"id": identifier, "events": [], "summaries": [], "started": live.stamp()},
            "results": {}, "gates": {}, "status": "RUNNING"}
        self.config = json.loads(CONFIG.read_text())
        self.checks = live.Checks(self.config["owner"], self.config["container"], self.directory)

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def event(self, name, result):
        self.data["results"][name] = result
        self.data["run"]["events"].append({"step": name, "timestamp": live.stamp(), **result})
        self.gates()
        self.save()

    def gates(self):
        r = self.data["results"]
        passed = lambda key: r.get(key, {}).get("status") == "PASS"
        self.data["gates"].update({
            "clean": passed("versions"), "update": self.data.get("update", False),
            "ephemeral_completed": all(r.get(k, {}).get("completed") for k in ("openclaw-precheck", "hermes-precheck")),
            "ephemeral_compatible": all(passed(k) for k in ("openclaw-precheck", "hermes-precheck")),
            "baseline": all(passed(k) for k in ("stable", "pull-stable", "quadlet-stable", "reload-stable")),
            "ephemeral_published": passed("publish-ephemeral"), "preflight": passed("preflight"),
            "own_patches": passed("own-patches"), "build": passed("track-build"),
            "checklist": passed("services"), "regressions": passed("inventory"),
            "healthy": passed("health"), "rollback_healthy": passed("rollback-health"),
        })
        required = ["inventory", "openclaw-mcp", "hermes-mcp", "openclaw-models", "hermes-models", "links", "tailscale"]
        covered = all(k in r for k in required)
        self.data["integration"] = {"status": "PASS" if covered and all(passed(k) for k in required) else "FAIL",
                                    "covered": covered, "report": str(self.path)}

    def quadlet(self, image):
        owner = self.config["owner"]
        source = '''import pathlib,re,sys
for raw in sys.argv[2:]:
 p=pathlib.Path(raw).resolve(); text=p.read_text()
 pattern=r'^Image=.*$' if p.suffix=='.container' else r'^    image:.*$'
 prefix='Image=' if p.suffix=='.container' else '    image: '
 text,n=re.subn(pattern,prefix+sys.argv[1],text,flags=re.M)
 if n!=1: raise RuntimeError('Expected exactly one image reference')
 p.write_text(text)
'''
        command(["sudo", "-n", "-u", owner, "python3", "-c", source, image,
                 self.config["quadlet"], self.config["compose"]])
        return {"status": "PASS", "image": image}

    def systemctl(self, action):
        owner = self.config["owner"]
        args = ["sudo", "-n", "-u", owner, "env", f"XDG_RUNTIME_DIR=/run/user/{pwd.getpwnam(owner).pw_uid}",
                "systemctl", "--user", action]
        if action == "restart":
            args.append(self.config["service"])
        command(args, timeout=180)

    def manifest(self, reference):
        # Login credentials are confined to the existing Podman auth file.
        raw = command(["skopeo", "inspect", "--authfile", self.config["registry_auth"],
                       "--raw", "docker://" + reference]).stdout
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()

    def tags(self, target, sources):
        receipts = []
        for image in IMAGES:
            receipt = mcp("tag_image", image=image, source=sources[image], target=target)
            receipts.append(receipt)
            self.data.setdefault("tag_receipts", []).append(receipt)
            self.save()
        return {"status": "PASS", "images": receipts}

    def pull(self, sources, tag=None):
        receipts = []
        existing = set(mcp("podman_smart1", action="list")["images"])
        for image, digest in sources.items():
            reference = image + ":" + tag if tag else image + "@" + digest
            if self.manifest(reference) != digest:
                raise ValueError("Pull reference no longer matches the recorded digest")
            mcp("podman_smart1", action="add", image=reference)
            if tag is None and reference not in existing:
                self.data.setdefault("transient_images", []).append(reference)
                self.save()
            receipts.append(mcp("podman_smart1", action="pull", image=reference))
            if self.manifest(reference) != digest:
                raise ValueError("Pull reference changed during the pull")
        return {"status": "PASS", "images": receipts}

    def versions(self):
        output = command(["bash", ROOT / "check-versions.sh"], timeout=30).stdout
        versions = {}
        for line in output.splitlines():
            match = re.fullmatch(r"(Hermes|OpenClaw): (?:up to date — version: ([0-9]+\.[0-9]+\.[0-9]+)|New version available! actual ([0-9]+\.[0-9]+\.[0-9]+), latest ([0-9]+\.[0-9]+\.[0-9]+))", line)
            if not match:
                raise ValueError("Unexpected version-check output")
            name, same, old, new = match.groups()
            old, new = same or old, same or new
            if tuple(map(int, new.split("."))) < tuple(map(int, old.split("."))):
                raise ValueError("Upstream release is older than the current pin")
            versions[name.lower()] = {"current": old, "latest": new}
        if set(versions) != {"hermes", "openclaw"}:
            raise ValueError("Incomplete version-check output")
        self.data["versions"] = versions
        self.data["update"] = any(v["current"] != v["latest"] for v in versions.values())
        if self.data["update"]:
            self.checks.baseline()
            self.data["source_baseline"] = {name: command(["git", "-C", REPO.parent / name, "rev-parse", "HEAD"]).stdout.strip()
                                            for name in self.config["repositories"]}
        return {"status": "PASS", "versions": versions}

    def precheck(self, component, target_version=None):
        version = self.data["versions"][component]
        if target_version is None and version["current"] == version["latest"]:
            return {"status": "PASS", "completed": True, "applicable": False}
        selected = target_version or version["latest"]
        generator = REPO.parent / f"{component}-ephemeral"
        if command(["git", "status", "--porcelain"], cwd=generator).stdout:
            if self.data["results"].get("repair-apply", {}).get("status") != "PASS" or self.data.get("repairRequest", {}).get("repository") != generator.name:
                raise ValueError("Generator checkout differs from its recorded commit")
            command(["git", "apply", "--reverse", "--check", self.directory / "repair.patch"], cwd=generator)
        arguments = ["python3", ROOT / "prepare-runtime.py", component, selected]
        if component == "openclaw":
            # Resolve through the same Core-pre selection used by container preparation.
            spec = importlib.util.spec_from_file_location("component_preparation", ROOT / "prepare-container.py")
            preparation = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(preparation)
            core = dict(line.split("=", 1) for line in (REPO / "fedora45-ai-core/build.conf").read_text().splitlines()
                        if re.match(r"^[A-Z_][A-Z0-9_]*=", line))
            inputs = preparation.patch_inputs(selected, core,
                gh("repos/safrano9999/openclaw-deterministic-latest/releases?per_page=100"),
                gh("repos/safrano9999/openclaw-deterministic-latest/releases/latest"))
            override = preparation.openclaw_override((REPO / "fedora45-ai-core-pre/Containerfile").read_text(), selected)
            expected = override or gh(f"repos/openclaw/openclaw/commits/v{selected}")["sha"]
            inputs["OPENCLAW_UPSTREAM_SHA"] = expected
            selection = self.directory / "openclaw-source-inputs.json"
            selection.write_text(json.dumps(inputs) + "\n")
            arguments += ["--source-inputs", selection]
        target = json.loads(command(arguments, timeout=900).stdout.splitlines()[-1])
        if selected == version["latest"]:
            self.data.setdefault("prepared_targets", {})[component] = target
        env = dict(os.environ, UPGRADE_TARGET_SOURCE=target["source"], UPGRADE_TARGET_VERSION=selected)
        env["PYTHONPATH"] = os.pathsep.join([str(REPO.parent / f"{component}-ephemeral"), target["source"]])
        if component == "openclaw":
            env["UPGRADE_TARGET_PACKAGE"] = target["package"]
            env["UPGRADE_TARGET_UPSTREAM_SHA"] = target["upstream_commit"]
        done = command(["python3", ROOT / f"probes/{component}_config.py"], env=env, timeout=180, check=False)
        return {"status": "PASS" if done.returncode == 0 else "FAIL", "completed": True,
                "target_version": selected, "upstream_commit": target["upstream_commit"], "exit_code": done.returncode}

    def repair(self, name):
        request = self.data.get("repairRequest", {})
        if name == "repair-proposal":
            payload = self.data.pop("adapter_payload")
            request = payload["repairRequest"]
            repository = request.get("repository", REPO.name if request["type"] == "version-script" else "")
            if repository not in (REPO.name, "openclaw-ephemeral", "hermes-ephemeral"):
                return {"status": "APPROVAL_REQUIRED", "reason": "Select one concrete generator repository for the proposed repair"}
            proposed = payload["repair"]["fixProposal"]
            fenced = re.search(r"```(?:diff|patch)?\n(.*?)```", proposed, re.S)
            patch = fenced.group(1) if fenced else proposed
            if not patch.startswith(("diff --git ", "--- ")):
                return {"status": "APPROVAL_REQUIRED", "reason": "Repair response contains no applicable unified diff"}
            digest = hashlib.sha256(patch.encode()).hexdigest()
            path = self.directory / "repair.patch"
            path.write_text(patch)
            commit = command(["git", "-C", REPO.parent / repository, "rev-parse", "HEAD"]).stdout.strip()
            self.data.update(repairRequest={**request, "repository": repository}, repair={
                "attempt": payload["repair"]["attempt"], "sha256": digest, "source_commit": commit,
                "status": "APPROVAL_REQUIRED", "fixProposal": patch, "verification": "NOT_RUN"})
            self.data["run"].setdefault("repairAttempts", {})[repository] = payload["repair"]["attempt"]
            approval_path = STATE / "approvals" / f"{digest}.json"
            approval = json.loads(approval_path.read_text()) if approval_path.exists() else {}
            approved = approval.get("decision") == "GO" and approval.get("sha256") == digest and approval.get("commit") == commit
            self.data["gates"]["repair_approved"] = approved
            return {"status": "PASS" if approved else "APPROVAL_REQUIRED", "sha256": digest, "source_commit": commit}
        if name == "repair-apply":
            if self.data["gates"].get("repair_approved") is not True:
                raise ValueError("Exact source diff has no explicit user Go")
            repo = REPO.parent / request["repository"]
            if command(["git", "status", "--porcelain"], cwd=repo).stdout:
                raise ValueError("Repair repository is not clean")
            if command(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip() != self.data["repair"]["source_commit"]:
                raise ValueError("Repair source changed since approval")
            path = self.directory / "repair.patch"
            if hashlib.sha256(path.read_bytes()).hexdigest() != self.data["repair"]["sha256"]:
                raise ValueError("Approved diff changed")
            if request["type"] == "version-script":
                names = command(["git", "apply", "--numstat", path], cwd=repo).stdout.splitlines()
                if not names or any(line.split("\t")[-1] != "upgrade-loop/check-versions.sh" for line in names):
                    raise ValueError("Version-script repair changes another artifact")
            command(["git", "apply", "--check", path], cwd=repo)
            command(["git", "apply", path], cwd=repo)
            return {"status": "PASS", "sha256": self.data["repair"]["sha256"]}
        if name == "repair-verify-live":
            if self.data["results"].get("repair-apply", {}).get("status") != "PASS":
                raise ValueError("No approved repair was applied")
            if request["type"] == "version-script":
                result = self.versions()
            else:
                result = self.precheck(request["repository"].removesuffix("-ephemeral"))
            return result
        if name == "repair-verify-fixtures":
            if request["type"] != "version-script":
                component = request["repository"].removesuffix("-ephemeral")
                return self.precheck(component, self.data["versions"][component]["current"])
            results = []
            fixtures = json.loads((ROOT / "version-fixtures.json").read_text())
            with tempfile.TemporaryDirectory(prefix="version-fixtures-") as raw:
                directory = Path(raw)
                wrapper = directory / "curl"
                wrapper.write_text('#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\nname="hermes" if any("NousResearch/hermes-agent" in a for a in sys.argv) else "openclaw"\nprint(Path(os.environ["VERSION_FIXTURE_DIR"],name+".json").read_text())\n')
                wrapper.chmod(0o700)
                for fixture in fixtures:
                    for component in ("hermes", "openclaw"):
                        (directory / f"{component}.json").write_text(json.dumps(fixture[component]))
                    env = dict(os.environ, PATH=raw + os.pathsep + os.environ["PATH"], VERSION_FIXTURE_DIR=raw)
                    output = command(["bash", ROOT / "check-versions.sh"], env=env, timeout=10).stdout
                    passed = all(re.search(r"(?:latest |version: )" + re.escape(version) + r"(?:\n|$)", output)
                                 for version in fixture["expected"].values())
                    results.append({"fixture": fixture["name"], "status": "PASS" if passed else "FAIL"})
            return {"status": "PASS" if results and all(r["status"] == "PASS" for r in results) else "FAIL", "results": results}
        if name == "repair-commit":
            if any(self.data["results"].get(k, {}).get("status") != "PASS" for k in ("repair-verify-live", "repair-verify-fixtures")):
                raise ValueError("Repair has not passed live and historical verification")
            repo = REPO.parent / request["repository"]
            command(["git", "add", "-u"], cwd=repo)
            command(["git", "commit", "-m", "Apply explicitly approved upgrade-loop compatibility repair"], cwd=repo)
            mcp("fire", repositories=[repo.name])
            if request["type"] != "version-script":
                self.data.setdefault("tested_sources", {})[repo.name] = command(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
                self.data["results"][repo.name.removesuffix("-ephemeral") + "-precheck"] = self.data["results"]["repair-verify-live"]
            self.data["repair"].update(status="VERIFIED", verification="PASS")
            self.data["repairResult"] = {"status": "script-repaired" if request["type"] == "version-script" else "ephemeral-retested",
                                         "verificationStatus": "PASS", "attempt": self.data["repair"]["attempt"]}
            self.data["repairRoute"] = "version-check" if request["type"] == "version-script" else "ephemeral-check"
            return {"status": "PASS"}
        if name == "repair-discard":
            repo = REPO.parent / request["repository"]
            patch = self.directory / "repair.patch"
            command(["git", "apply", "--reverse", "--check", patch], cwd=repo)
            command(["git", "apply", "--reverse", patch], cwd=repo)
            self.data["repair"].update(status="FAILED_VERIFICATION", verification="FAIL")
            return {"status": "PASS", "approved_diff_reverted": True}
        raise ValueError("Unknown repair operation")

    def stable(self):
        identity = self.checks.identity()
        info = json.loads(self.checks.command("image", "inspect", identity["image"]))[0]
        version = info["Labels"]["org.opencontainers.image.version"]
        if not re.fullmatch(r"[0-9]{4}\.[1-9][0-9]?\.[1-9][0-9]*", version):
            raise ValueError("Running image has no fixed YYYY.M.N version")
        sources = {image: self.manifest(image + ":" + version) for image in IMAGES}
        # The running container must match the frozen deployment image, not a moving tag.
        expected = json.loads(self.checks.command("image", "inspect", self.config["image"] + "@" + sources[self.config["image"]]))[0]
        if expected["Id"] != identity["image"]:
            raise ValueError("Stable baseline does not match the running image")
        self.data.update(stable=sources, original_identity=identity)
        self.save()
        result = self.tags("stable", sources)
        for name, commit in self.data["source_baseline"].items():
            mcp("tag_repository", repository="safrano9999/" + name, commit=commit)
        return result

    def publish_ephemeral(self):
        receipts = {}
        for component in ("openclaw", "hermes"):
            if self.data["versions"][component]["current"] == self.data["versions"][component]["latest"]:
                continue
            name = component + "-ephemeral"
            commit = self.data.get("tested_sources", {}).get(name, self.data["source_baseline"][name])
            remote = gh(f"repos/safrano9999/{name}/commits/main")["sha"]
            if remote != commit:
                raise ValueError("Generator main changed after its compatibility check")
            endpoint = f"repos/safrano9999/{name}/git/refs/tags/latest"
            existing = command(["gh", "api", endpoint], check=False)
            if existing.returncode:
                if "HTTP 404" not in existing.stderr:
                    raise RuntimeError("Cannot inspect generator latest tag")
                gh(f"repos/safrano9999/{name}/git/refs", "--method", "POST", "--input", "-",
                   data=json.dumps({"ref": "refs/tags/latest", "sha": commit}))
            else:
                gh(endpoint, "--method", "PATCH", "--input", "-", data=json.dumps({"sha": commit, "force": True}))
            if gh(f"repos/safrano9999/{name}/git/ref/tags/latest")["object"]["sha"] != commit:
                raise ValueError("Generator tag verification failed")
            receipts[name] = commit
        return {"status": "PASS", "commits": receipts}

    def pins(self, apply=False):
        path = REPO / "fedora45-ai-core-pre/Containerfile"
        before = path.read_text()
        after = before
        after = update_openclaw_source(after, self.data["versions"]["openclaw"]["latest"])
        for name, version in self.data["versions"].items():
            after, count = re.subn(r"^ARG " + name.upper() + r"_VERSION=.*$",
                                  f"ARG {name.upper()}_VERSION={version['latest']}", after, flags=re.M)
            if count != 1:
                raise ValueError("Expected exactly one version pin")
        changes = [(path, before, after)]
        core = REPO / "fedora45-ai-core/build.conf"
        old_core = core.read_text()
        new_core = old_core
        for key, value in self.data.get("core_inputs", {}).items():
            new_core, count = re.subn(r"^" + key + r"=.*$", key + "=" + value, new_core, flags=re.M)
            if count != 1: raise ValueError("Expected one Core input pin")
        changes.append((core, old_core, new_core))
        patch = "".join("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                      "a/" + str(file.relative_to(REPO)), "b/" + str(file.relative_to(REPO))))
                      for file, old, new in changes)
        digest = hashlib.sha256(patch.encode()).hexdigest()
        (self.directory / "version-pins.patch").write_text(patch)
        self.data["proposal"] = {"sha256": digest, "path": str(self.directory / "version-pins.patch")}
        # Updating these declared version/commit/checksum values is the authorized upgrade operation.
        # Application, generator, repair-script and SOT edits still require an exact-diff Go.
        if apply:
            if command(["git", "status", "--porcelain"], cwd=REPO).stdout:
                raise ValueError("Repository is not clean; refusing a mixed commit")
            command(["git", "apply", "--check", self.directory / "version-pins.patch"], cwd=REPO)
            command(["git", "apply", self.directory / "version-pins.patch"], cwd=REPO)
            command(["git", "add", "--", path, core], cwd=REPO)
            command(["git", "commit", "-m", "Update Fedora45 upstream version pins"], cwd=REPO)
            mcp("fire", repositories=[REPO.name])
            self.data["build_commit"] = command(["git", "rev-parse", "HEAD"], cwd=REPO).stdout.strip()
        return {"status": "PASS", "sha256": digest}

    def build(self):
        if self.data.get("dispatched"):
            return {"status": "PASS", "existing_dispatch": self.data["dispatched"]}
        tag = command(["bash", REPO / ".github/scripts/resolve-fedora-image-tag.sh", IMAGES[0], ""], cwd=REPO).stdout.strip()
        if not re.fullmatch(r"[0-9]{4}\.[1-9][0-9]?\.[1-9][0-9]*", tag):
            raise ValueError("Invalid fixed image tag")
        self.data.update(candidate_tag=tag, dispatched=live.stamp())
        self.save()  # A lost response must not dispatch a second build automatically.
        receipt = mcp("run_workflow", repository=GITHUB, workflow="fedora45-ai-core-pre-image.yml",
                      inputs={"image_tag": tag, "push_latest": False, "push_stable": False,
                              "cascade": True, "monitor_id": self.data["run"]["id"], "monitor_final": False})
        return {"status": "PASS", "tag": tag, "receipt": receipt}

    def track(self):
        deadline = time.monotonic() + 3 * 60 * 60
        while time.monotonic() < deadline:
            rows = gh(f"repos/{GITHUB}/actions/runs?per_page=100")["workflow_runs"]
            selected = [r for r in rows if r["created_at"] >= self.data["dispatched"][:19] + "Z"
                        and r["head_sha"] == self.data["build_commit"]
                        and f"monitor:{self.data['run']['id']}:" in r["display_title"]]
            failures = [r for r in selected if r["conclusion"] not in (None, "success")]
            if failures:
                return {"status": "FAIL", "runs": [{"id": r["id"], "url": r["html_url"], "conclusion": r["conclusion"]} for r in selected]}
            if {r["name"] for r in selected if r["conclusion"] == "success"} == {"fedora45-ai-" + layer + " image" for layer in LAYERS}:
                self.data["candidate"] = {image: self.manifest(image + ":" + self.data["candidate_tag"]) for image in IMAGES}
                return {"status": "PASS", "images": self.data["candidate"],
                        "runs": [{"id": r["id"], "url": r["html_url"]} for r in selected]}
            time.sleep(45)
        return {"status": "FAIL", "error": "BuildTimeLimit"}

    def perform(self, name):
        prerequisites = {
            "pull-stable": ["stable"], "quadlet-stable": ["stable"], "reload-stable": ["quadlet-stable"],
            "commit-pins": ["preflight", "own-patches"], "build": ["commit-pins"],
            "pull-candidate": ["track-build"], "quadlet-candidate": ["pull-candidate"],
            "reload-candidate": ["quadlet-candidate"], "restart-candidate": ["reload-candidate"],
            "latest": ["verified"], "quadlet-latest": ["latest"], "reload-latest": ["quadlet-latest"],
            "success": ["latest", "quadlet-latest", "reload-latest"],
        }
        if any(self.data["results"].get(step, {}).get("status") != "PASS" for step in prerequisites.get(name, [])):
            return {"status": "FAIL", "error": "RequiredPreviousStepFailed"}
        if name in ("repair-proposal", "repair-apply", "repair-verify-live", "repair-verify-fixtures", "repair-commit", "repair-discard"):
            return self.repair(name)
        if name == "versions": return self.versions()
        if name.endswith("-precheck"): return self.precheck(name.split("-")[0])
        if name == "stable": return self.stable()
        if name == "pull-stable": return self.pull(self.data["stable"], "stable")
        if name == "pull-candidate": return self.pull(self.data["candidate"])
        if name.startswith("quadlet-"):
            suffix = name.removeprefix("quadlet-")
            image = self.config["image"]
            reference = image + "@" + self.data["candidate"][image] if suffix == "candidate" else image + ":" + suffix
            return self.quadlet(reference)
        if name.startswith("reload-"):
            before = self.checks.identity()
            self.systemctl("daemon-reload")
            if self.checks.identity() != before: raise ValueError("Daemon-reload changed the running instance")
            return {"status": "PASS", "container_restarted": False}
        if name == "publish-ephemeral": return self.publish_ephemeral()
        if name == "preflight": return self.pins()
        if name == "commit-pins": return self.pins(apply=True)
        if name == "own-patches":
            # Core must explicitly name the new upstream and its reviewed artifact before building.
            values = dict(line.split("=", 1) for line in (REPO / "fedora45-ai-core/build.conf").read_text().splitlines() if re.match(r"^[A-Z_]+=", line))
            target = self.data["versions"]["openclaw"]["latest"]
            inputs = {}
            if values.get("OPENCLAW_VERSION") != target:
                releases = gh("repos/safrano9999/openclaw-deterministic-latest/releases?per_page=100")
                matches = [(release, asset) for release in releases if not release["draft"] and not release["prerelease"]
                           for asset in release["assets"] if asset["name"] == f"openclaw-{target}-deterministic.tar.gz"]
                if not matches:
                    return {"status": "APPROVAL_REQUIRED", "reason": "No published deterministic artifact for the target OpenClaw release; discuss required source work first"}
                release, asset = matches[0]
                digest = asset.get("digest", "")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ValueError("Published patch artifact has no verified checksum")
                inputs.update(OPENCLAW_VERSION=target, OPENCLAW_DETERMINISTIC_TAG=release["tag_name"], OPENCLAW_DETERMINISTIC_SHA256=digest[7:])
                foundation = (REPO / "fedora45-ai-core-pre/Containerfile").read_text()
                override = __import__("source_snapshot").openclaw_override(foundation, target)
                inputs["OPENCLAW_UPSTREAM_SHA"] = override or gh(f"repos/openclaw/openclaw/commits/v{target}")["sha"]
            for component in ("openclaw", "hermes"):
                version = self.data["versions"][component]
                if version["current"] != version["latest"]:
                    repository = component + "-ephemeral"
                    inputs[component.upper() + "_EPHEMERAL_COMMIT"] = self.data.get("tested_sources", {}).get(repository, self.data["source_baseline"][repository])
            self.data["core_inputs"] = inputs
            return {"status": "PASS", "patch_tag": values["OPENCLAW_DETERMINISTIC_TAG"],
                    "patch_sha256": values["OPENCLAW_DETERMINISTIC_SHA256"]}
        if name == "build": return self.build()
        if name == "track-build": return self.track()
        if name in ("restart-candidate", "restart-stable"):
            self.data["live_changed"] = True
            self.save()
            self.systemctl("restart")
            identity = self.checks.identity()
            image = self.config["image"]
            digest = self.data["candidate" if name == "restart-candidate" else "stable"][image]
            expected = json.loads(self.checks.command("image", "inspect", image + "@" + digest))[0]["Id"]
            if identity["image"] != expected: raise ValueError("Recreated instance has the wrong image")
            return {"status": "PASS", "identity": identity}
        if name == "wait": return self.checks.wait()
        if name in ("services", "inventory", "openclaw-mcp", "hermes-mcp", "openclaw-models", "hermes-models", "links", "tailscale", "health"):
            return self.checks.stage(name)
        if name == "rollback-health": return self.checks.stage("health")
        if name in ("verified", "latest"):
            if not all(self.data["gates"].get(k) for k in ("checklist", "regressions", "healthy")) or self.data["integration"]["status"] != "PASS":
                raise ValueError("Required live checks have not passed")
            result = self.tags(name, self.data["candidate"])
            if name == "latest":
                for owner in self.config["image_users"]:
                    prefix = ["sudo", "-n", "-u", owner, "env", f"XDG_RUNTIME_DIR=/run/user/{pwd.getpwnam(owner).pw_uid}", "podman", "tag"]
                    for image, digest in self.data["candidate"].items():
                        command(prefix + [image + "@" + digest, image + ":latest"])
            return result
        if name in ("success", "no-update", "abort", "rolled-back"):
            self.data["status"] = {"success": "SUCCESS", "no-update": "NO_UPDATE", "abort": "ABORTED", "rolled-back": "ROLLED_BACK"}[name]
            return {"status": self.data["status"]}
        if name in ("collect", "integration-result"):
            if name == "collect" and self.data.get("live_changed"):
                for stage in ("services", "inventory", "openclaw-mcp", "hermes-mcp", "openclaw-models", "hermes-models", "links", "tailscale", "health"):
                    if stage not in self.data["results"]:
                        self.event(stage, {"status": "NOT_TESTED", "reason": "An earlier gate stopped the run"})
            return {"status": "PASS", "report": str(self.path)}
        if name == "repair-input":
            self.data["repairRequest"] = {"type": "version-script", "artifact": "check-versions.sh",
                "content": (ROOT / "check-versions.sh").read_text(), "validationError": self.data["results"].get("versions"),
                "context": {"versions": self.data.get("versions"), "fixtures": "Prior structured run receipts"}}
            return {"status": "PASS"}
        if name == "repair-review":
            return {"status": "APPROVAL_REQUIRED", "reason": "A proposed source diff must be discussed and explicitly approved by the user"}
        if name == "report":
            report_spec = importlib.util.spec_from_file_location("report", ROOT / "render-report.py")
            report = importlib.util.module_from_spec(report_spec)
            report_spec.loader.exec_module(report)
            path = self.directory / "report.pdf"
            report.render(self.data, self.data.get("final_summary", "Summary unavailable; deterministic results follow."), path)
            self.data["report"] = {"path": str(path), "sent": False}
            return {"status": "PASS", "path": str(path), "bytes": path.stat().st_size}
        if name == "telegram":
            path = Path(self.data["report"]["path"])
            remote = "/tmp/fedora45-upgrade-report.pdf"
            subprocess.run(self.checks.prefix + ["exec", "-i", self.config["container"], "sh", "-c", f"cat > {remote}"],
                           input=path.read_bytes(), capture_output=True, check=True, timeout=30)
            source = '''import os,subprocess
target=os.environ.get('OPENCLAW_TELEGRAM_CHAT_ID')
if not target: raise RuntimeError('Main Telegram chat is not configured')
subprocess.run(['openclaw','message','send','--channel','telegram','--target',target,'--media','/tmp/fedora45-upgrade-report.pdf','--message','Fedora45 upgrade report'],capture_output=True,check=True,timeout=60)
'''
            self.checks.inside("python3", "-c", source, timeout=90)
            self.data["report"]["sent"] = True
            return {"status": "PASS", "delivery": "OpenClaw Telegram"}
        if name == "done":
            if self.data["status"] in ("SUCCESS", "ROLLED_BACK") or not self.data.get("live_changed"):
                for reference in list(self.data.get("transient_images", [])):
                    mcp("podman_smart1", action="remove", image=reference)
                    self.data["transient_images"].remove(reference)
                    self.save()
            return {"status": self.data["status"], "report": self.data.get("report")}
        raise ValueError("Unknown workflow operation")


def main():
    if sys.argv[1:] == ["--check"] or (sys.argv[1:] == ["--ssh"] and re.search(r"\bfedora45-loop --check\s*$", os.environ.get("SSH_ORIGINAL_COMMAND", ""))):
        os.chdir(ROOT)
        os.execl("/usr/bin/bash", "bash", "-o", "pipefail", "-c", "./check-versions.sh | sed -E 's/^(Hermes|OpenClaw): up to date — version: (.*)$/✅ \\1 version \\2 is actual/; s/^(Hermes|OpenClaw): New version available! actual ([^,]+), latest (.*)$/🟡 \\1 version \\2 is deprecated, new: \\3/' | tac")
    if sys.argv[1:] == ["--ssh"]:
        original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
        match = re.search(r"\bfedora45-loop ([a-z-]+) ([A-Za-z0-9_-]{1,80})(?: ([A-Za-z0-9_=-]{1,65536}))?\s*$", original)
        if not match:
            raise ValueError("Only named Fedora45 workflow operations are accepted")
        operation, identifier, encoded = match.groups()
    else:
        operation, identifier, *payload = sys.argv[1:]
        if len(payload) > 1: raise ValueError("Unexpected adapter arguments")
        encoded = payload[0] if payload else None
    run = Run(identifier)
    if encoded and operation == "report":
        run.data["final_summary"] = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()[:5000]
    elif encoded and operation == "repair-proposal":
        run.data["adapter_payload"] = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    try:
        result = run.perform(operation)
    except Exception as error:
        result = {"status": "FAIL", "error": type(error).__name__}
        if isinstance(error, (RuntimeError, ValueError)):
            result["reason"] = str(error)[:500]
    run.event(operation, result)
    print(json.dumps(run.data))


if __name__ == "__main__":
    main()
