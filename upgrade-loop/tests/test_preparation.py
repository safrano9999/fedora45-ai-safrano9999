import importlib.util
import json
import subprocess
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

    def test_either_upstream_update_captures_both_generators(self):
        pins = prep.current_versions((prep.REPO / "fedora45-ai-core-pre/Containerfile").read_text())
        for changed in ("openclaw", "hermes"):
            latest = dict(pins)
            numbers = list(prep.version(latest[changed]))
            numbers[-1] += 1
            latest[changed] = '.'.join(map(str, numbers))
            replies = [{"tag_name": "v" + latest["openclaw"]}, {"name": "Hermes Agent v" + latest["hermes"] + " release"},
                       {"sha": "a" * 40}, {"sha": "b" * 40}, []]
            report = {}
            with self.subTest(changed=changed), patch.dict(prep.os.environ, {"GITHUB_ACTIONS": "true"}), patch.object(prep, "github", side_effect=replies), patch.object(prep, "patch_inputs", side_effect=RuntimeError('snapshot captured')):
                with self.assertRaisesRegex(RuntimeError, 'snapshot captured'):
                    prep.prepare(report)
            self.assertEqual(report['ephemeral_commits'], {'openclaw': 'a' * 40, 'hermes': 'b' * 40})

    def test_export_boundary_has_no_host_or_build_operations(self):
        bundle = json.loads((prep.ROOT / 'n8n-fedora45-all.json').read_text())
        forbidden = {'n8n-nodes-base.ssh', 'n8n-nodes-base.executeCommand', 'n8n-nodes-base.executeWorkflow'}
        for workflow in bundle:
            self.assertEqual(workflow['settings']['availableInMCP'], workflow['id'] == 'fedora45LoopDraft')
            for node in workflow['nodes']:
                self.assertNotIn(node['type'], forbidden)
                self.assertNotIn('fedora45HostRunner', json.dumps(node))
                if node['type'] == 'n8n-nodes-base.httpRequest':
                    if node['name'] == 'Download preparation evidence':
                        self.assertEqual(node['parameters']['authentication'], 'none')
                        self.assertFalse(node.get('credentials'))
                        self.assertFalse(node['parameters'].get('sendHeaders'))
                        continue
                    self.assertIn('https://api.github.com/', node['parameters']['url'])
                    if node['parameters'].get('method', 'GET') != 'GET':
                        self.assertEqual(node['parameters']['method'], 'POST')
                        self.assertTrue(node['parameters']['url'].endswith('/fedora45-container-preparation.yml/dispatches'))

    def test_handoff_rejects_partial_or_wrong_generator_evidence(self):
        workflow = json.loads((prep.ROOT / 'n8n-fedora45-workflow.json').read_text())
        code = next(n['parameters']['jsCode'] for n in workflow['nodes'] if n['name'] == 'Handoff to Hermes')
        script = 'const code=' + json.dumps(code) + r''';
const assert=require('node:assert/strict');
const good={schema_version:1,status:'READY_FOR_BUILD',validate_only:false,build_started:false,image_pulled:false,container_restarted:false,build_commit:'c'.repeat(40),ephemeral_commits:{openclaw:'a'.repeat(40),hermes:'b'.repeat(40)},build_inputs:{OPENCLAW_EPHEMERAL_COMMIT:'a'.repeat(40),HERMES_EPHEMERAL_COMMIT:'b'.repeat(40)},checks:{openclaw:{status:'PASS',generator_commit:'a'.repeat(40)},hermes:{status:'PASS',generator_commit:'b'.repeat(40)},hermes_patch:{status:'PASS'}}};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
async function check(report,validation=false){
 const f=new AsyncFunction('$','$input',code);
 return f.call({helpers:{getBinaryDataBuffer:async()=>Buffer.from(JSON.stringify(report))}},()=>({first:()=>({json:{validate_only:validation}})}),{first:()=>({binary:{data:{fileName:'container-preparation.json'}}})});
}
(async()=>{
 assert.equal((await check(good))[0].json.status,'READY_FOR_BUILD');
 for(const mutate of [r=>delete r.checks.hermes,r=>r.build_inputs.HERMES_EPHEMERAL_COMMIT='f'.repeat(40),r=>r.checks.hermes_patch.status='NOT_TESTED',r=>r.container_restarted=true,r=>r.build_commit='',r=>r.status='BLOCKED']){
  const bad=structuredClone(good);mutate(bad);await assert.rejects(check(bad));
 }
 const validated={...good,status:'VALIDATED_ONLY',validate_only:true};delete validated.build_commit;
 assert.equal((await check(validated,true))[0].json.status,'VALIDATED_ONLY');
 await assert.rejects(check(validated));await assert.rejects(check(good,true));
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
