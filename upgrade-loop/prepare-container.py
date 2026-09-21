#!/usr/bin/env python3
"""Prepare build inputs on GitHub Actions; never build, pull or deploy an image."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
from source_snapshot import (build_inputs as snapshot_build_inputs,
                             openclaw_override, update_openclaw_source, snapshot_id, validate_snapshot)
import build_dependencies
COMPONENTS = {"openclaw": "openclaw/openclaw", "hermes": "NousResearch/hermes-agent"}


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, text=True, **kwargs)


def github(path):
    return json.loads(run(["gh", "api", path], capture_output=True).stdout)


def version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Expected a numeric stable release version")
    return tuple(map(int, value.split(".")))


def current_versions(text):
    result = {}
    for name in COMPONENTS:
        matches = re.findall(r"^ARG " + name.upper() + r"_VERSION=(\S+)$", text, re.M)
        if len(matches) != 1:
            raise ValueError("Expected exactly one version pin for " + name)
        version(matches[0])
        result[name] = matches[0]
    return result


def release_version(name, release):
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Expected a published stable release")
    if name == "hermes":
        match = re.match(r"Hermes Agent v(\d+\.\d+\.\d+)(?:\s|$)", release["name"])
        if not match:
            raise ValueError("Unrecognized Hermes release name")
        result = match[1]
    else:
        result = release["tag_name"].removeprefix("v")
    version(result)
    return result


def select_versions(current, latest):
    for name in COMPONENTS:
        if version(latest[name]) < version(current[name]):
            raise ValueError("Upstream release is older than the current pin")
    return {name: {"current": current[name], "latest": latest[name]} for name in COMPONENTS}


def has_update(versions):
    return any(v["current"] != v["latest"] for v in versions.values())


def exact_commit(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected an exact Git commit")
    return value


def replace_pin(text, key, value, prefix=""):
    result, count = re.subn(r"^" + re.escape(prefix + key) + r"=.*$",
                            prefix + key + "=" + value, text, flags=re.M)
    if count != 1:
        raise ValueError("Expected exactly one pin: " + key)
    return result


def release_asset(release, name):
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Expected a published stable Safrano release")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release.get("tag_name", "")):
        raise ValueError("Invalid Safrano release tag")
    assets = [a for a in release.get("assets", []) if a["name"] == name]
    if len(assets) != 1:
        raise ValueError("Missing or ambiguous release asset: " + name)
    digest = assets[0].get("digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Published asset has no verified checksum")
    return digest[7:]


def patch_inputs(target, core, releases, latest=None):
    asset_name = f"openclaw-{target}-deterministic.tar.gz"
    matches = [(r, a) for r in releases if not r["draft"] and not r["prerelease"]
               for a in r["assets"] if a["name"] == asset_name]
    # A rolling latest release may contain artifacts for several OpenClaw versions.
    # Prefer its current compatible payload; otherwise use the newest compatible release.
    matches.sort(key=lambda pair: pair[1].get("updated_at", pair[0].get("published_at", "")), reverse=True)
    if latest and any(a["name"] == asset_name for a in latest.get("assets", [])):
        release = latest
    elif matches:
        release = matches[0][0]
    else:
        release = None
    if release is None:
        raise ValueError("APPROVAL_REQUIRED: no published deterministic artifact for target OpenClaw")
    digest = release_asset(release, asset_name)
    if release["tag_name"] == "latest":
        # Keep a fixed release alias for this run when it contains exactly latest's bytes.
        release = next((r for r, a in matches if r["tag_name"] != "latest"
                        and a.get("digest") == "sha256:" + digest), release)
    if (release["tag_name"] == core.get("OPENCLAW_DETERMINISTIC_TAG") != "latest"
            and core.get("OPENCLAW_DETERMINISTIC_SHA256")
            and core.get("OPENCLAW_DETERMINISTIC_SHA256") != digest):
        raise ValueError("Published deterministic artifact differs from the existing pin")
    return {"OPENCLAW_VERSION": target, "OPENCLAW_DETERMINISTIC_TAG": release["tag_name"],
            "OPENCLAW_DETERMINISTIC_SHA256": digest}


def note_inputs(core, latest):
    digest = release_asset(latest, core["NOTE_RELEASE_ASSET"])
    if (latest["tag_name"] == core.get("NOTE_RELEASE_TAG") != "latest"
            and core.get("NOTE_RELEASE_SHA256") != digest):
        raise ValueError("Published NOTE artifact differs from the existing pin")
    return {"NOTE_RELEASE_TAG": latest["tag_name"], "NOTE_RELEASE_SHA256": digest}


def prepare(report, validate_only=False, snapshot=None, force_prepare=False, upgrade_safrano9999=False, upgrade_build_deps=False):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Preparation must run on GitHub Actions")
    foundation = REPO / "fedora45-ai-core-pre/Containerfile"
    core_path = REPO / "fedora45-ai-core/build.conf"
    before_foundation, before_core = foundation.read_text(), core_path.read_text()
    current = current_versions(before_foundation)
    if snapshot is not None:
        validate_snapshot(snapshot)
        actual = run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True).stdout.strip()
        if actual != snapshot["source_commit"] or current != {n: snapshot["versions"][n]["current"] for n in COMPONENTS}:
            raise ValueError("Checkout/version baseline differs from the n8n snapshot")
        latest = {n: snapshot["versions"][n]["latest"] for n in COMPONENTS}
        report.update(source_snapshot=snapshot, source_snapshot_id=snapshot_id(snapshot))
    else:
        latest = {name: release_version(name, github(f"repos/{repo}/releases/latest"))
                  for name, repo in COMPONENTS.items()}
    versions = select_versions(current, latest)
    report.update(versions=versions, update=has_update(versions), validate_only=validate_only)
    if upgrade_safrano9999 != bool((snapshot or {}).get("upgrade_safrano9999")):
        raise ValueError("Explicit upgrade-safrano9999 option and snapshot must agree")
    source_update = upgrade_safrano9999 and snapshot["build_plan"]["required"]
    if upgrade_safrano9999:
        report.update(upgrade_safrano9999=True, build_plan=snapshot["build_plan"])
    if upgrade_build_deps != bool((snapshot or {}).get('upgrade_build_deps')):
        raise ValueError('Explicit upgrade-build-deps option and snapshot must agree')
    dependency_files = {}
    dependency_update = False
    if upgrade_build_deps:
        dependencies, dependency_files = build_dependencies.plan(REPO)
        baseline = snapshot['build_dependencies_baseline']
        selected_hash = build_dependencies.hashlib.sha256(dependency_files[build_dependencies.POLICY].encode()).hexdigest()
        dependencies['published_policy_sha256'] = baseline['policy_sha256']
        dependencies['selected_policy_sha256'] = selected_hash
        dependencies['required'] = dependencies['required'] or selected_hash != baseline['policy_sha256']
        dependency_update = dependencies['required']
        report.update(upgrade_build_deps=True, build_dependencies=dependencies)
    # Source commits open the gate only with the explicit Safrano option.
    if not report["update"] and not source_update and not dependency_update and not validate_only and not force_prepare:
        report["status"] = "NO_UPDATE"
        return
    core = dict(line.split("=", 1) for line in before_core.splitlines()
                if re.match(r"^[A-Z_][A-Z0-9_]*=", line))
    if snapshot is not None:
        inputs = snapshot_build_inputs(snapshot, core)
        commits = {name: inputs[name.upper() + "_EPHEMERAL_COMMIT"] for name in COMPONENTS}
    else:
        commits = {name: exact_commit(github(f"repos/safrano9999/{name}-ephemeral/commits/HEAD")["sha"])
                   for name in COMPONENTS}
        report["ephemeral_commits"] = commits
        inputs = patch_inputs(latest["openclaw"], core,
                              github("repos/safrano9999/openclaw-deterministic-latest/releases?per_page=100"),
                              github("repos/safrano9999/openclaw-deterministic-latest/releases/latest"))
        inputs.update(note_inputs(core, github(f"repos/{core['NOTE_REPOSITORY']}/releases/latest")))
        inputs.update({name.upper() + "_EPHEMERAL_COMMIT": sha for name, sha in commits.items()})
    report["ephemeral_commits"] = commits
    override = openclaw_override(before_foundation, latest["openclaw"])
    if snapshot is not None:
        selected = snapshot.get("openclaw_source")
        if selected is None or selected.get("override_commit") != override:
            raise ValueError("Snapshot differs from the Core-pre source override")
        expected_upstream = override or selected["commit"]
    else:
        expected_upstream = override or exact_commit(github(
            f"repos/openclaw/openclaw/commits/v{latest['openclaw']}")["sha"])
    inputs["OPENCLAW_UPSTREAM_SHA"] = expected_upstream
    report["build_inputs"] = inputs
    report["source_policy"] = "latest-resolved-once" if snapshot is not None else "latest-resolved-per-preparation"
    spec = importlib.util.spec_from_file_location("prepare_runtime", ROOT / "prepare-runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    report["checks"] = {}
    with tempfile.TemporaryDirectory(prefix="container-preparation-") as raw:
        scratch = Path(raw)
        targets = {name: runtime.prepare(name, latest[name], scratch / "runtimes",
                    **({"source_inputs": inputs} if name == "openclaw" else {})) for name in COMPONENTS}
        # Install the selected Hermes release's declared runtime dependencies on the runner.
        run([sys.executable, "-m", "pip", "install", "-e", targets["hermes"]["source"]])
        for name in COMPONENTS:
            generator = scratch / (name + "-ephemeral")
            run(["git", "init", "-q", generator])
            run(["git", "-C", generator, "fetch", "--depth=1", "--no-tags",
                 f"https://github.com/safrano9999/{name}-ephemeral.git", commits[name]])
            run(["git", "-C", generator, "checkout", "--detach", "FETCH_HEAD"])
            target = targets[name]
            module = generator if name == "openclaw" else generator / "image/runtime/usr/local/lib/hermes-ephemeral"
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join([str(module), target["source"]]),
                       UPGRADE_TARGET_SOURCE=target["source"], UPGRADE_TARGET_VERSION=latest[name])
            if name == "openclaw":
                env["UPGRADE_TARGET_PACKAGE"] = target["package"]
                env["UPGRADE_TARGET_CODEX_PACKAGE"] = target["codex_package"]
                env["UPGRADE_TARGET_UPSTREAM_SHA"] = target["upstream_commit"]
            run([sys.executable, ROOT / f"probes/{name}_config.py"], env=env, timeout=300)
            if run(["git", "-C", generator, "status", "--porcelain"], capture_output=True).stdout:
                raise ValueError("Generator changed during compatibility check")
            report["checks"][name] = {"status": "PASS", "version": latest[name],
                                       "generator_commit": commits[name], "upstream_commit": target["upstream_commit"]}
        run(["git", "apply", "--check", REPO / "fedora45-ai-core-pre/build/hermes-nous-api-key.patch"],
            cwd=targets["hermes"]["source"])
        report["checks"]["hermes_patch"] = {"status": "PASS"}
    # A base-image refresh and upstream version changes share this file. Apply
    # version changes on the refreshed foundation so neither update is lost.
    after_foundation = dependency_files.pop(foundation.relative_to(REPO), before_foundation)
    after_core = before_core
    after_foundation = update_openclaw_source(after_foundation, latest["openclaw"])
    for name in ("hermes",):
        after_foundation = replace_pin(after_foundation, name.upper() + "_VERSION", latest[name], "ARG ")
    for key, value in inputs.items():
        after_core = replace_pin(after_core, key, value)
    if validate_only:
        report["status"] = "VALIDATED_ONLY"
        return
    if run(["git", "status", "--porcelain"], capture_output=True, cwd=REPO).stdout:
        raise ValueError("Repository changed during preparation")
    foundation.write_text(after_foundation)
    core_path.write_text(after_core)
    changed = [foundation, core_path]
    for path, content in dependency_files.items():
        (REPO / path).write_text(content)
        changed.append(REPO / path)
    if snapshot is not None:
        snapshot_path = ROOT / "prepared-sources.json"
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n")
        changed.append(snapshot_path)
    run(["git", "diff", "--check"], cwd=REPO)
    run(["git", "add", "--", *changed], cwd=REPO)
    run(["git", "-c", "user.name=Fedora45 preparation", "-c", "user.email=actions@users.noreply.github.com",
         "commit", "-m", "Prepare Fedora45 versions, tested Ephemeral commits and selected build dependencies"], cwd=REPO)
    commit = exact_commit(run(["git", "rev-parse", "HEAD"], capture_output=True, cwd=REPO).stdout.strip())
    # A concurrent main change fails the push. Never force-push or mix in untested inputs.
    run(["git", "push", "origin", "HEAD:main"], cwd=REPO)
    report.update(status="READY_FOR_BUILD", build_commit=commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-prepare", action="store_true", help="Explicit operator rebuild; never used by the automatic n8n version gate")
    parser.add_argument("--upgrade-safrano9999", action="store_true", help="Prepare changed Safrano inputs from the earliest affected published image")
    parser.add_argument("--upgrade-build-deps", action="store_true", help="Refresh allowlisted third-party pins and configured base-image release lines")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-snapshot", type=Path, required=True)
    args = parser.parse_args()
    report = {"schema_version": 1, "status": "BLOCKED", "scope": "container-preparation",
              "build_started": False, "image_pulled": False, "container_restarted": False,
              "actions_url": f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"}
    code = 0
    try:
        prepare(report, args.validate_only, json.loads(args.source_snapshot.read_text()), args.force_prepare, args.upgrade_safrano9999, args.upgrade_build_deps)
    except Exception as error:
        report.update(status="BLOCKED", reason=str(error))
        code = 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("## Container preparation\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
