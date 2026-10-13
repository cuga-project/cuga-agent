"""Judge-model client for the agentic_quality metrics: any backend that serves
the OpenAI-compatible chat completions API (POST {base_url}/chat/completions)
-- a remote vLLM service, OpenAI, a LiteLLM proxy, Ollama, TGI, etc.

The judge is deliberately configured independently of CUGA's own backbone
model (cuga.backend.llm.models.LLMManager / settings.toml): grading an
agent's trajectories with the same model that produced them biases the
scores, and it couples judge cost/availability to the agent under test.
Nothing here reads the agent's model settings or the OPENAI_* environment
variables the backbone uses -- every connection parameter is passed to
ChatOpenAI explicitly, so the backbone's env can never leak into the judge.

Configuration (environment variables; `cuga evaluate --judge-url/--judge-model`
set the first two for you):
- CUGA_JUDGE_BASE_URL    (required) the OpenAI-compatible API root, used as-is,
                         e.g. http://vllm-judge:8000/v1, https://api.openai.com/v1,
                         http://localhost:11434/v1 (Ollama)
- CUGA_JUDGE_MODEL       model name; if unset, the first model listed by
                         GET {base_url}/models is used (e.g. a vLLM server
                         usually serves exactly one)
- CUGA_JUDGE_API_KEY     sent as "Authorization: Bearer <key>", if the backend needs one
- CUGA_JUDGE_HEADERS     extra request headers as a JSON object, for gateways with
                         non-standard auth, e.g. '{"X-Gateway-Key": "..."}'  # pragma: allowlist secret
- CUGA_JUDGE_MAX_TOKENS  default 2000
- CUGA_JUDGE_TIMEOUT     per-request timeout in seconds, default 120
- CUGA_JUDGE_MAX_RETRIES default 3
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

from loguru import logger

ENV_BASE_URL = "CUGA_JUDGE_BASE_URL"
ENV_MODEL = "CUGA_JUDGE_MODEL"
ENV_API_KEY = "CUGA_JUDGE_API_KEY"  # pragma: allowlist secret
ENV_HEADERS = "CUGA_JUDGE_HEADERS"
ENV_MAX_TOKENS = "CUGA_JUDGE_MAX_TOKENS"
ENV_TIMEOUT = "CUGA_JUDGE_TIMEOUT"
ENV_MAX_RETRIES = "CUGA_JUDGE_MAX_RETRIES"

# Keyless backends (vLLM without --api-key, Ollama, ...) ignore it, but the
# OpenAI client refuses to send a request without one.
_NO_API_KEY = "EMPTY"  # pragma: allowlist secret


class JudgeConfigError(RuntimeError):
    """The judge backend is missing, misconfigured, or unreachable."""


@dataclass
class JudgeConfig:
    base_url: str
    model: Optional[str] = None
    api_key: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    temperature: float = 0.0
    max_tokens: int = 2000
    timeout: float = 120.0
    max_retries: int = 3

    @classmethod
    def from_env(cls, base_url=None, model=None):
        """Build a config from CUGA_JUDGE_* env vars; explicit base_url/model
        arguments (e.g. from CLI flags) take precedence."""
        base_url = base_url or os.environ.get(ENV_BASE_URL)
        if not base_url:
            raise JudgeConfigError(
                f"--agentic-quality needs a judge model: pass --judge-url or set {ENV_BASE_URL} to an "
                "OpenAI-compatible API root (the judge is intentionally separate from CUGA's agent model)."
            )
        headers = {}
        raw_headers = os.environ.get(ENV_HEADERS)
        if raw_headers:
            try:
                headers = json.loads(raw_headers)
            except json.JSONDecodeError as exp:
                raise JudgeConfigError(f"{ENV_HEADERS} is not valid JSON: {exp}") from exp
            if not isinstance(headers, dict):
                raise JudgeConfigError(f"{ENV_HEADERS} must be a JSON object of header name -> value.")
        return cls(
            base_url=base_url.rstrip("/"),
            model=model or os.environ.get(ENV_MODEL) or None,
            api_key=os.environ.get(ENV_API_KEY) or None,
            headers={str(k): str(v) for k, v in headers.items()},
            max_tokens=int(os.environ.get(ENV_MAX_TOKENS, 2000)),
            timeout=float(os.environ.get(ENV_TIMEOUT, 120)),
            max_retries=int(os.environ.get(ENV_MAX_RETRIES, 3)),
        )

    def request_headers(self):
        headers = dict(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        return headers


def _listed_models(config):
    """GET {base_url}/models. Returns the model ids, or None if the backend
    answered but doesn't support listing (not every OpenAI-compatible gateway
    implements /models). Raises JudgeConfigError if it can't be reached at all."""
    import httpx

    try:
        response = httpx.get(
            f"{config.base_url}/models", headers=config.request_headers(), timeout=min(config.timeout, 30.0)
        )
    except httpx.HTTPError as exp:
        raise JudgeConfigError(f"Judge backend at {config.base_url} is not reachable: {exp}") from exp
    if response.status_code != 200:
        logger.warning(
            f"Judge backend at {config.base_url} returned HTTP {response.status_code} for /models; "
            "skipping model validation."
        )
        return None
    try:
        return [m["id"] for m in response.json().get("data", []) if "id" in m]
    except Exception:
        logger.warning(f"Judge backend at {config.base_url} returned an unrecognized /models payload.")
        return None


def build_judge(config):
    """Check the backend is reachable and (where it can list models) serves the
    requested model, then return a LangChain chat model bound to it. Raises
    JudgeConfigError up front rather than letting every test case's judge
    call fail with label "NA"."""
    from langchain_openai import ChatOpenAI

    listed = _listed_models(config)
    model = config.model
    if model is None:
        if not listed:
            raise JudgeConfigError(
                f"Could not discover a model from {config.base_url}/models; set --judge-model or {ENV_MODEL}."
            )
        model = listed[0]
    elif listed and model not in listed:
        raise JudgeConfigError(
            f"Judge backend at {config.base_url} does not serve model {model!r}; it lists {listed}."
        )

    return ChatOpenAI(
        model=model,
        base_url=config.base_url,
        api_key=config.api_key or _NO_API_KEY,
        default_headers=config.headers or None,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
