#!/usr/bin/env python3
"""Download Citadel's checker and execute it on the workflow's SSH host."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen


def run(url, directory, timeout):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".ts.net")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Citadel must be addressed through its HTTPS Tailscale URL")
    started = time.monotonic()
    source = url.rstrip("/") + "/healthz/check.py"
    with urlopen(source, timeout=min(10, timeout)) as response:
        if response.status != 200 or response.geturl() != source:
            raise ValueError("Checker download failed or redirected")
        content = response.read(256_001)
    if not content or len(content) > 256_000:
        raise ValueError("Invalid checker size")
    compile(content, "citadel-health-check.py", "exec")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "citadel-health-check.py"
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o600)
    temporary.replace(path)
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("Citadel health budget exhausted during download")
    process = subprocess.run(
        [sys.executable, str(path), "--url", url, "--extensions", "tailscale", "cloudflare"],
        capture_output=True, text=True, timeout=remaining,
    )
    result = json.loads(process.stdout)
    if result.get("schema_version") != 1 or result.get("status") not in ("PASS", "FAIL"):
        raise ValueError("Invalid checker result")
    if process.returncode:
        result["status"] = "FAIL"
    if sorted(e["id"] for e in result.get("extensions", [])) != ["cloudflare", "tailscale"]:
        raise ValueError("Checker did not report requested extensions")
    if result["status"] == "PASS" and not all(result.get("gates", {}).get(g) is True for g in ("citadel_self", "citadel_links")):
        raise ValueError("Passing checker result has incomplete gates")
    result["checker"] = {"source": source, "sha256": hashlib.sha256(content).hexdigest(), "path": str(path)}
    result["context"] = "workflow SSH host tailnet"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        result = run(args.url, args.directory, args.timeout)
    except Exception as error:
        result = {"status": "FAIL", "error": type(error).__name__, "detail": "Citadel checker download or execution failed; no cached fallback"}
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
