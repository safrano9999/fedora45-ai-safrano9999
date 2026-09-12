"""Exercise the generator through a selected upstream Hermes configuration API."""

import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import yaml


class Models(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        body = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class HermesCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ["UPGRADE_TARGET_SOURCE"])
        cls.expected_version = os.environ["UPGRADE_TARGET_VERSION"]
        cls.scratch = tempfile.TemporaryDirectory(prefix="hermes-compatibility-")
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.home = Path(cls.scratch.name)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Models)
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        values = {
            "HOME": str(cls.home),
            "HERMES_HOME": str(cls.home / ".hermes"),
            "HERMES_CONFIG_TEMPLATE": str(source / "cli-config.yaml.example"),
            "HERMES_MODEL": "fixture/model-a",
            "OPENAI_V1_PROVIDER": "fixture",
            "OPENAI_V1_URL": f"http://127.0.0.1:{server.server_port}",
            "OPENAI_V1_KEY": "fixture-model-secret",
            "OPENAI_V1_API_KEY_ALIAS": "FIXTURE_API_KEY",
            "FIXTURE_API_KEY": "fixture-model-secret",
            "MCP_SERVER_NAME": "general",
            "MCP_SERVER_URL": "https://general.example.test/mcp",
            "MCP_SERVER_BEARER": "fixture-general-secret",
            "MCP_ALLOW": "main",
            "MCP_SERVER_NAME_02": "mtg",
            "MCP_SERVER_URL_02": "https://mtg.example.test/mcp",
            "MCP_SERVER_BEARER_02": "fixture-mtg-secret",
            "MCP_ALLOW_02": "mtg",
        }
        cls.environment = patch.dict(os.environ, values, clear=True)
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        from hermes_ephemeral.configuration import rebuild_configuration

        cls.generated = rebuild_configuration(os.environ).config

    def test_exact_upstream_version(self):
        from hermes_cli import __version__

        self.assertEqual(__version__, self.expected_version)

    def test_upstream_validator_accepts_generated_configuration(self):
        from hermes_cli.config import validate_config_structure

        errors = [issue.message for issue in validate_config_structure(self.generated)
                  if issue.severity == "error"]
        self.assertEqual(errors, [])

    def test_validator_rejects_a_broken_provider_shape(self):
        from hermes_cli.config import validate_config_structure

        self.assertTrue(any(issue.severity == "error" for issue in
                            validate_config_structure({"custom_providers": {"bad": {}}})))

    def test_upstream_loads_the_selected_model_and_provider(self):
        from hermes_cli.config import load_config
        from hermes_cli.config_providers import get_compatible_custom_providers
        from agent.secret_scope import get_secret

        config = load_config()
        self.assertEqual(config["model"]["default"], "model-a")
        self.assertEqual(config["model"]["provider"], "fixture")
        provider, = get_compatible_custom_providers(config)
        self.assertEqual(provider["name"], "fixture")
        self.assertEqual(provider["key_env"], "OPENAI_V1_KEY")
        self.assertEqual(get_secret(provider["key_env"]), "fixture-model-secret")

    def test_upstream_mcp_loader_preserves_global_access_and_bearers(self):
        from tools.mcp_tool_config import _load_mcp_config

        servers = _load_mcp_config()
        self.assertEqual(set(servers), {"general", "mtg"})
        for name in servers:
            self.assertNotIn("agents", servers[name])
            self.assertEqual(servers[name]["headers"]["Authorization"],
                             f"Bearer fixture-{name}-secret")

    def test_generated_file_keeps_environment_references(self):
        path = self.home / ".hermes/config.yaml"
        config = yaml.safe_load(path.read_text())
        self.assertEqual(config["providers"]["fixture"]["key_env"], "OPENAI_V1_KEY")
        for name in ("general", "mtg"):
            self.assertIn("${MCP_SERVER_BEARER", self.generated["mcp_servers"][name]["headers"]["Authorization"])
        self.assertNotIn("fixture-model-secret", path.read_text())
        self.assertNotIn("fixture-mtg-secret", path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
