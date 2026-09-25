"""Generate isolated fixtures, then exercise the selected OpenClaw release."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from openclaw_ephemeral.configuration import build_config
from openclaw_ephemeral.providers import OpenAIV1Provider


with tempfile.TemporaryDirectory(prefix="openclaw-compatibility-") as scratch:
    home = Path(scratch)
    injected = {
        "HOME": scratch,
        "OPENCLAW_CONTROL_UI_ALLOWED_ORIGINS": "http://127.0.0.1:18789",
        "OPENCLAW_MODEL": "fixture/model-a",
        "OPENCLAW_NOTE_FULL_MODE": "0",
        "OPENCLAW_TELEGRAMTOKEN": "fixture-main-token",
        "OPENCLAW_TELEGRAM_CHAT_ID": "12345",
        "OPENCLAW_TELEGRAMTOKEN_02": "fixture-mtg-token",
        "OPENCLAW_TELEGRAM_AGENT_02": "mtg",
        "OPENCLAW_TELEGRAM_CHAT_ID_02": "12345",
        "MCP_SERVER_NAME": "general",
        "MCP_SERVER_URL": "http://general.example.test/mcp",
        "MCP_SERVER_ALLOW_PRIVATE": "1",
        "MCP_SERVER_BEARER": "fixture-general-secret",
        "MCP_ALLOW": "main",
        "MCP_SERVER_NAME_02": "mtg",
        "MCP_SERVER_URL_02": "http://mtg.example.test/mcp",
        "MCP_SERVER_BEARER_02": "fixture-mtg-secret",
        "MCP_ALLOW_02": "mtg",
        "MCP_SERVER_NAME_03": "shared",
        "MCP_SERVER_URL_03": "https://shared.example.test/mcp",
        "MCP_ALLOW_03": "*",
    }
    provider = OpenAIV1Provider(index=1, provider_id="fixture", configured_name="fixture",
                               base_url="http://127.0.0.1:4000/v1", key_env="FIXTURE_API_KEY",
                               models=("model-a", "model-b"), streaming=True)
    config, _, _ = build_config(injected, destination=home / "generated.json",
                                 openai_v1_providers=(provider,))
    fixture = home / "fixture.json"
    fixture.write_text(json.dumps(config))
    (home / "cli.json").write_text("{}")
    env = {key: os.environ[key] for key in
           ("PATH", "UPGRADE_TARGET_SOURCE", "UPGRADE_TARGET_PACKAGE", "UPGRADE_TARGET_VERSION",
            "UPGRADE_TARGET_UPSTREAM_SHA")}
    env.update(injected, FIXTURE_API_KEY="fixture-model-secret",
               OPENCLAW_CONFIG_PATH=str(home / "cli.json"), OPENCLAW_STATE_DIR=str(home / "state"),
               UPGRADE_CONFIG_FIXTURE=str(fixture), NODE_NO_WARNINGS="1")
    result = subprocess.run(["node", "--test", "--test-reporter=tap",
                             str(Path(__file__).with_name("openclaw_runtime.mjs"))], env=env)
    raise SystemExit(result.returncode)
