#!/usr/bin/env python3
"""Checks for the optional runtime CA certificate importer."""

from __future__ import annotations

import hashlib
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "image/runtime/usr/local/libexec/import-ca-certificates"
UNIT = ROOT / "image/runtime/etc/systemd/system/ca-certificates-import.service"


class CertificateImportTests(unittest.TestCase):
    def make_ca(self, directory: pathlib.Path) -> pathlib.Path:
        certificate = directory / "test-ca.crt"
        key = directory / "test-ca.key"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=Runtime test CA", "-days", "1",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-keyout", str(key), "-out", str(certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return certificate

    def make_deceptive_leaf(self, directory: pathlib.Path) -> pathlib.Path:
        certificate = directory / "deceptive-leaf.crt"
        key = directory / "deceptive-leaf.key"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=CA:TRUE", "-days", "1",
                "-addext", "basicConstraints=critical,CA:FALSE",
                "-keyout", str(key), "-out", str(certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return certificate

    def run_importer(
        self, source: pathlib.Path, anchors: pathlib.Path, updater: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(IMPORTER), str(source), str(anchors), str(updater)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_valid_ca_is_canonicalized_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            anchors = root / "anchors"
            source.mkdir()
            anchors.mkdir()
            certificate = self.make_ca(source)
            (source / "duplicate.pem").write_bytes(
                certificate.read_bytes() + (source / "test-ca.key").read_bytes()
            )
            updater = root / "update"
            updater.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            updater.chmod(0o755)

            result = self.run_importer(source, anchors, updater)
            self.assertEqual(result.returncode, 0, result.stderr)
            imported = list(anchors.glob("runtime-extra-ca-*.crt"))
            self.assertEqual(len(imported), 1)
            der = subprocess.check_output(
                ["openssl", "x509", "-in", str(certificate), "-outform", "DER"]
            )
            self.assertEqual(imported[0].stem, f"runtime-extra-ca-{hashlib.sha256(der).hexdigest()}")
            self.assertEqual(stat.S_IMODE(imported[0].stat().st_mode), 0o644)
            self.assertNotIn(b"PRIVATE KEY", imported[0].read_bytes())

    def test_invalid_ca_fails_before_existing_anchor_is_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            anchors = root / "anchors"
            source.mkdir()
            anchors.mkdir()
            (source / "broken.crt").write_text("not a certificate\n", encoding="utf-8")
            existing = anchors / "runtime-extra-ca-existing.crt"
            existing.write_text("keep\n", encoding="utf-8")
            updater = root / "update"
            updater.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            updater.chmod(0o755)

            result = self.run_importer(source, anchors, updater)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep\n")

    def test_ca_true_in_subject_cannot_bypass_basic_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            anchors = root / "anchors"
            source.mkdir()
            anchors.mkdir()
            self.make_deceptive_leaf(source)
            updater = root / "update"
            updater.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            updater.chmod(0o755)

            result = self.run_importer(source, anchors, updater)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(anchors.iterdir()), [])

    @unittest.skipIf(os.geteuid() == 0, "root can read mode-000 directories")
    def test_unreadable_source_does_not_clear_existing_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            anchors = root / "anchors"
            source.mkdir()
            anchors.mkdir()
            existing = anchors / "runtime-extra-ca-existing.crt"
            existing.write_text("keep\n", encoding="utf-8")
            updater = root / "update"
            updater.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            updater.chmod(0o755)
            source.chmod(0)
            try:
                result = self.run_importer(source, anchors, updater)
            finally:
                source.chmod(0o700)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep\n")

    def test_empty_source_removes_only_importer_owned_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "source"
            anchors = root / "anchors"
            source.mkdir()
            anchors.mkdir()
            stale = anchors / "runtime-extra-ca-stale.crt"
            retained = anchors / "build-time-ca.crt"
            stale.write_text("stale\n", encoding="utf-8")
            retained.write_text("retained\n", encoding="utf-8")
            updater = root / "update"
            updater.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            updater.chmod(0o755)

            result = self.run_importer(source, anchors, updater)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual(retained.read_text(encoding="utf-8"), "retained\n")

    def test_unit_is_optional_and_ordered_before_persistainer(self) -> None:
        text = UNIT.read_text(encoding="utf-8")
        self.assertIn("ConditionPathIsDirectory=/usr/local/share/ca-certificates-extra", text)
        self.assertIn("Before=network.target persistainer.service", text)
        self.assertIn("Type=oneshot", text)


if __name__ == "__main__":
    unittest.main()
