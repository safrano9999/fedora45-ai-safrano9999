const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const { transcribeAudio } = require("./api");

const execFileAsync = promisify(execFile);
const DIRECT_TYPES = { ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac" };
const MIME_EXTENSIONS = { "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/flac": ".flac", "audio/x-flac": ".flac" };

function createTranscriber(cfg, resolveApiKey) {
  return async function transcribe(request) {
    request.signal?.throwIfAborted();
    const apiKey = request.auth?.kind === "api-key" ? request.auth.apiKey : request.apiKey || resolveApiKey();
    if (!apiKey) throw new Error("Speech API key not found");
    let buffer = request.buffer;
    const maxBytes = cfg.maxFileSize || 26214400;
    if (!Buffer.isBuffer(buffer)) throw new Error("Audio input must be a Buffer");
    if (buffer.length > maxBytes) throw new Error(`Audio file too large (max ${maxBytes} bytes)`);
    const timeoutMs = Number.isFinite(request.timeoutMs) && request.timeoutMs > 0 ? request.timeoutMs : 60000;
    const deadline = Date.now() + timeoutMs;
    let fileName = path.basename(request.fileName || "audio").replace(/[\r\n"\\]/g, "_");
    const mime = String(request.mime || "").split(";", 1)[0].trim().toLowerCase();
    const ext = MIME_EXTENSIONS[mime] || path.extname(fileName).toLowerCase();
    let mimeType = DIRECT_TYPES[ext];
    let directory;
    try {
      if (!mimeType) {
        directory = await fs.mkdtemp(path.join(os.tmpdir(), "mai-transcribe-"));
        const input = path.join(directory, "input.audio");
        const output = path.join(directory, "output.wav");
        await fs.writeFile(input, buffer, { mode: 0o600 });
        await execFileAsync("ffmpeg", ["-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", input, "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", output], { timeout: Math.min(timeoutMs, 30000), signal: request.signal, maxBuffer: 1024 * 1024 });
        buffer = await fs.readFile(output);
        mimeType = "audio/wav";
        fileName = "audio.wav";
      } else if (path.extname(fileName).toLowerCase() !== ext) fileName += ext;
      if (buffer.length > maxBytes) throw new Error(`Converted audio too large (max ${maxBytes} bytes)`);
      request.signal?.throwIfAborted();
      const remainingMs = deadline - Date.now();
      if (remainingMs <= 0) throw new Error("Azure Speech transcription timed out");
      return await transcribeAudio({ region: cfg.region, apiVersion: cfg.apiVersion, model: request.model || cfg.model, language: request.language, apiKey, audioBuffer: buffer, mimeType, fileName, timeoutMs: remainingMs, signal: request.signal });
    } finally {
      if (directory) await fs.rm(directory, { recursive: true, force: true });
    }
  };
}

function buildMediaProvider(cfg, resolveApiKey, transcribe = createTranscriber(cfg, resolveApiKey)) {
  return {
    id: "mai-transcribe",
    capabilities: ["audio"],
    defaultModels: { audio: cfg.model },
    resolveAuth: () => { const apiKey = resolveApiKey(); return apiKey ? { kind: "api-key", apiKey, source: "MAI Transcribe plugin configuration" } : null; },
    transcribeAudio: async (request) => { const result = await transcribe(request); return { text: result.text, model: request.model || cfg.model }; },
  };
}

module.exports = { createTranscriber, buildMediaProvider };
