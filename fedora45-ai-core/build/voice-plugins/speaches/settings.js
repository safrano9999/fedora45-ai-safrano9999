export function resolveSettings(inline = {}, env = process.env) {
  const fields = {
    baseUrl: ["SPEACHES_BASE_URL", ""],
    sttModel: ["SPEACHES_STT_MODEL", "Systran/faster-whisper-small"],
    ttsModel: ["SPEACHES_TTS_MODEL", "speaches-ai/piper-de_DE-thorsten-high"],
    ttsVoice: ["SPEACHES_TTS_VOICE", "thorsten"],
    responseFormat: ["SPEACHES_TTS_RESPONSE_FORMAT", "mp3"],
  };
  const settings = Object.fromEntries(Object.entries(fields).map(([field, [name, preset]]) =>
    [field, String(inline[field] ?? (env[name]?.trim() || preset)).trim()]));
  settings.baseUrl = settings.baseUrl.replace(/\/+$/, "");
  if (settings.baseUrl) {
    let url;
    try { url = new URL(settings.baseUrl); } catch { throw new Error("Invalid SPEACHES_BASE_URL"); }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error("SPEACHES_BASE_URL must be an HTTP(S) API base without credentials");
    }
  }
  if (!["mp3", "opus", "ogg", "aac", "flac", "wav", "pcm"].includes(settings.responseFormat)) {
    throw new Error("Unsupported SPEACHES_TTS_RESPONSE_FORMAT");
  }
  return settings;
}

export const configSchema = {
  type: "object", additionalProperties: false,
  properties: Object.fromEntries(["baseUrl", "sttModel", "ttsModel", "ttsVoice", "responseFormat"]
    .map((field) => [field, { type: "string" }])),
};
