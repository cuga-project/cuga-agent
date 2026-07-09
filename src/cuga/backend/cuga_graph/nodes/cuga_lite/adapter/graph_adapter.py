"""AgentGraphAdapter — CoreGraphAdapter implementation for CugaLite (single-agent).

Defines graph seams and call_model hook overrides. Prompt, tool, and execution
logic live in ``prepare_node.py`` and ``sandbox_node.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import BaseMessage
from loguru import logger

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    format_current_plan_section,
    format_task_todos_system_block,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import create_prepare_tools_and_apps_node
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.response_utils import (
    clean_empty_response_retry_meta,
    extract_code_from_response_tool_calls,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import resolve_model_with_bind_tools
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import _first_user_message_text
from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    classify_nl_auto_continue,
    normalize_assistant_text,
)
from cuga.backend.cuga_graph.utils.token_counter import clamp_watsonx_completion_for_messages
from cuga.backend.llm.errors import extract_code_from_tool_use_failed
from cuga.config import settings


class AgentGraphAdapter(CoreGraphAdapter):
    """CoreGraphAdapter implementation for the CugaLite single-agent graph."""

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
        model: Any = None,
        prompt_template: Any = None,
        instructions: Any = None,
        special_instructions: Any = None,
        tools_context: Optional[Dict[str, Any]] = None,
        static_prompt: Any = None,
        thread_id: Any = None,
    ) -> None:
        self._tracker = tracker
        self._base_callbacks = base_callbacks or []
        self._task_todos_ref = task_todos_ref
        self._tools_context_ref = tools_context_ref
        self._base_tool_provider = base_tool_provider
        self._model = model
        self._prompt_template = prompt_template
        self._instructions = instructions
        self._special_instructions = special_instructions
        self._tools_context = tools_context if tools_context is not None else {}
        self._static_prompt = static_prompt
        self._thread_id = thread_id

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
            clamp_watsonx_completion_for_messages(bound, messages)
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

    async def resolve_bind_tools(
        self,
        state: Any,
        active_model: Any,
        configurable: dict,
        config: Any = None,
    ) -> Any:
        try:
            return await resolve_model_with_bind_tools(
                active_model,
                configurable=configurable,
                tools_context_ref=self._tools_context_ref,
                tool_provider=self._base_tool_provider,
                query=_first_user_message_text(getattr(state, "chat_messages", None)),
                run_config=config,
            )
        except RuntimeError:
            # Cap/shortlist failures surface intentionally — do not silently fall back.
            raise
        except Exception as exc:
            logger.warning(f"AgentGraphAdapter.resolve_bind_tools failed: {exc}")
        return None

    def normalize_response(self, response: Any) -> Tuple[str, Optional[str]]:
        content = normalize_assistant_text(response.content)
        if not content:
            tool_code = extract_code_from_response_tool_calls(response)
            if tool_code:
                logger.warning("Empty content with tool_calls detected; recovering tool call as Python code")
                content = tool_code
        reasoning = normalize_assistant_text(
            (getattr(response, "additional_kwargs", None) or {}).get("reasoning_content")
        )
        return content, reasoning

    def on_response_processed(self, state: Any, code: Optional[str], content: str) -> None:
        try:
            self._tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))
            if code:
                self._tracker.collect_step(step=Step(name="Assistant_code", data=content))
            else:
                self._tracker.collect_step(step=Step(name="Assistant_nl", data=content))
        except Exception as exc:
            logger.debug(f"AgentGraphAdapter.on_response_processed tracker error: {exc}")

    def build_metadata_update(self, state: Any, *, playbook_fired: bool) -> dict:
        meta = clean_empty_response_retry_meta(self.get_metadata(state))
        if playbook_fired:
            return {**meta, "playbook_guidance_added": True}
        return meta

    async def classify_auto_continue(
        self, state: Any, model: Any, content: str, reasoning: Optional[str]
    ) -> bool:
        return await classify_nl_auto_continue(model, content, reasoning)

    def build_prepare_node(self, lc_bind_tools_meta: dict):
        return create_prepare_tools_and_apps_node(self, lc_bind_tools_meta)

    def build_sandbox_node(self, base_thread_id: Any, base_apps_list: Any):
        return create_sandbox_node(self, base_thread_id, base_apps_list)
