import os
from pathlib import Path
import subprocess
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "image/runtime/usr/local/bin/fedora45-ai-init-hooks"


class InitHooksTests(unittest.TestCase):
    def test_failed_hook_does_not_skip_remaining_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "10-fail.sh").write_text("exit 1\n")
            (root / "20-next.sh").write_text("echo subsequent-hook-ran\n")
            result = subprocess.run(["bash", str(RUNNER)],
                env={**os.environ, "FEDORA45_AI_INIT_HOOKS_DIR": directory},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("subsequent-hook-ran", result.stdout)
