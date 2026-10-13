"""The agentic_quality judge talks to any OpenAI-compatible backend (vLLM,
OpenAI, LiteLLM, Ollama, ...) configured independently of CUGA's agent
backbone model."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cuga.evaluation.agentic_quality_judge.judge_client import JudgeConfig, JudgeConfigError, build_judge

pytestmark = pytest.mark.unit

SERVED_MODEL = "judge-model"


class _FakeOpenAICompatible(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible surface, mounted under /v1 like `vllm serve`."""

    requests = []
    supports_models = True

    def log_message(self, *args):
        pass

    def _send(self, body, status=200):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/v1/models" or not type(self).supports_models:
            self._send({"error": "not found"}, status=404)
            return
        self._send({"object": "list", "data": [{"id": SERVED_MODEL, "object": "model"}]})

    def do_POST(self):
        assert self.path == "/v1/chat/completions"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"body": body, "headers": dict(self.headers)})
        system = body["messages"][0]["content"]
        if "tool-selection evaluator" in system:
            verdict = {"label": "Good", "reason": "ok", "steps": []}
        else:
            verdict = {"label": "Partially Advanced", "reason": "ok"}
        self._send(
            {
                "id": "cmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"```json\n{json.dumps(verdict)}\n```"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


@pytest.fixture
def backend():
    """Yields the backend's API root (…/v1); set .supports_models on the handler to vary it."""
    _FakeOpenAICompatible.requests = []
    _FakeOpenAICompatible.supports_models = True
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAICompatible)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("CUGA_JUDGE_BASE_URL", "CUGA_JUDGE_MODEL", "CUGA_JUDGE_API_KEY", "CUGA_JUDGE_HEADERS"):
        monkeypatch.delenv(var, raising=False)
    # The agent backbone's OpenAI settings must not leak into the judge.
    monkeypatch.setenv("OPENAI_BASE_URL", "http://backbone.invalid/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "backbone-key")
    return monkeypatch


def test_missing_judge_url_is_an_error(clean_env):
    with pytest.raises(JudgeConfigError, match="CUGA_JUDGE_BASE_URL"):
        JudgeConfig.from_env()


def test_cli_args_override_env(clean_env):
    clean_env.setenv("CUGA_JUDGE_BASE_URL", "http://env:8000/v1")
    clean_env.setenv("CUGA_JUDGE_MODEL", "env-model")
    config = JudgeConfig.from_env(base_url="https://gateway.example/openai/v1/", model="cli-model")
    # Used as-is (only a trailing slash is trimmed): API roots differ per provider.
    assert config.base_url == "https://gateway.example/openai/v1"
    assert config.model == "cli-model"


@pytest.mark.parametrize("raw", ["not json", '["a list"]'])
def test_bad_headers_rejected(clean_env, raw):
    clean_env.setenv("CUGA_JUDGE_HEADERS", raw)
    with pytest.raises(JudgeConfigError, match="CUGA_JUDGE_HEADERS"):
        JudgeConfig.from_env(base_url="http://x/v1")


def test_model_autodiscovered_and_backbone_env_ignored(clean_env, backend):
    clean_env.setenv("CUGA_JUDGE_BASE_URL", backend)
    judge = build_judge(JudgeConfig.from_env())
    assert judge.model_name == SERVED_MODEL
    assert judge.openai_api_base == backend
    assert judge.openai_api_key.get_secret_value() != "backbone-key"


def test_unlisted_model_rejected(clean_env, backend):
    with pytest.raises(JudgeConfigError, match="does not serve model"):
        build_judge(JudgeConfig(base_url=backend, model="other-model"))


def test_backend_without_models_endpoint_needs_explicit_model(clean_env, backend):
    _FakeOpenAICompatible.supports_models = False
    with pytest.raises(JudgeConfigError, match="set --judge-model"):
        build_judge(JudgeConfig(base_url=backend))
    judge = build_judge(JudgeConfig(base_url=backend, model="any-model"))
    assert judge.model_name == "any-model"


def test_unreachable_backend_rejected():
    with pytest.raises(JudgeConfigError, match="not reachable"):
        build_judge(JudgeConfig(base_url="http://127.0.0.1:1/v1", timeout=2))


def test_judge_calls_go_through_backend_with_auth(clean_env, backend):
    clean_env.setenv("CUGA_JUDGE_API_KEY", "judge-key")
    clean_env.setenv("CUGA_JUDGE_HEADERS", '{"X-Gateway-Key": "gw-secret"}')
    judge = build_judge(JudgeConfig.from_env(base_url=backend))

    response = judge.invoke([("system", "You are a tool-selection evaluator."), ("user", "q")])

    assert '"label": "Good"' in response.content
    assert len(_FakeOpenAICompatible.requests) == 1
    for req in _FakeOpenAICompatible.requests:
        assert req["body"]["model"] == SERVED_MODEL
        assert req["body"]["temperature"] == 0
        assert req["headers"]["Authorization"] == "Bearer judge-key"
        assert req["headers"]["X-Gateway-Key"] == "gw-secret"
