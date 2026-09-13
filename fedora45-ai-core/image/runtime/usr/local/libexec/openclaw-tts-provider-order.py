#!/usr/bin/env python3
"""Support configured TTS provider order in the pinned OpenClaw runtime."""
import subprocess
from pathlib import Path

root = Path(subprocess.check_output(["npm", "root", "-g"], text=True).strip())
dist = root / "openclaw" / "dist"
old = "return [...providers ?? registry.listSpeechProviders(cfg)].toSorted(compareSpeechProviderOrder);"
new = """// Safrano: optional tts.providers.<id>.fallbackOrder, lower values first.
\tconst order = (provider) => {
\t\tconst value = cfg?.tts?.providers?.[provider.id]?.fallbackOrder;
\t\treturn typeof value === "number" && Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
\t};
\treturn [...providers ?? registry.listSpeechProviders(cfg)].toSorted((a, b) =>
\t\torder(a) - order(b) || compareSpeechProviderOrder(a, b));"""
patched = 0
for path in dist.glob("runtime-api-*.mjs"):
    source = path.read_text()
    if new in source:
        patched += 1
    elif old in source:
        assert source.count(old) == 1, path
        path.write_text(source.replace(old, new))
        patched += 1
if patched != 1:
    raise SystemExit(f"Expected one OpenClaw TTS provider-order module, found {patched}")
print("OpenClaw: configured TTS fallback order enabled")
