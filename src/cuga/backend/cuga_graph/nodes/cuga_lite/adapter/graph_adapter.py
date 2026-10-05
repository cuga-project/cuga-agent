"""AgentGraphAdapter — CoreGraphAdapter implementation for CugaLite (single-agent).

Defines graph seams and call_model hook overrides. Prompt, tool, and execution
logic live in ``prepare_node.py`` and ``sandbox_node.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

from cuga.backend.activity_tracker.tracker import Step
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
    extract_code_from_model_response,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    TODOS_TOOL_NAME,
    format_current_plan_section,
    format_task_todos_system_block,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.stop_checks import (
    STOP_CHECKS_FIRED_KEY,
    StopContext,
    configured_stop_checks,
    fired_checks,
    run_stop_checks,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.model_call_recovery import (
    max_truncation_retries,
    pending_truncation_note,
    truncation_retries_used,
    truncation_retry_command,
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
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.wire_names import wire_name
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import EXECUTE_PYTHON_NAME
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import (
    for_model,
    harness_kind,
    harness_message,
    is_harness_message,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.prompt_cache import (
    add_cache_markers,
    cache_markers_enabled,
    stable_system_prompt,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.repeat_calls import (
    REPEAT_NUDGE_KEY,
    detect_repeat_streak,
    repeat_nudge_text,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_result_clearing import clear_old_tool_results
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import action_verifier  # noqa: F401 — registers verify_actions
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_effects import (
    NOOP_ANSWER_CORRECTION,
    NOOP_ANSWER_CORRECTION_KEY,
    has_write_tools,
    is_noop_answer,
    turn_made_a_write,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import (
    SEARCH_ONLY_CORRECTION,
    SEARCH_ONLY_CORRECTION_KEY,
    bind_deferred,
    bind_names,
    search_only_turn,
    surface_from_meta,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import (
    _bind_tools_mode_from_settings,
    resolve_model_with_bind_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    EXECUTION_MODE_HYBRID,
    is_native_mode,
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
from cuga.backend.llm.errors import extract_code_from_tool_use_failed, is_output_truncated
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

FC_TOOL_APPROVAL_UNVERIFIED = (
    "Function-calling mode could not verify whether a tool-approval policy exists ({error}). "
    "It fails closed: no tool ran. Fix policy storage, or use cuga_lite_execution_mode = \"codeact\"."
)

FC_BIND_FAILED = (
    "Function-calling mode could not advertise any tool natively (bind_tools failed or is unsupported "
    "by this model, or there is no executable tool to bind), so no native tool call is possible. "
    'It fails closed: no model call was made. Use cuga_lite_execution_mode = "codeact" for this model.'
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


_WIRE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HARMONY_LEAK = re.compile(r"<\|.*$", re.DOTALL)


def _clean_tool_name(name: Any) -> str:
    """A tool-call name every provider accepts on replay.

    Models (gpt-oss in particular) sometimes emit names with harmony tokens or a
    ``functions.`` prefix attached (``get_x<|channel|>commentary``). Stored as-is,
    the name poisons the thread: Bedrock rejects every later request whose history
    contains it (``toolUse.name`` must match ``[a-zA-Z0-9_-]+``). The leak is stripped;
    anything still invalid becomes ``_``, so tool_exec answers it as an unknown tool.
    """
    text = str(name or "")
    if _WIRE_TOOL_NAME.match(text):
        return text
    text = _HARMONY_LEAK.sub("", text).strip()
    if text.startswith("functions."):
        text = text[len("functions.") :]
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:64]
    return text or "unknown_tool"


def _replayable_tool_calls(response: AIMessage) -> AIMessage:
    """``response`` with tool-call names and invalid-call arguments a provider accepts on replay."""
    calls = list(response.tool_calls or [])
    invalid = list(response.invalid_tool_calls or [])
    clean_calls = [{**c, "name": _clean_tool_name(c.get("name"))} for c in calls]
    clean_invalid = []
    for c in invalid:
        args = c.get("args")
        try:
            ok = isinstance(json.loads(args), dict) if isinstance(args, str) else False
        except (TypeError, ValueError):
            ok = False
        # Bedrock needs a JSON object as toolUse.input; tool_exec still answers the call as malformed.
        clean_invalid.append({**c, "name": _clean_tool_name(c.get("name")), "args": args if ok else "{}"})
    if clean_calls == calls and clean_invalid == invalid:
        return response
    renamed = [
        (a.get("name"), b["name"])
        for a, b in zip(calls + invalid, clean_calls + clean_invalid)
        if a.get("name") != b["name"]
    ]
    logger.warning("[fc] cleaned tool-call names/args before persisting: {}", renamed or "invalid-call args")
    return response.model_copy(update={"tool_calls": clean_calls, "invalid_tool_calls": clean_invalid})


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
        if isinstance(m, HumanMessage) and is_harness_message(m):
            out.append(for_model(m))
            continue
        if isinstance(m, (HumanMessage, SystemMessage)):
            out.append(m)
            continue
        role = _message_role(m)
        text = _message_text(m)
        if role in ("human", "user"):
            kind = harness_kind(m)
            out.append(for_model(harness_message(text, kind)) if kind else HumanMessage(content=text))
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


_PYTHON_FENCE = re.compile(r"```(?:python|py)\b", re.IGNORECASE)
_UNTAGGED_FENCE = re.compile(r"```[ \t]*\n(.*?)```", re.DOTALL)


def _looks_like_python_block(content: str, tool_names: Any = ()) -> bool:
    """A fenced block the CodeAct sandbox would have executed.

    A ``python``/``py`` fence, or an untagged fence whose body ``await``s or
    calls one of the bound tools by name. Fences carrying JSON, text, shell
    output or plain notation (``f(x) = 2``) in a final answer are not violations.
    """
    if _PYTHON_FENCE.search(content):
        return True
    names = {n for n in (tool_names or ()) if n and not str(n).startswith("_")}
    for body in _UNTAGGED_FENCE.findall(content):
        if re.search(r"\bawait\b", body):
            return True
        if names and any(re.search(rf"\b{re.escape(n)}\s*\(", body) for n in names):
            return True
    return False


def _turn_start(history: List[Any]) -> int:
    """Index of the user message that opened the current turn (runtime notes excluded).

    Runtime notes are tagged (``harness_messages``); the text checks cover threads
    persisted before the tag existed.
    """
    synthetic = {EMPTY_RESPONSE_CORRECTION, FC_MODE_VIOLATION_CORRECTION}
    for i in range(len(history) - 1, -1, -1):
        m = history[i]
        if _message_role(m) in ("human", "user"):
            text = _message_text(m)
            if (
                is_harness_message(m)
                or text in synthetic
                or text.startswith(SEARCH_ONLY_CORRECTION[:40])
                or text.startswith(NOOP_ANSWER_CORRECTION[:20])
            ):
                continue
            return i
    return 0


def _fc_setting(configurable: dict, key: str, default: Any) -> Any:
    """``configurable`` first (per-invoke override), then ``advanced_features``."""
    if key in (configurable or {}):
        return configurable[key]
    return getattr(settings.advanced_features, key, default)


def _variable_exists_fn(state: Any):
    """Whether a saved variable is still in the store (stubs only point at live ones)."""
    manager = getattr(state, "variables_manager", None)
    try:
        names = set(manager.get_variable_names()) if manager is not None else None
    except Exception:
        names = None
    if names is None:
        return lambda _name: False
    return lambda name: name in names


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


TODOS_STALE_REMINDER = (
    "\nCurrent Plan was last updated {steps} steps ago and still has {unfinished} unfinished step(s). "
    "If you have made progress, update the statuses with create_update_todos; if the plan no longer "
    "fits the task, replace it. Do not mention this note to the user.\n"
)


def _steps_since_todos_update(history: List[Any]) -> int:
    """Assistant turns since the last one that updated the todos (native call or CodeAct block).

    0 when the latest assistant turn updated them; the whole count when none did.
    """
    steps = 0
    for message in reversed(history):
        if _message_role(message) not in ("ai", "assistant"):
            continue
        calls = getattr(message, "tool_calls", None) or []
        if any(isinstance(c, dict) and c.get("name") == TODOS_TOOL_NAME for c in calls):
            return steps
        if f"{TODOS_TOOL_NAME}(" in _message_text(message):
            return steps
        steps += 1
    return steps


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
        if stable_system_prompt(configurable):
            # Run state goes to the tail instead (prepare_turn_context).
            return base_prompt
        return base_prompt + self._run_state_blocks(state)

    def prepare_turn_context(self, state: Any, configurable: dict) -> str:
        if not stable_system_prompt(configurable):
            return ""
        context = self._run_state_blocks(state)
        every = int(_fc_setting(configurable, "cuga_lite_todo_reminder_after", 6) or 0)
        todos = self._task_todos_ref or getattr(state, "task_todos", None) or []
        if every > 0 and todos:
            unfinished = sum(
                1 for t in todos if str(t.get("status", "pending")).strip().lower() != "completed"
            )
            stale = _steps_since_todos_update(list(getattr(state, "chat_messages", None) or []))
            if unfinished and stale and stale % every == 0:
                context += TODOS_STALE_REMINDER.format(steps=stale, unfinished=unfinished)
        return context

    async def check_before_finish(
        self, state: Any, configurable: dict, answer: str
    ) -> Optional[Tuple[str, str]]:
        specs = configured_stop_checks(configurable, settings)
        if not specs:
            return None
        ctx = StopContext(
            answer=answer,
            messages=self.get_messages(state),
            todos=list(self._task_todos_ref or getattr(state, "task_todos", None) or []),
            configurable=dict(configurable or {}),
            execution_mode=self._execution_mode(configurable),
            tools_by_name=dict(
                (self._tools_context_ref or {}).get("_lc_bind_tools_structured_by_name") or {}
            ),
            tools_context=dict(self._tools_context or {}),
            model=(configurable or {}).get("llm") or self._model,
            invoke_config=self.get_invoke_config(configurable or {}),
            tracker=self._tracker,
        )
        return await run_stop_checks(ctx, specs, fired_checks(self.get_metadata(state)))

    def _run_state_blocks(self, state: Any) -> str:
        """Todos and observed tool shapes: what changes while a task runs."""
        if self._task_todos_ref:
            content = format_task_todos_system_block(self._task_todos_ref)
        else:
            task_todos = getattr(state, "task_todos", None)
            content = format_current_plan_section(task_todos) if task_todos else ""

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
        configurable = (invoke_config or {}).get("configurable") or {}
        if cache_markers_enabled(configurable, self._runtime_model_name(configurable)):
            messages = add_cache_markers(messages)
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
        if is_native_mode(self._execution_mode(configurable)):
            surface = surface_from_meta(self._tools_context_ref)
            if surface is not None:
                # Deferred loading: core ∪ pinned ∪ loaded, minified, no model call.
                names, evicted = bind_names(
                    surface, self.get_messages(state), getattr(state, "discovered_tools", None)
                )
                logger.info(
                    "[fc] tool_search bind: {} tool(s) ({} core, {} pinned, {} loaded){}",
                    len(names),
                    len(surface.core),
                    len(surface.pinned),
                    len(names) - len(set(surface.core) | set(surface.pinned)),
                    f"; evicted {evicted}" if evicted else "",
                )
                return bind_deferred(active_model, surface, names)
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
                    # Nothing executable was recorded: advertising the full catalogue
                    # would offer tools tool_exec cannot run. Bind nothing; the turn
                    # then fails closed in execute_call_model_fc.
                    logger.error("[fc] no executable tools recorded by prepare; binding nothing")
                    return None
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
        """True when an enabled tool-approval policy is stored.

        Function-calling has no approval interrupt yet — CodeAct's runs on the
        generated code string, after the seam — so the mode refuses to start
        rather than run a guarded tool unprompted. Raises when the answer
        cannot be established (policy system not initialised, storage backend
        down): ``strict=True`` keeps the storage layer from turning a backend
        failure into an empty list, and the caller fails closed on any error.
        """
        from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
        from cuga.backend.cuga_graph.policy.models import PolicyType

        policy_system = PolicyConfigurable.from_config(config or {})
        policies = await policy_system.agent.storage.list_policies(
            policy_type=PolicyType.TOOL_APPROVAL, enabled_only=True, limit=1, strict=True
        )
        return bool(policies)

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
        turn_context: str = "",
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

        Refuses to start when an enabled tool-approval policy exists — or when
        that cannot be verified: there is no approval interrupt on this path
        yet, and silently running a guarded tool is worse than not running.
        """
        if not is_native_mode(self._execution_mode(configurable)):
            return None

        from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import (
            TOOL_BUDGET_EXHAUSTED_INSTRUCTION,
        )

        cfg = configurable or {}
        history: list = list(modified_messages)

        if settings.policy.enabled:
            # Fail closed, stricter than CodeAct's own check: a refusal here is
            # cheap and explicit, a guarded tool running unprompted is not.
            try:
                blocked_reason = (
                    FC_TOOL_APPROVAL_UNSUPPORTED if await self._tool_approval_policies_exist(config) else None
                )
            except Exception as exc:
                logger.error("[fc] could not verify tool-approval policies ({}); refusing to start", exc)
                blocked_reason = FC_TOOL_APPROVAL_UNVERIFIED.format(error=exc)
            if blocked_reason:
                logger.error("[fc] refusing to start: {}", blocked_reason)
                return create_error_command(
                    self, history, AIMessage(content=blocked_reason), state.step_count
                )

        if not budget_exhausted and bound is active_model:
            # bind_tools failed, is unsupported by the model, or had nothing to bind —
            # call_model fell back to the unbound model. That run cannot make a native
            # tool call, so stop with a clear error instead of degrading to text.
            logger.error("[fc] refusing to start: no tools are bound to the model")
            return create_error_command(self, history, AIMessage(content=FC_BIND_FAILED), state.step_count)

        hybrid = self._execution_mode(configurable) == EXECUTION_MODE_HYBRID

        # Repeated native calls this turn (the same tool, one argument changing, or no
        # change at all): one runtime note per streak. Persisted ahead of the reply so the
        # transcript shows what the model was told.
        repeat_streak = None
        nudge_after = int(_fc_setting(cfg, "cuga_lite_repeat_call_nudge_after", 3) or 0)
        if nudge_after >= 2 and not budget_exhausted:
            streak = detect_repeat_streak(history[_turn_start(history) :])
            already = (self.get_metadata(state) or {}).get(REPEAT_NUDGE_KEY)
            if streak is not None and streak.length >= nudge_after and already != streak.first_call_id:
                logger.warning(
                    "[fc] {} called {} times in a row ({}) — runtime note",
                    streak.tool_name,
                    streak.length,
                    f"varying {streak.varying_arg}" if streak.varying_arg else "identical arguments",
                )
                repeat_streak = streak
                history = history + [
                    harness_message(repeat_nudge_text(streak, hybrid=hybrid), "repeat_calls")
                ]

        replay = _normalize_history_for_replay(history)
        keep_recent = int(_fc_setting(cfg, "cuga_lite_tool_result_keep_recent", 3) or 0)
        if keep_recent > 0:
            replay = clear_old_tool_results(
                replay,
                keep_recent=keep_recent,
                min_chars=int(_fc_setting(cfg, "cuga_lite_tool_result_clear_min_chars", 2000) or 0),
                var_exists=_variable_exists_fn(state),
            )

        msgs: List[BaseMessage] = [SystemMessage(content=system_content)]
        msgs.extend(_few_shot_to_messages(self.get_few_shot_messages(state)))
        msgs.extend(replay)
        if cache_markers_enabled(cfg, self._runtime_model_name(cfg)):
            msgs = add_cache_markers(msgs)
        if stable_system_prompt(cfg):
            # Outbound only (#600), and last: the run state changes after nearly every
            # call, so anywhere earlier it would invalidate the cached prefix behind it.
            tail = (turn_context + variables_addendum).strip()
            if tail:
                msgs.append(for_model(harness_message(tail, "run_state")))
        elif variables_addendum:
            # Outbound only, like call_model's CodeAct path (#600): never persisted.
            for i in range(len(msgs) - 1, -1, -1):
                if isinstance(msgs[i], HumanMessage):
                    msgs[i] = msgs[i].model_copy(
                        update={"content": _message_text(msgs[i]) + variables_addendum}
                    )
                    break
        if budget_exhausted:
            # Outbound only as well.
            msgs.append(for_model(harness_message(TOOL_BUDGET_EXHAUSTED_INSTRUCTION, "budget_exhausted")))
        else:
            # The previous reply was cut off at the output-token limit and discarded.
            truncation_note = pending_truncation_note(self.get_metadata(state))
            if truncation_note:
                msgs.append(for_model(harness_message(truncation_note, "output_truncated")))

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
        response = _replayable_tool_calls(response)
        tool_calls = list(response.tool_calls or [])
        invalid_tool_calls = list(response.invalid_tool_calls or [])

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
        if repeat_streak is not None:
            base_meta[REPEAT_NUDGE_KEY] = repeat_streak.first_call_id

        # Cut off at the output-token limit: tool calls whose arguments all parsed are
        # complete and run as usual; a call whose JSON never closed (it lands in
        # invalid_tool_calls) or a half-written answer is discarded and retried.
        if is_output_truncated(response) and not budget_exhausted:
            if tool_calls and not invalid_tool_calls:
                logger.warning("[fc] reply hit the output-token limit after complete tool call(s)")
            elif (
                truncation_retries_used(self.get_metadata(state)) < max_truncation_retries(settings)
                and new_step_count < max_steps
            ):
                logger.warning("[fc] reply hit the output-token limit — discarded, retrying with less output")
                return truncation_retry_command(
                    self, state, messages=history, base_meta=base_meta, new_step_count=new_step_count
                )
            else:
                logger.error("[fc] reply hit the output-token limit and no retries are left")

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
                    self.messages_key: final_messages
                    + [harness_message(EMPTY_RESPONSE_CORRECTION, "empty_reply")],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    self.metadata_key: retry_meta,
                },
            )

        # Hybrid: a fenced Python block is not a violation — it is CodeAct written as
        # text (weaker models do this). Run it as an execute_python call instead.
        if hybrid and not budget_exhausted and _looks_like_python_block(content, self._tools_context):
            code = extract_code_from_model_response(content, None)
            if code:
                logger.info("[hybrid] fenced code in reply -> execute_python")
                call = {
                    "name": EXECUTE_PYTHON_NAME,
                    "args": {"code": code},
                    "id": f"fence_{new_step_count}",
                    "type": "tool_call",
                }
                return Command(
                    goto="tool_exec",
                    update={
                        self.messages_key: history + [AIMessage(content="", tool_calls=[call])],
                        "script": None,
                        "step_count": new_step_count,
                        self.metadata_key: base_meta,
                    },
                )

        prior_violations = int(base_meta.get("fc_mode_violations", 0) or 0)
        if (
            _looks_like_python_block(content, self._tools_context)
            and not budget_exhausted
            and prior_violations == 0
        ):
            # Mode violation: never execute code here. Exactly one corrective turn
            # (charged as a step); a second fence is delivered as the answer, so a
            # fence-happy model cannot loop to cuga_lite_max_steps. Only Python
            # blocks count — a ```json or ```text snippet in an answer is fine.
            violations = prior_violations + 1
            logger.warning(
                "[fc] mode violation: python block emitted in function-calling mode — one corrective turn"
            )
            return Command(
                goto="call_model",
                update={
                    self.messages_key: final_messages
                    + [harness_message(FC_MODE_VIOLATION_CORRECTION, "mode_violation")],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    self.metadata_key: {**base_meta, "fc_mode_violations": violations},
                },
            )

        # Searched but never called a tool (M3 hockey #9: the right tool was loaded, the
        # model answered "no access"). One corrective turn per user turn, tool search only.
        if (
            surface_from_meta(self._tools_context_ref) is not None
            and not budget_exhausted
            and new_step_count < max_steps
            and not base_meta.get(SEARCH_ONLY_CORRECTION_KEY)
        ):
            loaded = search_only_turn(history, _turn_start(history))
            if loaded:
                logger.warning("[fc] answer after tool_search with no tool call — one corrective turn")
                correction = SEARCH_ONLY_CORRECTION.format(
                    loaded=", ".join(wire_name(x) for x in loaded[:10])
                )
                return Command(
                    goto="call_model",
                    update={
                        self.messages_key: final_messages + [harness_message(correction, "search_only")],
                        "script": None,
                        "final_answer": "",
                        "execution_complete": False,
                        "step_count": new_step_count,
                        self.metadata_key: {**base_meta, SEARCH_ONLY_CORRECTION_KEY: True},
                    },
                )

        # Did-nothing check: "N/A" (or no answer) after a turn that changed nothing, while
        # state-changing tools exist — on action requests the model sometimes stops before
        # acting (AppWorld: "order the benches" -> "N/A", no order call). One corrective
        # turn per user turn; plain questions with a real answer never trigger it.
        tools_by_name = (self._tools_context_ref or {}).get("_lc_bind_tools_structured_by_name") or {}
        if (
            not budget_exhausted
            and new_step_count < max_steps
            and not base_meta.get(NOOP_ANSWER_CORRECTION_KEY)
            and is_noop_answer(content)
            and has_write_tools(tools_by_name)
            and not turn_made_a_write(history, _turn_start(history), tools_by_name)
        ):
            logger.warning("[fc] no-op answer after a turn with no state change — one corrective turn")
            correction = NOOP_ANSWER_CORRECTION.format(answer=(content or "").strip() or "no answer")
            return Command(
                goto="call_model",
                update={
                    self.messages_key: final_messages + [harness_message(correction, "noop_answer")],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    self.metadata_key: {**base_meta, NOOP_ANSWER_CORRECTION_KEY: True},
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

        # Stop checks (stop_checks.py): each may send the model back once per turn.
        if not budget_exhausted and new_step_count < max_steps:
            objection = await self.check_before_finish(state, cfg, final_answer)
            if objection:
                check_name, correction = objection
                return Command(
                    goto="call_model",
                    update={
                        self.messages_key: final_messages + [harness_message(correction, "stop_check")],
                        "script": None,
                        "final_answer": "",
                        "execution_complete": False,
                        "step_count": new_step_count,
                        self.metadata_key: {
                            **base_meta,
                            STOP_CHECKS_FIRED_KEY: fired_checks(self.get_metadata(state)) + [check_name],
                        },
                    },
                )

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
