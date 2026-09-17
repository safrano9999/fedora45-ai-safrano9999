'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const https = require('node:https');
const ROOT = process.env.FEDORA45_FEEDBACK_ROOT || '/home/node/.n8n/fedora45-feedback';
const TERMINAL = new Set(['success', 'error', 'canceled', 'crashed']);

function atomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const tmp = file + '.' + crypto.randomUUID();
  fs.writeFileSync(tmp, JSON.stringify(value), { mode: 0o600 });
  fs.renameSync(tmp, file);
}
function endpoint(url, secret = '') {
  const u = new URL(url);
  if (!['http:', 'https:'].includes(u.protocol) || !u.hostname || u.username || u.password || u.hash || /[\r\n]/.test(url + secret)) throw new Error('Invalid callback endpoint');
  return { url, secret };
}
function register(id, input) {
  if (!/^[0-9]+$/.test(String(id))) throw new Error('Invalid execution ID');
  const enabled = input.feedback !== false && input.feedback !== 'false' && (Boolean(input.callback_url) || input.feedback === true || input.feedback === 'true');
  if (!enabled) return false;
  const defaults = path.join(ROOT, 'default.json');
  const saved = fs.existsSync(defaults) ? JSON.parse(fs.readFileSync(defaults)) : null;
  const url = input.callback_url || saved?.url;
  const secret = input.callback_secret || (saved?.url === url ? saved.secret : '') || '';
  if (!url) throw new Error('feedback=true requires callback_url or a configured default hook');
  const file = path.join(ROOT, 'jobs', id + '.json');
  if (!fs.existsSync(file)) atomic(file, { id: String(id), callback: endpoint(url, secret), delivery: crypto.randomUUID(), attempts: 0, state: 'waiting' });
  return true;
}
function send(job) {
  return new Promise((resolve, reject) => {
    const body = '{}', headers = { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body), 'X-GitHub-Delivery': job.delivery };
    if (job.callback.secret) headers['X-Hub-Signature-256'] = 'sha256=' + crypto.createHmac('sha256', job.callback.secret).update(body).digest('hex');
    const u = new URL(job.callback.url), client = u.protocol === 'https:' ? https : http;
    const request = client.request(u, { method: 'POST', headers, timeout: 15000 }, response => {
      response.resume();
      if (response.statusCode >= 200 && response.statusCode < 300) resolve();
      else reject(new Error('Callback rejected')); // Never follow redirects with the signature.
    });
    request.on('timeout', () => request.destroy(new Error('Callback timeout')));
    request.on('error', reject);
    request.end(body);
  });
}
async function tick(readExecution, deliver = send) {
  const jobs = path.join(ROOT, 'jobs');
  if (!fs.existsSync(jobs)) return;
  for (const name of fs.readdirSync(jobs).filter(n => /^\d+\.json$/.test(n))) {
    const file = path.join(jobs, name), job = JSON.parse(fs.readFileSync(file));
    if (job.state === 'delivered' || Date.now() < (job.retry_after || 0)) continue;
    try {
      const execution = await readExecution(job.id);
      if (!execution || execution.workflowId !== 'fedora45LoopDraft' || !TERMINAL.has(execution.status)) continue;
      await deliver(job);
      job.state = 'delivered'; job.delivered = new Date().toISOString();
      delete job.callback; delete job.error;
    } catch {
      job.attempts++; job.error = 'completion_delivery_failed'; job.retry_after = Date.now() + Math.min(300000, 15000 * job.attempts);
    }
    atomic(file, job);
  }
}
let launched = false;
function start() {
  if (launched || process.env.FEDORA45_FEEDBACK_NO_WORKER === '1') return;
  launched = true;
  const child = require('node:child_process').spawn(process.execPath, [path.join(__dirname, 'completion-worker.js')], { detached: true, stdio: 'ignore' });
  child.on('exit', code => {
    if (code !== 0) { launched = false; setTimeout(start, 5000).unref(); }
  });
  child.unref();
}
module.exports = { ROOT, TERMINAL, atomic, endpoint, register, send, tick, start };
