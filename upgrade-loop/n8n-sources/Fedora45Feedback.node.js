'use strict';
const feedback = require('./completion-feedback');
class Fedora45Feedback {
  constructor() { feedback.start(); }
  description = {
    displayName: 'Fedora45 Completion Hook', name: 'fedora45Feedback', group: ['transform'], version: 1,
    description: 'Optional completion-only POST after the n8n execution finishes, including failure or cancellation',
    defaults: { name: 'Register completion hook' }, inputs: ['main'], outputs: ['main'], properties: [],
  };
  async execute() {
    feedback.start();
    const input = this.getInputData();
    if (input.length !== 1) throw new Error('Expected one preparation request');
    const item = input[0];
    let body = item.json.body;
    if (item.binary?.data) body = (await this.helpers.getBinaryDataBuffer(0, 'data')).toString('utf8').trim();
    if (typeof body === 'string' && body.startsWith('{')) body = JSON.parse(body);
    const options = { ...(item.json.query || {}), ...(body && typeof body === 'object' ? body : {}) };
    const feedbackId = await feedback.register(this.getExecutionId(), options);
    // The durable queue retains a private secret; don't propagate it through the graph.
    const json = { ...item.json, completion_feedback: {
      enabled: Boolean(feedbackId), feedback_id: feedbackId || null,
      next: await feedback.instructions(Boolean(feedbackId)),
    } };
    if (json.query && typeof json.query === 'object') {
      json.query = { ...json.query }; delete json.query.callback_secret;
    }
    if (body && typeof body === 'object') {
      body = { ...body }; delete body.callback_secret;
      return [[{ json: { ...json, body } }]];
    }
    return [[{ ...item, json }]];
  }
}
module.exports = { Fedora45Feedback };
