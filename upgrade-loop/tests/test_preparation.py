import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("prepare_container", Path(__file__).parents[1] / "prepare-container.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


class PreparationTests(unittest.TestCase):
    def test_only_upstream_versions_open_gate(self):
        pins = {"openclaw": "2026.9.4", "hermes": "0.21.2"}
        self.assertFalse(prep.has_update(prep.select_versions(pins, pins)))
        for component, latest in (("openclaw", "2026.9.5"), ("hermes", "0.21.3")):
            with self.subTest(component=component):
                self.assertTrue(prep.has_update(prep.select_versions(pins, {**pins, component: latest})))

    def test_downgrade_and_malformed_versions_block(self):
        with self.assertRaises(ValueError):
            prep.select_versions({"openclaw": "2026.9.4", "hermes": "0.21.2"},
                                 {"openclaw": "2026.9.3", "hermes": "0.21.3"})
        for value in ("", "v0.21.2", "0.21.3-rc1", "0.21.2; echo oops"):
            with self.assertRaises(ValueError):
                prep.version(value)

    def test_no_update_does_not_resolve_generators_or_run_commands(self):
        pins = prep.current_versions((prep.REPO / "fedora45-ai-core-pre/Containerfile").read_text())
        replies = [{"tag_name": "v" + pins["openclaw"], "draft": False, "prerelease": False},
                   {"name": "Hermes Agent v" + pins["hermes"] + " release", "draft": False, "prerelease": False}]
        report = {}
        with patch.dict(prep.os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(prep, "github", side_effect=replies) as gh, patch.object(prep, "run") as run:
            prep.prepare(report)
        self.assertEqual(report["status"], "NO_UPDATE")
        self.assertEqual(gh.call_count, 2)
        run.assert_not_called()

    def test_missing_or_replaced_patch_blocks(self):
        core = {"OPENCLAW_VERSION": "2026.9.4", "OPENCLAW_DETERMINISTIC_TAG": "2026.9.4-deterministic.2", "OPENCLAW_DETERMINISTIC_SHA256": "a" * 64}
        release = {"tag_name": core["OPENCLAW_DETERMINISTIC_TAG"], "draft": False, "prerelease": False,
                   "assets": [{"name": "openclaw-2026.9.4-deterministic.tar.gz", "digest": "sha256:" + "a" * 64}]}
        self.assertEqual(prep.patch_inputs("2026.9.4", core, [release])["OPENCLAW_DETERMINISTIC_SHA256"], "a" * 64)
        with self.assertRaises(ValueError):
            prep.patch_inputs("2026.9.5", core, [release])
        release["assets"][0]["digest"] = "sha256:" + "b" * 64
        with self.assertRaises(ValueError):
            prep.patch_inputs("2026.9.4", core, [release])

    def test_replace_pins_requires_exact_single_match(self):
        with self.assertRaises(ValueError):
            prep.replace_pin("OTHER=1\n", "OPENCLAW_EPHEMERAL_COMMIT", "a" * 40)
        with self.assertRaises(ValueError):
            prep.replace_pin("ARG HERMES_VERSION=1.0.0\nARG HERMES_VERSION=1.0.1\n", "HERMES_VERSION", "1.0.2", "ARG ")


if __name__ == "__main__":
    unittest.main()
