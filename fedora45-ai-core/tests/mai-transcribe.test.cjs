const test = require("node:test");
const assert = require("node:assert/strict");
const register = require("../build/voice-plugins/mai-transcribe");

function withEnv(values, run) {
  const keys = ["MAI_TRANSCRIBE_REGION", "MAI_TRANSCRIBE_MODEL", "MAI_TRANSCRIBE_API_VERSION",
    "MAI_TRANSCRIBE_MAX_FILE_SIZE", "MAI_TRANSCRIBE_API_KEY", "SPEECH_API_KEY"];
  const original = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    for (const key of keys) {
      if (values[key] === undefined) delete process.env[key]; else process.env[key] = values[key];
    }
    run();
  } finally {
    for (const key of keys) {
      if (original[key] === undefined) delete process.env[key]; else process.env[key] = original[key];
    }
  }
}

function load(pluginConfig = {}) {
  const captured = {};
  register({ pluginConfig, registerMediaUnderstandingProvider: (p) => { captured.provider = p; },
    registerTool: (tool) => { captured.tool = tool; } });
  return captured;
}

test("MAI registers without credentials but does not claim authentication", () => {
  withEnv({}, () => {
    const { provider } = load();
    assert.deepEqual(provider.defaultModels, { audio: "MAI-Transcribe-2" });
    assert.equal(provider.resolveAuth(), null);
  });
});

test("Injected MAI key and model reach the provider without config files", () => {
  withEnv({ MAI_TRANSCRIBE_API_KEY: "test-only-bearer", MAI_TRANSCRIBE_MODEL: "chosen-model" }, () => {
    const { provider, tool } = load();
    assert.equal(provider.defaultModels.audio, "chosen-model");
    assert.equal(provider.resolveAuth().apiKey, "test-only-bearer");
    assert.match(tool.description, /chosen-model/);
  });
});

test("Explicit init configuration still overrides the generic preset", () => {
  withEnv({ MAI_TRANSCRIBE_MODEL: "preset", MAI_TRANSCRIBE_API_KEY: "test-env" }, () => {
    const { provider } = load({ model: "init-choice", apiKey: "test-inline" });
    assert.equal(provider.defaultModels.audio, "init-choice");
    assert.equal(provider.resolveAuth().apiKey, "test-inline");
  });
});

test("Invalid maximum file size fails at registration", () => {
  withEnv({ MAI_TRANSCRIBE_MAX_FILE_SIZE: "NaN" }, () => assert.throws(() => load(), /positive integer/));
});
