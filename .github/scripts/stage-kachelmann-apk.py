#!/usr/bin/env python3
# Source of truth: SCRIPTS/githubactions. Generated copies are overwritten.
"""Stage the verified KACHELMANN release APK in a Fedora build context."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from zipfile import BadZipFile, ZipFile, is_zipfile


REPOSITORY = "safrano9999/KACHELMANN"
RELEASE_TAG = re.compile(r"^20[0-9]{2}\.[0-9]+\.[0-9]+$")
VERSIONED_APK = re.compile(r"^kachelmann-(20[0-9]{2}\.[0-9]+\.[0-9]+)\.apk$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_APK_BYTES = 256 * 1024 * 1024


class StageError(RuntimeError):
    """Raised when a release asset cannot be staged safely."""


def run_text(arguments: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise StageError(f"Command failed ({' '.join(arguments)}): {detail}")
    return result.stdout.strip()


def gh_json(endpoint: str) -> dict[str, Any]:
    try:
        payload = json.loads(run_text(["gh", "api", endpoint]))
    except json.JSONDecodeError as error:
        raise StageError(f"GitHub returned invalid JSON for {endpoint}") from error
    if not isinstance(payload, dict):
        raise StageError(f"GitHub returned an invalid object for {endpoint}")
    return payload


def download_asset(repository: str, asset: dict[str, Any], target: Path) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int) or asset_id < 1:
        raise StageError("Release asset has no valid numeric ID")
    with target.open("wb") as stream:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--header",
                "Accept: application/octet-stream",
                f"repos/{repository}/releases/assets/{asset_id}",
            ],
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise StageError(f"Could not download release asset {asset_id}: {detail}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def github_digest(asset: dict[str, Any]) -> str:
    raw = asset.get("digest")
    if not isinstance(raw, str) or not raw.startswith("sha256:"):
        raise StageError(f"Release asset {asset.get('name')!r} has no SHA-256 digest")
    value = raw.removeprefix("sha256:").lower()
    if SHA256.fullmatch(value) is None:
        raise StageError(f"Release asset {asset.get('name')!r} has an invalid digest")
    return value


def validate_download(path: Path, asset: dict[str, Any], *, maximum: int) -> str:
    expected_size = asset.get("size")
    if not isinstance(expected_size, int) or not 1 <= expected_size <= maximum:
        raise StageError(f"Release asset {asset.get('name')!r} has an invalid size")
    if not path.is_file() or path.stat().st_size != expected_size:
        raise StageError(f"Downloaded size does not match {asset.get('name')!r}")
    actual = sha256_file(path)
    if actual != github_digest(asset):
        raise StageError(f"GitHub digest does not match {asset.get('name')!r}")
    return actual


def validate_sidecar(path: Path, apk_name: str) -> str:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except UnicodeError as error:
        raise StageError("APK checksum sidecar is not UTF-8") from error
    if len(lines) != 1:
        raise StageError("APK checksum sidecar must contain exactly one entry")
    match = re.fullmatch(r"([0-9A-Fa-f]{64})[ \t]+\*?([^\s/]+)", lines[0])
    if match is None or match.group(2) != apk_name:
        raise StageError("APK checksum sidecar has an invalid filename or format")
    return match.group(1).lower()


def validate_apk(path: Path) -> None:
    if not is_zipfile(path):
        raise StageError("Downloaded APK is not a ZIP archive")
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = {"AndroidManifest.xml", "classes.dex"} - names
            if missing:
                raise StageError(f"Downloaded APK is missing: {', '.join(sorted(missing))}")
            corrupt = archive.testzip()
            if corrupt is not None:
                raise StageError(f"Downloaded APK contains a corrupt member: {corrupt}")
    except BadZipFile as error:
        raise StageError("Downloaded APK is corrupt") from error


def release_assets(release: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if release.get("draft") is True or release.get("prerelease") is True:
        raise StageError("Latest KACHELMANN release is not a final release")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or RELEASE_TAG.fullmatch(tag) is None:
        raise StageError(f"Latest KACHELMANN release has an invalid tag: {tag!r}")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise StageError("Latest KACHELMANN release has no asset list")
    versioned = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and VERSIONED_APK.fullmatch(asset["name"]) is not None
    ]
    sidecars = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and VERSIONED_APK.fullmatch(asset["name"].removesuffix(".sha256")) is not None
        and asset["name"].endswith(".apk.sha256")
    ]
    if len(versioned) != 1:
        raise StageError("Latest release must contain exactly one versioned APK")
    apk_name = str(versioned[0]["name"])
    expected_sidecar = f"{apk_name}.sha256"
    if len(sidecars) != 1 or sidecars[0].get("name") != expected_sidecar:
        raise StageError("Latest release must contain exactly its matching APK checksum")
    return tag, versioned[0], sidecars[0]


def verify_checkout(destination: Path, repository: str, release_tag: str) -> str:
    destination = destination.resolve()
    if not destination.is_dir() or destination.is_symlink():
        raise StageError(f"KACHELMANN checkout is missing: {destination}")
    top = Path(run_text(["git", "rev-parse", "--show-toplevel"], cwd=destination)).resolve()
    if top != destination:
        raise StageError(f"Destination is not the KACHELMANN checkout root: {destination}")
    head = run_text(["git", "rev-parse", "HEAD"], cwd=destination).lower()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise StageError("KACHELMANN checkout has an invalid HEAD")
    comparison = gh_json(f"repos/{repository}/compare/{release_tag}...{head}")
    base = comparison.get("base_commit")
    merge_base = comparison.get("merge_base_commit")
    base_sha = base.get("sha", "").lower() if isinstance(base, dict) else ""
    merge_sha = merge_base.get("sha", "").lower() if isinstance(merge_base, dict) else ""
    if comparison.get("status") not in {"ahead", "identical"} or base_sha != merge_sha:
        raise StageError(
            f"Prepared KACHELMANN checkout {head} does not contain release {release_tag}"
        )
    return head


def atomic_stage(source: Path, destination: Path) -> Path:
    existing = sorted(
        path.name
        for path in destination.glob("kachelmann-*.apk")
        if path.is_file() or path.is_symlink()
    )
    unexpected = [name for name in existing if name != source.name]
    if unexpected:
        raise StageError(f"KACHELMANN checkout already contains other APKs: {', '.join(unexpected)}")
    descriptor, temporary_raw = tempfile.mkstemp(
        dir=destination,
        prefix=f".{source.name}.",
    )
    os.close(descriptor)
    temporary = Path(temporary_raw)
    target = destination / source.name
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(0o644)
        if sha256_file(temporary) != sha256_file(source):
            raise StageError("APK changed while it was copied into the build context")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def write_outputs(values: dict[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    for key, value in values.items():
        print(f"{key}={value}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--repository", default=REPOSITORY)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        if not os.environ.get("GH_TOKEN"):
            raise StageError("GH_TOKEN is required to read the KACHELMANN release")
        release = gh_json(f"repos/{arguments.repository}/releases/latest")
        tag, apk_asset, checksum_asset = release_assets(release)
        verify_checkout(arguments.destination, arguments.repository, tag)
        with tempfile.TemporaryDirectory(prefix="kachelmann-apk-") as raw_directory:
            directory = Path(raw_directory)
            apk = directory / str(apk_asset["name"])
            checksum = directory / str(checksum_asset["name"])
            download_asset(arguments.repository, apk_asset, apk)
            download_asset(arguments.repository, checksum_asset, checksum)
            apk_digest = validate_download(apk, apk_asset, maximum=MAX_APK_BYTES)
            validate_download(checksum, checksum_asset, maximum=4096)
            if validate_sidecar(checksum, apk.name) != apk_digest:
                raise StageError("APK checksum sidecar does not match the downloaded APK")
            validate_apk(apk)
            target = atomic_stage(apk, arguments.destination.resolve())
        if sha256_file(target) != apk_digest or target.stat().st_mode & 0o777 != 0o644:
            raise StageError("Staged APK failed its final mode or digest check")
        write_outputs(
            {
                "release_tag": tag,
                "apk_filename": target.name,
                "apk_sha256": apk_digest,
            }
        )
        return 0
    except (OSError, StageError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
