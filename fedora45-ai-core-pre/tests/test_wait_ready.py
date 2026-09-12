#!/usr/bin/env python3
"""Isolated checks for the Core-pre readiness helper."""

from __future__ import annotations

import contextlib
import http.server
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest


sys.dont_write_bytecode = True


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "image/runtime/usr/local/libexec/fedora44-wait-ready"


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_arguments: object) -> None:
        return


@contextlib.contextmanager
def running_http_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextlib.contextmanager
def running_unix_server(path: pathlib.Path):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen()
    stop = threading.Event()

    def accept_connections() -> None:
        while not stop.is_set():
            try:
                connection, _ = server.accept()
            except OSError:
                return
            connection.close()

    thread = threading.Thread(target=accept_connections, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        server.close()
        thread.join(timeout=1)


class WaitReadyTests(unittest.TestCase):
    def run_helper(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(HELPER), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )

    def test_tcp_ready(self) -> None:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            result = self.run_helper("--timeout", "1", "tcp", "127.0.0.1", str(port))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_http_ready_and_expected_status(self) -> None:
        with running_http_server() as port:
            result = self.run_helper(
                "--timeout",
                "1",
                "http",
                f"http://127.0.0.1:{port}/ready",
                "204",
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unix_socket_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ready.sock"
            with running_unix_server(path):
                result = self.run_helper("--timeout", "1", "unix", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_timeout_is_bounded(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        result = self.run_helper(
            "--timeout", "0.1", "tcp", "127.0.0.1", str(port)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out", result.stderr)

    def test_invalid_inputs_fail_before_waiting(self) -> None:
        cases = (
            ("--timeout", "0", "tcp", "127.0.0.1", "1"),
            ("tcp", "127.0.0.1", "0"),
            ("http", "file:///tmp/ready"),
            ("http", "http://user:secret@127.0.0.1/"),
            ("unix", "relative.sock"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_helper(*arguments).returncode, 2)


if __name__ == "__main__":
    unittest.main()
