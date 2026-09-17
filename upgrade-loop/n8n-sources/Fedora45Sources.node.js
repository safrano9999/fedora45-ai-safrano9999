'use strict';

const { inventory, resolveCommits, DEFAULT_TARGET, SHA } = require('./source-inventory');
const { upgradePlan, publishedImage } = require('./source-upgrade');
const { syncSources } = require('./source-sync');
const { makeSnapshot, snapshotId, validateSnapshot, cloneManifest } = require('./source-snapshot');

class Fedora45Sources {
  description = {
    displayName: 'Fedora45 Sources', name: 'fedora45Sources', group: ['transform'], version: 1,
    description: 'Resolve latest Safrano sources for previews; shallow sync prepared inputs only after READY_FOR_BUILD',
    defaults: { name: 'Fedora45 Sources' }, inputs: ['main'], outputs: ['main'],
    credentials: [{ name: 'httpHeaderAuth', required: true }],
    properties: [{ displayName: 'Operation', name: 'operation', type: 'options', default: 'inventory',
      options: [{ name: 'Inventory Only', value: 'inventory' }, { name: 'Resolve Shared Build Sources', value: 'resolve' }, { name: 'Sync Ready Build', value: 'sync' }] }],
  };
  async execute() {
    const input = this.getInputData();
    if (input.length !== 1) throw new Error('Expected one preparation request');
    const request = input[0].json, operation = this.getNodeParameter('operation', 0);
    if (operation === 'sync' && request.status !== 'READY_FOR_BUILD') return [input];
    if (operation === 'sync' && (request.validate_only !== false || !SHA.test(request.build_commit || '') ||
        request.build_started !== false || request.image_pulled !== false || request.container_restarted !== false)) {
      throw new Error('Source sync requires a validated READY_FOR_BUILD result');
    }
    if (!['inventory', 'resolve', 'sync'].includes(operation)) throw new Error('Invalid source operation');
    if (operation === 'resolve' && request.update !== true && request.validate_only !== true && request.upgrade_safrano9999 !== true) {
      throw new Error('Source resolution requires an upstream update, upgrade-safrano9999 or explicit validation');
    }
    const credentials = await this.getCredentials('httpHeaderAuth');
    if (String(credentials.name).toLowerCase() !== 'authorization' || !/^Bearer [^\r\n]+$/.test(credentials.value)) {
      throw new Error('Expected GitHub bearer credential');
    }
    if (operation === 'sync') {
      const snapshot = validateSnapshot(request.source_snapshot, request.source_snapshot_id);
      if (snapshot.target !== request.target) throw new Error('Snapshot target differs from handoff');
      const synced = await syncSources(cloneManifest(snapshot, request.build_commit), credentials.value);
      return [[{ json: { ...request, sources: synced, sources_ready: true } }]];
    }
    const get = async endpoint => {
      if (!endpoint.startsWith('/repos/safrano9999/')) throw new Error('Repository outside Safrano scope');
      try {
        return await this.helpers.httpRequestWithAuthentication.call(this, 'httpHeaderAuth', {
          method: 'GET', url: 'https://api.github.com' + endpoint, json: true, timeout: 30000,
          headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
          disableFollowRedirect: true,
        });
      } catch { throw new Error('GitHub source lookup failed: ' + endpoint); }
    };
    const manifest = await inventory(get, { target: request.target || DEFAULT_TARGET,
      ref: request.source_ref || 'main', latest: true,
      openclawVersion: operation === 'resolve' ? request.versions?.openclaw?.latest : undefined });
    const resolved = await resolveCommits(get, manifest);
    if (operation === 'inventory') return [[{ json: { ...resolved, status: 'SOURCES_LISTED', cloned: false } }]];
    const snapshot = makeSnapshot(resolved, request.versions);
    if (request.upgrade_safrano9999 === true) {
      snapshot.upgrade_safrano9999 = true;
      snapshot.build_plan = await upgradePlan(get, resolved, request.versions,
        layer => publishedImage(layer, credentials.value));
      if (!snapshot.build_plan.required && request.validate_only !== true) {
        return [[{ json: { ...request, status: 'NO_UPDATE', build_required: false,
          build_plan: snapshot.build_plan, build_started: false, image_pulled: false, container_restarted: false } }]];
      }
    }
    const dispatch_body = JSON.stringify({ ref: 'main', inputs: { run_id: request.run_id,
      validate_only: request.validate_only === true, upgrade_safrano9999: request.upgrade_safrano9999 === true, source_snapshot: JSON.stringify(snapshot) } });
    if (Buffer.byteLength(dispatch_body) > 60000) throw new Error('Source snapshot exceeds dispatch limit');
    return [[{ json: { ...request, build_required: true, source_snapshot: snapshot, source_snapshot_id: snapshotId(snapshot), dispatch_body } }]];
  }
}

module.exports = { Fedora45Sources };
