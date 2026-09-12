"""Run with Hermes' installed Python: discover all configured MCPs and call reviewed read tools."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/hermes-agent")
from tools.mcp_tool_discovery import discover_mcp_tools, get_mcp_status
from tools.mcp_tool_lifecycle import shutdown_mcp_servers
from tools.mcp_tool_schema import mcp_prefixed_tool_name
from tools.registry import registry

probes = json.loads(Path(__file__).with_name("mcp-read-calls.json").read_text())
results = []
try:
    names = set(discover_mcp_tools())  # No OpenClaw agent filter: Hermes exposes all configured servers.
    status = get_mcp_status()
    assert {entry["name"] for entry in status} == set(probes), "Review changed MCP inventory"
    for entry in status:
        server = entry["name"]
        tool, arguments = probes[server]
        result = {"server": server, "tool": tool, "timestamp": datetime.now(timezone.utc).isoformat()}
        try:
            assert entry["connected"] and entry["tools"] > 0
            name = mcp_prefixed_tool_name(server, tool)
            assert name in names and registry.get_toolset_for_tool(name) == f"mcp-{server}"
            reply = registry.dispatch(name, arguments)
            payload = json.loads(reply) if isinstance(reply, str) else reply
            assert payload and not (isinstance(payload, dict) and "error" in payload)
            result.update(status="PASS", registered_tools=entry["tools"])
        except Exception as error:
            result.update(status="FAIL", error=type(error).__name__)
        results.append(result)
finally:
    shutdown_mcp_servers()
passed = bool(results) and all(result["status"] == "PASS" for result in results)
print(json.dumps({"status": "PASS" if passed else "FAIL", "results": results}, indent=2))
raise SystemExit(0 if passed else 1)
