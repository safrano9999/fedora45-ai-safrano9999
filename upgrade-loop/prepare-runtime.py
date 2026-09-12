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


def prepare(component, version, cache):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a numeric release version")
    repository = {"openclaw": "openclaw/openclaw", "hermes": "NousResearch/hermes-agent"}[component]
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
    commit = github(f"repos/{repository}/commits/{tag}")["sha"]
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
    if component == "openclaw":
        manifest_path = cache / f"openclaw-{version}-release-manifest.json"
        download(f"https://github.com/{repository}/releases/download/{tag}/{manifest_path.name}", manifest_path)
        package = json.loads(manifest_path.read_text())["publicationArtifacts"]["npmPreflight"]["preparedBundle"]["package"]
        if package["version"] != version or package["sourceSha"] != commit:
            raise ValueError("OpenClaw package and source release identities disagree")
        archive = cache / f"openclaw-{version}.tgz"
        download(f"https://registry.npmjs.org/openclaw/-/{archive.name}", archive, package["sha256"])
        prefix = cache / f"openclaw-{version}-runtime"
        installed = prefix / "node_modules/openclaw"
        if not (installed / "package.json").exists():
            subprocess.run(["npm", "install", "--prefix", str(prefix), "--ignore-scripts", "--omit=dev",
                            "--no-audit", "--no-fund", "--prefer-offline", "--maxsockets=3", str(archive)], check=True)
        if json.loads((installed / "package.json").read_text())["version"] != version:
            raise ValueError("Installed OpenClaw version mismatch")
        result.update(package=str(installed), artifact_sha256=package["sha256"])
    temporary = evidence.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(evidence)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("openclaw", "hermes"))
    parser.add_argument("version")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/fedora-upgrade-loop")
    args = parser.parse_args()
    print(json.dumps(prepare(args.component, args.version, args.cache)))
