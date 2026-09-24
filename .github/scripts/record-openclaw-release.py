#!/usr/bin/env python3
# Source of truth: SCRIPTS/githubactions. Generated copies are overwritten.
"""Record only the exact successful component release; no moving-tag fallback."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, "upgrade-loop")
from source_snapshot import update_openclaw_source

foundation_text = Path("fedora45-ai-core-pre/Containerfile").read_text()
versions = re.findall(r"^ARG OPENCLAW_VERSION=(\d+\.\d+\.\d+)$", foundation_text, re.M)
overrides = re.findall(r"^ARG OPENCLAW_UPSTREAM_SHA=((?:[0-9a-f]{40})?)$", foundation_text, re.M)
if len(versions) != 1 or len(overrides) != 1:
    raise ValueError("Invalid Core-pre source of truth")
source_version = versions[0]
source_upstream = overrides[0] or json.loads(subprocess.check_output([
    "gh", "api", f"repos/openclaw/openclaw/commits/v{source_version}",
], text=True))["sha"]
target_version = os.environ.get("TARGET_OPENCLAW_VERSION", "").strip()
if target_version:
    if not re.fullmatch(r"\d+\.\d+\.\d+", target_version):
        raise ValueError("Invalid requested OpenClaw target version")
    if tuple(map(int, target_version.split("."))) < tuple(map(int, source_version.split("."))):
        raise ValueError("Cannot downgrade the Core-pre version")
    version = target_version
    upstream = os.environ.get("OPENCLAW_UPSTREAM_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", upstream):
        raise ValueError("Missing upstream commit for targeted runtime")
else:
    version = source_version
    upstream = source_upstream
    if (os.environ.get("OPENCLAW_VERSION", version) != version
            or os.environ.get("OPENCLAW_UPSTREAM_SHA", upstream) != upstream):
        raise ValueError("Runtime selection differs from Core-pre")
tag = os.environ["RELEASE_TAG"]
if not re.fullmatch(re.escape(version) + r"-deterministic\.\d+", tag):
    raise ValueError("Expected a version-pinned Deterministic release")
release = json.loads(subprocess.check_output([
    "gh", "api", "repos/safrano9999/openclaw-deterministic-latest/releases/tags/" + tag,
], text=True))
assets = [a for a in release["assets"] if a["name"] == f"openclaw-{version}-deterministic.tar.gz"]
if release["draft"] or release["prerelease"] or len(assets) != 1:
    raise ValueError("Missing verified runtime release")
digest = assets[0].get("digest", "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    raise ValueError("Missing runtime checksum")
path = Path("fedora45-ai-core/build.conf")
text = path.read_text()
for key, value in {"OPENCLAW_VERSION": version, "OPENCLAW_UPSTREAM_SHA": upstream,
                   "OPENCLAW_DETERMINISTIC_TAG": tag, "OPENCLAW_DETERMINISTIC_SHA256": digest[7:]}.items():
    text, count = re.subn(r"^" + key + r"=.*$", key + "=" + value, text, flags=re.M)
    if count != 1:
        raise ValueError("Missing or duplicate Core pin: " + key)
text = text.replace("# The runtime bundle has not been built yet. Preparation resolves its published\n# SHA-256 from the exact release asset and verifies it before installation.\n", "")
foundation = Path("fedora45-ai-core-pre/Containerfile")
updated = update_openclaw_source(foundation.read_text(), version)
path.write_text(text)
foundation.write_text(updated)
