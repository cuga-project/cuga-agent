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
    EMPTY_RESPONSE_CORRECTION,
    EMPTY_RESPONSE_CORRECTION_KEY,
    EXECUTION_OUTPUT_PREFIX,
    CoreGraphAdapter,
    create_error_command,
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

FC_TOOL_APPROVAL_UNSUPPORTED = (
    "Function-calling mode does not support tool-approval policies yet. An enabled tool-approval "
    "policy exists, so this run was stopped before any tool ran. Use cuga_lite_execution_mode = "
    '"codeact" for this agent, or disable the policy.'
)

FC_STEP_LIMIT_CALL_REPLY = "Not executed: the step limit was reached before this call could run."
FC_BUDGET_CALL_REPLY = (
    "Not executed: the tool budget for this turn is spent. Answer from the data already retrieved."
)
FC_UNANSWERED_CALL_REPLY = "No result was recorded for this call."


def _message_role(m: Any) -> str:
    if isinstance(m, dict):
        return str(m.get("role") or m.get("type") or "")
    return str(getattr(m, "type", "") or "")


def _message_text(m: Any) -> str:
    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
    if content is None:
        return ""
    return content if isinstance(content, str) else str(content)


def _message_name(m: Any) -> Optional[str]:
    return m.get("name") if isinstance(m, dict) else getattr(m, "name", None)


def _tool_result_as_text(name: Optional[str], content: str) -> str:
    return f"Tool result ({name or 'tool'}):\n{content}"


def _unanswered_call_replies(calls: List[Any], invalid: List[Any], reason: str) -> List[ToolMessage]:
    """One error ``ToolMessage`` per id the model issued when nothing will execute them.

    A persisted assistant turn whose ``tool_calls`` have no replies makes strict
    providers reject the next replay of the thread, so every id is answered even
    when the run ends here (step limit, spent budget).
    """
    out: List[ToolMessage] = []
    for index, call in enumerate(list(calls or []) + list(invalid or [])):
        call_id = (str(call.get("id") or "") if isinstance(call, dict) else "") or f"call_{index}"
        name = (call.get("name") if isinstance(call, dict) else None) or "unknown"
        out.append(ToolMessage(content=reason, tool_call_id=call_id, name=str(name), status="error"))
    return out


def _normalize_history_for_replay(messages: List[Any]) -> List[BaseMessage]:
    """Persisted history as a provider-valid function-calling transcript.

    Two things go wrong with history as persisted. State crosses the SDK and
    server boundary through ``state.model_dump()`` against ``List[BaseMessage]``,
    so pydantic serialises by the declared type: subclass fields are dropped and
    the messages come back as bare ``BaseMessage`` shells — an assistant turn
    without its ``tool_calls``, a tool turn without its ``tool_call_id``. And a
    turn can end on a call nobody answered. Either makes a strict provider
    reject the replay (or raise on the unknown message type), so rebuild it:

    - bare shells become typed messages; a lost tool result is rendered as user
      text, and an empty assistant shell (a lost tool-call turn) is dropped;
    - every ``tool_calls`` id is followed by its ``ToolMessage`` — a synthetic
      error reply when none was recorded;
    - a ``ToolMessage`` that answers no open call is rendered as user text;
    - assistant reasoning payloads are dropped and harmony framing stripped
      (gpt-oss emits both; strict OpenAI-compatible proxies 400 on them).
    """
    out: List[BaseMessage] = []
    pending: Dict[str, str] = {}  # call id -> tool name, from the last assistant turn

    def close_pending() -> None:
        for call_id, name in pending.items():
            out.append(
                ToolMessage(
                    content=FC_UNANSWERED_CALL_REPLY,
                    tool_call_id=call_id,
                    name=name or "unknown",
                    status="error",
                )
            )
        pending.clear()

    for m in messages or []:
        if isinstance(m, ToolMessage):
            if m.tool_call_id in pending:
                pending.pop(m.tool_call_id, None)
                out.append(m)
            else:
                close_pending()
                out.append(HumanMessage(content=_tool_result_as_text(m.name, _message_text(m))))
            continue
        close_pending()
        if isinstance(m, AIMessage):
            ak = m.additional_kwargs or {}
            update: Dict[str, Any] = {}
            if any(k in ak for k in _REASONING_KEYS):
                update["additional_kwargs"] = {k: v for k, v in ak.items() if k not in _REASONING_KEYS}
            if isinstance(m.content, str) and "<|" in m.content:
                update["content"] = strip_harmony_tokens(m.content)
            msg = m.model_copy(update=update) if update else m
            if msg.tool_calls:
                for i, c in enumerate(msg.tool_calls):
                    pending[str(c.get("id") or f"call_{i}")] = str(c.get("name") or "")
                out.append(msg)
            elif _message_text(msg).strip():
                out.append(msg)
            continue  # an empty assistant shell is dropped
        if isinstance(m, (HumanMessage, SystemMessage)):
            out.append(m)
            continue
        role = _message_role(m)
        text = _message_text(m)
        if role in ("human", "user"):
            out.append(HumanMessage(content=text))
        elif role in ("ai", "assistant"):
            if "<|" in text:
                text = strip_harmony_tokens(text)
            if text.strip():
                out.append(AIMessage(content=text))
        elif role == "tool":
            out.append(HumanMessage(content=_tool_result_as_text(_message_name(m), text)))
        elif role == "system":
            out.append(SystemMessage(content=text))
        elif text.strip():
            out.append(HumanMessage(content=text))
    close_pending()
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
                # Advertise exactly the executable set prepare built (filtered per
                # sub-task / relevant apps), not the registry-wide catalogue: a tool
                # the model can see but the sandbox could not call is an
                # "Unknown tool" reply waiting to happen.
                names = list((self._tools_context_ref or {}).get("_lc_bind_tools_executable_names") or [])
                if names:
                    configurable = {
                        **(configurable or {}),
                        "cuga_lite_bind_tools_mode": "tools",
                        "cuga_lite_bind_tools_tool_names": names,
                    }
                    logger.info(
                        "[fc] bind_tools mode was 'none'; advertising the {} executable tool(s)", len(names)
                    )
                else:
                    configurable = {**(configurable or {}), "cuga_lite_bind_tools_mode": "all"}
                    logger.info(
                        "[fc] bind_tools mode was 'none'; upgraded to 'all' for function-calling mode"
                    )
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

    async def _tool_approval_policies_exist(self, config: Any) -> bool:
        """True when an enabled tool-approval policy is configured.

        Function-calling has no approval interrupt yet — CodeAct's runs on the
        generated code string, after the seam — so the mode refuses to start
        rather than run a guarded tool unprompted. On a storage error this
        warns and proceeds, exactly as CodeAct's own approval check does.
        """
        from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
        from cuga.backend.cuga_graph.policy.models import PolicyType

        try:
            policy_system = PolicyConfigurable.from_config(config or {})
            policies = await policy_system.agent.storage.list_policies(
                policy_type=PolicyType.TOOL_APPROVAL, enabled_only=True, limit=1
            )
            return bool(policies)
        except Exception as exc:
            logger.warning("[fc] could not query tool-approval policies ({}); proceeding", exc)
            return False

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
        variables_addendum: str = "",
    ) -> Optional[Command]:
        """Function-calling turn: invoke with real message objects, route on ``tool_calls``.

        Returns ``None`` in codeact mode so the shared CodeAct path runs untouched.
        Otherwise builds the outbound list from ``system_content`` (the FC prompt
        that ``prepare`` selected), the few-shot demos as chat messages, and the
        history normalised into a provider-valid transcript — hands them straight
        to ``bound.ainvoke`` so the shared dict serializer is never involved — then:

        - ``tool_calls`` present  -> ``Command(goto="tool_exec")``, assistant turn kept verbatim
        - otherwise               -> final answer, ``END``
        - an empty reply gets one corrective turn, like the CodeAct path
        - a fenced code block with no ``tool_calls`` is a mode violation: it is
          never executed; the model gets one corrective turn instead.

        Refuses to start when an enabled tool-approval policy exists: there is
        no approval interrupt on this path yet, and silently running a guarded
        tool is worse than not running at all.
        """
        if self._execution_mode(configurable) != EXECUTION_MODE_FUNCTION_CALLING:
            return None

        from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
            TOOL_BUDGET_EXHAUSTED_INSTRUCTION,
        )

        cfg = configurable or {}
        history: list = list(modified_messages)

        if settings.policy.enabled and await self._tool_approval_policies_exist(config):
            logger.error(
                "[fc] refusing to start: an enabled tool-approval policy exists and "
                "function-calling mode has no approval interrupt"
            )
            return create_error_command(
                self, history, AIMessage(content=FC_TOOL_APPROVAL_UNSUPPORTED), state.step_count
            )

        msgs: List[BaseMessage] = [SystemMessage(content=system_content)]
        msgs.extend(_few_shot_to_messages(self.get_few_shot_messages(state)))
        msgs.extend(_normalize_history_for_replay(history))
        if variables_addendum:
            # Outbound only, like call_model's CodeAct path (#600): never persisted.
            for i in range(len(msgs) - 1, -1, -1):
                if isinstance(msgs[i], HumanMessage):
                    msgs[i] = msgs[i].model_copy(
                        update={"content": _message_text(msgs[i]) + variables_addendum}
                    )
                    break
        if budget_exhausted:
            # Outbound only as well.
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

        max_steps = self.resolve_max_steps(state, cfg.get("cuga_lite_max_steps"))
        new_step_count: int = state.step_count + 1
        final_messages: list = history + [response]

        if budget_exhausted and (tool_calls or invalid_tool_calls):
            # No tools were bound for the grace turn, so the calls are noise — but
            # every id still gets a reply, or the persisted thread cannot be replayed.
            final_messages += _unanswered_call_replies(tool_calls, invalid_tool_calls, FC_BUDGET_CALL_REPLY)
            tool_calls, invalid_tool_calls = [], []

        has_calls = bool(tool_calls or invalid_tool_calls)
        if has_calls:
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

        # Step limit. On a breach with calls pending they are answered first, so
        # the persisted transcript never ends on a dangling tool_calls turn.
        limit_cmd = (
            None
            if budget_exhausted
            else enforce_step_limit(
                self,
                state=state,
                messages=final_messages
                + (
                    _unanswered_call_replies(tool_calls, invalid_tool_calls, FC_STEP_LIMIT_CALL_REPLY)
                    if has_calls
                    else []
                ),
                new_step_count=new_step_count,
                limit=max_steps,
            )
        )
        if limit_cmd is not None:
            return limit_cmd

        base_meta = dict(self.build_metadata_update(state, playbook_fired=playbook_fired) or {})

        if has_calls:
            logger.info("[fc] {} native tool_call(s) -> tool_exec", len(tool_calls) + len(invalid_tool_calls))
            return Command(
                goto="tool_exec",
                update={
                    self.messages_key: final_messages,
                    "script": None,
                    "step_count": new_step_count,
                    self.metadata_key: base_meta,
                },
            )

        # Empty reply: one retry, same contract as the CodeAct path (#756).
        both_blank = not content.strip() and not (reasoning or "").strip()
        already_retried = bool(self.get_metadata(state).get(EMPTY_RESPONSE_CORRECTION_KEY))
        if both_blank and not already_retried and not budget_exhausted and new_step_count < max_steps:
            logger.warning(
                "[fc] model returned an empty reply (no content, no reasoning, no tool_calls) — retrying once"
            )
            retry_meta = {**base_meta, EMPTY_RESPONSE_CORRECTION_KEY: True}
            return Command(
                goto="call_model",
                update={
                    self.messages_key: final_messages + [HumanMessage(content=EMPTY_RESPONSE_CORRECTION)],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    self.metadata_key: retry_meta,
                },
            )

        if "```" in content and not budget_exhausted:
            # Mode violation: never execute code here. One corrective turn, charged
            # as a step so it cannot loop past cuga_lite_max_steps.
            violations = int(base_meta.get("fc_mode_violations", 0) or 0) + 1
            logger.warning("[fc] mode violation #{}: code block emitted in function-calling mode", violations)
            return Command(
                goto="call_model",
                update={
                    self.messages_key: final_messages + [HumanMessage(content=FC_MODE_VIOLATION_CORRECTION)],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    self.metadata_key: {**base_meta, "fc_mode_violations": violations},
                },
            )

        final_answer = content
        if not final_answer.strip() and reasoning and not contains_harmony_tokens(reasoning):
            final_answer = reasoning.strip()
        if not final_answer.strip():
            for m in reversed(history):
                if _message_role(m) == "tool" and _message_text(m).strip():
                    final_answer = _message_text(m)
                    break
        if not content.strip() and final_answer:
            final_messages = history + [AIMessage(content=final_answer)]

        logger.info("[fc] no tool_calls -> final answer (END)")
        return Command(
            goto=END,
            update={
                self.messages_key: final_messages,
                "script": None,
                "final_answer": final_answer,
                "execution_complete": True,
                "step_count": new_step_count,
                self.metadata_key: base_meta,
            },
        )

    def build_tool_exec_node(self):
        return create_tool_exec_node(self)

    def build_prepare_node(self, lc_bind_tools_meta: dict):
        return create_prepare_tools_and_apps_node(self, lc_bind_tools_meta)

    def build_sandbox_node(self, base_thread_id: Any, base_apps_list: Any):
        return create_sandbox_node(self, base_thread_id, base_apps_list)
