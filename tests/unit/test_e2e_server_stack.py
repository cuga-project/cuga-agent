"""Unit tests for e2e server_stack wait helper."""

from __future__ import annotations

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from system_tests.e2e.server_stack import wait_for_http_ready


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


@pytest.mark.unit
def test_wait_for_http_ready_succeeds_when_server_is_up():
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_for_http_ready(port, timeout=5.0, interval=0.05)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.unit
def test_wait_for_http_ready_raises_if_process_dies():
    process = subprocess.Popen(["python", "-c", "raise SystemExit(3)"])
    process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="died with return code"):
        wait_for_http_ready(1, process=process, process_name="dead", timeout=1.0, interval=0.05)
