const https = require("https");

function transcribeAudio({ region, apiVersion, model, language, apiKey, audioBuffer, mimeType, fileName, timeoutMs = 60000, signal }) {
  return new Promise((resolve, reject) => {
    const boundary = `----FormBoundary${Date.now()}${Math.random().toString(36).slice(2)}`;
    const definition = JSON.stringify({
      enhancedMode: { enabled: true, model },
      ...(language && /^[a-z]{2,3}(?:[-_][a-z0-9]+)*$/i.test(language) && language !== "auto"
        ? { locales: [language.split(/[-_]/)[0].toLowerCase()] } : {}),
    });
    const defPart = `--${boundary}\r\nContent-Disposition: form-data; name="definition"\r\nContent-Type: application/json\r\n\r\n${definition}\r\n`;
    const audioHeader = `--${boundary}\r\nContent-Disposition: form-data; name="audio"; filename="${fileName}"\r\nContent-Type: ${mimeType}\r\n\r\n`;
    const audioFooter = `\r\n--${boundary}--\r\n`;
    const body = Buffer.concat([Buffer.from(defPart + audioHeader, "utf-8"), audioBuffer, Buffer.from(audioFooter, "utf-8")]);
    const req = https.request({
      hostname: `${region}.api.cognitive.microsoft.com`,
      path: `/speechtotext/transcriptions:transcribe?api-version=${apiVersion}`,
      method: "POST",
      signal,
      headers: { "Content-Type": `multipart/form-data; boundary=${boundary}`, "Ocp-Apim-Subscription-Key": apiKey, "Content-Length": body.length },
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("error", reject);
      res.on("end", () => {
        const raw = Buffer.concat(chunks).toString();
        let json;
        try { json = JSON.parse(raw); } catch { return reject(new Error(`Azure Speech: invalid JSON response (HTTP ${res.statusCode})`)); }
        if (res.statusCode !== 200) {
          const msg = String(json?.error?.message || JSON.stringify(json)).split(apiKey).join("[REDACTED]").slice(0, 2000);
          return reject(new Error(`Azure Speech HTTP ${res.statusCode}: ${msg}`));
        }
        const combined = json.combinedPhrases?.[0]?.text || "";
        const phrases = json.phrases || [];
        resolve({ text: (combined || phrases.map((p) => p.text).join(" ")).trim(), language: json.language || "unknown", confidence: phrases.length ? phrases.reduce((s, p) => s + (p.confidence || 0), 0) / phrases.length : 0 });
      });
    });
    req.on("error", reject);
    const timer = setTimeout(() => req.destroy(new Error("Azure Speech transcription timed out")), timeoutMs);
    req.once("close", () => clearTimeout(timer));
    req.write(body);
    req.end();
  });
}

module.exports = { transcribeAudio };
