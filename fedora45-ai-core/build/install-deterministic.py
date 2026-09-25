#!/usr/bin/env python3
"""Install OpenClaw from the pinned Deterministic source."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


def extract_package(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive) as payload:
        names = set()
        for member in payload.getmembers():
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or name.parts[:1] != ("package",)
                    or member.name in names or not (member.isfile() or member.isdir())):
                raise ValueError(f"Unsafe npm package entry: {member.name}")
            names.add(member.name)
        # npm publishes regular files; never import source-checkout dependency links.
        payload.extractall(destination, filter="data")
    return destination / "package"


def validate_package(root: Path, name: str, version: str) -> None:
    package = json.loads((root / "package.json").read_text())
    if package.get("name") != name or package.get("version") != version:
        raise ValueError(f"Package version/identity mismatch: expected {name}@{version}")
    if name == "openclaw":
        required = ("openclaw.mjs", "dist/control-ui/index.html", "dist/deterministic-gateway-replies.txt")
        if not any((root / "dist" / entry).is_file() for entry in ("index.js", "index.mjs")):
            raise ValueError("Missing OpenClaw runtime entry point")
    for relative in required:
        target = (root / relative).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError(f"Missing or external runtime asset: {name}/{relative}")


def install(archive: Path, package_root: Path, version: str, upstream_sha: str, release: str) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("The update version must remain a stable numeric version")
    installed = json.loads((package_root / "package.json").read_text())["version"]
    if installed != version:
        raise ValueError(f"OpenClaw version mismatch: expected {version}, found {installed}")
    with tempfile.TemporaryDirectory(prefix="openclaw-deterministic-") as scratch:
        staged = Path(scratch)
        expected = {"manifest.json", "openclaw.tgz"}
        with tarfile.open(archive) as payload:
            members = payload.getmembers()
            if ({m.name for m in members} != expected or len(members) != len(expected)
                    or not all(m.isfile() for m in members)):
                raise ValueError("Expected a complete Deterministic runtime bundle, not a dist overlay")
            payload.extractall(staged, filter="data")
        manifest = json.loads((staged / "manifest.json").read_text())
        if (manifest.get("schemaVersion") != 1 or manifest.get("version") != version
                or manifest.get("upstreamCommit") != upstream_sha
                or manifest.get("releaseTag") != release
                or manifest.get("displayVersion") != f"{version}-patched"):
            raise ValueError("Deterministic provenance differs from the Containerfile pins")
        artifact = staged / "openclaw.tgz"
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != manifest["artifacts"].get("openclaw.tgz"):
            raise ValueError("Deterministic artifact checksum mismatch: openclaw.tgz")
        root = extract_package(artifact, staged / "openclaw")
        validate_package(root, "openclaw", version)

        # npm installs the matching manifest, exports, bundled workspaces and
        # dependencies. A same-version local tarball still replaces the package.
        subprocess.run([
            "npm", "install", "--global", "--include=optional",
            "--allow-scripts=openclaw,@google/genai,koffi,tree-sitter-bash,protobufjs",
            str(staged / "openclaw.tgz"),
        ], check=True)
        if json.loads((package_root / "package.json").read_text())["version"] != version:
            raise ValueError("Installed OpenClaw does not match the update baseline")
        # Display/provenance are separate from package.json and dist/build-info.json.
        (package_root / "deterministic-build.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("package_root", type=Path)
    parser.add_argument("version")
    parser.add_argument("upstream_sha")
    parser.add_argument("release")
    args = parser.parse_args()
    install(args.archive, args.package_root.resolve(), args.version, args.upstream_sha, args.release)
