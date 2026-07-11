"""Typed native function-calling configuration for the CugaLite SDK (issue #471).

``ToolCalling`` is the first-class, opt-in way to enable native tool calls on a
``CugaAgent``. It serializes to the existing ``cuga_lite_bind_tools_*`` +
``cuga_lite_tool_invocation_mode`` ``configurable`` keys — the same channel the
graph already reads — so no graph rewiring is needed.

Default is ``mode="code"`` → serializes to ``{}`` → nothing is set → the agent
behaves exactly as it does today (code-act only).
"""

from typing import Any, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, Field

VALID_INVOCATION_MODES = ("code", "native", "hybrid")


def resolve_tool_invocation_mode(configurable: Optional[Dict[str, Any]]) -> str:
    """Effective tool-invocation mode: ``configurable`` overrides the global
    ``advanced_features.cuga_lite_tool_invocation_mode`` setting; default ``code``.

    Fully guarded — any error resolves to ``code`` (the unchanged legacy path).
    """
    try:
        mode = (configurable or {}).get("cuga_lite_tool_invocation_mode")
        if mode is None or not str(mode).strip():
            from cuga.config import settings

            mode = getattr(settings.advanced_features, "cuga_lite_tool_invocation_mode", "code")
        mode_s = str(mode).strip().lower()
        return mode_s if mode_s in VALID_INVOCATION_MODES else "code"
    except Exception:
        return "code"


def native_tool_calls_enabled(configurable: Optional[Dict[str, Any]]) -> bool:
    """True when native function calling is opted in (mode native/hybrid)."""
    return resolve_tool_invocation_mode(configurable) in ("native", "hybrid")


class ToolCalling(BaseModel):
    """How the agent may invoke tools.

    - ``code`` (default): tools are called only via generated Python (unchanged).
    - ``native``: the model is permitted to emit native tool calls; they execute
      through the same sandbox-backed, guarded, tracked pipeline as code.
    - ``hybrid``: both encodings are offered; the model picks per step.

    Tool selection (which tools are bound) mirrors the existing bind-tools modes:
    give ``native_tools`` (specific tool names) or ``apps`` (whole apps); if
    neither is set, all available tools are bound.
    """

    mode: Literal["code", "native", "hybrid"] = "code"
    native_tools: Optional[List[str]] = Field(
        default=None, description="Bind only these tool names (StructuredTool.name)."
    )
    apps: Optional[List[str]] = Field(default=None, description="Bind all tools of these apps.")
    include_find_tools: bool = Field(
        default=False, description="Also bind find_tools alongside the selection."
    )
    max_bound_tools: Optional[int] = Field(
        default=None,
        description="Per-agent cap on tools sent to bind_tools (0 disables the cap). "
        "Overrides advanced_features.cuga_lite_bind_tools_max_count for this run.",
    )
    tool_choice: Optional[Any] = Field(
        default=None,
        description="Passed to bind_tools: 'auto' (model decides), 'required'/'any' "
        "(force a tool call), 'none', or a provider-specific value. Useful for "
        "code-preferring models that otherwise never emit native tool calls. "
        "Providers that don't support it degrade to a plain bind. WARNING: "
        "'required'/'any' forces a tool call on EVERY turn, so the model can "
        "never emit a plain-text final answer — the run will loop until "
        "cuga_lite_max_steps and return a truncated result. Prefer 'auto'; use "
        "'required' only for single-call flows with a low max_steps.",
    )


def tool_calling_to_configurable(tc: Optional[ToolCalling]) -> Dict[str, Any]:
    """Serialize ``ToolCalling`` to graph ``configurable`` keys.

    Returns ``{}`` for ``None`` (no opt-in). An explicit ``mode="code"`` returns
    ``{"cuga_lite_tool_invocation_mode": "code"}`` so a per-agent/per-invoke
    opt-out overrides a global ``native`` default. Fully guarded: on any error it
    disables FC rather than raising.
    """
    try:
        if tc is None:
            return {}
        if tc.mode == "code":
            return {"cuga_lite_tool_invocation_mode": "code"}
        cfg: Dict[str, Any] = {"cuga_lite_tool_invocation_mode": tc.mode}
        if tc.native_tools:
            cfg["cuga_lite_bind_tools_mode"] = "tools"
            cfg["cuga_lite_bind_tools_tool_names"] = list(tc.native_tools)
        elif tc.apps:
            cfg["cuga_lite_bind_tools_mode"] = "apps"
            cfg["cuga_lite_bind_tools_apps"] = list(tc.apps)
        else:
            cfg["cuga_lite_bind_tools_mode"] = "all"
        if tc.include_find_tools:
            cfg["cuga_lite_bind_tools_include_find_tools"] = True
        if tc.max_bound_tools is not None:
            cfg["cuga_lite_bind_tools_max_count"] = int(tc.max_bound_tools)
        if tc.tool_choice is not None:
            cfg["cuga_lite_tool_choice"] = tc.tool_choice
        return cfg
    except Exception as e:
        logger.warning(f"ToolCalling serialization failed; native function calling disabled: {e}")
        return {}
