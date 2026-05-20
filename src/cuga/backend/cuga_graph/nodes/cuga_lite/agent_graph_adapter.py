"""AgentGraphAdapter — CoreGraphAdapter implementation for CugaLite (single-agent).

Provides all hook overrides that the shared ``create_call_model_node`` factory
delegates to for Lite-specific behaviour:

- Few-shot messages, PI injection, todos system block
- normalize_response: normalize_assistant_text + tool-call code recovery
- Tracker side-effects, Langfuse callbacks
- Metadata cleanup (_clean_empty_response_retry_meta)
- NL auto-continue via classify_nl_auto_continue

Also houses the format_task_todos_system_block / format_current_plan_section
helpers that were previously defined in cuga_lite_graph.py; cuga_lite_graph.py
imports them from here in Phase 6.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import BaseMessage
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    classify_nl_auto_continue,
    normalize_assistant_text,
)
from cuga.backend.llm.errors import extract_code_from_tool_use_failed
from cuga.config import settings


# ── Helpers (moved from cuga_lite_graph.py) ────────────────────────────────


def _clean_empty_response_retry_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    m = {**(meta or {})}
    m.pop("_empty_response_correction", None)
    return m


def format_task_todos_system_block(todos: List[Dict[str, str]]) -> str:
    if not todos:
        return ""
    lines = [
        "",
        "---",
        "",
        "## Current task todos",
        "",
        "Execution only prints **Todos updated** after each change; use this list as the source of truth.",
        "",
    ]
    for i, item in enumerate(todos, start=1):
        status = item.get("status", "pending")
        text = item.get("text", "")
        lines.append(f"{i}. **[{status}]** {text}")
    lines.append("")
    return "\n".join(lines)


def format_current_plan_section(task_todos: List[Dict[str, Any]]) -> str:
    lines = ["## Current Plan", ""]
    for item in task_todos:
        text = str(item.get("text", "")).strip()
        status = str(item.get("status", "pending")).strip()
        lines.append(f"- **[{status}]** {text}")
    return "\n".join(lines) + "\n"


def _tool_call_kwarg_literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def _extract_code_from_response_tool_calls(response: Any) -> Optional[str]:
    """Recover fenced Python from AIMessage.tool_calls when content is empty."""
    tool_calls = getattr(response, "tool_calls", None) or (
        getattr(response, "additional_kwargs", None) or {}
    ).get("tool_calls")
    if not tool_calls:
        return None

    tc = tool_calls[0]
    if not isinstance(tc, dict):
        return None

    name = tc.get("name") or (tc.get("function") or {}).get("name")
    args = tc.get("args") or (tc.get("function") or {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}

    if not name:
        return None

    args_str = ", ".join(
        f"{k}={_tool_call_kwarg_literal(v)}" for k, v in (args if isinstance(args, dict) else {}).items()
    )
    return f"```python\nresult = await {name}({args_str})\nprint(result)\n```"


# ── AgentGraphAdapter ──────────────────────────────────────────────────────


class AgentGraphAdapter(CoreGraphAdapter):
    """CoreGraphAdapter implementation for the CugaLite single-agent graph.

    Overrides all call_model hooks that differ from the no-op defaults:
    few-shot messages, PI, todos, normalize, tracker, callbacks, metadata
    cleanup, and NL auto-continue.
    """

    messages_key: str = "chat_messages"
    execute_node_name: str = "sandbox"
    metadata_key: str = "cuga_lite_metadata"
    sender_name: str = "CugaLite"

    def __init__(
        self,
        *,
        tracker: Any,
        base_callbacks: Optional[List[Any]],
        task_todos_ref: List[Dict[str, str]],
        tools_context_ref: Optional[Dict[str, Any]],
        base_tool_provider: Any,
    ) -> None:
        self._tracker = tracker
        self._base_callbacks = base_callbacks or []
        self._task_todos_ref = task_todos_ref
        self._tools_context_ref = tools_context_ref
        self._base_tool_provider = base_tool_provider

    # ── Abstract method implementations ───────────────────────────────────

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        if override is not None:
            return override
        return (
            state.cuga_lite_max_steps
            if getattr(state, "cuga_lite_max_steps", None) is not None
            else getattr(settings.advanced_features, "cuga_lite_max_steps", 50)
        )

    # ── Pre-invocation hook overrides ─────────────────────────────────────

    def get_few_shot_messages(self, state: Any) -> List[Any]:
        return list(state.mcp_few_shot_messages or [])

    def get_pi(self, state: Any) -> Optional[str]:
        return getattr(state, "pi", None)

    def prepare_system_content(self, state: Any, configurable: dict, base_prompt: str) -> str:
        if self._task_todos_ref:
            return base_prompt + format_task_todos_system_block(self._task_todos_ref)
        task_todos = getattr(state, "task_todos", None)
        if task_todos:
            return base_prompt + format_current_plan_section(task_todos)
        return base_prompt

    def get_tracker(self) -> Any:
        return self._tracker

    def get_invoke_config(self, configurable: dict) -> dict:
        callbacks = configurable.get("callbacks", self._base_callbacks)
        return {"callbacks": callbacks}

    async def ainvoke_model(self, bound: Any, messages: list, invoke_config: dict) -> Any:
        try:
            return await bound.ainvoke(messages, config=invoke_config)
        except Exception as exc:
            code = extract_code_from_tool_use_failed(exc)
            if code:
                logger.warning(
                    "Model attempted tool call without tools bound (tool_use_failed). "
                    "Using generated code in sandbox"
                )

                class _FakeResponse:
                    content = f"```python\n{code}\n```"
                    additional_kwargs: dict = {}

                return _FakeResponse()
            raise

    async def resolve_bind_tools(self, state: Any, active_model: Any, configurable: dict) -> Any:
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            resolve_model_with_bind_tools,
        )

        try:
            return await resolve_model_with_bind_tools(
                active_model,
                configurable=configurable,
                tools_context_ref=self._tools_context_ref,
                tool_provider=self._base_tool_provider,
            )
        except Exception as exc:
            logger.warning("AgentGraphAdapter.resolve_bind_tools failed: %s", exc)
            return None

    # ── Post-invocation hook overrides ────────────────────────────────────

    def normalize_response(self, response: Any) -> Tuple[str, Optional[str]]:
        content = normalize_assistant_text(response.content)
        if not content:
            tool_code = _extract_code_from_response_tool_calls(response)
            if tool_code:
                logger.warning("Empty content with tool_calls detected; recovering tool call as Python code")
                content = tool_code
        reasoning = normalize_assistant_text(
            (getattr(response, "additional_kwargs", None) or {}).get("reasoning_content")
        )
        return content, reasoning

    def on_response_processed(self, state: Any, code: Optional[str], content: str) -> None:
        try:
            from cuga.backend.activity_tracker.tracker import Step

            self._tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))
            if code:
                self._tracker.collect_step(step=Step(name="Assistant_code", data=content))
            else:
                self._tracker.collect_step(step=Step(name="Assistant_nl", data=content))
        except Exception as exc:
            logger.debug("AgentGraphAdapter.on_response_processed tracker error: %s", exc)

    def build_metadata_update(self, state: Any, *, playbook_fired: bool) -> dict:
        meta = _clean_empty_response_retry_meta(self.get_metadata(state))
        if playbook_fired:
            return {**meta, "playbook_guidance_added": True}
        return meta

    async def classify_auto_continue(
        self, state: Any, model: Any, content: str, reasoning: Optional[str]
    ) -> bool:
        return await classify_nl_auto_continue(model, content, reasoning)
