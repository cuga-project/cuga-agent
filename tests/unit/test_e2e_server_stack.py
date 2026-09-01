"""Unit tests for e2e server_stack wait helper."""

from __future__ import annotations

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pytest

from system_tests.e2e import server_stack as stack_mod
from system_tests.e2e.server_stack import (
    start_crm_stack,
    start_digital_sales_stack,
    wait_for_http_ready,
)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


class _NotFoundHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


class _ServerErrorHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(500)
        self.end_headers()

    def log_message(self, format, *args):
        return


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.mark.unit
def test_wait_for_http_ready_succeeds_when_server_is_up():
    server = _serve(_OkHandler)
    try:
        wait_for_http_ready(server.server_address[1], timeout=5.0, interval=0.05)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.unit
def test_wait_for_http_ready_treats_http_404_as_ready():
    server = _serve(_NotFoundHandler)
    try:
        wait_for_http_ready(server.server_address[1], timeout=5.0, interval=0.05)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.unit
def test_wait_for_http_ready_raises_if_process_dies():
    process = subprocess.Popen(["python", "-c", "raise SystemExit(3)"])
    process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="died with return code"):
        wait_for_http_ready(1, process=process, process_name="dead", timeout=1.0, interval=0.05)


@pytest.mark.unit
def test_wait_for_http_ready_treats_http_5xx_as_not_ready():
    server = _serve(_ServerErrorHandler)
    try:
        with pytest.raises(TimeoutError, match="did not become ready"):
            wait_for_http_ready(server.server_address[1], timeout=0.4, interval=0.05)
    finally:
        server.shutdown()
        server.server_close()


class _FakePopen:
    def __init__(self, *args, **kwargs):
        self.pid = id(self)
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False


def _patch_stack_launch(monkeypatch, tmp_path, popen_cls, ready_side_effect):
    monkeypatch.setattr(stack_mod.subprocess, "Popen", popen_cls)
    monkeypatch.setattr(stack_mod, "wait_for_http_ready", ready_side_effect)
    monkeypatch.setattr(stack_mod, "_class_log_dir", lambda name: tmp_path)
    monkeypatch.setattr(stack_mod, "_open_log", lambda path: MagicMock())
    monkeypatch.setattr(stack_mod, "get_preexec_fn", lambda: None)


@pytest.mark.unit
def test_start_digital_sales_stack_stops_partial_launch(monkeypatch, tmp_path):
    launched: list[_FakePopen] = []
    stopped: list = []

    class TrackingPopen(_FakePopen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            launched.append(self)

    calls = {"n": 0}

    def fail_on_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("registry died")

    def spy_stop(handles):
        stopped.append(list(handles.processes))
        for proc in handles.processes:
            proc._alive = False

    _patch_stack_launch(monkeypatch, tmp_path, TrackingPopen, fail_on_second)
    monkeypatch.setattr(stack_mod, "stop_stack", spy_stop)

    with pytest.raises(RuntimeError, match="registry died"):
        start_digital_sales_stack("Klass")

    assert len(launched) >= 2
    assert stopped and stopped[0] == launched


@pytest.mark.unit
def test_start_crm_stack_stops_partial_launch(monkeypatch, tmp_path):
    launched: list[_FakePopen] = []
    stopped: list = []

    class TrackingPopen(_FakePopen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            launched.append(self)

    def fail_ready(*args, **kwargs):
        raise RuntimeError("crm died")

    def spy_stop(handles):
        stopped.append(list(handles.processes))
        for proc in handles.processes:
            proc._alive = False

    _patch_stack_launch(monkeypatch, tmp_path, TrackingPopen, fail_ready)
    monkeypatch.setattr(stack_mod, "stop_stack", spy_stop)

    with pytest.raises(RuntimeError, match="crm died"):
        start_crm_stack("Klass")

    assert launched
    assert stopped and stopped[0] == launched
