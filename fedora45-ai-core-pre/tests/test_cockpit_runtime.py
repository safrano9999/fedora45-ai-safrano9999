"""Optional TLS selection; no host services or real credentials are used."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "image/runtime/usr/local/libexec/cockpit-prepare"


class CockpitTests(unittest.TestCase):
    def test_empty_mount_keeps_http_and_invalid_certificate_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            certs = root / "certs"
            certs.mkdir()
            script = root / "prepare"
            script.write_text(SCRIPT.read_text().replace("/etc/cockpit/ws-certs.d", str(certs)))
            env = {**os.environ, "RUNTIME_DIRECTORY": raw}
            run = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual((root / "mode.env").read_text(), "COCKPIT_TLS_ARGS=--no-tls\n")
            (root / "mode.env").unlink()
            (certs / "node.crt").write_text("not a certificate")
            run = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((root / "mode.env").exists())

    def test_tls_uses_native_backend_and_no_separate_oneshot(self):
        unit = (ROOT / "image/runtime/etc/systemd/system/cockpit.service").read_text()
        self.assertIn("ExecStartPre=/usr/local/libexec/cockpit-prepare", unit)
        self.assertIn("/usr/libexec/cockpit-tls", unit)
        self.assertIn("Requires=dbus.socket", unit)
        self.assertNotIn("Type=oneshot", unit)
        self.assertNotIn("tailscale serve reset", unit)
        containerfile = (ROOT / "Containerfile").read_text()
        self.assertIn("/usr/local/libexec/cockpit-prepare", containerfile)
        self.assertIn("systemd-sysusers /usr/lib/sysusers.d/cockpit-container.conf", containerfile)

    def test_workers_are_unprivileged_without_nested_mounts(self):
        for name in ("cockpit-wsinstance-http.service", "cockpit-wsinstance-https@.service"):
            dropin = (ROOT / "image/runtime/etc/systemd/system" / (name + ".d/10-container.conf")).read_text()
            self.assertIn("NoNewPrivileges=yes", dropin)
            self.assertIn("CapabilityBoundingSet=\n", dropin)
            self.assertIn("SystemCallFilter=~capset", dropin)
            self.assertIn("DynamicUser=no", dropin)
