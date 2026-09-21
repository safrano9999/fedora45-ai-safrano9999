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

version = os.environ["OPENCLAW_VERSION"]
upstream = os.environ["OPENCLAW_UPSTREAM_SHA"]
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
