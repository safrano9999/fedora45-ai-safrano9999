"""Keep image tags immutable unless a fixed-tag rebuild is explicitly authorized."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/resolve-fedora-image-tag.sh"
IMAGE = "ghcr.io/safrano9999/fedora45-ai-base"


class TagReplacementTests(unittest.TestCase):
    def run_resolver(self, *args):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "gh"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' 2026.9.17.5\n")
            fake.chmod(0o755)
            return subprocess.run(
                ["bash", str(SCRIPT), IMAGE, *args], capture_output=True, text=True,
                env={**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]},
            )

    def test_existing_tag_is_rejected_by_default(self):
        result = self.run_resolver("2026.9.17.5")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)

    def test_explicit_replacement_keeps_exact_version_and_is_logged(self):
        result = self.run_resolver("2026.9.17.5", "true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2026.9.17.5\n")
        self.assertIn("Explicitly authorized replacement", result.stderr)

    def test_new_tag_does_not_require_replacement(self):
        result = self.run_resolver("2026.9.17.6")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "2026.9.17.6\n")

    def test_replacement_requires_explicit_fixed_tag(self):
        result = self.run_resolver("", "true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit fixed version", result.stderr)

    def test_invalid_boolean_is_rejected(self):
        result = self.run_resolver("2026.9.17.5", "yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("true or false", result.stderr)

    def test_all_six_workflows_default_to_protection_and_propagate_authorization(self):
        for stage in ("core-pre", "core", "base", "kachelmann", "safrano9999", "safrano9999-full"):
            with self.subTest(stage=stage):
                workflow = (ROOT / "workflows" / f"fedora45-ai-{stage}-image.yml").read_text()
                definition = workflow.split("      replace_existing:\n", 1)[1].split("      push_latest:", 1)[0]
                self.assertIn("        type: boolean\n        default: false\n", definition)
                self.assertIn('"$REQUESTED_TAG" "$REPLACE_EXISTING")', workflow)
                self.assertIn("REPLACE_EXISTING: ${{ inputs.replace_existing }}", workflow)
                if stage != "safrano9999-full":
                    self.assertIn('-f "replace_existing=$REPLACE_EXISTING"', workflow)
                    self.assertEqual(workflow.count("REPLACE_EXISTING: ${{ inputs.replace_existing }}"), 2)


if __name__ == "__main__":
    unittest.main()
