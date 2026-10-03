import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { configSchema, resolveSettings } from "./settings.js";
import { transcribeOpenAiCompatibleAudio } from "openclaw/plugin-sdk/media-understanding";
import { assertOkOrThrowHttpError, postJsonRequest, readProviderBinaryResponse } from "openclaw/plugin-sdk/provider-http";

export default definePluginEntry({
  id: "speaches",
  name: "Speaches",
  description: "Local no-auth Speaches transcription provider.",
  configSchema,
  register(api) {
    const { baseUrl, sttModel, ttsModel: model, ttsVoice: voice, responseFormat } = resolveSettings(api.pluginConfig);
    api.registerMediaUnderstandingProvider({
      id: "speaches", capabilities: ["audio"], resolveAuth: () => ({ kind: "none", source: "local Speaches" }),
      transcribeAudio: (request) => {
        const endpoint = request.baseUrl || baseUrl;
        if (!endpoint) throw new Error("Configure SPEACHES_BASE_URL before using Speaches STT");
        return transcribeOpenAiCompatibleAudio({ ...request, provider: "speaches", baseUrl: endpoint, defaultBaseUrl: endpoint, defaultModel: sttModel, request: { ...request.request, allowPrivateNetwork: true } });
      },
    });
    api.registerSpeechProvider({
      id: "speaches", label: "Speaches", defaultModel: model, models: [model], voices: [voice],
      resolveConfig: ({ rawConfig }) => { const raw = rawConfig?.providers?.speaches ?? rawConfig?.speaches ?? {}; return { baseUrl: raw.baseUrl ?? baseUrl, model: raw.model ?? model, voice: raw.voice ?? raw.speakerVoice ?? voice, responseFormat: raw.responseFormat ?? responseFormat }; },
      isConfigured: ({ providerConfig } = {}) => Boolean(providerConfig?.baseUrl || baseUrl),
      synthesize: async ({ text, providerConfig, providerOverrides, timeoutMs }) => {
        const endpoint = String(providerConfig.baseUrl ?? baseUrl).replace(/\/+$/, "");
        if (!endpoint) throw new Error("Configure SPEACHES_BASE_URL before using Speaches TTS");
        const format = String(providerConfig.responseFormat ?? responseFormat);
        const { response, release } = await postJsonRequest({ url: `${endpoint}/audio/speech`, headers: new Headers({ "content-type": "application/json" }), body: { model: providerOverrides?.model ?? providerConfig.model ?? model, input: text, voice: providerOverrides?.voice ?? providerConfig.voice ?? voice, response_format: format }, timeoutMs, fetchFn: fetch, allowPrivateNetwork: true, pinDns: false, auditContext: "speaches-tts" });
        try { await assertOkOrThrowHttpError(response, "Speaches TTS failed"); return { audioBuffer: Buffer.from(await readProviderBinaryResponse(response, "Speaches TTS failed", "audio")), outputFormat: format, fileExtension: `.${format}`, voiceCompatible: format === "opus" || format === "ogg" }; } finally { await release(); }
      },
    });
  },
});
