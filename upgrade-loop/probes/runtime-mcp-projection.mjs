// Check the installed OpenClaw projection against the frozen, secret-free inventory.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [packageDir, baselinePath] = process.argv.slice(2);
const baseline = JSON.parse(fs.readFileSync(baselinePath));
const config = JSON.parse(fs.readFileSync("/root/.openclaw/openclaw.json"));
const { buildCodexUserMcpServersThreadConfigPatch, resolveCodexMcpToolOverridesForAgent } =
  await import(pathToFileURL(path.join(packageDir, "dist/plugin-sdk/codex-mcp-projection.js")));
const entries = config.agents.entries;
const agents = Array.isArray(entries) ? entries.map((entry) => entry.id) : Object.keys(entries);
assert.deepEqual(agents.toSorted(), Object.keys(baseline.openclaw_agents).toSorted());
const results = [];
for (const agentId of agents) {
  const expected = baseline.openclaw_agents[agentId].mcp.toSorted();
  const overrides = resolveCodexMcpToolOverridesForAgent(config, { agentId });
  const projected = buildCodexUserMcpServersThreadConfigPatch(config, { agentId });
  assert.deepEqual(Object.keys(projected?.mcp_servers ?? {}).toSorted(), expected, agentId);
  for (const [server, settings] of Object.entries(config.mcp.servers)) {
    if (settings.enabled !== false && !expected.includes(server)) {
      assert.equal(overrides?.mcpServers?.[server], false, `${agentId}/${server}`);
    }
  }
  results.push({ agent: agentId, status: "PASS", servers: expected });
}
console.log(JSON.stringify({ status: "PASS", results }, null, 2));
