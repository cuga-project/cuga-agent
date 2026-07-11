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


def tool_calling_to_configurable(tc: Optional[ToolCalling]) -> Dict[str, Any]:
    """Serialize ``ToolCalling`` to graph ``configurable`` keys.

    Returns ``{}`` for ``None`` or ``mode="code"`` so the default path is
    untouched. Fully guarded: on any error it disables FC rather than raising.
    """
    try:
        if tc is None or tc.mode == "code":
            return {}
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
        return cfg
    except Exception as e:
        logger.warning(f"ToolCalling serialization failed; native function calling disabled: {e}")
        return {}
