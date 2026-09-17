'use strict';

const { createHash } = require('node:crypto');
const { IMAGE_REPO, SHA } = require('./source-inventory');

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
  return value;
}

function snapshotId(snapshot) {
  return createHash('sha256').update(JSON.stringify(canonical(snapshot))).digest('hex');
}

function validateSnapshot(snapshot, expectedId) {
  if (snapshot?.schema_version !== 1 || snapshot.source_policy !== 'latest-resolved-once' ||
      !SHA.test(snapshot.source_commit || '') || !/^fedora45-ai-[a-z0-9-]+$/.test(snapshot.target || '') ||
      !Array.isArray(snapshot.repositories) || snapshot.repositories.length === 0 || snapshot.repositories.length > 128 ||
      snapshotId(snapshot) !== expectedId) throw new Error('Invalid shared source snapshot');
  const seen = new Set();
  for (const entry of snapshot.repositories) {
    if (!/^safrano9999\/[A-Za-z0-9][A-Za-z0-9._-]*$/.test(entry.repository || '') ||
        entry.repository === IMAGE_REPO || seen.has(entry.repository.toLowerCase()) || !SHA.test(entry.commit || '')) {
      throw new Error('Invalid snapshot repository');
    }
    seen.add(entry.repository.toLowerCase());
    if (entry.release && (!/^[0-9a-f]{64}$/.test(entry.release.sha256 || '') ||
        !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(entry.release.asset || '') || entry.release.ref !== entry.ref)) {
      throw new Error('Invalid snapshot release');
    }
    for (const a of entry.runtime_assets || []) {
      if (!Number.isSafeInteger(a.id) || a.id <= 0 || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(a.name || '') ||
          !/^[0-9a-f]{64}$/.test(a.sha256 || '')) throw new Error('Invalid snapshot runtime asset');
    }
  }
  for (const name of ['openclaw', 'hermes']) {
    const v = snapshot.versions?.[name];
    if (!v || !/^\d+\.\d+\.\d+$/.test(v.current) || !/^\d+\.\d+\.\d+$/.test(v.latest)) throw new Error('Missing snapshot versions');
  }
  return snapshot;
}

function makeSnapshot(manifest, versions) {
  const snapshot = { schema_version: 1, source_policy: 'latest-resolved-once',
    source_commit: manifest.image_commit, target: manifest.target, chain: manifest.chain,
    resolved_at: manifest.resolved_at, versions,
    repositories: manifest.repositories.filter(e => e.repository !== IMAGE_REPO).map(e => ({
      repository: e.repository, ref: e.ref, commit: e.commit,
      ...(e.release ? { release: e.release } : {}),
      ...(e.runtime_assets ? { runtime_assets: e.runtime_assets } : {}),
    })) };
  validateSnapshot(snapshot, snapshotId(snapshot));
  return snapshot;
}

function cloneManifest(snapshot, buildCommit) {
  if (!SHA.test(buildCommit || '')) throw new Error('Missing prepared build commit');
  return { schema_version: 1, source_policy: snapshot.source_policy, source_snapshot_id: snapshotId(snapshot),
    target: snapshot.target, chain: snapshot.chain, resolved_at: snapshot.resolved_at,
    image_repository: IMAGE_REPO, image_commit: buildCommit, clone_depth: 1, clone_gate: 'READY_FOR_BUILD', local_build: false,
    repositories: [{ repository: IMAGE_REPO, ref: buildCommit, commit: buildCommit }, ...snapshot.repositories] };
}

module.exports = { makeSnapshot, snapshotId, validateSnapshot, cloneManifest };
