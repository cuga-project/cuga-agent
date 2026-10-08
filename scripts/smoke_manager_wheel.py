"""Install a wheel without project context; check credential-free manager startup."""

import json
import os
import platform
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestProvider(BaseHTTPRequestHandler):
    """Exercise real model clients without external credentials or inference."""

    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((body.get("model"), self.headers.get("Authorization")))
        response = {
            "id": "smoke-completion",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "CUGA first task completed."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 5, "total_tokens": 6},
        }
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def request_json(base, path, body=None, method=None):
    request = Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urlopen(request, timeout=180) as response:
        return json.load(response)


def main():
    wheel = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="cuga-wheel-smoke-") as temp:
        root = Path(temp)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "ENV_FILE", "MODEL_NAME", "CUGA_SECRET_KEY")
            and not k.endswith(("API_KEY", "APIKEY"))
            and not k.startswith(("DYNACONF_", "CUGA_"))
        }
        env.update(
            UV_TOOL_DIR=str(root / "tools"),
            UV_TOOL_BIN_DIR=str(root / "bin"),
            CUGA_DATA_DIR=str(root / "data"),
            CUGA_TEST_ENV="true",
            CUGA_MANAGER_MODE="true",
        )
        wheels = json.loads((source / "install/torch-wheels.json").read_text())
        cpu = wheels[f"{platform.system()}-{platform.machine()}"]
        install_command = [
            "uv",
            "tool",
            "install",
            "--force",
            "--no-config",
            "--python",
            "3.12",
            "--with",
            cpu["torch"],
            "--with",
            cpu["torchvision"],
            "--constraints",
            str(source / "install/constraints.txt"),
            "--overrides",
            str(source / "install/overrides.txt"),
            str(wheel),
        ]
        subprocess.run(install_command, cwd=root, env=env, check=True)
        subprocess.run(
            [str(root / "tools/cuga/bin/python"), "-c", "import torch; assert torch.version.cuda is None"],
            cwd=root,
            env=env,
            check=True,
        )
        demo, registry = free_port(), free_port()
        env.update(DYNACONF_SERVER_PORTS__DEMO=str(demo), DYNACONF_SERVER_PORTS__REGISTRY=str(registry))
        base = f"http://127.0.0.1:{demo}"
        provider = ThreadingHTTPServer(("127.0.0.1", 0), TestProvider)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        endpoint = f"http://127.0.0.1:{provider.server_port}/v1"
        env["CUGA_RUN_TOKEN"] = "cuga-wheel-smoke-local-token"
        env["CUGA_EVENTS_ENABLED"] = "true"
        key = None
        try:
            for repeat in (False, True):
                cwd = root / ("second-directory" if repeat else "first-directory")
                cwd.mkdir()
                if repeat:
                    subprocess.run(install_command, cwd=cwd, env=env, check=True)
                with (root / f"startup-{repeat}.log").open("w+") as log:
                    process = subprocess.Popen(
                        [str(root / "bin/cuga"), "start", "manager"],
                        cwd=cwd,
                        env=env,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                    try:
                        check_manager(base, process, root, endpoint, repeat, env["CUGA_RUN_TOKEN"])
                        current_key = (root / "data/secret.key").read_bytes()
                        assert len(current_key) == 44
                        if repeat:
                            assert current_key == key, "Reinstallation must preserve encryption key"
                        key = current_key
                    except Exception:
                        log.seek(0)
                        print(log.read(), file=sys.stderr)
                        raise
                    finally:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=15)
        finally:
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=5)
        assert TestProvider.requests
        assert all(auth == "Bearer cuga-wheel-smoke-local-value" for _, auth in TestProvider.requests)
        print(
            "Verified installed wheel: manager/frontend, provider validation, first task, and preserved configuration after reinstall from another directory."
        )


def check_manager(base, process, root, endpoint, repeat, token):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urlopen(base + "/api/manage/setup/status", timeout=2) as response:
                status = json.load(response)
            assert status == {"enabled": True, "configured": repeat}, status
            break
        except OSError:
            if process.poll() is not None:
                raise RuntimeError("Manager exited before setup was available")
            time.sleep(1)
    else:
        raise RuntimeError("Manager did not serve setup within 180 seconds")
    with urlopen(base + "/manage", timeout=5) as response:
        html = response.read().decode()
    for asset in re.findall(r'src=["\']([^"\']+\.js)["\']', html):
        with urlopen(base + "/" + asset.lstrip("/"), timeout=5) as response:
            assert response.status == 200
    if not repeat:
        secret = request_json(
            base,
            "/api/secrets",
            {"id": "wheel-smoke", "value": "cuga-wheel-smoke-local-value", "agent_id": "cuga-default"},
        )
        request_json(
            base,
            "/api/manage/config/draft/llm",
            {"provider": "openai", "model": "cuga-smoke", "base_url": endpoint, "api_key": secret["ref"]},
            "PATCH",
        )
        request_json(base, "/api/manage/setup/validate", {}, "POST")
        draft = request_json(base, "/api/manage/config?draft=1")["config"]
        assert draft["llm"]["api_key"] == "db://wheel-smoke"
        request = Request(
            base + "/stream",
            data=json.dumps({"query": "Say hello."}).encode(),
            headers={"Content-Type": "application/json", "X-Use-Draft": "true"},
        )
        with urlopen(request, timeout=180) as response:
            stream = response.read().decode()
        assert "event: Answer" in stream and "CUGA first task completed." in stream, stream
        # The manager's full-save path also rebuilds the already-created draft.
        request_json(base, "/api/manage/config/draft", {"config": draft}, "POST")
        with urlopen(request, timeout=180) as response:
            saved_stream = response.read().decode()
        assert "event: Answer" in saved_stream and "CUGA first task completed." in saved_stream, saved_stream
        request_json(
            base,
            "/api/manage/config/draft/knowledge",
            {"enabled": False, "citations_enabled": False},
            "PATCH",
        )
        with urlopen(request, timeout=180) as response:
            knowledge_stream = response.read().decode()
        assert "event: Answer" in knowledge_stream and "CUGA first task completed." in knowledge_stream, (
            knowledge_stream
        )
        request_json(base, "/api/manage/config", {"config": draft}, "POST")
        assert request_json(base, "/api/manage/setup/status")["configured"]
    request = Request(
        base + "/run",
        data=json.dumps({"query": "Say hello.", "disable_history": True}).encode(),
        headers={"Content-Type": "application/json", "X-Gateway-Token": token},
    )
    with urlopen(request, timeout=180) as response:
        task = json.load(response)
    assert task["ok"] is True, task
    assert "CUGA first task completed." in task["answer"], task


if __name__ == "__main__":
    main()
