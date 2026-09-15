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
    """Normalize provider prefix and separator so ``openai/gpt-oss-20b`` and
    Ollama's ``gpt-oss:20b`` both match ``gpt-oss-20b``."""
    s = model_name.strip().lower()
    if "/" in s:
        s = s.rsplit("/", 1)[-1].strip()
    return s.replace(":", "-")


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


def _normalize_bind_tools_string_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def normalize_bind_tools_apps_value(raw: Any) -> List[str]:
    return _normalize_bind_tools_string_list(raw)


def normalize_bind_tools_tool_names_value(raw: Any) -> List[str]:
    return _normalize_bind_tools_string_list(raw)


def resolve_bind_tools_fields(
    configurable: Optional[Dict[str, Any]],
    model_name: Optional[str],
    *,
    settings_mode_fn: Callable[[], str],
    settings_apps_fn: Callable[[], List[str]],
    settings_tool_names_fn: Callable[[], List[str]],
    settings_include_fn: Callable[[], bool],
) -> tuple[str, List[str], List[str], bool]:
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

    tnames_raw = cfg.get("cuga_lite_bind_tools_tool_names")
    if tnames_raw is None or tnames_raw == []:
        tnames_raw = prof.get("cuga_lite_bind_tools_tool_names")
    if tnames_raw is None or tnames_raw == []:
        tnames_raw = settings_tool_names_fn()
    tool_names_list = normalize_bind_tools_tool_names_value(tnames_raw)

    inc = cfg.get("cuga_lite_bind_tools_include_find_tools")
    if inc is None:
        inc = prof.get("cuga_lite_bind_tools_include_find_tools")
    if inc is None:
        inc = settings_include_fn()

    return mode_s, apps_list, tool_names_list, _bool_coerce(inc)


# ── Execution mode / step discipline ─────────────────────────────────────────
#
# Same three-layer resolution as the bind_tools keys above: ``configurable``
# (per invoke) overrides the per-model runtime profile overrides ``settings``.
# Unknown values never raise — they fall back to the default with a warning,
# so a typo in an env var cannot take an agent down.

EXECUTION_MODE_CODEACT = "codeact"
EXECUTION_MODE_FUNCTION_CALLING = "function_calling"

_EXECUTION_MODE_ALIASES: Dict[str, str] = {
    "codeact": EXECUTION_MODE_CODEACT,
    "code_act": EXECUTION_MODE_CODEACT,
    "code": EXECUTION_MODE_CODEACT,
    "sandbox": EXECUTION_MODE_CODEACT,
    "function_calling": EXECUTION_MODE_FUNCTION_CALLING,
    "functioncalling": EXECUTION_MODE_FUNCTION_CALLING,
    "function-calling": EXECUTION_MODE_FUNCTION_CALLING,
    "fc": EXECUTION_MODE_FUNCTION_CALLING,
    "native": EXECUTION_MODE_FUNCTION_CALLING,
    "native_tool_calling": EXECUTION_MODE_FUNCTION_CALLING,
    "tool_calling": EXECUTION_MODE_FUNCTION_CALLING,
    "toolcalling": EXECUTION_MODE_FUNCTION_CALLING,
}

STEP_DISCIPLINE_OFF = "off"
STEP_DISCIPLINE_ONE_TOOL_PER_STEP = "one_tool_per_step"

_STEP_DISCIPLINE_ALIASES: Dict[str, str] = {
    "off": STEP_DISCIPLINE_OFF,
    "none": STEP_DISCIPLINE_OFF,
    "false": STEP_DISCIPLINE_OFF,
    "0": STEP_DISCIPLINE_OFF,
    "one_tool_per_step": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    "one_tool_per_turn": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    "one_tool_per_block": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    "stepwise": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    "one": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    "true": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
    "1": STEP_DISCIPLINE_ONE_TOOL_PER_STEP,
}

FC_PROMPT_FRAGMENT_EVIDENCE_FIRST = "evidence_first"
KNOWN_FC_PROMPT_FRAGMENTS = frozenset({FC_PROMPT_FRAGMENT_EVIDENCE_FIRST})


def _settings_value(key: str, default: Any) -> Any:
    """Read ``settings.advanced_features.<key>`` lazily; never raises."""
    try:
        from cuga.config import settings

        return getattr(settings.advanced_features, key, default)
    except Exception:
        return default


def _layered(
    key: str,
    configurable: Optional[Dict[str, Any]],
    model_name: Optional[str],
    settings_fn: Optional[Callable[[], Any]],
    default: Any,
) -> Any:
    """configurable > per-model profile > settings > default, skipping empties."""
    cfg = configurable or {}
    prof = runtime_defaults_for_model((model_name or "").strip())
    for candidate in (
        cfg.get(key),
        prof.get(key),
        settings_fn() if settings_fn else _settings_value(key, None),
    ):
        if candidate is None:
            continue
        if isinstance(candidate, str) and not candidate.strip():
            continue
        if isinstance(candidate, (list, tuple)) and len(candidate) == 0:
            continue
        return candidate
    return default


def normalize_execution_mode(raw: Any) -> str:
    """Map any accepted spelling to a canonical mode; unknown -> codeact + warning."""
    if raw is None:
        return EXECUTION_MODE_CODEACT
    key = str(raw).strip().lower()
    if key in _EXECUTION_MODE_ALIASES:
        return _EXECUTION_MODE_ALIASES[key]
    try:
        from loguru import logger

        logger.warning(
            "cuga_lite_execution_mode={!r} is not recognised; falling back to {!r}",
            raw,
            EXECUTION_MODE_CODEACT,
        )
    except Exception:
        pass
    return EXECUTION_MODE_CODEACT


def normalize_step_discipline(raw: Any) -> str:
    if raw is None:
        return STEP_DISCIPLINE_OFF
    if isinstance(raw, bool):
        return STEP_DISCIPLINE_ONE_TOOL_PER_STEP if raw else STEP_DISCIPLINE_OFF
    key = str(raw).strip().lower()
    if key in _STEP_DISCIPLINE_ALIASES:
        return _STEP_DISCIPLINE_ALIASES[key]
    try:
        from loguru import logger

        logger.warning(
            "cuga_lite_step_discipline={!r} is not recognised; falling back to {!r}", raw, STEP_DISCIPLINE_OFF
        )
    except Exception:
        pass
    return STEP_DISCIPLINE_OFF


def resolve_execution_mode(
    configurable: Optional[Dict[str, Any]],
    model_name: Optional[str] = None,
    *,
    settings_mode_fn: Optional[Callable[[], Any]] = None,
) -> str:
    """Resolved execution mode: ``codeact`` or ``function_calling``."""
    raw = _layered(
        "cuga_lite_execution_mode", configurable, model_name, settings_mode_fn, EXECUTION_MODE_CODEACT
    )
    return normalize_execution_mode(raw)


def resolve_step_discipline(
    configurable: Optional[Dict[str, Any]],
    model_name: Optional[str] = None,
    *,
    settings_fn: Optional[Callable[[], Any]] = None,
) -> str:
    """Resolved step discipline: ``off`` or ``one_tool_per_step``. Applies to both modes."""
    raw = _layered("cuga_lite_step_discipline", configurable, model_name, settings_fn, STEP_DISCIPLINE_OFF)
    return normalize_step_discipline(raw)


def resolve_fc_prompt_fragments(
    configurable: Optional[Dict[str, Any]],
    model_name: Optional[str] = None,
    *,
    settings_fn: Optional[Callable[[], Any]] = None,
) -> List[str]:
    """Opt-in function-calling prompt fragments, unknown names dropped with a warning."""
    raw = _layered("cuga_lite_fc_prompt_fragments", configurable, model_name, settings_fn, [])
    names = _normalize_bind_tools_string_list(raw)
    out: List[str] = []
    for n in names:
        key = n.strip().lower()
        if key in KNOWN_FC_PROMPT_FRAGMENTS:
            if key not in out:
                out.append(key)
        else:
            try:
                from loguru import logger

                logger.warning("Unknown cuga_lite_fc_prompt_fragments entry {!r} ignored", n)
            except Exception:
                pass
    return out
