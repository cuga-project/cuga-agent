"""Find a chat model's context window at runtime instead of only from a hard-coded table.

Sources, first answer wins:

1. ``settings`` - the ``context_window`` key of the model's settings (``LLMManager``
   pins it on the model with :func:`set_context_window_override`).
2. The serving endpoint, one cached HTTP GET:
   ``openrouter``   OpenRouter ``/models`` (``top_provider.context_length``),
   ``watsonx``      watsonx.ai ``foundation_model_specs`` (``max_sequence_length``),
   ``litellm-proxy`` a LiteLLM proxy ``/model/info`` (``max_input_tokens``, else the
                    backend model looked up in LiteLLM's map),
   ``vllm``         an OpenAI-compatible ``/models`` listing ``max_model_len``.
3. ``litellm-map`` - LiteLLM's bundled model map, exact names only.
4. ``table`` - ``MODEL_CONTEXT_SIZES`` (longest-prefix match).
5. ``profile`` - ``model.profile["max_input_tokens"]`` (not ChatWatsonx: its profile is a generic 8K).
6. ``default`` - ``DEFAULT_CONTEXT_SIZE``.

The endpoint knows the window the deployment actually serves (Bedrock serves gpt-oss-120b
at 128,000; a self-hosted server may cap a model below its native window), so it comes
before the generic maps. Endpoint lookups are time-boxed, cached (failures too), never
raise, and are skipped when ``advanced_features.context_window_discovery`` is false.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from loguru import logger

OVERRIDE_ATTR = "_cuga_context_window"
DISCOVERY_TIMEOUT_SECONDS = 3.0
WATSONX_SPECS_VERSION = "2024-05-01"
# Public provider APIs whose model listings carry no context window: do not probe them.
_NO_WINDOW_HOSTS = (
    "api.openai.com",
    "openai.azure.com",
    "api.anthropic.com",
    "googleapis.com",
    "api.groq.com",
    "api.mistral.ai",
)
# Routing prefixes LiteLLM clients put before a proxy alias.
_PROXY_PREFIXES = ("litellm_proxy/", "openai/")
# LiteLLM map providers whose windows are not trusted. watsonx: 12 of 29 entries say 8,192
# and gpt-oss-120b / granite-4-h-small say 8,192 / 20,480 where the watsonx.ai specs API
# says 131,072. watsonx models resolve through that API, then the table.
_UNTRUSTED_MAP_PROVIDERS = ("watsonx",)


@dataclass(frozen=True)
class ContextWindow:
    tokens: int
    source: str


@dataclass(frozen=True)
class _Endpoint:
    kind: str  # "openrouter" | "watsonx" | "openai-compatible"
    base_url: str
    api_key: Optional[str] = None
    model_id: Optional[str] = None


_resolved: dict[tuple, ContextWindow] = {}
_responses: dict[tuple, Any] = {}


def clear_context_window_cache() -> None:
    _resolved.clear()
    _responses.clear()


def set_context_window_override(model: Any, tokens: Any) -> None:
    """Pin *model*'s context window, e.g. from the ``context_window`` model setting."""
    if tokens is None or tokens == "":
        return
    if isinstance(tokens, bool):
        raise ValueError(f"context_window must be a positive integer, got {tokens!r}")
    value = int(tokens)
    if value <= 0:
        raise ValueError(f"context_window must be a positive integer, got {tokens!r}")
    # object.__setattr__: chat models are pydantic models that reject unknown fields.
    object.__setattr__(_unwrap(model), OVERRIDE_ATTR, value)


def resolve_context_window(model: Any = None, model_name: Optional[str] = None) -> ContextWindow:
    """Context window of *model* in tokens, and which source gave it. Never raises."""
    llm = _unwrap(model)
    name = (model_name or "").strip() or _model_name(llm)
    endpoint = _endpoint(llm)
    key = (
        type(llm).__name__,
        endpoint,
        name,
        _override(llm),
        _profile_tokens(llm),
        _discovery_enabled(),
    )
    cached = _resolved.get(key)
    if cached is not None:
        return cached
    try:
        result = _resolve(llm, name, endpoint)
    except Exception as e:  # a lookup bug must not break the LLM call
        from cuga.backend.cuga_graph.utils.token_counter import DEFAULT_CONTEXT_SIZE

        logger.warning("Context window lookup failed for {}: {}", name or "unknown model", e)
        result = ContextWindow(DEFAULT_CONTEXT_SIZE, "default")
    _resolved[key] = result
    log = logger.warning if result.source == "default" else logger.info
    log(
        "Context window for {}: {} tokens (source: {})",
        name or "unknown model",
        result.tokens,
        result.source,
    )
    return result


def _resolve(llm: Any, name: str, endpoint: Optional[_Endpoint]) -> ContextWindow:
    from cuga.backend.cuga_graph.utils.token_counter import (
        DEFAULT_CONTEXT_SIZE,
        lookup_model_context_size,
    )

    override = _override(llm)
    if override:
        return ContextWindow(override, "settings")
    if endpoint is not None and _discovery_enabled():
        found = _from_endpoint(endpoint, name)
        if found is not None:
            return found
    tokens = litellm_map_tokens(name)
    if tokens:
        return ContextWindow(tokens, "litellm-map")
    tokens = lookup_model_context_size(name)
    if tokens:
        return ContextWindow(tokens, "table")
    tokens = _profile_tokens(llm)
    if tokens:
        return ContextWindow(tokens, "profile")
    return ContextWindow(DEFAULT_CONTEXT_SIZE, "default")


# ── model introspection ───────────────────────────────────────────────────────


def _unwrap(model: Any) -> Any:
    llm = model
    for _ in range(5):  # RunnableBinding wraps the chat model in .bound
        inner = getattr(llm, "bound", None)
        if inner is None or inner is llm:
            break
        llm = inner
    return llm


def _secret(value: Any) -> Optional[str]:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        value = getter()
    return value if isinstance(value, str) and value.strip() else None


def _model_name(llm: Any) -> str:
    from cuga.backend.cuga_graph.utils.token_counter import resolve_model_identifier

    return resolve_model_identifier(llm, fallback_name="") if llm is not None else ""


def _override(llm: Any) -> Optional[int]:
    value = getattr(llm, OVERRIDE_ATTR, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _is_watsonx(llm: Any) -> bool:
    return type(llm).__name__ == "ChatWatsonx"


def _profile_tokens(llm: Any) -> Optional[int]:
    if llm is None or _is_watsonx(llm):
        return None
    try:
        profile = llm.profile
    except Exception:
        return None
    if not isinstance(profile, Mapping):
        return None
    value = profile.get("max_input_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _endpoint(llm: Any) -> Optional[_Endpoint]:
    if llm is None:
        return None
    if _is_watsonx(llm):
        url = _secret(getattr(llm, "url", None))
        model_id = getattr(llm, "model_id", None)
        if url and isinstance(model_id, str) and model_id:
            return _Endpoint("watsonx", url.rstrip("/"), model_id=model_id)
        return None  # deployment_id models are not in foundation_model_specs
    base_url = None
    for attr in ("openai_api_base", "api_base", "base_url"):
        base_url = _secret(getattr(llm, attr, None))
        if base_url:
            break
    if not base_url or not base_url.startswith(("http://", "https://")):
        return None
    host = (urlparse(base_url).hostname or "").lower()
    api_key = None
    for attr in ("openai_api_key", "api_key"):
        api_key = _secret(getattr(llm, attr, None))
        if api_key:
            break
    if host.endswith("openrouter.ai"):
        return _Endpoint("openrouter", base_url.rstrip("/"), api_key)
    if any(host == h or host.endswith("." + h) for h in _NO_WINDOW_HOSTS):
        return None
    return _Endpoint("openai-compatible", base_url.rstrip("/"), api_key)


# ── endpoint lookups ──────────────────────────────────────────────────────────


def _discovery_enabled() -> bool:
    try:
        from cuga.config import settings

        value = getattr(settings.advanced_features, "context_window_discovery", True)
    except Exception:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def _ssl_verify() -> Any:
    if os.environ.get("CUGA_DISABLE_SSL", "").lower() in ("true", "1", "yes"):
        return False
    if os.environ.get("OPENAI_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
        return False
    return os.environ.get("CUGA_SSL_CA_BUNDLE") or True


def _get_json(url: str, *, api_key: Optional[str] = None, params: Optional[dict] = None) -> Any:
    """GET *url* once per process; ``None`` on any failure (which is cached too)."""
    key = (url, tuple(sorted((params or {}).items())), api_key is not None)
    if key in _responses:
        return _responses[key]
    data = None
    try:
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        response = httpx.get(
            url, params=params, headers=headers, timeout=DISCOVERY_TIMEOUT_SECONDS, verify=_ssl_verify()
        )
        if response.status_code == 200:
            data = response.json()
        else:
            logger.debug("Context window lookup {} returned HTTP {}", url, response.status_code)
    except Exception as e:
        logger.debug("Context window lookup {} failed: {}", url, e)
    _responses[key] = data
    return data


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def _items(data: Any) -> list:
    if isinstance(data, Mapping):
        data = data.get("data")
    return [x for x in data if isinstance(x, Mapping)] if isinstance(data, list) else []


def _from_endpoint(endpoint: _Endpoint, name: str) -> Optional[ContextWindow]:
    if endpoint.kind == "openrouter":
        tokens = _openrouter_tokens(endpoint, name)
        return ContextWindow(tokens, "openrouter") if tokens else None
    if endpoint.kind == "watsonx":
        tokens = _watsonx_tokens(endpoint)
        return ContextWindow(tokens, "watsonx") if tokens else None
    found = _litellm_proxy_window(endpoint, name)
    if found is not None:
        return found
    tokens = _vllm_tokens(endpoint, name)
    return ContextWindow(tokens, "vllm") if tokens else None


def _openrouter_tokens(endpoint: _Endpoint, name: str) -> Optional[int]:
    for item in _items(_get_json(f"{endpoint.base_url}/models")):
        if item.get("id") != name:
            continue
        top = item.get("top_provider") if isinstance(item.get("top_provider"), Mapping) else {}
        sizes = [
            s
            for s in (_positive_int(item.get("context_length")), _positive_int(top.get("context_length")))
            if s
        ]
        # Requests route to the top provider, which may serve less than the model's maximum.
        return min(sizes) if sizes else None
    return None


def _watsonx_tokens(endpoint: _Endpoint) -> Optional[int]:
    data = _get_json(
        f"{endpoint.base_url}/ml/v1/foundation_model_specs",
        params={"version": WATSONX_SPECS_VERSION, "filters": f"modelid_{endpoint.model_id}"},
    )
    resources = data.get("resources") if isinstance(data, Mapping) else None
    for item in resources or []:
        if isinstance(item, Mapping) and item.get("model_id") == endpoint.model_id:
            limits = item.get("model_limits") if isinstance(item.get("model_limits"), Mapping) else {}
            return _positive_int(limits.get("max_sequence_length"))
    return None


def _proxy_names(name: str) -> set[str]:
    names = {name}
    for prefix in _PROXY_PREFIXES:
        if name.startswith(prefix):
            names.add(name[len(prefix) :])
    return names


def _litellm_proxy_window(endpoint: _Endpoint, name: str) -> Optional[ContextWindow]:
    root = endpoint.base_url[: -len("/v1")] if endpoint.base_url.endswith("/v1") else endpoint.base_url
    names = _proxy_names(name)
    for item in _items(_get_json(f"{root}/model/info", api_key=endpoint.api_key)):
        if item.get("model_name") not in names:
            continue
        info = item.get("model_info") if isinstance(item.get("model_info"), Mapping) else {}
        tokens = _positive_int(info.get("max_input_tokens"))
        if tokens:
            return ContextWindow(tokens, "litellm-proxy")
        params = item.get("litellm_params") if isinstance(item.get("litellm_params"), Mapping) else {}
        backend = params.get("model")
        tokens = litellm_map_tokens(backend) if isinstance(backend, str) else None
        if tokens:
            return ContextWindow(tokens, "litellm-proxy-backend")
        return None
    return None


def _vllm_tokens(endpoint: _Endpoint, name: str) -> Optional[int]:
    for item in _items(_get_json(f"{endpoint.base_url}/models", api_key=endpoint.api_key)):
        if item.get("id") in _proxy_names(name):
            return _positive_int(item.get("max_model_len"))
    return None


# ── LiteLLM's bundled map ─────────────────────────────────────────────────────


def litellm_map_tokens(name: Optional[str]) -> Optional[int]:
    """``max_input_tokens`` for an exact LiteLLM model-map key (case-insensitive)."""
    if not name:
        return None
    try:
        # cuga.config sets LITELLM_LOCAL_MODEL_COST_MAP first, so LiteLLM reads its
        # bundled map instead of fetching one.
        import cuga.config  # noqa: F401
        import litellm  # heavy; imported on first lookup only
    except Exception:
        return None
    cost_map = getattr(litellm, "model_cost", None)
    if not isinstance(cost_map, Mapping):
        return None
    for candidate in dict.fromkeys((name, name.lower())):
        info = cost_map.get(candidate)
        if isinstance(info, Mapping):
            if str(info.get("litellm_provider") or "").startswith(_UNTRUSTED_MAP_PROVIDERS):
                return None
            tokens = _positive_int(info.get("max_input_tokens"))
            if tokens:
                return tokens
    return None
