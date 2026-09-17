"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'n8n-feedback-test-'));
process.env.FEDORA45_FEEDBACK_ROOT = root;
process.env.FEDORA45_FEEDBACK_NO_WORKER = '1';
const f = require('../n8n-sources/completion-feedback');
const { Fedora45Feedback } = require('../n8n-sources/Fedora45Feedback.node');
let rv;
test.before(async () => { rv = await f.client(); });
test.beforeEach(async () => { fs.rmSync(root, { recursive: true, force: true }); await rv.heartbeat(); });
test.after(() => fs.rmSync(root, { recursive: true, force: true }));

async function receiver(t, statuses = [202]) {
  const calls = [];
  const server = http.createServer(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    calls.push({ body: Buffer.concat(chunks).toString(), headers: req.headers });
    res.writeHead(statuses[Math.min(calls.length - 1, statuses.length - 1)]); res.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  return { url: `http://127.0.0.1:${server.address().port}/hook`, calls };
}
const execution = status => ({ status, workflowId: 'fedora45LoopDraft' });

test('opt-in and saved default stay compatible; another URL never inherits its secret', async () => {
  assert.equal(await f.register('1', { callback_url: 'http://example.test', feedback: false }), false);
  assert.equal(await f.register('2', { feedback: 'false', callback_url: 'http://example.test' }), false);
  assert.equal(await f.register('3', {}), false);
  await rv.configure('set', 'http://example.test', 'private');
  const id = await f.register('4', { feedback: true });
  const saved = JSON.parse(fs.readFileSync(rv.path(id)));
  assert.equal(saved.callback.secret, 'private');
  assert.equal(saved.execution_id, '4');
  assert.equal(fs.statSync(rv.path(id)).mode & 0o777, 0o600);
  assert.ok(!('callback' in await rv.status(id)));
  const other = await f.register('5', { callback_url: 'http://other.test' });
  assert.equal(JSON.parse(fs.readFileSync(rv.path(other))).callback.secret, '');
});

test('each terminal status delivers signed empty JSON once through the npm library', async t => {
  const { url, calls } = await receiver(t);
  for (const [index, status] of ['success', 'error', 'canceled', 'crashed'].entries()) {
    const executionId = String(index + 10);
    const id = await f.register(executionId, { callback_url: url, callback_secret: 'secret' });
    await f.tick(async actual => actual === executionId ? execution(status) : null);
    const result = await rv.status(id);
    assert.equal(result.state, 'finished');
    assert.equal(result.notification, 'delivered');
    assert.equal(result.outcome, status === 'success' ? 'success' : status === 'canceled' ? 'cancelled' : 'failure');
    assert.equal(result.execution_status, status);
    assert.equal(calls[index].body, '{}');
    assert.equal(calls[index].headers['x-github-delivery'], id);
    assert.equal(calls[index].headers['x-hub-signature-256'], 'sha256=' + crypto.createHmac('sha256', 'secret').update('{}').digest('hex'));
  }
  await f.tick(async () => assert.fail('finished jobs must not query n8n again'));
  assert.equal(calls.length, 4);
});

test('nonterminal, wrong workflow and failed database reads never notify', async t => {
  const { url, calls } = await receiver(t);
  const id = await f.register('20', { callback_url: url });
  await f.tick(async () => execution('running'));
  await f.tick(async () => ({ status: 'success', workflowId: 'other' }));
  await f.tick(async () => null);
  await f.tick(async () => { throw new Error('temporary database outage'); });
  assert.equal(calls.length, 0);
  assert.equal((await rv.status(id)).state, 'pending');
});

test('retry survives a fresh process with the original delivery ID and no execution recheck', async t => {
  const { url, calls } = await receiver(t, [503, 202]);
  const id = await f.register('30', { callback_url: url });
  await f.tick(async () => execution('error'));
  assert.equal((await rv.status(id)).notification, 'pending');
  await rv.update(id, { retry_after: 0 });
  await execFile(process.execPath, ['-e', `const f=require(${JSON.stringify(require.resolve('../n8n-sources/completion-feedback'))});(async()=>{const rv=await f.client();await rv.recoverDeliveries();await f.tick(async()=>{throw new Error('not needed')});})().catch(()=>process.exit(1));`], { env: process.env });
  assert.equal((await rv.status(id)).notification, 'delivered');
  assert.deepEqual(calls.map(c => c.headers['x-github-delivery']), [id, id]);
});

test('node retries recover one persisted registration, including concurrent attempts', async t => {
  const { url, calls } = await receiver(t);
  const ids = await Promise.all([f.register('40', { callback_url: url }), f.register('40', { callback_url: url })]);
  assert.equal(ids[0], ids[1]); assert.equal((await rv.jobs()).length, 1);
  await f.tick(async () => execution('success'));
  assert.equal(await f.register('40', { callback_url: url }), ids[0]);
  await f.tick(async () => assert.fail('already complete'));
  assert.equal(calls.length, 1);
});

test('delivered legacy history is preserved; pending legacy callbacks block migration', () => {
  fs.mkdirSync(path.join(root, 'jobs'), { recursive: true });
  const file = path.join(root, 'jobs', '56.json');
  const old = JSON.stringify({ id: '56', state: 'delivered', delivery: 'legacy-id' });
  fs.writeFileSync(file, old); f.assertLegacyDrained();
  assert.equal(fs.readFileSync(file, 'utf8'), old);
  fs.writeFileSync(file, JSON.stringify({ id: '56', state: 'waiting' }));
  assert.throws(() => f.assertLegacyDrained(), /Drain legacy/);
});

test('registration fails before continuing the workflow when its worker is stale', async () => {
  fs.unlinkSync(path.join(root, 'heartbeat'));
  await assert.rejects(f.register('50', { callback_url: 'http://example.test' }), /worker is not running/);
  assert.equal((await rv.jobs()).length, 0);
});

test('the n8n adapter rejects Herdr targets without starting a job', async () => {
  await assert.rejects(f.register('51', { herdr_target: 'w1:p5' }), /webhook feedback only/);
  assert.equal((await rv.jobs()).length, 0);
});

test('custom node awaits registration and removes callback secrets from body, query and raw binary', async t => {
  const { url } = await receiver(t);
  const node = new Fedora45Feedback();
  const item = { json: { query: { callback_secret: 'query-secret' } }, binary: { data: { id: 'raw-request' } } };
  const context = { getInputData: () => [item], getExecutionId: () => '60', helpers: { getBinaryDataBuffer: async () => Buffer.from(JSON.stringify({ mode: '--check', callback_url: url, callback_secret: 'body-secret' })) } };
  const output = await node.execute.call(context);
  assert.equal((await rv.jobs()).length, 1);
  assert.equal(output[0][0].json.body.mode, '--check');
  assert.equal(output[0][0].json.completion_feedback.enabled, true);
  assert.match(output[0][0].json.completion_feedback.next, /Do not poll/);
  assert.match(output[0][0].json.completion_feedback.next, /webhook/);
  assert.equal(output[0][0].binary, undefined);
  assert.ok(!JSON.stringify(output).includes('body-secret'));
  assert.ok(!JSON.stringify(output).includes('query-secret'));
  assert.equal(JSON.parse(fs.readFileSync((await rv.jobs())[0])).callback.secret, 'body-secret');
});

test('generated request and acknowledgement carry the actual feedback choice without secrets', async t => {
  const { url } = await receiver(t);
  const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '../n8n-fedora45-workflow.json')));
  const nodes = Object.fromEntries(workflow.nodes.map(node => [node.name, node]));
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const readRequest = new AsyncFunction('$input', nodes['Read request'].parameters.jsCode);
  const acknowledge = new Function('$json', '$execution', 'return (' + nodes['Accept preparation'].parameters.responseBody.slice(3, -2) + ')');
  const node = new Fedora45Feedback();
  for (const enabled of [true, false]) {
    const id = enabled ? '70' : '71';
    const context = { getInputData: () => [{ json: { headers: {}, body: { feedback: enabled, callback_url: url, callback_secret: 'private' } } }], getExecutionId: () => id };
    const registered = (await node.execute.call(context))[0][0];
    const request = (await readRequest({ first: () => registered }))[0].json;
    const response = acknowledge(request, { id });
    assert.equal(request.webhook, true);
    assert.equal(response.execution_id, id);
    assert.equal(response.feedback_enabled, enabled);
    assert.equal(Boolean(response.feedback_id), enabled);
    assert.match(response.next, /Do not poll/);
    assert.match(response.next, enabled ? /notified via webhook/ : /No completion notification/);
    assert.ok(!JSON.stringify({ request, response }).includes('private'));
  }
});

test('auto shorthand selects saved preparation feedback and carries its build choice without credentials', async t => {
  const { url } = await receiver(t);
  await rv.configure('set', url, 'saved-auto-secret');
  const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '../n8n-fedora45-workflow.json')));
  const nodes = Object.fromEntries(workflow.nodes.map(node => [node.name, node]));
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  const normalize = new AsyncFunction('$input', nodes['Normalize auto mode'].parameters.jsCode);
  const parse = new AsyncFunction('$input', nodes['Read request'].parameters.jsCode);
  const feedback = new Fedora45Feedback();
  const cases = [
    ['upgrade-safrano9999 + auto', true, true],
    [{mode:'upgrade-safrano9999+auto'}, true, true],
    [{mode:'upgrade-safrano9999', auto:true}, true, true],
    [{mode:'upgrade-safrano9999+auto', feedback:false}, true, false],
    [{mode:'upgrade-safrano9999', auto:'true', feedback:'false'}, true, false],
    [{mode:'upgrade-safrano9999+auto', auto:false}, false, false],
    ['--check', false, false],
  ];
  for (const [index,[body,auto,notify]] of cases.entries()) {
    const normalized = (await normalize({first:()=>({json:{headers:{},body}})}))[0];
    const registered = (await feedback.execute.call({getInputData:()=>[normalized],getExecutionId:()=>String(80+index)}))[0][0];
    const request = (await parse({first:()=>registered}))[0].json;
    assert.equal(request.auto,auto);assert.equal(request.build_feedback,notify);
    assert.equal(request.completion_feedback.enabled,notify);
    assert.ok(!JSON.stringify(request).includes('saved-auto-secret'));
    if (notify) assert.equal(JSON.parse(fs.readFileSync(rv.path(request.completion_feedback.feedback_id))).callback.secret,'saved-auto-secret');
  }
  for(const body of [{auto:'yes'},{mode:'--check',auto:true},{mode:'--sources',auto:true},{mode:'--validate-only',auto:true}])
    await assert.rejects(normalize({first:()=>({json:{body}})}));
  const binary={json:{headers:{}},binary:{data:{id:'test'}}};
  const raw=(await normalize.call({helpers:{getBinaryDataBuffer:async()=>Buffer.from('upgrade-safrano9999+auto')}},{first:()=>binary}))[0];
  assert.equal(raw.json.body.mode,'upgrade-safrano9999');assert.equal(raw.json.body.feedback,true);
  assert.equal(raw.binary,undefined);
  const edges=workflow.connections;
  assert.equal(edges['Webhook - preparation or --check'].main[0][0].node,'Normalize auto mode');
  assert.equal(edges['Normalize auto mode'].main[0][0].node,'Register completion hook');
});

test('maximum mode preserves feedback choice and returns whitelist upgrade even without a build', async () => {
  const w=JSON.parse(fs.readFileSync(path.join(__dirname,'../n8n-fedora45-workflow.json')));
  const nodes=Object.fromEntries(w.nodes.map(n=>[n.name,n]));
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  const normalize=new AsyncFunction('$input',nodes['Normalize auto mode'].parameters.jsCode);
  const parse=new AsyncFunction('$input',nodes['Read request'].parameters.jsCode);
  const noUpdate=new Function('$json',nodes['No update'].parameters.jsCode);
  for(const body of ['upgrade-safrano9999 + auto-upgrade',{mode:'upgrade-safrano9999+auto-upgrade'},
      {mode:'upgrade-safrano9999',auto_upgrade:true}, {mode:'upgrade-safrano9999+auto-upgrade',feedback:false}]) {
    const normalized=(await normalize({first:()=>({json:{headers:{},body}})}))[0];
    const request=(await parse({first:()=>normalized}))[0].json;
    assert.equal(request.auto,true);assert.equal(request.auto_upgrade,true);assert.equal(request.upgrade_safrano9999,true);
    const output=noUpdate(request)[0].json;
    assert.equal(output.status,'NO_UPDATE');assert.equal(output.safrano_build_request,undefined);
    assert.deepEqual(output.safrano_upgrade_request,{action:'auto-update',feedback:typeof body==='object'&&body.feedback===false?false:true});
  }
  assert.equal(noUpdate({auto:true})[0].json.safrano_upgrade_request,undefined);
});

test('build dependency option composes with source upgrades and automatic modes', async()=>{
  const workflow=JSON.parse(fs.readFileSync(path.join(__dirname,'../n8n-fedora45-workflow.json')));
  const nodes=Object.fromEntries(workflow.nodes.map(n=>[n.name,n]));
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  const normalize=new AsyncFunction('$input',nodes['Normalize auto mode'].parameters.jsCode);
  const read=new AsyncFunction('$input',nodes['Read request'].parameters.jsCode);
  for(const body of ['upgrade-build-deps','--upgrade-build-deps + auto',
    'upgrade-safrano9999 + upgrade-build-deps + auto-upgrade',
    {mode:'upgrade-safrano9999',upgrade_build_deps:true,auto_upgrade:true}]) {
    const normalized=(await normalize({first:()=>({json:{body,headers:{}}})}))[0];
    const request=(await read({first:()=>normalized}))[0].json;
    assert.equal(request.upgrade_build_deps,true);
    if(typeof body==='object'||body.includes('safrano9999')) {
      assert.equal(request.upgrade_safrano9999,true);assert.equal(request.auto_upgrade,true);
    }
  }
  for(const mode of ['upgrade-build-deps + nightly','upgrade-build-deps + auto + auto-upgrade','upgrade-build-deps + upgrade-build-deps'])
    await assert.rejects(normalize({first:()=>({json:{body:{mode},headers:{}}})}));
});
