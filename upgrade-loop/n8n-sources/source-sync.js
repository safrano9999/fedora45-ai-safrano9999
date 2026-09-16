'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);
const { SHA } = require('./source-inventory');
const ROOT = '/home/node/.n8n/fedora45-sources';

async function directory(p) {
  await fs.mkdir(p, { recursive: true, mode: 0o700 });
  const stat = await fs.lstat(p);
  if (!stat.isDirectory() || stat.isSymbolicLink() || await fs.realpath(p) !== p) {
    throw new Error('Unsafe source directory');
  }
}

async function syncSources(manifest, authorization, root = ROOT, remoteForTests = null) {
  // This module is called only after the custom node validates READY_FOR_BUILD.
  // A test-only remote factory permits isolated local Git fixtures, never node input.
  if (!/^Bearer [^\r\n]+$/.test(authorization)) throw new Error('Missing GitHub bearer credential');
  for (const entry of manifest.repositories) {
    if (!/^safrano9999\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(entry.repository) || !SHA.test(entry.commit)) {
      throw new Error('Invalid resolved source manifest');
    }
  }
  await directory(root);
  const lock = path.join(root, '.sync-lock');
  try { await fs.mkdir(lock, { mode: 0o700 }); }
  catch { throw new Error('Source sync locked; another run or interrupted sync needs review'); }
  // Header goes only into child-process environment, never argv, URLs or .git/config.
  const env = { PATH: process.env.PATH, HOME: root, LANG: 'C', GIT_TERMINAL_PROMPT: '0',
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_COUNT: '4',
    GIT_CONFIG_KEY_0: 'http.https://github.com/.extraheader', GIT_CONFIG_VALUE_0: 'Authorization: Basic ' + Buffer.from('x-access-token:' + authorization.slice(7)).toString('base64'),
    GIT_CONFIG_KEY_1: 'core.hooksPath', GIT_CONFIG_VALUE_1: '/dev/null',
    GIT_CONFIG_KEY_2: 'credential.helper', GIT_CONFIG_VALUE_2: '',
    GIT_CONFIG_KEY_3: 'http.followRedirects', GIT_CONFIG_VALUE_3: 'false' };
  async function git(args, label) {
    try {
      const result = await execFile('git', args, { env, timeout: 300000, maxBuffer: 1000000 });
      return result.stdout.trim();
    } catch { throw new Error('Git ' + label + ' failed; no repository scripts were executed'); }
  }
  const results = [];
  try {
    for (const entry of manifest.repositories) {
      const name = entry.repository.split('/')[1], destination = path.join(root, name);
      const origin = remoteForTests ? remoteForTests(entry.repository) : 'https://github.com/' + entry.repository + '.git';
      let exists = false;
      try { await fs.lstat(destination); exists = true; } catch (e) { if (e.code !== 'ENOENT') throw e; }
      if (exists) {
        await directory(destination);
        const dotGit = await fs.lstat(path.join(destination, '.git'));
        if (!dotGit.isDirectory() || dotGit.isSymbolicLink()) throw new Error('Unsafe Git directory: ' + name);
        if (await git(['-C', destination, 'remote', 'get-url', 'origin'], 'origin check') !== origin) throw new Error('Unexpected origin: ' + name);
        if (await git(['-C', destination, 'status', '--porcelain', '--untracked-files=all'], 'worktree check')) {
          throw new Error('Local changes preserved; source sync stopped: ' + name);
        }
      }
      const checkout = exists ? destination : await fs.mkdtemp(path.join(root, '.clone-'));
      try {
        if (!exists) await git(['clone', '--depth', '1', '--no-tags', '--single-branch', '--no-checkout', '--', origin, checkout], 'clone ' + name);
        await git(['-C', checkout, 'fetch', '--depth', '1', '--no-tags', 'origin', entry.commit], 'fetch ' + name);
        await git(['-C', checkout, 'checkout', '--detach', entry.commit], 'checkout ' + name);
        const commit = await git(['-C', checkout, 'rev-parse', 'HEAD'], 'identity check');
        const depth = await git(['-C', checkout, 'rev-list', '--count', 'HEAD'], 'depth check');
        if (commit !== entry.commit || depth !== '1') throw new Error('Shallow checkout identity mismatch: ' + name);
        if (!exists) await fs.rename(checkout, destination);
        results.push({ repository: entry.repository, commit, path: destination, action: exists ? 'updated' : 'cloned', depth: 1 });
      } finally { if (!exists) await fs.rm(checkout, { recursive: true, force: true }); }
    }
    const result = { ...manifest, synced_at: new Date().toISOString(), root, checkouts: results };
    const pending = path.join(root, '.manifest.pending');
    await fs.writeFile(pending, JSON.stringify(result, null, 2) + '\n', { mode: 0o600 });
    await fs.rename(pending, path.join(root, 'manifest.json'));
    return result;
  } finally { await fs.rmdir(lock); }
}

module.exports = { syncSources, ROOT };
