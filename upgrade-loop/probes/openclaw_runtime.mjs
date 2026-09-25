import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

const source = process.env.UPGRADE_TARGET_SOURCE;
const installed = process.env.UPGRADE_TARGET_PACKAGE;
// Bundle filenames and export aliases change between releases. Resolve the exact
// named functions exported by the selected installed release; never substitute
// a copied validator or silently accept a missing interface.
const required = new Map([['validateConfigObjectRaw', []], ['resolveDefaultModelForAgent', []]]);
for (const file of fs.readdirSync(path.join(installed, 'dist'))) {
  if (!file.endsWith('.mjs')) continue;
  const location = path.join(installed, 'dist', file);
  const text = fs.readFileSync(location, 'utf8');
  const exports = text.match(/^export \{([^}]+)\}/m)?.[1];
  for (const entry of exports?.split(',') ?? []) {
    const [name, alias = name] = entry.trim().split(/\s+as\s+/);
    if (required.has(name) && new RegExp(`^function ${name}\\(`, 'm').test(text))
      required.get(name).push([location, alias]);
  }
}
async function releaseFunction(name) {
  const candidates = required.get(name);
  assert.equal(candidates.length, 1, `Expected one exported upstream interface: ${name}`);
  const [file, alias] = candidates[0];
  const value = (await import(pathToFileURL(file).href))[alias];
  assert.equal(typeof value, 'function', `Missing upstream interface: ${name}`);
  return value;
}
const validateConfigObjectRaw = await releaseFunction('validateConfigObjectRaw');
const resolveDefaultModelForAgent = await releaseFunction('resolveDefaultModelForAgent');
const { resolveCodexMcpToolOverridesForAgent } = await import(
  pathToFileURL(path.join(installed, 'dist/plugin-sdk/codex-mcp-projection.js')).href);
const config = JSON.parse(fs.readFileSync(process.env.UPGRADE_CONFIG_FIXTURE));

test("exact upstream source and installed package versions", () => {
  assert.match(process.env.UPGRADE_TARGET_UPSTREAM_SHA, /^[0-9a-f]{40}$/);
  for (const directory of [source, installed]) {
    assert.equal(JSON.parse(fs.readFileSync(path.join(directory, "package.json"))).version,
                 process.env.UPGRADE_TARGET_VERSION);
  }
});

test("upstream accepts generated agent, model, MCP and plugin configuration", () => {
  const result = validateConfigObjectRaw(config);
  assert.equal(result.ok, true, JSON.stringify(result.issues));
});

test("upstream rejects an invalid generated agent roster", () => {
  const broken = structuredClone(config);
  broken.agents.entries = "invalid";
  assert.equal(validateConfigObjectRaw(broken).ok, false);
});

test("both agents inherit the configured default model", () => {
  for (const agentId of ["main", "mtg"]) {
    assert.equal(config.agents.entries[agentId].model, undefined);
    const model = resolveDefaultModelForAgent({cfg: config, agentId, allowManifestNormalization: false});
    assert.deepEqual(model, {provider: "fixture", model: "model-a"});
  }
});

test("upstream applies exact MCP assignments and preserves the shared server", () => {
  for (const [agentId, denied, allowed] of [["main", "mtg", "general"], ["mtg", "general", "mtg"]]) {
    const overrides = resolveCodexMcpToolOverridesForAgent(config, {agentId});
    assert.equal(overrides.mcpServers[denied], false);
    assert.notEqual(overrides.mcpServers[allowed], false);
    assert.notEqual(overrides.mcpServers.shared, false);
  }
});

function help(args) {
  const result = spawnSync(process.execPath, [path.join(installed, "openclaw.mjs"), ...args, "--help"],
                           {env: process.env, encoding: "utf8", timeout: 60000});
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}

test("target cron CLI supports the generator's scheduled command arguments", () => {
  const text = help(["cron", "add"]);
  for (const flag of ["--cron", "--name", "--agent", "--session", "--tz", "--exact",
                      "--command-argv", "--timeout-seconds", "--no-deliver", "--json"]) {
    assert.ok(text.includes(flag), `Missing target cron option: ${flag}`);
  }
});

test("target plugin registry supports generator refresh", () => {
  assert.ok(help(["plugins", "registry"]).includes("--refresh"));
});
