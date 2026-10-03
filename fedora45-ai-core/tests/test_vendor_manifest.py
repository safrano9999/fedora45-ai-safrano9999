"""The workflow and staged source allowlists must describe the same payload."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class VendorManifestTests(unittest.TestCase):
    def test_every_staged_ephemeral_module_is_in_the_action_allowlist(self):
        prepare = (ROOT / 'fedora45-ai-core/build/prepare-build-context.sh').read_text()
        block = re.search(r'^ephemeral_files=\(\n(.*?)^\)', prepare, re.M | re.S)
        self.assertIsNotNone(block)
        staged = set(block.group(1).split())
        workflow = (ROOT / '.github/workflows/fedora45-ai-core-image.yml').read_text()
        declared = set(re.findall(r'^\s+build/vendor/openclaw-ephemeral/(\S+\.py)$', workflow, re.M))
        declared = {name for name in declared if not name.startswith('image/')}
        self.assertEqual(staged, declared)
        self.assertIn('openclaw_ephemeral/voice.py', staged)
