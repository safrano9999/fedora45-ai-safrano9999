from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OpenCodeRuntimeTests(unittest.TestCase):
    def test_server_and_bridge_use_actual_server_auth_not_an_api_key_gate(self):
        for name in ('opencode', 'opencode-telegram'):
            unit = (ROOT / f'image/runtime/etc/systemd/system/{name}.service').read_text()
            self.assertNotIn('OPENCODE_API_KEY', unit)
            self.assertIn('OPENCODE_SERVER_PASSWORD', unit)
            condition = next(line for line in unit.splitlines() if line.startswith('ExecCondition='))
            self.assertNotIn('PASSWORD', condition)
            self.assertIn('OPENCODE_START', condition)

    def test_citadel_waits_for_the_opencode_listener(self):
        unit = (ROOT / 'image/runtime/etc/systemd/system/opencode.service').read_text()
        self.assertIn('ExecStartPost=', unit)
        self.assertIn('fedora45-wait-ready', unit)
        ordering = (ROOT.parent / 'fedora45-ai-core/image/runtime/etc/systemd/system/citadel-scan.service.d/10-opencode.conf').read_text()
        self.assertIn('After=opencode-config.service opencode.service', ordering)
