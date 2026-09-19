'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);
const { inventory, resolveCommits, latestRelease, IMAGE_REPO } = require('../n8n-sources/source-inventory');
const { syncSources } = require('../n8n-sources/source-sync');
const { Fedora45Sources } = require('../n8n-sources/Fedora45Sources.node');
const repoRoot = path.resolve(__dirname, '../..');
const imageCommit = 'a'.repeat(40);

async function fixture(edits = {}) {
  const files = {};
  for (const layer of await fs.readdir(repoRoot)) {
    if (!layer.startsWith('fedora45-ai-')) continue;
    for (const suffix of ['Containerfile', 'build.conf', 'build/prepare-build-context.sh']) {
      const name = layer + '/' + suffix;
      try { files[name] = await fs.readFile(path.join(repoRoot, name), 'utf8'); } catch (e) { if (e.code !== 'ENOENT') throw e; }
    }
  }
  Object.assign(files, edits);
  return async url => {
    if (url === '/repos/' + IMAGE_REPO + '/commits/main') return { sha: imageCommit };
    if (url === '/repos/' + IMAGE_REPO + '/git/trees/' + imageCommit + '?recursive=1') {
      return { truncated: false, tree: Object.keys(files).map(p => ({ path: p, type: 'blob' })) };
    }
    const prefix = '/repos/' + IMAGE_REPO + '/contents/';
    assert.ok(url.startsWith(prefix));
    assert.ok(url.endsWith('?ref=' + imageCommit), 'all files use one immutable image commit');
    const name = url.slice(prefix.length).split('?')[0];
    assert.ok(Object.hasOwn(files, name));
    return { encoding: 'base64', size: files[name].length, content: Buffer.from(files[name]).toString('base64') };
  };
}

test('derive only selected parent chain, provenance, release refs and both generator pins', async () => {
  const get = await fixture(), regular = await inventory(get), full = await inventory(get, { target: 'fedora45-ai-safrano9999-full' });
  assert.equal(regular.repositories.length, 22);
  assert.equal(full.repositories.length, 23);
  assert.equal(regular.chain.length, 5);
  assert.equal(full.chain.length, 6);
  assert.ok(!regular.repositories.some(r => r.repository.endsWith('/VikAI')));
  assert.ok(full.repositories.some(r => r.repository.endsWith('/VikAI')));
  assert.ok(!full.repositories.some(r => r.repository === 'safrano9999/SCRIPTS'));
  for (const name of ['openclaw', 'hermes']) assert.match(regular.repositories.find(r => r.repository.endsWith('/' + name + '-ephemeral')).ref, /^[a-f0-9]{40}$/);
  assert.equal(regular.repositories.find(r => r.repository.endsWith('/NOTE')).ref, '2026.8.4');
  assert.equal(regular.repositories.find(r => r.repository.endsWith('/openclaw-deterministic-latest')).ref, '2026.9.4-deterministic.2');
});

test('latest preview refreshes every Git source and both release inputs instead of reusing old pins', async () => {
  const files = await fixture();
  const patch = { tag_name: '2026.9.4-deterministic.99', assets: [{ name: 'openclaw-2026.9.4-deterministic.tar.gz', digest: 'sha256:' + 'c'.repeat(64) }] };
  const note = { tag_name: '2026.9.99', assets: [{ name: 'note-latest.zip', digest: 'sha256:' + 'd'.repeat(64) }] };
  const get = async url => {
    if (url.endsWith('/NEXTCLOUD/releases/tags/latest')) return {assets:['nextcloud-fedora64-plugin-latest.zip','nextcloud-fedora64-plugin-latest.zip.sha256'].map((name,i)=>({name,id:i+1,digest:'sha256:'+'a'.repeat(64)}))};
    if (url.endsWith('/openclaw-deterministic-latest/releases/latest')) return { ...patch, tag_name: 'latest' };
    if (url.endsWith('/openclaw-deterministic-latest/releases?per_page=100')) return [patch];
    if (url.endsWith('/NOTE/releases/latest')) return note;
    return files(url);
  };
  const preview = await inventory(get, { latest: true, target: 'fedora45-ai-safrano9999-full' });
  assert.equal(preview.repositories.length, 23);
  assert.equal(preview.source_policy, 'latest-resolved-for-preview');
  for (const entry of preview.repositories) {
    if (entry.repository === IMAGE_REPO) assert.equal(entry.ref, imageCommit);
    else if (entry.repository.endsWith('/NOTE')) {
      assert.equal(entry.ref, note.tag_name);
      assert.equal(entry.release.sha256, 'd'.repeat(64));
    } else if (entry.repository.endsWith('/openclaw-deterministic-latest')) {
      assert.equal(entry.ref, patch.tag_name);
      assert.equal(entry.release.sha256, 'c'.repeat(64));
    } else assert.equal(entry.ref, 'HEAD', entry.repository);
  }
  assert.match(preview.repositories.find(r => r.repository.endsWith('/hermes-ephemeral')).declared_ref, /^[a-f0-9]{40}$/);
  const resolved = await resolveCommits(async url => ({ sha: url.endsWith('/' + imageCommit) ? imageCommit : 'b'.repeat(40) }), preview);
  assert.ok(resolved.repositories.every(r => /^[a-f0-9]{40}$/.test(r.commit)));
});

test('latest release selection checks compatible assets and fails on invalid release evidence', async () => {
  const asset = 'openclaw-2026.9.4-deterministic.tar.gz';
  const release = { tag_name: '2026.9.4-deterministic.2', assets: [{ name: asset, digest: 'sha256:' + 'a'.repeat(64) }] };
  const fallback = await latestRelease(async url => url.endsWith('/latest') ? { tag_name: '2026.9.5', assets: [] } : [release], 'safrano9999/FIXTURE', asset);
  assert.equal(fallback.ref, release.tag_name);
  for (const change of [{ draft: true }, { prerelease: true }, { tag_name: '../bad' }, { assets: [{ name: asset }] }]) {
    await assert.rejects(latestRelease(async () => ({ ...release, ...change }), 'safrano9999/FIXTURE', asset));
  }
});

test('missing version-specific patch reports the required asset without selecting an older version', async () => {
  const requested = 'openclaw-2026.9.5-deterministic.tar.gz';
  const old = { tag_name: '2026.9.4-deterministic.2', assets: [
    { name: 'openclaw-2026.9.4-deterministic.tar.gz', digest: 'sha256:' + 'a'.repeat(64) },
  ] };
  await assert.rejects(latestRelease(async url => url.endsWith('/latest') ? old : [old],
    'safrano9999/openclaw-deterministic-latest', requested),
  { message: 'No compatible latest release: safrano9999/openclaw-deterministic-latest (required asset: ' + requested + ')' });
});

test('repository CSV changes, @branch and deduplication are derived, never a hard-coded list', async () => {
  const conf = 'AI_CORE_IMAGE=ghcr.io/safrano9999/fedora45-ai-core:latest\nEXTENSIONS="EXAMPLE@feature/topic"\nSTANDALONE="EXAMPLE@feature/topic,NEW_REPO"\n';
  const result = await inventory(await fixture({ 'fedora45-ai-base/build.conf': conf }), { target: 'fedora45-ai-base' });
  const entry = result.repositories.find(r => r.repository === 'safrano9999/EXAMPLE');
  assert.equal(entry.ref, 'feature/topic');
  assert.equal(entry.sources.length, 2);
  assert.ok(result.repositories.some(r => r.repository.endsWith('/NEW_REPO')));
  assert.ok(!result.repositories.some(r => r.repository.endsWith('/WELCOME')));
});

test('dynamic shell declarations and broken/cyclic parent graphs fail closed', async () => {
  for (const conf of [
    'AI_CORE_IMAGE=$(touch /tmp/never-run)\n',
    'AI_CORE_IMAGE=ghcr.io/safrano9999/fedora45-ai-base:latest\n',
    'AI_CORE_IMAGE=ghcr.io/safrano9999/fedora45-ai-missing:latest\n',
    'AI_CORE_IMAGE=ghcr.io/safrano9999/fedora45-ai-core:latest\nEXTENSIONS="../bad"\n',
  ]) await assert.rejects(inventory(await fixture({ 'fedora45-ai-base/build.conf': conf }), { target: 'fedora45-ai-base' }));
  await assert.rejects(inventory(await fixture(), { ref: '../bad' }));
});

test('every moving source ref is resolved once to a full commit before filesystem work', async () => {
  const source = await inventory(await fixture());
  const resolved = await resolveCommits(async url => ({ sha: /^[a-f0-9]{40}$/.test(url.split('/').at(-1)) ? url.split('/').at(-1) : 'b'.repeat(40) }), source);
  assert.ok(resolved.repositories.every(r => /^[a-f0-9]{40}$/.test(r.commit)));
  await assert.rejects(resolveCommits(async () => ({ sha: 'main' }), source));
});

test('NO_UPDATE and VALIDATED_ONLY skip credential access, network and volume mutations', async () => {
  for (const status of ['NO_UPDATE', 'VALIDATED_ONLY', 'BLOCKED']) {
    const input = [{ json: { status } }];
    const result = await Fedora45Sources.prototype.execute.call({ getInputData: () => input,
      getNodeParameter: () => 'sync', getCredentials: () => { throw Error('must not access'); } });
    assert.deepEqual(result, [input]);
  }
  await assert.rejects(Fedora45Sources.prototype.execute.call({ getInputData: () => [{ json: { status: 'READY_FOR_BUILD' } }], getNodeParameter: () => 'sync' }));
});

test('initial clone and subsequent exact-commit update stay depth 1; dirty files survive', async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'fedora45-sources-test-'));
  const source = path.join(temp, 'upstream'), volume = path.join(temp, 'volume');
  const git = async args => (await execFile('git', args)).stdout.trim();
  try {
    await git(['init', '-q', source]);
    await git(['-C', source, 'config', 'user.email', 'fixture@example.invalid']);
    await git(['-C', source, 'config', 'user.name', 'Fixture']);
    const commits = [];
    for (const text of ['one', 'two', 'three']) {
      await fs.writeFile(path.join(source, 'input'), text);
      await git(['-C', source, 'add', 'input']);
      await git(['-C', source, 'commit', '-qm', text]);
      commits.push(await git(['-C', source, 'rev-parse', 'HEAD']));
    }
    const manifest = { repositories: [{ repository: 'safrano9999/FIXTURE', commit: commits[0] }] };
    const remote = () => 'file://' + source;
    const first = await syncSources(manifest, 'Bearer fixture-secret', volume, remote);
    assert.equal(first.checkouts[0].action, 'cloned');
    const checkout = path.join(volume, 'FIXTURE');
    assert.equal(await fs.readFile(path.join(checkout, 'input'), 'utf8'), 'one');
    manifest.repositories[0].commit = commits[2];
    const next = await syncSources(manifest, 'Bearer fixture-secret', volume, remote);
    assert.equal(next.checkouts[0].action, 'updated');
    assert.equal(await git(['-C', checkout, 'rev-list', '--count', 'HEAD']), '1');
    assert.equal(await fs.readFile(path.join(checkout, 'input'), 'utf8'), 'three');
    assert.ok(!(await fs.readFile(path.join(checkout, '.git/config'), 'utf8')).includes('fixture-secret'));
    await fs.writeFile(path.join(checkout, 'input'), 'local edit');
    await assert.rejects(syncSources(manifest, 'Bearer fixture-secret', volume, remote), /Local changes preserved/);
    assert.equal(await fs.readFile(path.join(checkout, 'input'), 'utf8'), 'local edit');
    await fs.mkdir(path.join(volume, '.sync-lock'));
    await assert.rejects(syncSources(manifest, 'Bearer fixture-secret', volume, remote), /locked/);
  } finally { await fs.rm(temp, { recursive: true, force: true }); }
});
