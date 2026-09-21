#!/usr/bin/env python3
"""Prepare an isolated, version-pinned upstream runtime for compatibility checks."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile


def github(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def download(url, destination, sha256=None):
    if not destination.exists():
        temporary = destination.with_suffix(destination.suffix + ".part")
        subprocess.run(["curl", "-fsSL", "--retry", "2", "--max-time", "180",
                        url, "-o", str(temporary)], check=True)
        temporary.replace(destination)
    if sha256:
        with destination.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != sha256:
            raise ValueError(f"Downloaded artifact checksum mismatch: {destination.name}")


def extract(archive, destination, root_name):
    if not destination.exists():
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix="extract-") as raw:
            with tarfile.open(archive) as source:
                source.extractall(raw, filter="data")
            (Path(raw) / root_name).rename(destination)


def prepare(component, version, cache, source_inputs=None):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a numeric release version")
    if component == "openclaw" and source_inputs is None:
        raise ValueError("OpenClaw requires the Core-pre selected Deterministic runtime")
    repository = {"openclaw": "openclaw/openclaw", "hermes": "NousResearch/hermes-agent"}[component]
    if source_inputs is not None:
        if component != "openclaw" or source_inputs["OPENCLAW_VERSION"] != version:
            raise ValueError("Selected source inputs differ from the requested runtime")
        for key, length in (("OPENCLAW_UPSTREAM_SHA", 40), ("OPENCLAW_DETERMINISTIC_SHA256", 64)):
            if not re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", source_inputs[key]):
                raise ValueError("Invalid selected runtime pin: " + key)
        cache = cache / (source_inputs["OPENCLAW_UPSTREAM_SHA"] + "-" + source_inputs["OPENCLAW_DETERMINISTIC_SHA256"][:16])
    cache.mkdir(parents=True, exist_ok=True)
    evidence = cache / f"{component}-{version}-inputs.json"
    if evidence.exists():
        result = json.loads(evidence.read_text())
        if result["component"] != component or result["version"] != version:
            raise ValueError("Cached runtime identity mismatch")
        if not Path(result["source"]).is_dir() or (component == "openclaw" and
                not (Path(result["package"]) / "package.json").is_file()):
            raise ValueError("Cached runtime is incomplete")
        return result
    if component == "hermes":
        releases = github(f"repos/{repository}/releases?per_page=100")
        matching = [r for r in releases if not r["draft"] and not r["prerelease"]
                    and r["name"].startswith(f"Hermes Agent v{version} ")]
        if len(matching) != 1:
            raise ValueError("Expected exactly one matching Hermes release")
        tag = matching[0]["tag_name"]
    else:
        tag = f"v{version}"
    commit = source_inputs["OPENCLAW_UPSTREAM_SHA"] if source_inputs else github(f"repos/{repository}/commits/{tag}")["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Upstream release did not resolve to a commit")
    archive = cache / (f"hermes-{version}.tar.gz" if component == "hermes"
                       else f"openclaw-{version}-source.tar.gz")
    download(f"https://codeload.github.com/{repository}/tar.gz/{commit}", archive)
    root_name = f"{repository.split('/')[1]}-{commit}"
    source = cache / f"{component}-{version}-source" / root_name
    source.parent.mkdir(exist_ok=True)
    extract(archive, source, root_name)
    result = {"component": component, "version": version, "repository": repository,
              "tag": tag, "upstream_commit": commit, "source": str(source)}
    if component == "openclaw" and source_inputs:
        release = source_inputs["OPENCLAW_DETERMINISTIC_TAG"]
        bundle = cache / f"openclaw-{version}-deterministic.tar.gz"
        download(f"https://github.com/safrano9999/openclaw-deterministic-latest/releases/download/{release}/{bundle.name}",
                 bundle, source_inputs["OPENCLAW_DETERMINISTIC_SHA256"])
        packed = cache / "packed"
        packed.mkdir(exist_ok=True)
        with tarfile.open(bundle) as payload:
            members = payload.getmembers()
            if (len(members) != 3 or {m.name for m in members} != {"manifest.json", "openclaw.tgz", "codex.tgz"}
                    or not all(m.isfile() for m in members)):
                raise ValueError("Expected a complete Deterministic runtime bundle")
            payload.extractall(packed, filter="data")
        manifest = json.loads((packed / "manifest.json").read_text())
        if (manifest.get("schemaVersion") != 1 or manifest.get("version") != version
                or manifest.get("upstreamCommit") != commit or manifest.get("releaseTag") != release):
            raise ValueError("Runtime bundle differs from Core-pre source selection")
        for filename in ("openclaw.tgz", "codex.tgz"):
            if hashlib.sha256((packed / filename).read_bytes()).hexdigest() != manifest["artifacts"][filename]:
                raise ValueError("Invalid selected package checksum")
        prefix = cache / "selected-runtime"
        subprocess.run(["npm", "install", "--prefix", str(prefix), "--ignore-scripts", "--omit=dev",
                        "--no-audit", "--no-fund", str(packed / "openclaw.tgz"), str(packed / "codex.tgz")], check=True)
        installed = prefix / "node_modules/openclaw"
        codex = prefix / "node_modules/@openclaw/codex"
        for directory in (source, installed, codex):
            if json.loads((directory / "package.json").read_text())["version"] != version:
                raise ValueError("Selected source and runtime versions differ")
        (installed / "deterministic-build.json").write_text(json.dumps(manifest) + "\n")
        result.update(package=str(installed), codex_package=str(codex),
                      artifact_sha256=source_inputs["OPENCLAW_DETERMINISTIC_SHA256"])
    temporary = evidence.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(evidence)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("openclaw", "hermes"))
    parser.add_argument("version")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/fedora-upgrade-loop")
    parser.add_argument("--source-inputs", type=Path)
    args = parser.parse_args()
    if args.component == "openclaw" and args.source_inputs is None:
        parser.error("OpenClaw tests require --source-inputs selected from Core-pre")
    selected = json.loads(args.source_inputs.read_text()) if args.source_inputs else None
    print(json.dumps(prepare(args.component, args.version, args.cache, selected)))
