"""Runtime defaults keyed by resolved LLM model name (overrides TOML unless configurable wins)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

GPT_OSS_20B_ID = "gpt-oss-20b"

GPT_OSS_20B_RUNTIME_DEFAULTS: Dict[str, Any] = {
    "cuga_lite_enable_few_shots": True,
    "cuga_lite_bind_tools_mode": "apps",
    "cuga_lite_bind_tools_apps": ["knowledge", "filesystem"],
    "cuga_lite_bind_tools_include_find_tools": True,
}


def _normalized_model_key(model_name: str) -> str:
    """Strip provider prefix so ``openai/gpt-oss-20b`` matches ``gpt-oss-20b``."""
    s = model_name.strip().lower()
    if "/" in s:
        return s.rsplit("/", 1)[-1].strip()
    return s


def runtime_defaults_for_model(model_name: Optional[str]) -> Dict[str, Any]:
    if not model_name:
        return {}
    key = _normalized_model_key(model_name)
    if key == GPT_OSS_20B_ID:
        return dict(GPT_OSS_20B_RUNTIME_DEFAULTS)
    return {}


def resolved_runtime_model_name(
    *,
    configurable_llm: Any,
    graph_default_model: Any,
) -> str:
    for src in (configurable_llm, graph_default_model):
        if src is None:
            continue
        name = getattr(src, "model_name", None) or getattr(src, "model", None)
        if name:
            return str(name).strip()
    return ""


def _bool_coerce(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return bool(val)


def normalize_bind_tools_apps_value(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def resolve_bind_tools_fields(
    configurable: Optional[Dict[str, Any]],
    model_name: Optional[str],
    *,
    settings_mode_fn: Callable[[], str],
    settings_apps_fn: Callable[[], List[str]],
    settings_include_fn: Callable[[], bool],
) -> tuple[str, List[str], bool]:
    """Configurable overrides profile overrides plain settings for bind_tools_* keys."""
    cfg = configurable or {}
    mn = (model_name or "").strip()
    prof = runtime_defaults_for_model(mn)

    mode = cfg.get("cuga_lite_bind_tools_mode")
    if mode is None or str(mode).strip() == "":
        mode = prof.get("cuga_lite_bind_tools_mode")
    if mode is None or str(mode).strip() == "":
        mode = settings_mode_fn()
    mode_s = str(mode).strip().lower()

    apps_raw = cfg.get("cuga_lite_bind_tools_apps")
    if apps_raw is None or apps_raw == []:
        apps_raw = prof.get("cuga_lite_bind_tools_apps")
    if apps_raw is None or apps_raw == []:
        apps_raw = settings_apps_fn()
    apps_list = normalize_bind_tools_apps_value(apps_raw)

    inc = cfg.get("cuga_lite_bind_tools_include_find_tools")
    if inc is None:
        inc = prof.get("cuga_lite_bind_tools_include_find_tools")
    if inc is None:
        inc = settings_include_fn()

    return mode_s, apps_list, _bool_coerce(inc)
