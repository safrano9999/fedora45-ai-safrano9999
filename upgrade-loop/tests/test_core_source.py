import copy
import json
import importlib.util
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from source_snapshot import openclaw_override, update_openclaw_source, validate_snapshot


class CoreSourceTests(unittest.TestCase):
    def test_same_stable_keeps_manual_override_and_new_release_clears_it(self):
        original = 'ARG OPENCLAW_VERSION=2026.9.5\nARG OPENCLAW_UPSTREAM_SHA=' + 'a' * 40 + '\nARG OTHER=keep\n'
        self.assertEqual(update_openclaw_source(original, '2026.9.5'), original)
        updated = update_openclaw_source(original, '2026.9.6')
        self.assertEqual(updated, 'ARG OPENCLAW_VERSION=2026.9.6\nARG OPENCLAW_UPSTREAM_SHA=\nARG OTHER=keep\n')
        self.assertEqual(openclaw_override(updated, '2026.9.6'), '')
        with self.assertRaises(ValueError): update_openclaw_source(original, '2026.9.4')

    def test_runtime_never_falls_back_to_unpatched_npm(self):
        spec = importlib.util.spec_from_file_location('runtime', ROOT / 'prepare-runtime.py')
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        with tempfile.TemporaryDirectory() as raw, self.assertRaisesRegex(ValueError, 'Core-pre'):
            runtime.prepare('openclaw', '2026.9.5', Path(raw))

    def test_python_and_n8n_reject_snapshot_that_changes_manual_override(self):
        source = {'version':'2026.9.5','override_commit':'a'*40,'commit':'a'*40}
        snapshot = {'schema_version':1,'source_policy':'latest-resolved-once','source_commit':'c'*40,
            'target':'fedora45-ai-core','repositories':[{'repository':'safrano9999/FIXTURE','ref':'HEAD','commit':'d'*40}],
            'versions':{'openclaw':{'current':'2026.9.5','latest':'2026.9.5'},'hermes':{'current':'0.21.3','latest':'0.21.3'}},
            'openclaw_source':source}
        validate_snapshot(snapshot)
        script = "const m=require(process.argv[1]);const s=JSON.parse(process.argv[2]);m.validateSnapshot(s,m.snapshotId(s));"
        for mismatch in ('commit','version','next_release'):
            bad = copy.deepcopy(snapshot)
            if mismatch == 'commit': bad['openclaw_source']['commit'] = 'b'*40
            elif mismatch == 'version': bad['openclaw_source']['version'] = '2026.9.6'
            else:
                bad['versions']['openclaw']['latest'] = '2026.9.6'
                bad['openclaw_source']['version'] = '2026.9.6'
            with self.subTest(mismatch=mismatch), self.assertRaises(ValueError): validate_snapshot(bad)
            result = subprocess.run(['node','-e',script,str(ROOT/'n8n-sources/source-snapshot.js'),json.dumps(bad)],capture_output=True)
            self.assertNotEqual(result.returncode,0)


if __name__ == '__main__': unittest.main()
