"""Exercise package replacement with real npm and isolated image fixtures."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

BUILD = Path(__file__).resolve().parents[1] / "build"
VERSION = "2026.9.5"
UPSTREAM = "1c4ee884396e509cc63abe87669e279e4e7d313c"
RELEASE = VERSION + "-deterministic.3"


def write_tar(path, files, link=None):
    with tarfile.open(path, "w:gz") as archive:
        for name, value in files.items():
            data = value.encode() if isinstance(value, str) else value
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith("openclaw.mjs") else 0o644
            archive.addfile(info, io.BytesIO(data))
        if link:
            info = tarfile.TarInfo(link[0])
            info.type = tarfile.SYMTYPE
            info.linkname = link[1]
            archive.addfile(info)


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.prefix = self.root / "image/usr/local"
        self.package = self.prefix / "lib/node_modules/openclaw"
        self.package.mkdir(parents=True)
        (self.package / "package.json").write_text(json.dumps({"name": "openclaw", "version": VERSION}))
        (self.package / "dist").mkdir()
        (self.package / "dist/old.js").write_text("old runtime")
        (self.package / "node_modules").mkdir()
        (self.package / "node_modules/old-dependency.txt").write_text("old dependency")
        self.archive = self.root / "deterministic.tar.gz"
        self.env = {
            **os.environ,
            "PATH": str(self.prefix / "bin") + os.pathsep + os.environ["PATH"],
            "npm_config_prefix": str(self.prefix),
            "npm_config_cache": str(self.root / "npm-cache"),
            "npm_config_offline": "true", "npm_config_audit": "false", "npm_config_fund": "false",
            "PLUGIN_RECEIPT": str(self.root / "plugin-install.json"),
        }

    def make_archive(self, *, link=None, version=VERSION, commit=UPSTREAM, tampered=False):
        dependency = self.root / "dependency.tgz"
        write_tar(dependency, {
            "package/package.json": json.dumps({"name": "fixture-dependency", "version": "2.0.0", "main": "index.js"}),
            "package/index.js": "module.exports = 'new dependency';\n",
        })
        runtime = self.root / "openclaw.tgz"
        write_tar(runtime, {
            "package/package.json": json.dumps({
                "name": "openclaw", "version": version, "type": "module",
                "bin": {"openclaw": "openclaw.mjs"},
                "exports": {"./plugin-sdk/new": "./dist/new.js"},
                "dependencies": {"fixture-dependency": "file:" + str(dependency)},
            }),
            "package/openclaw.mjs": "#!/usr/bin/env node\nimport fs from 'node:fs';\nfs.writeFileSync(process.env.PLUGIN_RECEIPT, JSON.stringify(process.argv.slice(2)));\n",
            "package/dist/index.js": "export const patched = true;\n",
            "package/dist/new.js": "export const addedExport = true;\n",
            "package/dist/control-ui/index.html": "UI",
            "package/dist/deterministic-gateway-replies.txt": "dummy reply",
        }, link=link)
        artifacts = {"openclaw.tgz": runtime.read_bytes()}
        self.manifest = {"schemaVersion": 1, "version": VERSION, "displayVersion": VERSION + "-patched",
                         "upstreamCommit": commit, "releaseTag": RELEASE,
                         "artifacts": {name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()}}
        if tampered:
            artifacts["openclaw.tgz"] += b"modified"
        write_tar(self.archive, {**artifacts, "manifest.json": json.dumps(self.manifest)})

    def run_installer(self, *args):
        return subprocess.run([sys.executable, str(BUILD / "install-deterministic.py"),
            str(self.archive), str(self.package), VERSION, UPSTREAM, RELEASE, *args],
            text=True, capture_output=True, env=self.env)

    def assert_original_intact(self):
        self.assertEqual((self.package / "dist/old.js").read_text(), "old runtime")
        self.assertFalse((self.root / "plugin-install.json").exists())

    def test_same_version_main_package_replaces_dist_dependencies_and_exports(self):
        self.make_archive()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        package = json.loads((self.package / "package.json").read_text())
        self.assertEqual(package["version"], VERSION)
        self.assertIn("./plugin-sdk/new", package["exports"])
        dependency = json.loads((self.package / "node_modules/fixture-dependency/package.json").read_text())
        self.assertEqual(dependency["version"], "2.0.0")
        self.assertFalse((self.package / "dist/old.js").exists())
        self.assertFalse((self.package / "node_modules/old-dependency.txt").exists())
        self.assertFalse((self.root / "plugin-install.json").exists())
        self.assertEqual(json.loads((self.package / "deterministic-build.json").read_text()), self.manifest)

    def test_hollow_bedrock_link_is_rejected_before_install(self):
        self.make_archive(link=("package/dist/extensions/amazon-bedrock/node_modules/@aws-sdk/client-bedrock-runtime",
                                "../../../../../node_modules/.pnpm/missing"))
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe npm package entry", result.stderr)
        self.assert_original_intact()

    def test_mismatched_runtime_version_is_rejected_before_install(self):
        self.make_archive(version="2026.9.6")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version/identity mismatch", result.stderr)
        self.assert_original_intact()

    def test_wrong_source_or_tampered_runtime_is_rejected_before_install(self):
        for options in ({"commit": "a" * 40}, {"tampered": True}):
            with self.subTest(options=options):
                self.make_archive(**options)
                self.assertNotEqual(self.run_installer().returncode, 0)
                self.assert_original_intact()

    def test_ephemeral_install_leaves_patched_runtime_and_provenance_untouched(self):
        provenance = self.package / "deterministic-build.json"
        provenance.write_text(json.dumps({"version": VERSION, "upstreamCommit": UPSTREAM}))
        source = self.root / "ephemeral"
        for relative in ("openclaw_ephemeral/__init__.py", "openclaw-ephemeral.py", "runtime/yolo.sh",
                         "image/runtime/etc/systemd/system/openclaw-config.service"):
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        result = subprocess.run([sys.executable, str(BUILD / "install-ephemeral.py"),
            str(source), str(self.root / "image")], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_original_intact()
        self.assertEqual(json.loads(provenance.read_text())["upstreamCommit"], UPSTREAM)
        launcher = self.prefix / "bin/openclaw-ephemeral.py"
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
