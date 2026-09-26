import importlib.util
import copy
import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("prepare_container", Path(__file__).parents[1] / "prepare-container.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


class PreparationTests(unittest.TestCase):
    def test_base_and_upstream_version_changes_survive_the_same_preparation(self):
        with tempfile.TemporaryDirectory() as raw, ExitStack() as stack:
            root = Path(raw)
            foundation = root / 'fedora45-ai-core-pre/Containerfile'
            core = root / 'fedora45-ai-core/build.conf'
            foundation.parent.mkdir(); core.parent.mkdir(); (root / 'upgrade-loop').mkdir()
            before = 'FROM quay.io/fedora/fedora@sha256:' + 'a'*64 + ' AS ai-core-pre\nARG OPENCLAW_VERSION=2026.9.4\nARG OPENCLAW_UPSTREAM_SHA=\nARG HERMES_VERSION=0.21.2\n'
            foundation.write_text(before)
            inputs = {'OPENCLAW_UPSTREAM_SHA': 'a'*40, 'OPENCLAW_VERSION': '2026.9.5', 'OPENCLAW_EPHEMERAL_COMMIT': 'b'*40, 'HERMES_EPHEMERAL_COMMIT': 'c'*40}
            core.write_text(''.join(key + '=old\n' for key in inputs))
            selected_base = before.replace('@sha256:' + 'a'*64, ':45@sha256:' + 'd'*64)
            snapshot = {'source_commit': 'e'*40, 'upgrade_build_deps': True,
                        'openclaw_source': {'version':'2026.9.5','override_commit':'','commit':'a'*40},
                        'versions': {'openclaw': {'current': '2026.9.4', 'latest': '2026.9.5'},
                                     'hermes': {'current': '0.21.2', 'latest': '0.21.2'}},
                        'build_dependencies_baseline': {'policy_sha256': 'f'*64}}
            files = {foundation.relative_to(root): selected_base,
                     prep.build_dependencies.CONF: 'DEPENDENCY=new\n', prep.build_dependencies.POLICY: '{}\n'}
            runtime = SimpleNamespace(prepare=lambda name, version, path, **kwargs: {
                'source': str(root / name), 'package': str(root / name), 'upstream_commit': 'a'*40})
            def command(args, **kwargs):
                return SimpleNamespace(stdout='e'*40 if 'rev-parse' in args else '')
            stack.enter_context(patch.dict(prep.os.environ, {'GITHUB_ACTIONS': 'true'}))
            for target, name, value in [
                (prep, 'REPO', root), (prep, 'ROOT', root / 'upgrade-loop'),
                (prep, 'validate_snapshot', Mock()), (prep, 'snapshot_build_inputs', Mock(return_value=inputs)),
                (prep, 'run', Mock(side_effect=command)),
                (prep.build_dependencies, 'plan', Mock(return_value=({'required': True}, files))),
                (prep.importlib.util, 'spec_from_file_location', Mock(return_value=SimpleNamespace(loader=Mock()))),
                (prep.importlib.util, 'module_from_spec', Mock(return_value=runtime)),
            ]:
                stack.enter_context(patch.object(target, name, value))
            report = {}
            prep.prepare(report, snapshot=snapshot, upgrade_build_deps=True)
            self.assertEqual(report['status'], 'READY_FOR_BUILD')
            self.assertEqual(foundation.read_text(), selected_base.replace('2026.9.4', '2026.9.5'))
            self.assertIn('OPENCLAW_VERSION=2026.9.5', core.read_text())
            self.assertEqual((root / prep.build_dependencies.CONF).read_text(), 'DEPENDENCY=new\n')

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

    def test_new_patch_is_selected_even_when_openclaw_version_is_unchanged(self):
        core = {"OPENCLAW_VERSION": "2026.9.4", "OPENCLAW_DETERMINISTIC_TAG": "2026.9.4-deterministic.1", "OPENCLAW_DETERMINISTIC_SHA256": "a" * 64}
        newer = {"tag_name": "2026.9.4-deterministic.2", "draft": False, "prerelease": False,
                 "assets": [{"name": "openclaw-2026.9.4-deterministic.tar.gz", "digest": "sha256:" + "b" * 64}]}
        rolling = {**newer, "tag_name": "latest"}
        result = prep.patch_inputs("2026.9.4", core, [newer, rolling], rolling)
        self.assertEqual(result["OPENCLAW_DETERMINISTIC_TAG"], newer["tag_name"])
        self.assertEqual(result["OPENCLAW_DETERMINISTIC_SHA256"], "b" * 64)

    def test_rolling_latest_payload_wins_over_an_older_fixed_release(self):
        core = {"OPENCLAW_VERSION": "2026.9.4", "OPENCLAW_DETERMINISTIC_TAG": "2026.9.4-deterministic.1", "OPENCLAW_DETERMINISTIC_SHA256": "a" * 64}
        old = {"tag_name": core["OPENCLAW_DETERMINISTIC_TAG"], "draft": False, "prerelease": False,
               "assets": [{"name": "openclaw-2026.9.4-deterministic.tar.gz", "digest": "sha256:" + "a" * 64}]}
        rolling = copy.deepcopy(old)
        rolling["tag_name"] = "latest"
        rolling["assets"][0]["digest"] = "sha256:" + "b" * 64
        result = prep.patch_inputs("2026.9.4", core, [old, rolling], rolling)
        self.assertEqual(result["OPENCLAW_DETERMINISTIC_TAG"], "latest")
        self.assertEqual(result["OPENCLAW_DETERMINISTIC_SHA256"], "b" * 64)

    def test_latest_release_for_different_openclaw_falls_back_to_latest_compatible(self):
        compatible = {"tag_name": "2026.9.4-deterministic.2", "draft": False, "prerelease": False,
                      "assets": [{"name": "openclaw-2026.9.4-deterministic.tar.gz", "digest": "sha256:" + "b" * 64}]}
        unrelated = {"tag_name": "2026.9.5-deterministic.1", "draft": False, "prerelease": False, "assets": []}
        self.assertEqual(prep.patch_inputs("2026.9.4", {}, [unrelated, compatible], unrelated)["OPENCLAW_DETERMINISTIC_TAG"], compatible["tag_name"])

    def test_latest_note_updates_and_requires_verified_stable_asset(self):
        core = {"NOTE_RELEASE_TAG": "2026.7.36", "NOTE_RELEASE_ASSET": "note-latest.zip", "NOTE_RELEASE_SHA256": "a" * 64}
        latest = {"tag_name": "2026.8.4", "draft": False, "prerelease": False,
                  "assets": [{"name": "note-latest.zip", "digest": "sha256:" + "b" * 64}]}
        self.assertEqual(prep.note_inputs(core, latest), {"NOTE_RELEASE_TAG": "2026.8.4", "NOTE_RELEASE_SHA256": "b" * 64})
        for change in ({"assets": []}, {"draft": True}, {"prerelease": True},
                       {"tag_name": "bad;command"}, {"assets": [{"name": "note-latest.zip"}]},
                       {"tag_name": core["NOTE_RELEASE_TAG"]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prep.note_inputs(core, {**latest, **change})

    def test_replace_pins_requires_exact_single_match(self):
        with self.assertRaises(ValueError):
            prep.replace_pin("OTHER=1\n", "OPENCLAW_EPHEMERAL_COMMIT", "a" * 40)
        with self.assertRaises(ValueError):
            prep.replace_pin("ARG HERMES_VERSION=1.0.0\nARG HERMES_VERSION=1.0.1\n", "HERMES_VERSION", "1.0.2", "ARG ")

    def test_either_upstream_update_captures_both_generators(self):
        pins = prep.current_versions((prep.REPO / "fedora45-ai-core-pre/Containerfile").read_text())
        for changed in ("openclaw", "hermes"):
            latest = dict(pins)
            numbers = list(prep.version(latest[changed]))
            numbers[-1] += 1
            latest[changed] = '.'.join(map(str, numbers))
            replies = [{"tag_name": "v" + latest["openclaw"]}, {"name": "Hermes Agent v" + latest["hermes"] + " release"},
                       {"sha": "a" * 40}, {"sha": "b" * 40}, [], {}]
            report = {}
            with self.subTest(changed=changed), patch.dict(prep.os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(prep, "github", side_effect=replies), patch.object(prep, "patch_inputs", side_effect=RuntimeError('snapshot captured')):
                with self.assertRaisesRegex(RuntimeError, 'snapshot captured'):
                    prep.prepare(report)
            self.assertEqual(report['ephemeral_commits'], {'openclaw': 'a' * 40, 'hermes': 'b' * 40})

    def test_preparation_refreshes_all_four_inputs_before_runtime_checks(self):
        pins = prep.current_versions((prep.REPO / "fedora45-ai-core-pre/Containerfile").read_text())
        for changed in ("openclaw", "hermes"):
            latest = dict(pins)
            numbers = list(prep.version(latest[changed]))
            numbers[-1] += 1
            latest[changed] = '.'.join(map(str, numbers))
            patch_release = {"tag_name": latest["openclaw"] + "-deterministic.99", "draft": False, "prerelease": False,
                             "assets": [{"name": f"openclaw-{latest['openclaw']}-deterministic.tar.gz", "digest": "sha256:" + "c" * 64}]}
            note_release = {"tag_name": "2026.9.99", "draft": False, "prerelease": False,
                            "assets": [{"name": "note-latest.zip", "digest": "sha256:" + "d" * 64}]}
            replies = [{"tag_name": "v" + latest["openclaw"]}, {"name": "Hermes Agent v" + latest["hermes"]},
                       {"sha": "a" * 40}, {"sha": "b" * 40}, [patch_release], patch_release,
                       note_release, {"sha": "8" * 40}]
            report = {}
            with self.subTest(changed=changed), patch.dict(prep.os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(prep, "github", side_effect=replies) as gh, patch.object(prep.importlib.util, "spec_from_file_location", side_effect=RuntimeError('before runtime checks')):
                with self.assertRaisesRegex(RuntimeError, 'before runtime checks'):
                    prep.prepare(report)
            self.assertEqual(report['build_inputs']['OPENCLAW_EPHEMERAL_COMMIT'], 'a' * 40)
            self.assertEqual(report['build_inputs']['HERMES_EPHEMERAL_COMMIT'], 'b' * 40)
            self.assertEqual(report['build_inputs']['OPENCLAW_DETERMINISTIC_TAG'], patch_release['tag_name'])
            self.assertEqual(report['build_inputs']['NOTE_RELEASE_TAG'], note_release['tag_name'])
            self.assertEqual(report['source_policy'], 'latest-resolved-per-preparation')
            self.assertIn('repos/safrano9999/openclaw-ephemeral/commits/HEAD', [c.args[0] for c in gh.call_args_list])

    def test_export_boundary_has_no_host_or_build_operations(self):
        bundle = json.loads((prep.ROOT / 'n8n-fedora45-all.json').read_text())
        forbidden = {'n8n-nodes-base.ssh', 'n8n-nodes-base.executeCommand', 'n8n-nodes-base.executeWorkflow'}
        for workflow in bundle:
            self.assertEqual(workflow['settings']['availableInMCP'], workflow['id'] == 'fedora45LoopDraft')
            for node in workflow['nodes']:
                self.assertNotIn(node['type'], forbidden)
                self.assertNotIn('fedora45HostRunner', json.dumps(node))
                if node['type'] == 'CUSTOM.fedora45Sources':
                    self.assertIn(node['parameters']['operation'], ['inventory', 'resolve', 'sync'])
                    self.assertEqual(node['credentials']['httpHeaderAuth']['id'], 'fedora45GitHubPreparation')
                if node['type'] == 'n8n-nodes-base.httpRequest':
                    if node['name'] == 'Download preparation evidence':
                        self.assertEqual(node['parameters']['authentication'], 'none')
                        self.assertFalse(node.get('credentials'))
                        self.assertFalse(node['parameters'].get('sendHeaders'))
                        continue
                    self.assertIn('https://api.github.com/', node['parameters']['url'])
                    if node['parameters'].get('method', 'GET') != 'GET':
                        self.assertEqual(node['parameters']['method'], 'POST')
                        self.assertTrue(node['parameters']['url'].endswith(('/fedora45-container-preparation.yml/dispatches',
                                                                            '/openclaw-components.yml/dispatches')))

    def test_source_sync_is_after_validated_handoff_only(self):
        workflow = json.loads((prep.ROOT / 'n8n-fedora45-workflow.json').read_text())
        incoming = [source for source, outputs in workflow['connections'].items()
                    for channel in outputs['main'] for edge in channel
                    if edge['node'] == 'Sync sources for ready build']
        self.assertEqual(incoming, ['Handoff to Hermes'])
        preview = workflow['connections']['Sources preview?']['main']
        self.assertEqual(preview[0][0]['node'], 'Discover source repositories')
        self.assertEqual(preview[1][0]['node'], 'Published version pins')

    def test_handoff_rejects_partial_or_wrong_generator_evidence(self):
        workflow = json.loads((prep.ROOT / 'n8n-fedora45-workflow.json').read_text())
        code = next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['name'] == 'Handoff to Hermes')
        script = 'const code=' + json.dumps(code) + r''';
const assert=require('node:assert/strict');
const good={schema_version:1,status:'READY_FOR_BUILD',validate_only:false,build_started:false,image_pulled:false,container_restarted:false,build_commit:'c'.repeat(40),ephemeral_commits:{openclaw:'a'.repeat(40),hermes:'b'.repeat(40)},build_inputs:{OPENCLAW_EPHEMERAL_COMMIT:'a'.repeat(40),HERMES_EPHEMERAL_COMMIT:'b'.repeat(40)},checks:{openclaw:{status:'PASS',generator_commit:'a'.repeat(40)},hermes:{status:'PASS',generator_commit:'b'.repeat(40)}}};
good.source_snapshot_id='d'.repeat(64);
good.source_snapshot={openclaw_source:{commit:'1'.repeat(40)},repositories:[{repository:'safrano9999/openclaw-ephemeral',commit:'a'.repeat(40)},{repository:'safrano9999/hermes-ephemeral',commit:'b'.repeat(40)},{repository:'safrano9999/openclaw-deterministic-latest',release:{ref:'patch-release',sha256:'e'.repeat(64),upstream_commit:'1'.repeat(40)}},{repository:'safrano9999/NOTE',release:{ref:'note-release',sha256:'f'.repeat(64)}}]};
Object.assign(good.build_inputs,{OPENCLAW_UPSTREAM_SHA:'1'.repeat(40),OPENCLAW_DETERMINISTIC_TAG:'patch-release',OPENCLAW_DETERMINISTIC_SHA256:'e'.repeat(64),NOTE_RELEASE_TAG:'note-release',NOTE_RELEASE_SHA256:'f'.repeat(64)});
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
async function check(report,validation=false,request={}){
 const f=new AsyncFunction('$','$input',code);
 return f.call({helpers:{getBinaryDataBuffer:async()=>Buffer.from(JSON.stringify(report))}},name=>({first:()=>({json:name==='Resolve latest sources once'?good:name==='Read request'?request:{validate_only:validation}})}),{first:()=>({binary:{data:{fileName:'container-preparation.json'}}})});
}
(async()=>{
 const ready=(await check(good))[0].json;
 assert.equal(ready.status,'READY_FOR_BUILD');
 assert.deepEqual(ready.safrano_build_inputs,{build_commit:good.build_commit});
 assert.equal(ready.safrano_build_request.key,'fedora45_core_pre');
 assert.equal(ready.safrano_build_request.cascade,true);
 assert.equal(ready.safrano_build_request.auto_pull,undefined);
 const auto=(await check(good,false,{auto:true,build_feedback:true}))[0].json;
 assert.equal(auto.safrano_build_request.auto_pull,true);
 assert.equal(auto.safrano_build_request.feedback,true);
 assert.deepEqual(auto.safrano_build_request.inputs,ready.safrano_build_inputs);
 assert.match(auto.next,/unchanged/);
 assert.equal((await check(good,false,{auto:true,build_feedback:false}))[0].json.safrano_build_request.feedback,false);
 const maximum=(await check(good,false,{auto:true,auto_upgrade:true,build_feedback:true}))[0].json.safrano_build_request;
 assert.equal(maximum.auto_pull,true);assert.equal(maximum.auto_upgrade,true);assert.equal(maximum.feedback,true);
 assert.equal(auto.safrano_build_request.auto_upgrade,undefined);

 const unchanged={schema_version:1,status:'NO_UPDATE',validate_only:false,build_started:false,image_pulled:false,container_restarted:false};
 assert.equal((await check(unchanged,false,{auto:true}))[0].json.safrano_build_request,undefined);
 assert.deepEqual((await check(unchanged,false,{auto:true,auto_upgrade:true,build_feedback:false}))[0].json.safrano_upgrade_request,{action:'auto-update',feedback:false});

 for(const mutate of [r=>delete r.checks.hermes,r=>r.build_inputs.HERMES_EPHEMERAL_COMMIT='f'.repeat(40),r=>r.container_restarted=true,r=>r.build_commit='',r=>r.status='BLOCKED',r=>r.source_snapshot_id='0'.repeat(64),r=>r.source_snapshot.repositories[0].commit='0'.repeat(40),r=>r.build_inputs.NOTE_RELEASE_SHA256='0'.repeat(64),r=>r.build_inputs.OPENCLAW_UPSTREAM_SHA='0'.repeat(40)]){
  const bad=structuredClone(good);mutate(bad);await assert.rejects(check(bad));
 }
 const validated={...good,status:'VALIDATED_ONLY',validate_only:true};delete validated.build_commit;
 const validation=(await check(validated,true))[0].json;
 assert.equal(validation.status,'VALIDATED_ONLY');
 assert.equal(validation.safrano_build_inputs,undefined);
 await assert.rejects(check(validated));await assert.rejects(check(good,true));
 good.source_snapshot.build_plan={required:true,start_key:'fedora45_core'};
 good.build_plan=structuredClone(good.source_snapshot.build_plan);
 assert.equal((await check(good))[0].json.safrano_build_request.key,'fedora45_core');
 const wrong=structuredClone(good);wrong.build_plan.start_key='fedora45_base';await assert.rejects(check(wrong));
 const depRequest={upgrade_build_deps:true,target:'fedora45-ai-safrano9999',auto:true};
 await assert.rejects(check(good,false,depRequest));
 const deps={...good,upgrade_build_deps:true,build_dependencies:{schema_version:1,status:'PASS',image:'fedora45-ai-core-pre',required:true}};
 const depBuild=(await check(deps,false,depRequest))[0].json.safrano_build_request;
 assert.equal(depBuild.key,'fedora45_core_pre');assert.equal(depBuild.cascade,true);
 assert.equal((await check({...deps,build_dependencies:{...deps.build_dependencies,required:false}},false,depRequest))[0].json.safrano_build_request.key,'fedora45_core');
 await assert.rejects(check(deps));

})().catch(e=>{console.error(e);process.exit(1)});
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)

    def test_artifact_redirect_requires_github_storage_and_https(self):
        workflow = json.loads((prep.ROOT / 'n8n-fedora45-workflow.json').read_text())
        code = next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['name'] == 'Validate evidence URL')
        script = 'const code=' + json.dumps(code) + r''';
const assert=require('node:assert/strict'),f=new Function('$json',code);
const url='https://productionresultssa12.blob.core.windows.net/actionsresults/fixture.zip?sig=fixture';
assert.equal(f({statusCode:302,headers:{location:url}})[0].json.url,url);
for(const location of ['http://productionresultssa12.blob.core.windows.net/file','https://evil.example/file','https://productionresultssa12.blob.core.windows.net.evil.example/file','https://user@productionresultssa12.blob.core.windows.net/file',null]){
 assert.throws(()=>f({statusCode:302,headers:{location}}));
}
assert.throws(()=>f({statusCode:200,headers:{location:url}}));
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
