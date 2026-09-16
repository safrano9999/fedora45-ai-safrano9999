import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


adapter = module("citadel_adapter", "citadel-health.py")
steps = module("steps", "steps.py")
URL = "https://citadel.example.ts.net:10002"


class HealthAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def response(self, body=b"print('checker')\n"):
        response = Mock(status=200)
        response.geturl.return_value = URL + "/healthz/check.py"
        response.read.return_value = body
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_download_update_and_explicit_arguments(self):
        script = self.directory / "citadel-health-check.py"
        script.write_text("obsolete")
        receipt = {"schema_version": 1, "status": "PASS", "gates": {"citadel_self": True, "citadel_links": True},
                   "extensions": [{"id": "tailscale"}, {"id": "cloudflare"}]}
        with patch.object(adapter, "urlopen", return_value=self.response()), patch.object(adapter.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(receipt))) as execute:
            result = adapter.run(URL, self.directory, 90)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(script.read_text(), "print('checker')\n")
        self.assertEqual(execute.call_args.args[0][-3:], ["--extensions", "tailscale", "cloudflare"])
        self.assertEqual(len(result["checker"]["sha256"]), 64)

    def test_download_failure_does_not_execute_old_script(self):
        (self.directory / "citadel-health-check.py").write_text("obsolete")
        with patch.object(adapter, "urlopen", side_effect=OSError), patch.object(adapter.subprocess, "run") as execute:
            with self.assertRaises(OSError):
                adapter.run(URL, self.directory, 90)
        execute.assert_not_called()

    def test_redirect_and_syntax_error_do_not_execute(self):
        redirect = self.response()
        redirect.geturl.return_value = "https://login.example/"
        for response in [redirect, self.response(b"<html>login</html>")]:
            with patch.object(adapter, "urlopen", return_value=response), patch.object(adapter.subprocess, "run") as execute:
                with self.assertRaises((ValueError, SyntaxError)):
                    adapter.run(URL, self.directory, 90)
            execute.assert_not_called()

    def test_non_tailnet_url_rejected(self):
        with self.assertRaises(ValueError):
            adapter.run("http://localhost:8000", self.directory, 90)

    def run_object(self, statuses):
        run = object.__new__(steps.Run)
        run.data = {"results": {}}
        run.checks = Mock()
        run.checks.stage.side_effect = [{"status": s} for s in statuses]
        return run

    def test_external_failure_fails_health(self):
        run = self.run_object(["PASS", "FAIL"])
        self.assertEqual(run.perform("health")["status"], "FAIL")
        self.assertEqual([c.args[0] for c in run.checks.stage.call_args_list], ["health", "citadel-health"])

    def test_base_failure_skips_external(self):
        run = self.run_object(["FAIL"])
        self.assertEqual(run.perform("health")["citadel"]["status"], "NOT_TESTED")
        run.checks.stage.assert_called_once_with("health")

    def test_rollback_works_without_new_endpoint(self):
        run = self.run_object(["PASS"])
        self.assertEqual(run.perform("rollback-health")["status"], "PASS")
        run.checks.stage.assert_called_once_with("health")


if __name__ == "__main__":
    unittest.main()
