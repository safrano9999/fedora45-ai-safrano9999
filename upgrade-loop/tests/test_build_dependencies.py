import base64
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_dependencies as deps


class BuildDependencyTests(unittest.TestCase):
    def fixture(self, name='lnd'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for file in (deps.POLICY, deps.CONF):
            (root / file).parent.mkdir(parents=True, exist_ok=True)
        entry = next(e for e in json.loads((deps.ROOT / deps.POLICY).read_text())['entries'] if e['id'] == name)
        policy = {'schema_version': 1, 'image': 'fedora45-ai-core-pre', 'entries': [entry]}
        (root / deps.POLICY).write_text(json.dumps(policy, indent=2) + '\n')
        (root / deps.CONF).write_text('# keep\n' + '\n'.join(k + '=' + v for k, v in entry['pins'].items()) + '\nOTHER=untouched\n')
        return root, entry, policy

    def test_new_release_changes_version_and_hash_together_without_mutation(self):
        root, entry, _ = self.fixture()
        before = (root / deps.CONF).read_text()
        wanted = {'LND_VERSION': 'v0.22.0-beta', 'LND_SHA256': 'a' * 64}
        with patch.object(deps, 'resolve', return_value=wanted):
            report, files = deps.plan(root)
        self.assertTrue(report['required'])
        self.assertEqual(report['changes'][0]['before'], entry['pins'])
        self.assertEqual(json.loads(files[deps.POLICY])['entries'][0]['pins'], wanted)
        self.assertIn('OTHER=untouched', files[deps.CONF])
        self.assertIn('LND_SHA256=' + 'a' * 64, files[deps.CONF])
        self.assertEqual((root / deps.CONF).read_text(), before)

    def test_no_update_is_a_true_noop(self):
        root, entry, _ = self.fixture()
        with patch.object(deps, 'resolve', return_value=entry['pins']):
            report, files = deps.plan(root)
        self.assertFalse(report['required'])
        self.assertTrue(all((root / p).read_text() == value for p, value in files.items()))

    def test_duplicate_unknown_and_drift_fail_before_resolution(self):
        for kind in ('duplicate', 'unknown', 'drift'):
            root, entry, policy = self.fixture()
            if kind == 'duplicate': policy['entries'].append(copy.deepcopy(entry))
            elif kind == 'unknown': entry['repository'] = 'somebody/lnd'
            else: (root / deps.CONF).write_text('LND_VERSION=other\n')
            (root / deps.POLICY).write_text(json.dumps(policy))
            with patch.object(deps, 'resolve', return_value=entry['pins']), self.assertRaises(ValueError):
                deps.plan(root)

    def test_downgrades_same_version_replacements_and_rcs_fail(self):
        root, entry, _ = self.fixture()
        for wanted in ({**entry['pins'], 'LND_SHA256': 'b' * 64},
                       {**entry['pins'], 'LND_VERSION': 'v0.1.0-beta'},
                       {**entry['pins'], 'LND_VERSION': 'v0.22.0-beta.rc1'}):
            with patch.object(deps, 'resolve', return_value=wanted), self.assertRaises(ValueError):
                deps.plan(root)
        for tag in ('v0.22.0-beta.rc1', 'v1.0.0-nightly', 'v1.0.0-alpha.1'):
            with self.assertRaises(ValueError):
                deps.resolve(entry, get=lambda _: {'tag_name': tag, 'draft': False, 'prerelease': False})

    def test_regular_lnd_beta_and_release_line(self):
        _, entry, _ = self.fixture()
        release = lambda v: {'tag_name': v, 'draft': False, 'prerelease': False,
                            'assets': [{'name': 'lnd-linux-amd64-' + v + '.tar.gz', 'digest': 'sha256:' + 'a' * 64}]}
        entry['release_line'] = '0.21'
        selected = deps.resolve(entry, get=lambda _: [release('v0.22.0-beta'), release('v0.21.9-beta')])
        self.assertEqual(selected['LND_VERSION'], 'v0.21.9-beta')

    def test_solana_uses_stable_channel_instead_of_latest_release(self):
        _, entry, _ = self.fixture('solana')
        calls = []
        def get(endpoint):
            calls.append(endpoint)
            if '/contents/' in endpoint:
                return {'content': base64.b64encode(b'[workspace.package]\nversion="4.2.2"').decode()}
            self.assertTrue(endpoint.endswith('/releases/tags/v4.2.2'))
            return {'tag_name': 'v4.2.2', 'draft': False, 'prerelease': False,
                    'assets': [{'name': 'solana-release-x86_64-unknown-linux-gnu.tar.bz2', 'digest': 'sha256:' + 'a' * 64}]}
        selected = deps.resolve(entry, get=get, read=lambda _: 'commit: ' + 'b' * 40 + '\n')
        self.assertEqual(selected['SOLANA_VERSION'], 'v4.2.2')
        self.assertFalse(any('/latest' in path for path in calls))

    def test_all_saved_pins_are_emitted_and_consumed_without_network(self):
        emitted = subprocess.check_output([sys.executable, str(deps.ROOT / 'upgrade-loop/build_dependencies.py'), '--emit'], text=True)
        values = dict(line.split('=', 1) for line in emitted.splitlines())
        policy = json.loads((deps.ROOT / deps.POLICY).read_text())
        self.assertEqual(len(policy['entries']), 13)
        for entry in policy['entries']:
            self.assertEqual(entry['release_line'], 'stable')
            for key, value in entry['pins'].items(): self.assertEqual(values[key], value)
        containerfile = (deps.ROOT / 'fedora45-ai-core-pre/Containerfile').read_text()
        self.assertTrue(set(values) <= set(re.findall(r'^ARG ([A-Z0-9_]+)$', containerfile, re.M)))
        for key in values:
            if key.endswith('_SHA256'): self.assertIn('${' + key + '}', containerfile)
        self.assertNotIn('SOLANA_INSTALLER_MD5', containerfile)
        self.assertNotIn('/stable/', containerfile)
        old_from = subprocess.check_output(['git', 'show', 'HEAD:fedora45-ai-core-pre/Containerfile'], cwd=deps.ROOT, text=True).splitlines()[0]
        self.assertEqual(containerfile.splitlines()[0], old_from)

    def test_preparation_gate_uses_published_policy_and_checks_both_generators_when_needed(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('dependency_preparation', deps.ROOT / 'upgrade-loop/prepare-container.py')
        prep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prep)
        selected = json.loads((deps.ROOT / 'upgrade-loop/prepared-sources.json').read_text())
        selected.pop('upgrade_safrano9999', None)
        selected.pop('build_plan', None)
        pins = prep.current_versions((deps.ROOT / 'fedora45-ai-core-pre/Containerfile').read_text())
        selected['versions'] = {n: {'current': v, 'latest': v} for n, v in pins.items()}
        policy = (deps.ROOT / deps.POLICY).read_text()
        selected['upgrade_build_deps'] = True
        selected['build_dependencies_baseline'] = {'revision': 'a' * 40, 'digest': 'sha256:' + 'b' * 64,
            'policy_sha256': deps.hashlib.sha256(policy.encode()).hexdigest()}
        files = {deps.POLICY: policy}
        with patch.dict(prep.os.environ, {'GITHUB_ACTIONS': 'true'}), patch.object(prep, 'run', return_value=SimpleNamespace(stdout=selected['source_commit'])):
            with patch.object(deps, 'plan', return_value=({'required': False}, files)):
                report = {}
                prep.prepare(report, snapshot=selected, upgrade_build_deps=True)
                self.assertEqual(report['status'], 'NO_UPDATE')
            # Already prepared but not yet present in the published image also opens the gate.
            selected['build_dependencies_baseline']['policy_sha256'] = None
            with patch.object(deps, 'plan', return_value=({'required': False}, files)), \
                 patch.object(prep.importlib.util, 'spec_from_file_location', side_effect=RuntimeError('runtime checks reached')):
                report = {}
                with self.assertRaisesRegex(RuntimeError, 'runtime checks reached'):
                    prep.prepare(report, snapshot=selected, upgrade_build_deps=True)
                self.assertTrue(report['build_dependencies']['required'])
                self.assertEqual(set(report['ephemeral_commits']), {'openclaw', 'hermes'})


if __name__ == '__main__':
    unittest.main()
