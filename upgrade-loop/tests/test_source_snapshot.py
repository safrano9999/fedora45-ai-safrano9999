import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from source_snapshot import build_inputs, snapshot_id, validate_snapshot


def snapshot(repository="safrano9999/FIXTURE", commit="a" * 40):
    return {"schema_version": 1, "source_policy": "latest-resolved-once", "source_commit": "c" * 40,
            "target": "fedora45-ai-safrano9999", "chain": [], "resolved_at": "2026-09-17T00:00:00Z",
            "versions": {"openclaw": {"current": "2026.9.4", "latest": "2026.9.4"},
                         "hermes": {"current": "0.21.2", "latest": "0.21.3"}},
            "repositories": [{"repository": repository, "ref": "HEAD", "commit": commit}]}


class SnapshotTests(unittest.TestCase):
    def test_n8n_and_python_use_the_same_identity_and_clone_keeps_selected_commit(self):
        selected = snapshot()
        script = "const m=require(process.argv[1]);const s=JSON.parse(process.argv[2]);m.validateSnapshot(s,m.snapshotId(s));console.log(JSON.stringify({id:m.snapshotId(s),clone:m.cloneManifest(s,'b'.repeat(40))}));"
        result = json.loads(subprocess.check_output(['node', '-e', script, str(ROOT/'n8n-sources/source-snapshot.js'), json.dumps(selected)], text=True))
        self.assertEqual(result['id'], snapshot_id(selected))
        self.assertEqual(result['clone']['repositories'][1]['commit'], 'a' * 40)
        self.assertEqual(result['clone']['repositories'][0]['commit'], 'b' * 40)
        for change in ('duplicate', 'bad_commit', 'foreign_owner'):
            bad = copy.deepcopy(selected)
            if change == 'duplicate': bad['repositories'].append(bad['repositories'][0])
            elif change == 'bad_commit': bad['repositories'][0]['commit'] = 'main'
            else: bad['repositories'][0]['repository'] = 'someone/FIXTURE'
            with self.assertRaises(ValueError): validate_snapshot(bad)

    def test_actual_build_git_helper_uses_snapshot_even_after_upstream_advances(self):
        helper = (ROOT.parent/'fedora45-ai-base/build/prepare-build-context.sh').read_text()
        function = 'sync_repository() {' + helper.split('sync_repository() {', 1)[1].split('\nvalidate_repository_roles()', 1)[0]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); upstream = root/'upstream'; sources = root/'sources'; sources.mkdir()
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git('init', '-q', str(upstream))
            git('-C', str(upstream), 'config', 'user.email', 'fixture@example.invalid')
            git('-C', str(upstream), 'config', 'user.name', 'Fixture')
            commits = []
            for value in ('selected', 'newer upstream'):
                (upstream/'payload').write_text(value)
                git('-C', str(upstream), 'add', 'payload'); git('-C', str(upstream), 'commit', '-qm', value)
                commits.append(git('-C', str(upstream), 'rev-parse', 'HEAD'))
            manifest = root/'prepared-sources.json'; manifest.write_text(json.dumps(snapshot(commit=commits[0])))
            shutil.copyfile(ROOT/'source_snapshot.py', root/'source_snapshot.py')
            config = root/'gitconfig'
            config.write_text(f'[url "file://{upstream}"]\n\tinsteadOf = https://github.com/safrano9999/FIXTURE\n[protocol "file"]\n\tallow = always\n')
            script = 'set -euo pipefail\nrepository_name(){ printf "%s" "${1%@*}"; }\nrepository_branch(){ :; }\n' + function + '\nsync_repository FIXTURE\n'
            env = {**os.environ, 'SOURCE_DIR': str(sources), 'OFFLINE': 'false', 'NO_CACHE': 'false',
                   'SAFRANO_SOURCE_MANIFEST': str(manifest), 'GIT_CONFIG_GLOBAL': str(config), 'GIT_CONFIG_NOSYSTEM': '1'}
            for _ in range(2):
                subprocess.run(['bash', '-c', script], cwd=root, env=env, check=True, capture_output=True, text=True)
                self.assertEqual((sources/'FIXTURE/payload').read_text(), 'selected')
                self.assertEqual(git('-C', str(sources/'FIXTURE'), 'rev-parse', 'HEAD'), commits[0])

    def test_prepare_consumes_n8n_snapshot_without_resolving_latest_again(self):
        spec = importlib.util.spec_from_file_location('snapshot_prep', ROOT/'prepare-container.py')
        prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
        selected = snapshot('safrano9999/openclaw-ephemeral')
        current = prep.current_versions((ROOT.parent/'fedora45-ai-core-pre/Containerfile').read_text())
        selected['versions'] = {k: {'current': v, 'latest': v} for k,v in current.items()}
        numbers = list(map(int, current['hermes'].split('.'))); numbers[-1] += 1
        selected['versions']['hermes']['latest'] = '.'.join(map(str,numbers))
        selected['repositories'] += [
            {'repository':'safrano9999/hermes-ephemeral','ref':'HEAD','commit':'b'*40},
            {'repository':'safrano9999/openclaw-deterministic-latest','ref':'patch','commit':'d'*40,
             'release':{'ref':'patch','asset':f"openclaw-{current['openclaw']}-deterministic.tar.gz",'sha256':'e'*64}},
            {'repository':'safrano9999/NOTE','ref':'note','commit':'f'*40,
             'release':{'ref':'note','asset':'note-latest.zip','sha256':'a'*64}}]
        report = {}
        with patch.dict(os.environ, {'GITHUB_ACTIONS':'true'}), patch.object(prep,'github',side_effect=AssertionError('must not resolve again')), patch.object(prep,'run',return_value=subprocess.CompletedProcess([],0,'c'*40+'\n')), patch.object(prep.importlib.util,'spec_from_file_location',side_effect=RuntimeError('inputs selected')):
            with self.assertRaisesRegex(RuntimeError,'inputs selected'): prep.prepare(report,snapshot=selected)
        self.assertEqual(report['source_snapshot_id'],snapshot_id(selected))
        self.assertEqual(report['build_inputs']['OPENCLAW_EPHEMERAL_COMMIT'],'a'*40)
        self.assertEqual(report['build_inputs']['HERMES_EPHEMERAL_COMMIT'],'b'*40)

    def test_explicit_source_upgrade_gate_and_earliest_stage_validation(self):
        spec = importlib.util.spec_from_file_location('source_upgrade_prep', ROOT/'prepare-container.py')
        prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
        selected = json.loads((ROOT/'prepared-sources.json').read_text())
        selected['source_commit'] = 'c' * 40
        pins = prep.current_versions((ROOT.parent/'fedora45-ai-core-pre/Containerfile').read_text())
        selected['versions'] = {n: {'current':v, 'latest':v} for n,v in pins.items()}
        selected['upgrade_safrano9999'] = True
        selected['build_plan'] = {'schema_version':1,'required':True,'start_image':'fedora45-ai-core',
            'start_key':'fedora45_core','cascade':True,'target':selected['target'],
            'baseline':'published-latest-images','baseline_images':[
                {'image':i,'revision':'a'*40,'digest':'sha256:'+'b'*64} for i in selected['chain']],
            'changes':[{'image':'fedora45-ai-core','reason':'source-changed'}]}
        validate_snapshot(selected)
        with patch.dict(os.environ, {'GITHUB_ACTIONS':'true'}), patch.object(prep,'github',side_effect=AssertionError('No second resolution')), patch.object(prep,'run',return_value=subprocess.CompletedProcess([],0,'c'*40+'\n')), patch.object(prep.importlib.util,'spec_from_file_location',side_effect=RuntimeError('compatibility tests reached')):
            with self.assertRaisesRegex(ValueError,'option and snapshot'): prep.prepare({},snapshot=selected)
            report={}
            with self.assertRaisesRegex(RuntimeError,'compatibility tests reached'):
                prep.prepare(report,snapshot=selected,upgrade_safrano9999=True)
            self.assertEqual(report['build_plan']['start_key'],'fedora45_core')
            selected['build_plan'].update(required=False,start_image=None,start_key=None,cascade=False,changes=[])
            report={};prep.prepare(report,snapshot=selected,upgrade_safrano9999=True)
            self.assertEqual(report['status'],'NO_UPDATE')
        selected['build_plan'].update(required=True,start_image='fedora45-ai-base',start_key='fedora45_base',changes=[{'image':'fedora45-ai-core'}])
        with self.assertRaisesRegex(ValueError,'earliest'): validate_snapshot(selected)

    def test_no_update_gate_precedes_snapshot_resolution(self):
        workflow=json.loads((ROOT/'n8n-fedora45-workflow.json').read_text())
        edges=workflow['connections']
        self.assertEqual(edges['Upstream update available?']['main'][1][0]['node'],'No update')
        self.assertEqual(edges['Upstream update available?']['main'][0][0]['node'],'Prepare request')
        self.assertEqual(edges['Prepare request']['main'][0][0]['node'],'Resolve latest sources once')
        self.assertEqual(edges['Resolve latest sources once']['main'][0][0]['node'],'Sources require preparation?')
        self.assertEqual(edges['Sources require preparation?']['main'][0][0]['node'],'Dispatch preparation Action')
        self.assertEqual(edges['Sources require preparation?']['main'][1][0]['node'],'No update')
        script="const {Fedora45Sources}=require(process.argv[1]);(async()=>{try{await Fedora45Sources.prototype.execute.call({getInputData:()=>[{json:{update:false}}],getNodeParameter:()=> 'resolve',getCredentials:()=>{throw Error('credential access')}});process.exit(1)}catch(e){if(!e.message.includes('upstream update'))throw e}})();"
        subprocess.run(['node','-e',script,str(ROOT/'n8n-sources/Fedora45Sources.node.js')],check=True,capture_output=True)


if __name__ == '__main__': unittest.main()
