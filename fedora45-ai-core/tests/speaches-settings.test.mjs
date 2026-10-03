import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolveSettings, configSchema } from "../build/voice-plugins/speaches/settings.js";

test("Speech presets do not invent a deployed endpoint", () => {
  const settings = resolveSettings({}, {});
  assert.equal(settings.baseUrl, "");
  assert.equal(settings.sttModel, "Systran/faster-whisper-small");
  assert.equal(settings.ttsModel, "speaches-ai/piper-de_DE-thorsten-high");
  assert.equal(settings.ttsVoice, "thorsten");
  assert.equal(settings.responseFormat, "mp3");
});

test("Explicit plugin settings override injected environment and presets", () => {
  const settings = resolveSettings({ baseUrl: "https://speech.example/v1/", ttsVoice: "chosen" }, {
    SPEACHES_BASE_URL: "http://127.0.0.1:8000/v1", SPEACHES_TTS_VOICE: "other",
    SPEACHES_STT_MODEL: "custom/stt", SPEACHES_TTS_MODEL: "custom/tts", SPEACHES_TTS_RESPONSE_FORMAT: "wav",
  });
  assert.deepEqual(settings, { baseUrl: "https://speech.example/v1", sttModel: "custom/stt",
    ttsModel: "custom/tts", ttsVoice: "chosen", responseFormat: "wav" });
});

test("Unsafe endpoint settings fail without printing credentials", () => {
  for (const url of ["file:///tmp/x", "https://user:secret@host/v1", "https://host/v1?key=secret"]) {
    assert.throws(() => resolveSettings({ baseUrl: url }, {}), (error) => !error.message.includes("secret"));
  }
  assert.throws(() => resolveSettings({ responseFormat: "../../private" }, {}));
});

test("Runtime schema and discovery manifest agree", async () => {
  const manifest = JSON.parse(await readFile(new URL("../build/voice-plugins/speaches/openclaw.plugin.json", import.meta.url)));
  assert.deepEqual(manifest.configSchema, configSchema);
});
