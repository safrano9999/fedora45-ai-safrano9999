import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { readFileSync } from "node:fs";
import { transcribeOpenAiCompatibleAudio } from "openclaw/plugin-sdk/media-understanding";
import { assertOkOrThrowHttpError, postJsonRequest, readProviderBinaryResponse } from "openclaw/plugin-sdk/provider-http";

const settings = Object.fromEntries(readFileSync(new URL("./config.conf", import.meta.url), "utf8").split(/\r?\n/).filter((line) => line && !line.startsWith("#") && line.includes("=")).map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]));
const baseUrl = settings.SPEACHES_OPENCLAW_URL;
const sttModel = settings.SPEACHES_STT_MODEL;
const model = settings.SPEACHES_TTS_MODEL;
const voice = settings.SPEACHES_TTS_VOICE;
const responseFormat = settings.SPEACHES_TTS_RESPONSE_FORMAT;

export default definePluginEntry({
  id: "speaches",
  name: "Speaches",
  description: "Local no-auth Speaches transcription provider.",
  configSchema: { type: "object", additionalProperties: false, properties: {} },
  register(api) {
    api.registerMediaUnderstandingProvider({
      id: "speaches", capabilities: ["audio"], resolveAuth: () => ({ kind: "none", source: "local Speaches" }),
      transcribeAudio: (request) => transcribeOpenAiCompatibleAudio({ ...request, provider: "speaches", baseUrl, defaultBaseUrl: baseUrl, defaultModel: sttModel, request: { ...request.request, allowPrivateNetwork: true } }),
    });
    api.registerSpeechProvider({
      id: "speaches", label: "Speaches", defaultModel: model, models: [model], voices: [voice],
      resolveConfig: ({ rawConfig }) => { const raw = rawConfig?.providers?.speaches ?? rawConfig?.speaches ?? {}; return { baseUrl: raw.baseUrl ?? baseUrl, model: raw.model ?? model, voice: raw.voice ?? raw.speakerVoice ?? voice, responseFormat: raw.responseFormat ?? responseFormat }; },
      isConfigured: () => true,
      synthesize: async ({ text, providerConfig, providerOverrides, timeoutMs }) => {
        const endpoint = String(providerConfig.baseUrl ?? baseUrl).replace(/\/+$/, "");
        const format = String(providerConfig.responseFormat ?? responseFormat);
        const { response, release } = await postJsonRequest({ url: `${endpoint}/audio/speech`, headers: new Headers({ "content-type": "application/json" }), body: { model: providerOverrides?.model ?? providerConfig.model ?? model, input: text, voice: providerOverrides?.voice ?? providerConfig.voice ?? voice, response_format: format }, timeoutMs, fetchFn: fetch, allowPrivateNetwork: true, pinDns: false, auditContext: "speaches-tts" });
        try { await assertOkOrThrowHttpError(response, "Speaches TTS failed"); return { audioBuffer: Buffer.from(await readProviderBinaryResponse(response, "Speaches TTS failed", "audio")), outputFormat: format, fileExtension: `.${format}`, voiceCompatible: format === "opus" || format === "ogg" }; } finally { await release(); }
      },
    });
  },
});
