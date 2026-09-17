"""The n8n-selected source snapshot shared by preparation, clones and image builds."""
import hashlib
import json
import re

IMAGE_REPO = "safrano9999/fedora45-ai-safrano9999"


def snapshot_id(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_snapshot(snapshot):
    if (snapshot.get("schema_version") != 1 or snapshot.get("source_policy") != "latest-resolved-once"
            or not re.fullmatch(r"[0-9a-f]{40}", snapshot.get("source_commit", ""))
            or not re.fullmatch(r"fedora45-ai-[a-z0-9-]+", snapshot.get("target", ""))):
        raise ValueError("Invalid shared source snapshot")
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, list) or not 0 < len(repositories) <= 128:
        raise ValueError("Missing snapshot repositories")
    entries = {}
    for entry in repositories:
        repo = entry.get("repository", "")
        if (not re.fullmatch(r"safrano9999/[A-Za-z0-9][A-Za-z0-9._-]*", repo)
                or repo == IMAGE_REPO or repo.lower() in entries
                or not re.fullmatch(r"[0-9a-f]{40}", entry.get("commit", ""))):
            raise ValueError("Invalid snapshot repository")
        entries[repo.lower()] = entry
        for asset in entry.get("runtime_assets", []):
            if (type(asset.get("id")) is not int or asset["id"] <= 0
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", asset.get("name", ""))
                    or not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", ""))):
                raise ValueError("Invalid snapshot runtime asset")
        if "release" in entry:
            release = entry["release"]
            if (not re.fullmatch(r"[0-9a-f]{64}", release.get("sha256", ""))
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release.get("asset", ""))
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", release.get("ref", ""))
                    or release["ref"] != entry.get("ref")):
                raise ValueError("Invalid snapshot release")
    for name in ("openclaw", "hermes"):
        version = snapshot.get("versions", {}).get(name, {})
        if any(not re.fullmatch(r"\d+\.\d+\.\d+", version.get(k, "")) for k in ("current", "latest")):
            raise ValueError("Missing snapshot versions")
    if snapshot.get("upgrade_safrano9999"):
        plan = snapshot.get("build_plan", {})
        chain = snapshot.get("chain", [])
        keys = {"fedora45-ai-core-pre": "fedora45_core_pre", "fedora45-ai-core": "fedora45_core",
                "fedora45-ai-base": "fedora45_base", "fedora45-ai-kachelmann": "fedora45_kachelmann",
                "fedora45-ai-safrano9999": "fedora45_safrano", "fedora45-ai-safrano9999-full": "fedora45_safrano_full"}
        if (plan.get("schema_version") != 1 or type(plan.get("required")) is not bool
                or not chain or any(layer not in keys for layer in chain)
                or plan.get("target") != snapshot["target"] or type(plan.get("cascade")) is not bool
                or plan.get("baseline") != "published-latest-images"):
            raise ValueError("Invalid Safrano upgrade plan")
        changed = [layer for layer in chain if any(c.get("image") == layer for c in plan.get("changes", []))]
        start = changed[0] if changed else None
        if (plan["required"] != bool(start) or plan.get("start_image") != start
                or plan.get("start_key") != keys.get(start)
                or plan["cascade"] != bool(start and start != snapshot["target"])):
            raise ValueError("Upgrade must start at the earliest changed image")
        baseline = plan.get("baseline_images", [])
        if ([b.get("image") for b in baseline] != chain or any(
                not re.fullmatch(r"[0-9a-f]{40}", b.get("revision", "")) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", b.get("digest", "")) for b in baseline)):
            raise ValueError("Missing published image baseline")
    return entries


def build_inputs(snapshot, core):
    entries = validate_snapshot(snapshot)
    try:
        inputs = {name.upper() + "_EPHEMERAL_COMMIT": entries[f"safrano9999/{name}-ephemeral"]["commit"]
                  for name in ("openclaw", "hermes")}
        version = snapshot["versions"]["openclaw"]["latest"]
        patch = entries["safrano9999/openclaw-deterministic-latest"]["release"]
        note = entries[core["NOTE_REPOSITORY"].lower()]["release"]
        if patch["asset"] != f"openclaw-{version}-deterministic.tar.gz" or note["asset"] != core["NOTE_RELEASE_ASSET"]:
            raise ValueError("Snapshot asset differs from target runtime")
        inputs.update(OPENCLAW_VERSION=version, OPENCLAW_DETERMINISTIC_TAG=patch["ref"],
                      OPENCLAW_DETERMINISTIC_SHA256=patch["sha256"], NOTE_RELEASE_TAG=note["ref"],
                      NOTE_RELEASE_SHA256=note["sha256"])
        return inputs
    except KeyError as error:
        raise ValueError("Incomplete shared source snapshot") from error


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repository")
    parser.add_argument("--verify-core", type=Path)
    parser.add_argument("--runtime-assets", action="store_true")
    parser.add_argument("--runtime-asset-hash")
    args = parser.parse_args()
    snapshot = json.loads(args.manifest.read_text())
    entries = validate_snapshot(snapshot)
    if args.repository:
        if args.repository.lower() not in entries:
            raise SystemExit("Repository missing from prepared source snapshot: " + args.repository)
        entry = entries[args.repository.lower()]
        if args.runtime_assets:
            if not entry.get("runtime_assets"):
                raise SystemExit("Runtime assets missing from snapshot")
            print(json.dumps({"assets": entry["runtime_assets"]}))
        elif args.runtime_asset_hash:
            assets = [a for a in entry.get("runtime_assets", []) if a["name"] == args.runtime_asset_hash]
            if len(assets) != 1:
                raise SystemExit("Runtime asset missing or duplicated in snapshot")
            print(assets[0]["sha256"])
        else:
            print(entry["commit"])
    if args.verify_core:
        core = dict(line.split("=", 1) for line in args.verify_core.read_text().splitlines()
                    if re.match(r"^[A-Z_][A-Z0-9_]*=", line))
        if any(core.get(k) != v for k, v in build_inputs(snapshot, core).items()):
            raise SystemExit("Core build inputs differ from the prepared source snapshot")
