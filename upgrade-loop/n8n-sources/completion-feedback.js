"use strict";
// n8n owns execution monitoring; mcp-rendezvous owns persistence and delivery.
const fs = require('node:fs');
const path = require('node:path');
const ROOT = process.env.FEDORA45_FEEDBACK_ROOT || '/home/node/.n8n/fedora45-feedback';
const CONFIG = process.env.FEDORA45_RENDEZVOUS_CONFIG || path.join(__dirname, 'rendezvous.json');
const WORKFLOW = 'fedora45LoopDraft';
const OUTCOMES = { success: 'success', error: 'failure', canceled: 'cancelled', crashed: 'failure' };
const TERMINAL = new Set(Object.keys(OUTCOMES));
let instance;
let registration = Promise.resolve();

function assertLegacyDrained() {
  const jobs = path.join(ROOT, 'jobs');
  if (!fs.existsSync(jobs)) return;
  for (const name of fs.readdirSync(jobs).filter(n => /^\d+\.json$/.test(n))) {
    const job = JSON.parse(fs.readFileSync(path.join(jobs, name)));
    if (job.state !== 'delivered') throw new Error('Drain legacy completion notifications before upgrading the worker');
  }
  // Delivered numeric jobs remain as history. The SDK reads only its UUID jobs.
}

async function client() {
  if (!instance) instance = import('mcp-rendezvous').then(({ Rendezvous }) => {
    assertLegacyDrained();
    return new Rendezvous(CONFIG, ROOT);
  });
  return instance;
}

async function registerOnce(id, input) {
  if (!/^[0-9]+$/.test(String(id))) throw new Error('Invalid execution ID');
  if (input.herdr_target) throw new Error('This n8n workflow supports webhook feedback only; pass callback_url or feedback=true for the saved hook');
  const enabled = input.feedback !== false && input.feedback !== 'false' &&
    (Boolean(input.callback_url) || input.feedback === true || input.feedback === 'true');
  if (!enabled) return false;
  const rv = await client();
  const destination = await rv.resolve(true, input.callback_url || '', input.callback_secret || '');
  // Recover registration after a node retry or a crash following the durable write.
  // The workflow has one registration node and one request item per execution.
  for (const file of await rv.jobs()) {
    const job = JSON.parse(fs.readFileSync(file));
    if (job.tool === 'execute_workflow' && job.execution_id === String(id) && job.workflow_id === WORKFLOW) return job.id;
  }
  return rv.queue('execute_workflow', 'run', destination, {
    kind: 'n8n_execution', execution_id: String(id), workflow_id: WORKFLOW,
  });
}

function register(id, input) {
  const next = registration.catch(() => {}).then(() => registerOnce(id, input));
  registration = next;
  return next;
}

async function instructions(enabled) {
  const { completionNext } = await import('mcp-rendezvous');
  return completionNext(enabled ? { kind: 'webhook' } : null);
}

async function tick(readExecution) {
  const rv = await client();
  for (const file of await rv.jobs()) {
    const job = JSON.parse(fs.readFileSync(file));
    if (job.state === 'finished' || job.tool !== 'execute_workflow' || job.workflow_id !== WORKFLOW) continue;
    try {
      const execution = await readExecution(job.execution_id);
      if (!execution || execution.workflowId !== WORKFLOW || !TERMINAL.has(execution.status)) continue;
      await rv.complete(job.id, OUTCOMES[execution.status], { execution_status: execution.status });
    } catch {
      // A temporary database failure must never become a false terminal result.
      continue;
    }
  }
  await rv.deliverPending();
}

let launched = false;
function start() {
  if (launched || process.env.FEDORA45_FEEDBACK_NO_WORKER === '1') return;
  launched = true;
  const child = require('node:child_process').spawn(process.execPath, [path.join(__dirname, 'completion-worker.js')], { detached: true, stdio: 'ignore' });
  const retry = () => { launched = false; setTimeout(start, 5000).unref(); };
  child.on('error', retry);
  child.on('exit', code => { if (code !== 0) retry(); });
  child.unref();
}
module.exports = { ROOT, CONFIG, TERMINAL, assertLegacyDrained, client, register, instructions, tick, start };
