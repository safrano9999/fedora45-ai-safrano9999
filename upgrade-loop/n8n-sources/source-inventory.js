'use strict';

// Parse build declarations as data. Never source a build.conf or run repository code.
const OWNER = 'safrano9999';
const IMAGE_REPO = OWNER + '/fedora45-ai-safrano9999';
const DEFAULT_TARGET = 'fedora45-ai-safrano9999';
const SHA = /^[0-9a-f]{40}$/;

function literal(text, key, required = true) {
  const matches = [...text.matchAll(new RegExp('^' + key + '=(.*)$', 'gm'))];
  if (!matches.length && !required) return null;
  if (matches.length !== 1) throw new Error('Missing/duplicate declaration: ' + key);
  let value = matches[0][1].trim();
  if ((value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
  if (/[\s$`\\;|&<>"']/.test(value)) throw new Error('Expected literal declaration: ' + key);
  return value;
}

function validRef(ref) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(ref) || ref.includes('..') ||
      ref.includes('//') || ref.endsWith('/') || ref.endsWith('.lock')) {
    throw new Error('Invalid Git reference');
  }
  return ref;
}

async function latestRelease(get, repository, assetName) {
  const base = '/repos/' + repository + '/releases';
  let release = await get(base + '/latest'), releases;
  const stable = r => !r.draft && !r.prerelease;
  const asset = r => (r.assets || []).find(a => a.name === assetName);
  async function compatible() {
    releases = releases || await get(base + '?per_page=100');
    return releases.filter(r => stable(r) && asset(r)).sort((a, b) =>
      String(asset(b).updated_at || b.published_at || '').localeCompare(String(asset(a).updated_at || a.published_at || '')));
  }
  if (!stable(release)) throw new Error('Expected a stable release: ' + repository);
  if (assetName && !asset(release)) release = (await compatible())[0];
  if (!release) throw new Error('No compatible latest release: ' + repository + ' (required asset: ' + assetName + ')');
  const digest = assetName ? asset(release).digest : null;
  if (assetName && !/^sha256:[0-9a-f]{64}$/.test(digest || '')) throw new Error('Missing asset checksum: ' + repository);
  if (assetName && release.tag_name === 'latest') {
    release = (await compatible()).find(r => r.tag_name !== 'latest' && asset(r).digest === digest) || release;
  }
  return { ref: validRef(release.tag_name), ...(assetName ? { asset: assetName, sha256: digest.slice(7) } : {}) };
}

async function inventory(get, { target = DEFAULT_TARGET, ref = 'main', latest = false, openclawVersion } = {}) {
  if (!/^fedora45-ai-[a-z0-9-]+$/.test(target)) throw new Error('Invalid image target');
  const commit = (await get('/repos/' + IMAGE_REPO + '/commits/' + encodeURIComponent(validRef(ref)))).sha;
  if (!SHA.test(commit || '')) throw new Error('Missing image source commit');
  const tree = await get('/repos/' + IMAGE_REPO + '/git/trees/' + commit + '?recursive=1');
  if (tree.truncated || !Array.isArray(tree.tree)) throw new Error('Incomplete image repository tree');
  const paths = new Set(tree.tree.filter(x => x.type === 'blob').map(x => x.path));
  async function file(path) {
    if (!paths.has(path)) throw new Error('Missing build input: ' + path);
    const result = await get('/repos/' + IMAGE_REPO + '/contents/' + path + '?ref=' + commit);
    if (result.encoding !== 'base64' || result.size > 1000000) throw new Error('Invalid build input: ' + path);
    return Buffer.from(result.content, 'base64').toString('utf8');
  }
  const repos = new Map(), chain = [], runtimeAssets = new Map();
  function add(repo, sourceRef, layer, file, kind, selection = { type: 'default-branch' }) {
    if (!repo.startsWith(OWNER + '/') && repo !== 'f24sales/litellm-free') return;
    if (!/^safrano9999\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(repo) && repo !== 'f24sales/litellm-free') throw new Error('Invalid Safrano repository');
    validRef(sourceRef);
    const key = repo.toLowerCase(), old = repos.get(key);
    if (old && old.ref !== sourceRef) throw new Error('Conflicting source refs: ' + repo);
    const entry = old || { repository: repo, ref: sourceRef, selection, sources: [] };
    entry.sources.push({ layer, file, kind });
    repos.set(key, entry);
  }
  add(IMAGE_REPO, commit, target, 'Containerfile chain', 'image-definition');
  let layer = target, externalBase, openclawSource;
  while (layer) {
    if (chain.includes(layer) || chain.length >= 16) throw new Error('Cyclic or excessive image chain');
    chain.push(layer);
    const cfPath = layer + '/Containerfile', confPath = layer + '/build.conf';
    const cf = await file(cfPath), conf = await file(confPath);
    if (layer === 'fedora45-ai-core-pre') {
      const current = cf.match(/^ARG OPENCLAW_VERSION=(\d+\.\d+\.\d+)$/m)?.[1];
      const override = cf.match(/^ARG OPENCLAW_UPSTREAM_SHA=([0-9a-f]*)$/m)?.[1] ?? '';
      if (/^ARG OPENCLAW_UPSTREAM_SHA=/m.test(cf) && !/^ARG OPENCLAW_UPSTREAM_SHA=(?:[0-9a-f]{40})?$/m.test(cf)) throw new Error('Invalid Core-pre source override');
      if (!current || override === undefined || (override && !SHA.test(override))) {
        throw new Error('Invalid OpenClaw source pins in Core-pre Containerfile');
      }
      const version = openclawVersion || current;
      openclawSource = { version, override_commit: version === current ? override : '' };
    }
    // Report the direct Containerfile sources; these commits are diagnostic
    // evidence only and are never injected into the image build.
    for (const match of cf.matchAll(/^ADD(?: --[\w-]+=[^ ]+)* https:\/\/github\.com\/(safrano9999\/[A-Za-z0-9._-]+)\.git(?:\s)/gm)) {
      add(match[1], 'HEAD', layer, cfPath, 'containerfile-git');
    }
    if (layer === 'fedora45-ai-core') {
      const declared = cf.replace(/^ARG /gm, '');
      const tag = literal(declared, 'OPENCLAW_DETERMINISTIC_TAG');
      const version = openclawVersion || literal(declared, 'OPENCLAW_VERSION');
      add(OWNER + '/openclaw-deterministic-latest', tag, layer, cfPath, 'containerfile-release',
          { type: 'pinned-release', asset: 'openclaw-' + version + '-deterministic.tar.gz' });
      add(OWNER + '/NOTE', 'latest', layer, cfPath, 'containerfile-release',
          { type: 'latest-release', asset: 'note-latest.zip' });
    }
    if (/--repo safrano9999\/NEXTCLOUD\s/.test(cf)) {
      runtimeAssets.set(OWNER + '/NEXTCLOUD', ['nextcloud-fedora64-plugin-latest.zip', 'nextcloud-fedora64-plugin-latest.zip.sha256']);
    }
    for (const key of ['EXTENSIONS', 'STANDALONE']) {
      const value = literal(conf, key, false);
      if (value === null || value === '') continue;
      for (const entry of value.split(',')) {
        const match = entry.match(/^([A-Za-z0-9][A-Za-z0-9._-]*)$/);
        if (!match) throw new Error('Invalid ' + key + ' repository specification');
        const declared = repos.get((OWNER + '/' + match[1]).toLowerCase());
        if (!declared?.sources.some(source => source.layer === layer && source.kind === 'containerfile-git')) {
          throw new Error('Runtime role has no Containerfile Git source: ' + match[1]);
        }
      }
    }
    const from = [...cf.matchAll(/^FROM\s+(\S+)(?:\s+AS\s+\S+)?\s*$/gim)];
    if (!from.length) throw new Error('Missing final FROM in ' + cfPath);
    let image = from.at(-1)[1];
    const arg = image.match(/^\$\{([A-Z][A-Z0-9_]*)\}$/);
    if (arg) image = literal(conf, arg[1]);
    if (image.includes('$')) throw new Error('Unresolved parent image');
    if (image.startsWith('ghcr.io/' + OWNER + '/')) {
      const parent = image.match(/^ghcr\.io\/safrano9999\/(fedora45-ai-[a-z0-9-]+)(?::[A-Za-z0-9._-]+|@sha256:[a-f0-9]{64})?$/);
      if (!parent) throw new Error('Unrecognized Safrano parent image');
      layer = parent[1];
    } else {
      externalBase = image;
      layer = null;
    }
  }
  if (latest) {
    for (const entry of repos.values()) {
      if (entry.repository === IMAGE_REPO) continue;
      entry.declared_ref = entry.ref;
      if (entry.selection.type === 'pinned-release') {
        const release = await get('/repos/' + entry.repository + '/releases/tags/' + entry.ref);
        const asset = (release.assets || []).find(a => a.name === entry.selection.asset);
        if (release.draft || release.prerelease || !/^sha256:[0-9a-f]{64}$/.test(asset?.digest || '')) {
          throw new Error('Missing pinned Deterministic release asset');
        }
        entry.release = {ref: entry.ref, asset: entry.selection.asset, sha256: asset.digest.slice(7)};
      } else if (entry.selection.type === 'latest-release') {
        const selected = await latestRelease(get, entry.repository, entry.selection.asset);
        entry.ref = selected.ref;
        entry.release = selected;
      } else entry.ref = 'HEAD';
      if (runtimeAssets.has(entry.repository)) {
        const release = await get('/repos/' + entry.repository + '/releases/tags/latest');
        if (release.draft || release.prerelease) throw new Error('Invalid runtime asset release');
        entry.runtime_assets = runtimeAssets.get(entry.repository).map(name => {
          const matches = (release.assets || []).filter(a => a.name === name);
          if (matches.length !== 1 || !Number.isSafeInteger(matches[0].id) || matches[0].id <= 0 ||
              !/^sha256:[0-9a-f]{64}$/.test(matches[0].digest || '')) throw new Error('Invalid runtime asset: ' + name);
          return { name, id: matches[0].id, sha256: matches[0].digest.slice(7) };
        });
      }
    }
  }
  return { schema_version: 1, target, image_repository: IMAGE_REPO, image_commit: commit,
    ...(openclawSource ? { openclaw_source: openclawSource } : {}),
    chain: chain.reverse(), external_base: externalBase, repositories: [...repos.values()],
    source_policy: 'containerfile-latest-with-pinned-deterministic',
    clone_depth: 1, clone_gate: 'READY_FOR_BUILD', local_build: false };
}

async function resolveCommits(get, manifest) {
  const repositories = [];
  let source = manifest.openclaw_source;
  if (source) {
    const commit = source.override_commit || (await get('/repos/openclaw/openclaw/commits/v' + source.version)).sha;
    if (!SHA.test(commit || '')) throw new Error('Invalid Core-pre selected source commit');
    source = { ...source, commit };
  }
  for (const entry of manifest.repositories) {
    const result = await get('/repos/' + entry.repository + '/commits/' + encodeURIComponent(entry.ref));
    if (!SHA.test(result.sha || '') || (SHA.test(entry.ref) && result.sha !== entry.ref)) {
      throw new Error('Source commit mismatch: ' + entry.repository);
    }
    repositories.push({ ...entry, commit: result.sha });
  }
  return { ...manifest, ...(source ? { openclaw_source: source } : {}), resolved_at: new Date().toISOString(), repositories };
}

module.exports = { inventory, resolveCommits, latestRelease, DEFAULT_TARGET, IMAGE_REPO, SHA, literal, validRef };
