"""A custom state path must retain Tailscale's certificate storage too."""
from pathlib import Path
import unittest


class TailscaledStateDirectoryTests(unittest.TestCase):
    def test_daemon_sets_the_directory_not_only_the_identity_file(self):
        runtime = Path(__file__).resolve().parents[1] / "image/runtime"
        unit = (runtime / "etc/systemd/system/tailscaled.service").read_text()
        self.assertIn("ExecStart=/usr/sbin/tailscaled --statedir=${TS_STATE_DIR}\n", unit)
        self.assertNotIn("--state=", unit)
        # --statedir defaults the identity file to tailscaled.state, as expected
        # by the auth/restore condition and bootstrap helper.
        self.assertIn('/tailscaled.state', unit)


if __name__ == "__main__":
    unittest.main()
