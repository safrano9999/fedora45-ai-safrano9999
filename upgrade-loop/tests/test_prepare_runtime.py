import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("prepare_runtime", Path(__file__).parents[1] / "prepare-runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeBundleTests(unittest.TestCase):
    def prepare(self, alteration=None):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            version, commit = "2026.9.4", "a" * 40
            selected = {"OPENCLAW_VERSION": version, "OPENCLAW_UPSTREAM_SHA": commit,
                        "OPENCLAW_DETERMINISTIC_TAG": version + "-deterministic.4",
                        "OPENCLAW_DETERMINISTIC_SHA256": "b" * 64}
            payload = {name: name.encode() for name in ("openclaw.tgz", "brave.tgz", "mai-transcribe.tgz")}
            manifest = {"schemaVersion": 2, "version": version, "upstreamCommit": commit,
                        "releaseTag": selected["OPENCLAW_DETERMINISTIC_TAG"],
                        "artifacts": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
            if alteration:
                alteration(manifest, payload)
            payload["manifest.json"] = json.dumps(manifest).encode()

            def download(url, path, sha256=None):
                if sha256:
                    self.assertEqual(sha256, selected["OPENCLAW_DETERMINISTIC_SHA256"])
                    with tarfile.open(path, "w:gz") as target:
                        for name, data in payload.items():
                            info = tarfile.TarInfo(name); info.size = len(data)
                            target.addfile(info, io.BytesIO(data))

            def extract(archive, destination, name):
                destination.mkdir(parents=True)
                (destination / "package.json").write_text(json.dumps({"version": version}))

            def install(args, **kwargs):
                package = Path(args[args.index("--prefix") + 1]) / "node_modules/openclaw"
                package.mkdir(parents=True)
                (package / "package.json").write_text(json.dumps({"version": version}))

            with patch.object(runtime, "download", side_effect=download), patch.object(runtime, "extract", side_effect=extract), patch.object(runtime.subprocess, "run", side_effect=install) as npm:
                try:
                    result = runtime.prepare("openclaw", version, root, source_inputs=selected)
                except ValueError:
                    npm.assert_not_called()
                    raise
                npm.assert_called_once()
                self.assertEqual(result["version"], version)
                self.assertEqual(json.loads((Path(result["package"]) / "deterministic-build.json").read_text()), manifest)

    def test_complete_bundle_is_verified_before_install(self):
        self.prepare()

    def test_missing_plugin_rejected_before_install(self):
        with self.assertRaisesRegex(ValueError, "bundle"):
            self.prepare(lambda manifest, payload: payload.pop("brave.tgz"))

    def test_tampered_plugin_rejected_before_install(self):
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.prepare(lambda manifest, payload: payload.update({"mai-transcribe.tgz": b"changed"}))

    def test_wrong_schema_rejected_before_install(self):
        with self.assertRaisesRegex(ValueError, "selection"):
            self.prepare(lambda manifest, payload: manifest.update(schemaVersion=1))

    def test_incomplete_manifest_rejected_before_install(self):
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.prepare(lambda manifest, payload: manifest["artifacts"].pop("brave.tgz"))
