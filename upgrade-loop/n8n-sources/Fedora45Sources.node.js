'use strict';

const { inventory, resolveCommits, DEFAULT_TARGET, SHA } = require('./source-inventory');
const { syncSources } = require('./source-sync');

class Fedora45Sources {
  description = {
    displayName: 'Fedora45 Sources', name: 'fedora45Sources', group: ['transform'], version: 1,
    description: 'Resolve latest Safrano sources for previews; shallow sync prepared inputs only after READY_FOR_BUILD',
    defaults: { name: 'Fedora45 Sources' }, inputs: ['main'], outputs: ['main'],
    credentials: [{ name: 'httpHeaderAuth', required: true }],
    properties: [{ displayName: 'Operation', name: 'operation', type: 'options', default: 'inventory',
      options: [{ name: 'Inventory Only', value: 'inventory' }, { name: 'Sync Ready Build', value: 'sync' }] }],
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
    if (!['inventory', 'sync'].includes(operation)) throw new Error('Invalid source operation');
    const credentials = await this.getCredentials('httpHeaderAuth');
    if (String(credentials.name).toLowerCase() !== 'authorization' || !/^Bearer [^\r\n]+$/.test(credentials.value)) {
      throw new Error('Expected GitHub bearer credential');
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
      ref: operation === 'sync' ? request.build_commit : request.source_ref || 'main',
      latest: operation === 'inventory' });
    const resolved = await resolveCommits(get, manifest);
    if (operation === 'inventory') return [[{ json: { ...resolved, status: 'SOURCES_LISTED', cloned: false } }]];
    const synced = await syncSources(resolved, credentials.value);
    return [[{ json: { ...request, sources: synced, sources_ready: true } }]];
  }
}

module.exports = { Fedora45Sources };
