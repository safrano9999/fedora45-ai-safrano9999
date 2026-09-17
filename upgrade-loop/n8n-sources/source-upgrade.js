'use strict';

const { createHash } = require('node:crypto');
const { inventory, IMAGE_REPO, SHA } = require('./source-inventory');
const { validateSnapshot, snapshotId, canonical } = require('./source-snapshot');
const KEYS = Object.freeze({
  'fedora45-ai-core-pre': 'fedora45_core_pre', 'fedora45-ai-core': 'fedora45_core',
  'fedora45-ai-base': 'fedora45_base', 'fedora45-ai-kachelmann': 'fedora45_kachelmann',
  'fedora45-ai-safrano9999': 'fedora45_safrano', 'fedora45-ai-safrano9999-full': 'fedora45_safrano_full',
});
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

// Registry reads only. Follow blob redirects without forwarding registry credentials.
async function registryRequest(url, headers = {}, redirects = 0) {
  const response = await fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(30000) });
  if ([301, 302, 303, 307, 308].includes(response.status)) {
    const next = new URL(response.headers.get('location'), url);
    if (redirects >= 3 || next.protocol !== 'https:' || next.username || next.password ||
        !['ghcr.io', 'pkg-containers.githubusercontent.com'].includes(next.hostname)) {
      throw new Error('Unexpected registry redirect');
    }
    return registryRequest(next.href, {}, redirects + 1);
  }
  if (!response.ok) {
    const error = new Error('Published image lookup failed: HTTP ' + response.status);
    error.status = response.status; throw error;
  }
  if (Number(response.headers.get('content-length')) > 5000000) throw new Error('Registry metadata too large');
  const body = await response.text();
  if (Buffer.byteLength(body) > 5000000) throw new Error('Registry metadata too large');
  return body;
}

async function publishedImage(layer, credential = '', request = registryRequest) {
  if (!KEYS[layer]) throw new Error('Unsupported published image');
  const repository = 'safrano9999/' + layer;
  const tokenUrl = 'https://ghcr.io/token?service=ghcr.io&scope=' + encodeURIComponent('repository:' + repository + ':pull');
  let auth;
  try { auth = JSON.parse(await request(tokenUrl)); }
  catch (error) {
    if (![401, 403].includes(error.status) || !credential.startsWith('Bearer ')) throw error;
    auth = JSON.parse(await request(tokenUrl, { Authorization: 'Basic ' + Buffer.from('safrano9999:' + credential.slice(7)).toString('base64') }));
  }
  const token = auth.token || auth.access_token;
  if (typeof token !== 'string' || !token || /[\r\n]/.test(token)) throw new Error('Missing registry pull token');
  const headers = { Authorization: 'Bearer ' + token,
    Accept: 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' };
  async function read(kind, ref) {
    const raw = await request('https://ghcr.io/v2/' + repository + '/' + kind + '/' + ref, headers);
    const digest = 'sha256:' + createHash('sha256').update(raw).digest('hex');
    if (DIGEST.test(ref) && digest !== ref) throw new Error('Registry metadata digest mismatch');
    return { value: JSON.parse(raw), digest };
  }
  let manifest = await read('manifests', 'latest');
  const digest = manifest.digest;
  if (manifest.value.manifests) {
    const matches = manifest.value.manifests.filter(m => m.platform?.os === 'linux' && m.platform?.architecture === 'amd64');
    if (matches.length !== 1 || !DIGEST.test(matches[0].digest)) throw new Error('Missing unambiguous amd64 image');
    manifest = await read('manifests', matches[0].digest);
  }
  if (!DIGEST.test(manifest.value.config?.digest || '')) throw new Error('Missing image configuration');
  const config = (await read('blobs', manifest.value.config.digest)).value;
  const revision = config.config?.Labels?.['org.opencontainers.image.revision'];
  if (!SHA.test(revision || '') || !Array.isArray(config.rootfs?.diff_ids) || !config.rootfs.diff_ids.length ||
      !config.rootfs.diff_ids.every(d => DIGEST.test(d))) throw new Error('Published image lacks verifiable source/layer evidence');
  return { image: layer, digest, revision, layers: config.rootfs.diff_ids };
}

function runtimeIdentity(entry) {
  return entry ? { commit: entry.commit, release: entry.release || null,
    runtime_assets: (entry.runtime_assets || []).map(a => ({ name: a.name, sha256: a.sha256 })).sort((a,b) => a.name.localeCompare(b.name)) } : null;
}

async function upgradePlan(get, manifest, versions, readImage) {
  const chain = manifest.chain;
  if (!chain?.length || chain.some(layer => !KEYS[layer]) || new Set(chain).size !== chain.length) throw new Error('Unsupported build chain');
  const cache = new Map();
  const cached = endpoint => {
    if (!cache.has(endpoint)) cache.set(endpoint, get(endpoint));
    return cache.get(endpoint);
  };
  const tree = async revision => {
    const result = await cached('/repos/' + IMAGE_REPO + '/git/trees/' + revision + '?recursive=1');
    if (result.truncated || !Array.isArray(result.tree)) throw new Error('Incomplete published source tree');
    return result.tree;
  };
  const currentTree = await tree(manifest.image_commit);
  const baseline = [], changes = [];
  let previous;
  for (let index = 0; index < chain.length; index++) {
    const layer = chain[index], image = await readImage(layer);
    if (image.image !== layer || !SHA.test(image.revision || '') || !DIGEST.test(image.digest || '')) throw new Error('Invalid published image evidence');
    const content = await cached('/repos/' + IMAGE_REPO + '/contents/upgrade-loop/prepared-sources.json?ref=' + image.revision);
    if (content.encoding !== 'base64' || content.size > 1000000) throw new Error('Missing published source snapshot');
    const snapshot = JSON.parse(Buffer.from(content.content, 'base64').toString('utf8'));
    validateSnapshot(snapshot, snapshotId(snapshot));
    const old = await inventory(cached, { target: layer, ref: image.revision });
    const oldEntries = new Map(snapshot.repositories.map(e => [e.repository.toLowerCase(), e]));
    const owned = entry => entry.repository !== IMAGE_REPO && entry.sources.some(s => chain.slice(0, index + 1).includes(s.layer));
    const selected = new Map(manifest.repositories.filter(owned).map(e => [e.repository.toLowerCase(), e]));
    const oldRepos = new Map(old.repositories.filter(e => e.repository !== IMAGE_REPO).map(e => [e.repository.toLowerCase(), e]));
    for (const name of new Set([...selected.keys(), ...oldRepos.keys()])) {
      const wanted = selected.get(name), before = oldRepos.has(name) ? oldEntries.get(name) : null;
      if (oldRepos.has(name) && !before) throw new Error('Published snapshot omits consumed repository: ' + name);
      if (!equal(runtimeIdentity(wanted), runtimeIdentity(before))) changes.push({ image: layer,
        repository: wanted?.repository || oldRepos.get(name).repository, reason: 'source-changed',
        before: before?.commit || null, after: wanted?.commit || null });
    }
    for (const name of ['openclaw', 'hermes']) {
      if (snapshot.versions[name].latest !== versions[name].latest) changes.push({ image: layer,
        repository: name, reason: 'upstream-version', before: snapshot.versions[name].latest, after: versions[name].latest });
    }
    const inputs = t => t.filter(f => f.type === 'blob' && chain.slice(0, index + 1).some(parent =>
      f.path.startsWith(parent + '/') && !f.path.includes('/CONTAINER/') && !/\.(md|example)$/.test(f.path)))
      .map(f => ({ path: f.path, sha: f.sha })).sort((a,b) => a.path.localeCompare(b.path));
    if (!equal(inputs(currentTree), inputs(await tree(image.revision)))) changes.push({ image: layer,
      repository: IMAGE_REPO, reason: 'image-definition-changed', before: image.revision, after: manifest.image_commit });
    if (previous && !previous.layers.every((digest, n) => image.layers[n] === digest)) changes.push({ image: layer,
      repository: IMAGE_REPO, reason: 'published-parent-changed', parent: previous.image });
    baseline.push({ image: layer, digest: image.digest, revision: image.revision });
    previous = image;
  }
  const start = chain.find(layer => changes.some(c => c.image === layer)) || null;
  return { schema_version: 1, required: Boolean(start), start_image: start, start_key: start ? KEYS[start] : null,
    cascade: Boolean(start && start !== manifest.target), target: manifest.target, baseline: 'published-latest-images', baseline_images: baseline, changes };
}

async function dependencyBaseline(get, image) {
  if (!SHA.test(image.revision || '') || !DIGEST.test(image.digest || '')) throw new Error('Invalid dependency image baseline');
  let policyHash = null;
  try {
    const content = await get('/repos/' + IMAGE_REPO + '/contents/upgrade-loop/build-dependencies-whitelist.json?ref=' + image.revision);
    if (content.encoding !== 'base64' || content.size > 100000) throw new Error('Invalid published dependency policy');
    const bytes = Buffer.from(content.content, 'base64');
    if (bytes.length > 100000) throw new Error('Invalid published dependency policy');
    JSON.parse(bytes.toString('utf8'));
    policyHash = createHash('sha256').update(bytes).digest('hex');
  } catch (error) { if (error.status !== 404) throw error; }
  return { revision: image.revision, digest: image.digest, policy_sha256: policyHash };
}

module.exports = { upgradePlan, publishedImage, dependencyBaseline, runtimeIdentity, KEYS };
