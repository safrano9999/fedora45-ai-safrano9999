#!/usr/bin/env python3
"""Runner-only regression: a fresh, offline dashboard opens its real chat PTY.

Run in the built image with --network none. No provider credentials or requests
are needed. This exercises PM, Node, the packaged TUI and its WebSocket route.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path("/usr/local/lib/hermes-runtime/hermes-agent")
sys.path.insert(0, str(ROOT))


async def check_chat():
    import aiohttp

    output = ""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:19119/") as response:
            html = await response.text()
        token = re.search(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"', html)
        assert token, "Dashboard did not provide its browser session token"
        async with session.ws_connect("http://127.0.0.1:19119/api/pty?fresh=1&token=" + token[1],
                                      origin="http://127.0.0.1:19119") as ws:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    output += msg.data
                    assert not any(error in output.lower() for error in (
                        "packaged pm runtime is missing", "chat could not start",
                        "cannot find module", "modulenotfounderror", "traceback (most recent call last)")), output
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise AssertionError(f"Chat exited early ({ws.close_code}): {output}")
            # ANSI setup alone is not a working chat terminal.
            visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
            assert len(re.findall(r"[A-Za-z]", visible)) > 30, output
    print("Hermes dashboard chat: packaged PM, Node and live PTY passed offline")


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-dashboard-smoke-") as temp:
        os.environ["HERMES_HOME"] = str(Path(temp) / "profile")
        os.environ.pop("HERMES_NODE", None)  # Must resolve Node through PM.
        os.environ.pop("HERMES_RUNTIME_DIR", None)  # Must use the packaged manifest.
        from pm.runtime import runtime_python
        from pm import ensure

        stamp = json.loads((ROOT / "install-stamp.json").read_text())
        assert stamp["pmRuntime"] == str(ROOT / "pm-runtime")
        assert runtime_python(bootstrap=False).is_file()
        assert str(ROOT / "tools") in ensure("node").env["PATH"]
        with (Path(temp) / "dashboard.log").open("w+") as log:
            server = subprocess.Popen([
                "/usr/local/bin/hermes", "dashboard", "--host", "127.0.0.1",
                "--port", "19119", "--insecure", "--skip-build", "--no-open",
            ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                for _ in range(90):
                    assert server.poll() is None, "Dashboard exited during startup"
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:19119/", timeout=1) as response:
                            assert response.status == 200
                        break
                    except (urllib.error.URLError, TimeoutError):
                        time.sleep(1)
                else:
                    raise AssertionError("Dashboard did not become ready")
                asyncio.run(check_chat())
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                os.killpg(server.pid, signal.SIGTERM)
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid, signal.SIGKILL)
                    server.wait()


if __name__ == "__main__":
    main()
