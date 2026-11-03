#!/usr/bin/env python3
# Source of truth: SCRIPTS/githubactions. Generated copies are overwritten.
"""Resolve native tool downloads on Actions without changing release channels."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request


def read(url):
    headers = {"User-Agent": "fedora45-third-party-refresh"}
    if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180) as response:
        return response.read()


def document(url):
    return json.loads(read(url))


def github(path):
    return document("https://api.github.com/" + path)


def sha256(url):
    return hashlib.sha256(read(url)).hexdigest()


def asset(repository, tag, filename):
    release = github(f"repos/{repository}/releases/tags/{tag}")
    matches = [a for a in release["assets"] if a["name"] == filename]
    if len(matches) != 1:
        raise ValueError(f"Missing unique asset: {repository}/{tag}/{filename}")
    item = matches[0]
    digest = item.get("digest") or ""
    return digest[7:] if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else sha256(item["browser_download_url"])


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Download resolution runs only on GitHub Actions")
    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / "upgrade-loop/build-dependencies-whitelist.json").read_text())
    pins = {key: value for entry in policy["entries"] for key, value in entry["pins"].items()}
    node = next(row for row in document("https://nodejs.org/dist/index.json") if re.fullmatch(r"v26\.\d+\.\d+", row["version"]))
    node_version = node["version"].removeprefix("v")
    sums = dict((name, checksum) for checksum, name in re.findall(r"^([0-9a-f]{64})\s+(\S+)$", read(f"https://nodejs.org/dist/v{node_version}/SHASUMS256.txt").decode(), re.M))
    result = {"checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "openclaw_version": "2026.9.4", "node_version": node_version, "architectures": {}, "python_bootstrap": {}, "npm": {}}
    for arch, node_arch in (("amd64", "x64"), ("arm64", "arm64")):
        geth_url = f"https://gethstore.blob.core.windows.net/builds/geth-linux-{arch}-{pins['GETH_VERSION']}-{pins['GETH_COMMIT']}.tar.gz"
        result["architectures"][arch] = {
            "NODE_ARCH": node_arch,
            "NODE_SHA256": sums[f"node-v{node_version}-linux-{node_arch}.tar.xz"],
            "CLOUDFLARED_SHA256": asset("cloudflare/cloudflared", pins["CLOUDFLARED_VERSION"], f"cloudflared-linux-{arch}"),
            "WEBHOOK_SHA256": asset("adnanh/webhook", pins["WEBHOOK_VERSION"], f"webhook-linux-{arch}.tar.gz"),
            "LND_SHA256": asset("lightningnetwork/lnd", pins["LND_VERSION"], f"lnd-linux-{arch}-{pins['LND_VERSION']}.tar.gz"),
            "GETH_SHA256": sha256(geth_url),
        }
    for package in ("pip", "certifi"):
        doc = document(f"https://pypi.org/pypi/{package}/json")
        wheel = next(row for row in doc["urls"] if row["filename"].endswith("-py3-none-any.whl") and not row["yanked"])
        result["python_bootstrap"][package] = {"version": doc["info"]["version"], "url": wheel["url"], "sha256": wheel["digests"]["sha256"]}
    for package in ("opencode-ai", "@grinev/opencode-telegram-bot"):
        doc = document("https://registry.npmjs.org/" + urllib.parse.quote(package, safe="") + "/latest")
        result["npm"][package] = {"version": doc["version"], "integrity": doc["dist"]["integrity"]}
    hermes = github("repos/NousResearch/hermes-agent/commits/main")
    result["hermes"] = {"branch": "main", "commit": hermes["sha"], "date": hermes["commit"]["committer"]["date"]}
    path = root / "fedora45-ai-core-pre/build/architecture-inputs.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
