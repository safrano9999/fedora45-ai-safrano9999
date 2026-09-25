const fs = require("fs");
const path = require("path");
const { createTranscriber, buildMediaProvider } = require("./lib/provider");

function register(api) {
  const inlineConfig = api.pluginConfig || {};
  const configPath = inlineConfig.configPath || path.join(__dirname, "config.json");
  const fileConfig = fs.existsSync(configPath)
    ? JSON.parse(fs.readFileSync(configPath, "utf8"))
    : {};
  if (!fileConfig || typeof fileConfig !== "object" || Array.isArray(fileConfig)) {
    throw new Error("MAI Transcribe config must be a JSON object");
  }
  const cfg = Object.assign(
    { region: "eastus", apiVersion: "2025-10-15", model: "MAI-Transcribe-2", maxFileSize: 26214400 },
    fileConfig,
    inlineConfig,
  );

  function resolveApiKey() {
    if (cfg.apiKey) return cfg.apiKey;
    if (api.resolveSecret) {
      const secret = api.resolveSecret("speech-api-key");
      if (secret) return secret;
    }
    return process.env.SPEECH_API_KEY || "";
  }

  const transcribe = createTranscriber(cfg, resolveApiKey);
  api.registerMediaUnderstandingProvider(buildMediaProvider(cfg, resolveApiKey, transcribe));
  api.registerTool({
    name: "mai_transcribe",
    label: "mai_transcribe",
    description: `Transcribe an audio file to text using ${cfg.model}. Provide the path to an audio file (WAV, MP3, FLAC, OGG).`,
    parameters: {
      type: "object",
      required: ["filePath"],
      properties: { filePath: { type: "string", description: "Path to an audio file to transcribe." } },
    },
    execute: async (_toolCallId, params) => {
      const apiKey = resolveApiKey();
      if (!apiKey) return { content: [{ type: "text", text: "Error: Speech API key not found." }], details: { status: "error" } };
      try {
        const result = await transcribe({
          apiKey,
          buffer: fs.readFileSync(params.filePath),
          fileName: path.basename(params.filePath),
          timeoutMs: 60000,
        });
        return { content: [{ type: "text", text: result.text }], details: { status: "ok", language: result.language, confidence: result.confidence } };
      } catch (err) {
        return { content: [{ type: "text", text: `Transcription failed: ${err.message}` }], details: { status: "error", error: err.message } };
      }
    },
  });
  api.logger?.info?.(`mai-transcribe plugin ready: region=${cfg.region}, model=${cfg.model}`);
}

module.exports = register;
