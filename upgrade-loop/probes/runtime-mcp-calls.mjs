// Exercise the installed OpenClaw MCP projection and transport with reviewed read-only calls.
// This checks server assignment; the separate model probe checks the model/harness path.
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [packageDir, baselinePath] = process.argv.slice(2);
const baseline = JSON.parse(fs.readFileSync(baselinePath));
const api = (file) => import(pathToFileURL(path.join(packageDir, "dist", file)));
const { loadConfig } = await api("plugin-sdk/config-runtime.js");
const cfg = loadConfig();
const { acquireSessionMcpRuntime, releaseSessionMcpRuntime, retireSessionMcpRuntime } =
  await api("agents/agent-bundle-mcp-manager-api.js");
const { resolveCodexMcpToolOverridesForAgent } = await api("plugin-sdk/codex-mcp-projection.js");
const { resolveAgentWorkspaceDir, resolveAgentDir } = await api("plugin-sdk/agent-runtime.js");
const probes = JSON.parse(fs.readFileSync(new URL("./mcp-read-calls.json", import.meta.url)));
const entries = cfg.agents.entries;
const agents = Array.isArray(entries) ? entries.map((entry) => entry.id) : Object.keys(entries);
assert.deepEqual(agents.toSorted(), Object.keys(baseline.openclaw_agents).toSorted());
const enabled = Object.keys(cfg.mcp.servers).filter((name) => cfg.mcp.servers[name].enabled !== false);
assert.deepEqual(enabled.toSorted(), Object.keys(probes).toSorted(), "Review changed MCP inventory");
const results = [];
for (const agent of agents) {
  const sessionId = `upgrade-mcp-${randomUUID()}`;
  const result = { agent, timestamp: new Date().toISOString(), calls: [], denied: [], status: "FAIL" };
  let lease;
  try {
    const expected = baseline.openclaw_agents[agent].mcp.toSorted();
    lease = await acquireSessionMcpRuntime({
      cfg, sessionId,
      workspaceDir: resolveAgentWorkspaceDir(cfg, agent),
      agentDir: resolveAgentDir(cfg, agent),
      toolOverrides: resolveCodexMcpToolOverridesForAgent(cfg, { agentId: agent }),
    });
    const catalog = await lease.runtime.getCatalog();
    assert.equal(catalog.diagnostics?.length ?? 0, 0, "Catalog diagnostics present");
    assert.deepEqual(Object.keys(catalog.servers).toSorted(), expected, "Server assignment mismatch");
    for (const server of expected) {
      const [tool, input] = probes[server];
      assert(catalog.tools.some((entry) => entry.serverName === server && entry.toolName === tool));
      const reply = await lease.runtime.callTool(server, tool, input);
      assert.notEqual(reply.isError, true, "Read-only tool returned an error");
      assert(Array.isArray(reply.content) || reply.structuredContent !== undefined);
      result.calls.push({ server, tool, status: "PASS" });
    }
    for (const server of enabled.filter((name) => !expected.includes(name))) {
      const [tool, input] = probes[server];
      await assert.rejects(
        lease.runtime.callTool(server, tool, input),
        (error) => error.message === `bundle-mcp server "${server}" is not connected`,
      );
      result.denied.push({ server, status: "PASS" });
    }
    result.status = "PASS";
  } catch (error) {
    result.error = error.name; // Never include tool content, credentials or raw transport errors.
  } finally {
    if (lease) {
      await releaseSessionMcpRuntime(lease);
      if (!(await retireSessionMcpRuntime({ sessionId, reason: "upgrade-probe" }))) result.status = "FAIL";
      await lease.runtime.joinCleanup?.();
    }
  }
  results.push(result);
}
const passed = results.length > 0 && results.every((result) => result.status === "PASS");
console.log(JSON.stringify({ status: passed ? "PASS" : "FAIL", results }, null, 2));
process.exitCode = passed ? 0 : 1;
