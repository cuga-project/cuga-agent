"""AgentGraphAdapter — CoreGraphAdapter implementation for CugaLite (single-agent).

Defines graph seams and call_model hook overrides. Prompt, tool, and execution
logic live in ``prepare_node.py`` and ``sandbox_node.py``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    format_current_plan_section,
    format_task_todos_system_block,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    EXECUTION_OUTPUT_PREFIX,
    CoreGraphAdapter,
    enforce_step_limit,
)
from cuga.backend.cuga_graph.utils.harmony import contains_harmony_tokens, strip_harmony_tokens
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import create_prepare_tools_and_apps_node
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.response_utils import (
    clean_empty_response_retry_meta,
    extract_code_from_response_tool_calls,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import create_tool_exec_node
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
    _bind_tools_mode_from_settings,
    resolve_model_with_bind_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    EXECUTION_MODE_FUNCTION_CALLING,
    resolve_execution_mode,
    resolved_runtime_model_name,
    runtime_defaults_for_model,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import _first_user_message_text
from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    BLOCKED_CLAIM_CORRECTION,
    BlockedClaimEvidence,
    classify_nl_auto_continue_decision,
    normalize_assistant_text,
)
from cuga.backend.cuga_graph.utils.token_counter import clamp_watsonx_completion_for_messages
from cuga.backend.llm.errors import extract_code_from_tool_use_failed
from cuga.config import settings


_REASONING_KEYS = ("reasoning_content", "reasoning")

FC_MODE_VIOLATION_CORRECTION = (
    "You wrote a code block, but this session executes tools through native function-calling only. "
    "Code is never run here. Issue the tool call natively instead, or give the final answer as plain text."
)


def _sanitize_for_replay(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Copy history for replay to the provider.

    Assistant turns are replayed verbatim (``tool_calls`` intact) — but their
    ``reasoning_content`` is dropped and harmony framing stripped from string
    content: gpt-oss emits both, and strict OpenAI-compatible proxies 400 when
    they come back in a later round. Irrelevant in CodeAct, whose history is
    flattened to text before it leaves; in function-calling mode the raw turns
    are replayed from round 2 onward, so it matters here.
    """
    out: List[BaseMessage] = []
    for m in messages:
        ak = getattr(m, "additional_kwargs", None) or {}
        drop_reasoning = any(k in ak for k in _REASONING_KEYS)
        content = getattr(m, "content", None)
        new_content = (
            strip_harmony_tokens(content) if isinstance(content, str) and "<|" in content else content
        )
        if not drop_reasoning and new_content is content:
            out.append(m)
            continue
        update: Dict[str, Any] = {}
        if drop_reasoning:
            update["additional_kwargs"] = {k: v for k, v in ak.items() if k not in _REASONING_KEYS}
        if new_content is not content:
            update["content"] = new_content
        try:
            out.append(m.model_copy(update=update))
        except Exception:
            out.append(m)
    return out


def _few_shot_to_messages(few_shot: List[Any]) -> List[BaseMessage]:
    """The prepare node's normalized ``{role, content}`` demos, as real chat messages."""
    msgs: List[BaseMessage] = []
    for ex in few_shot or []:
        if not isinstance(ex, dict):
            continue
        role = (ex.get("role") or "").strip().lower()
        content = ex.get("content") or ""
        if not content:
            continue
        if role in ("user", "human"):
            msgs.append(HumanMessage(content=content))
        elif role in ("assistant", "ai"):
            msgs.append(AIMessage(content=content))
    return msgs


def _format_observed_tool_shapes_block(shapes: Dict[str, str]) -> str:
    lines = ["", "---", "", "## Observed tool output shapes (this session)", ""]
    for name, description in shapes.items():
        lines.append(f"- `{name}`: {description}. Use this shape directly — no need to probe again.")
    return "\n".join(lines) + "\n"


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
        spawn_futures_ref: Optional[Dict[str, Any]] = None,
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
        self._spawn_futures: Dict[str, Any] = spawn_futures_ref if spawn_futures_ref is not None else {}
        self._weak_schema_tool_names: frozenset = frozenset()
        self._observed_tool_shapes: Dict[str, str] = {}

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
            content = base_prompt + format_task_todos_system_block(self._task_todos_ref)
        else:
            task_todos = getattr(state, "task_todos", None)
            content = base_prompt + format_current_plan_section(task_todos) if task_todos else base_prompt

        if self._observed_tool_shapes:
            content += _format_observed_tool_shapes_block(self._observed_tool_shapes)
        return content

    def get_tools_needing_probing(self) -> frozenset[str]:
        return self._weak_schema_tool_names - self._observed_tool_shapes.keys()

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
        # Function-calling mode is inert without advertised tools, and the shipped
        # default is bind mode "none". Upgrade only when the *resolved* mode is none
        # — an explicit non-none choice from configurable, a model profile or
        # settings is respected. Additive: no effect in codeact.
        if self._execution_mode(configurable) == EXECUTION_MODE_FUNCTION_CALLING:
            if self._resolved_bind_mode(configurable) == "none":
                configurable = {**(configurable or {}), "cuga_lite_bind_tools_mode": "all"}
                logger.info("[fc] bind_tools mode was 'none'; upgraded to 'all' for function-calling mode")
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
        # Harmony framing is removed here, at the decode boundary, so every
        # downstream surface inherits clean text (see the base implementation).
        content = strip_harmony_tokens(normalize_assistant_text(response.content))
        if not content:
            tool_code = extract_code_from_response_tool_calls(response)
            if tool_code:
                logger.warning("Empty content with tool_calls detected; recovering tool call as Python code")
                content = tool_code
        reasoning = normalize_assistant_text(
            (getattr(response, "additional_kwargs", None) or {}).get("reasoning_content")
        )
        return content, reasoning

    def on_response_processed(
        self,
        state: Any,
        code: Optional[str],
        content: str,
        reasoning: Optional[str] = None,
    ) -> None:
        try:
            self._tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))
            if reasoning:
                self._tracker.collect_step(step=Step(name="Assistant_reasoning", data=reasoning))
            if code:
                fenced_code = f"```python\n{code}\n```"
                self._tracker.collect_step(step=Step(name="Assistant_code", data=fenced_code))
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
    ) -> bool | str:
        """Bool as in the base contract; a non-empty ``str`` means "continue, and
        use this text as the synthetic user message" (unverified-blocker retry,
        issue #610)."""
        evidence = BlockedClaimEvidence(
            tools_available=bool(self._tools_context),
            code_executed=self._any_execution_ran(state),
            retry_used=bool(self.get_metadata(state).get("_blocked_claim_retry")),
        )
        decision = await classify_nl_auto_continue_decision(model, content, reasoning, evidence=evidence)
        if decision.blocked_override:
            # One-shot: record the spent retry so a second refusal finalizes.
            # shared_nodes re-reads metadata after this call, so the marker
            # persists through the auto-continue Command update.
            self.set_metadata(state, {**self.get_metadata(state), "_blocked_claim_retry": True})
            return BLOCKED_CLAIM_CORRECTION
        return decision.auto_continue

    def _any_execution_ran(self, state: Any) -> bool:
        """Has any sandbox execution produced feedback this task? Detected via the
        shared ``EXECUTION_OUTPUT_PREFIX`` emitted by ``execution_output_text``."""
        for msg in self.get_messages(state):
            if not isinstance(msg, HumanMessage):
                continue
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.startswith(EXECUTION_OUTPUT_PREFIX):
                return True
        return False

    # ── Native function-calling mode ──────────────────────────────────────

    def _runtime_model_name(self, configurable: dict) -> str:
        return resolved_runtime_model_name(
            configurable_llm=(configurable or {}).get("llm"), graph_default_model=self._model
        )

    def _execution_mode(self, configurable: dict) -> str:
        return resolve_execution_mode(configurable, self._runtime_model_name(configurable))

    def _resolved_bind_mode(self, configurable: dict) -> str:
        cfg = configurable or {}
        prof = runtime_defaults_for_model(self._runtime_model_name(configurable))
        for candidate in (cfg.get("cuga_lite_bind_tools_mode"), prof.get("cuga_lite_bind_tools_mode")):
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip().lower()
        return _bind_tools_mode_from_settings()

    async def execute_call_model_fc(
        self,
        *,
        state: Any,
        config: Any,
        configurable: dict,
        active_model: Any,
        bound: Any,
        invoke_config: dict,
        system_content: str,
        modified_messages: list,
        budget_exhausted: bool,
        playbook_fired: bool,
    ) -> Optional[Command]:
        """Function-calling turn: invoke with real message objects, route on ``tool_calls``.

        Returns ``None`` in codeact mode so the shared CodeAct path runs untouched.
        Otherwise builds the outbound list from ``system_content`` (the FC prompt
        that ``prepare`` selected), the few-shot demos as chat messages, and the
        sanitized history — hands them straight to ``bound.ainvoke`` so the shared
        dict serializer (which flattens ``tool_calls``) is never involved — then:

        - ``tool_calls`` present  -> ``Command(goto="tool_exec")``, assistant turn kept verbatim
        - otherwise               -> final answer, ``END``
        - a fenced code block with no ``tool_calls`` is a mode violation: it is
          never executed; the model gets one corrective turn instead.
        """
        if self._execution_mode(configurable) != EXECUTION_MODE_FUNCTION_CALLING:
            return None

        from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
            TOOL_BUDGET_EXHAUSTED_INSTRUCTION,
        )

        msgs: List[BaseMessage] = [SystemMessage(content=system_content)]
        msgs.extend(_few_shot_to_messages(self.get_few_shot_messages(state)))
        msgs.extend(_sanitize_for_replay(list(modified_messages)))
        if budget_exhausted:
            # Outbound only, like call_model's CodeAct path: never persisted.
            msgs.append(HumanMessage(content=TOOL_BUDGET_EXHAUSTED_INSTRUCTION))

        clamp_watsonx_completion_for_messages(bound, msgs)
        response = await bound.ainvoke(msgs, config=invoke_config)

        tool_calls = list(getattr(response, "tool_calls", None) or [])
        invalid_tool_calls = list(getattr(response, "invalid_tool_calls", None) or [])
        content = strip_harmony_tokens(normalize_assistant_text(getattr(response, "content", "")) or "")
        reasoning = normalize_assistant_text(
            (getattr(response, "additional_kwargs", None) or {}).get("reasoning_content")
        )
        if not isinstance(response, AIMessage):
            response = AIMessage(content=content, tool_calls=tool_calls)

        if budget_exhausted:
            # No tools were bound for the grace turn, so any tool_calls are noise.
            tool_calls, invalid_tool_calls = [], []

        if tool_calls or invalid_tool_calls:
            try:
                self._tracker.collect_step(
                    step=Step(
                        name="Assistant_tool_calls",
                        data=json.dumps(tool_calls, ensure_ascii=False, default=str),
                    )
                )
            except Exception as exc:
                logger.debug(f"AgentGraphAdapter fc tracker error: {exc}")
        else:
            self.on_response_processed(state, None, content, reasoning)

        final_messages: list = list(modified_messages) + [response]
        new_step_count: int = state.step_count + 1
        limit_cmd = (
            None
            if budget_exhausted
            else enforce_step_limit(
                self,
                state=state,
                messages=final_messages,
                new_step_count=new_step_count,
                limit=self.resolve_max_steps(state, (configurable or {}).get("cuga_lite_max_steps")),
            )
        )
        if limit_cmd is not None:
            return limit_cmd

        meta_update = {self.metadata_key: self.build_metadata_update(state, playbook_fired=playbook_fired)}

        if tool_calls or invalid_tool_calls:
            logger.info("[fc] {} native tool_call(s) -> tool_exec", len(tool_calls) + len(invalid_tool_calls))
            return Command(
                goto="tool_exec",
                update={
                    self.messages_key: final_messages,
                    "script": None,
                    "step_count": new_step_count,
                    **meta_update,
                },
            )

        if "```" in content and not budget_exhausted:
            # Mode violation: never execute code here. One corrective turn, charged
            # as a step so it cannot loop past cuga_lite_max_steps.
            meta = dict(self.get_metadata(state))
            violations = int(meta.get("fc_mode_violations", 0) or 0) + 1
            self.set_metadata(state, {**meta, "fc_mode_violations": violations})
            logger.warning("[fc] mode violation #{}: code block emitted in function-calling mode", violations)
            return Command(
                goto="call_model",
                update={
                    self.messages_key: final_messages + [HumanMessage(content=FC_MODE_VIOLATION_CORRECTION)],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    self.metadata_key: self.build_metadata_update(state, playbook_fired=playbook_fired),
                },
            )

        final_answer = content
        if not final_answer.strip() and reasoning and not contains_harmony_tokens(reasoning):
            final_answer = reasoning.strip()
        if not final_answer.strip():
            for m in reversed(modified_messages):
                if isinstance(m, ToolMessage) and isinstance(m.content, str) and m.content.strip():
                    final_answer = m.content
                    break
        if not content.strip() and final_answer:
            final_messages = list(modified_messages) + [AIMessage(content=final_answer)]

        logger.info("[fc] no tool_calls -> final answer (END)")
        return Command(
            goto=END,
            update={
                self.messages_key: final_messages,
                "script": None,
                "final_answer": final_answer,
                "execution_complete": True,
                "step_count": new_step_count,
                **meta_update,
            },
        )

    def build_tool_exec_node(self):
        return create_tool_exec_node(self)

    def build_prepare_node(self, lc_bind_tools_meta: dict):
        return create_prepare_tools_and_apps_node(self, lc_bind_tools_meta)

    def build_sandbox_node(self, base_thread_id: Any, base_apps_list: Any):
        return create_sandbox_node(self, base_thread_id, base_apps_list)
