"""Shared agent-loop helpers and CoreGraphAdapter ABC.

``append_chat_messages_with_step_limit`` and ``create_error_command`` were
duplicated verbatim (modulo a couple of graph-specific bits) in
``cuga_lite_graph`` and ``cuga_supervisor_graph``. The graph-specific bits
are isolated behind :class:`CoreGraphAdapter`; the loop logic, the error
text and the log lines are shared and behavior-identical to the originals.

``CoreGraphAdapter`` also carries four optional call_model hooks that the
shared ``create_call_model_node`` factory (``shared_nodes.py``) delegates to.
Subclasses override only the hooks they need; defaults are no-ops.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger


class CoreGraphAdapter(ABC):
    """Graph-specific seam for the shared loop helpers.

    Subsequent slices (4b) extend this with prepare/call/execute hooks; for
    now it only carries what the step-limit + error-command helpers need.
    """

    #: State key the error Command writes the message list back to.
    messages_key: str

    #: Approval seams — defaults are exactly the legacy Lite values so the
    #: shared ToolApprovalHandler stays byte-identical for Lite.
    metadata_key: str = "cuga_lite_metadata"
    execute_node_name: str = "sandbox"
    sender_name: str = "CugaLite"

    @abstractmethod
    def get_messages(self, state: Any) -> List[BaseMessage]:
        """Base message list to append onto (already None-safe)."""

    @abstractmethod
    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        """Resolve the step limit for this graph (override wins when given)."""

    def get_variable_manager(self, state: Any) -> Any:
        """Variable manager new execution vars are recorded into.

        Defaults to ``state.variables_manager`` (Lite). Supervisor must
        override to return ``state.supervisor_variables_manager`` — the
        seam phase 9 uses to fix the supervisor variable-coupling bug.
        """
        return getattr(state, "variables_manager", None)

    def get_metadata(self, state: Any) -> dict:
        """Approval/policy metadata dict (Lite: ``cuga_lite_metadata``;
        Supervisor overrides ``metadata_key`` to ``supervisor_metadata``)."""
        return getattr(state, self.metadata_key, None) or {}

    def set_metadata(self, state: Any, metadata: dict) -> None:
        setattr(state, self.metadata_key, metadata)

    # ── Optional call_model hooks ──────────────────────────────────────────
    # Defaults are no-ops so the Supervisor adapter (which has none of these
    # features) requires no changes.  AgentGraphAdapter (Lite) overrides all
    # hooks it needs to restore full Lite behaviour in the shared call_model.

    # ── Pre-invocation hooks ───────────────────────────────────────────────

    def get_few_shot_messages(self, state: Any) -> List[BaseMessage]:
        """Return graph-specific few-shot example messages prepended to the
        model call.  Default: empty list (Supervisor behaviour)."""
        return []

    def get_pi(self, state: Any) -> Optional[str]:
        """Return the Personal Instructions string to inject into the first
        user message, or ``None`` to skip injection.  Default: ``None``."""
        return None

    def prepare_system_content(self, state: Any, configurable: dict, base_prompt: str) -> str:
        """Augment the system prompt before the model call (e.g. add todos).
        Default: returns ``base_prompt`` unchanged (Supervisor behaviour)."""
        return base_prompt

    def get_variables_storage(self, state: Any) -> Optional[Any]:
        """Return the variables-storage dict for context summarisation.
        Default: ``state.variables_storage``; Supervisor uses a different key."""
        return getattr(state, "variables_storage", None)

    def get_tracker(self) -> Optional[Any]:
        """Return the ActivityTracker for context summarisation, or ``None``.
        Default: ``None`` (Supervisor has no tracker)."""
        return None

    def get_invoke_config(self, configurable: dict) -> dict:
        """Return the ``config`` dict passed to ``model.ainvoke``.
        Default: ``{}``; Lite overrides to include Langfuse callbacks."""
        return {}

    async def resolve_bind_tools(
        self,
        state: Any,
        active_model: Any,
        configurable: dict,
        config: Any = None,
    ) -> Any:
        """Return a model instance with tools bound, or ``None`` to use the
        plain model without binding.  Default: ``None`` (Supervisor never
        binds tools; only AgentGraphAdapter overrides this).

        *config* is the LangGraph node ``RunnableConfig`` (Lite passes it for
        nested shortlister / bind-tools LLM calls).
        """
        return None

    async def ainvoke_model(self, bound: Any, messages: list, invoke_config: dict) -> Any:
        """Invoke the bound model and return its response.

        Default: ``await bound.ainvoke(messages, config=invoke_config)``.
        Lite overrides to handle ``tool_use_failed`` errors (proxy → native
        tool-call conversion) by extracting code from the exception payload.
        """
        return await bound.ainvoke(messages, config=invoke_config)

    # ── Post-invocation hooks ──────────────────────────────────────────────

    def normalize_response(self, response: Any) -> Tuple[str, Optional[str]]:
        """Extract ``(content, reasoning)`` from the model response.

        Default passes through ``response.content`` and the
        ``reasoning_content`` additional kwarg unchanged.
        Lite overrides to run ``normalize_assistant_text`` and recover
        tool-call code from proxy responses.
        """
        content = response.content or ""
        reasoning = (getattr(response, "additional_kwargs", None) or {}).get("reasoning_content")
        return content, reasoning

    def on_response_processed(self, state: Any, code: Optional[str], content: str) -> None:
        """Side-effect hook called after code extraction.  Default: no-op.
        Lite uses this to record tracker steps."""

    def build_metadata_update(self, state: Any, *, playbook_fired: bool) -> dict:
        """Return the *value* (not the full key/value pair) for the metadata
        state key after a call_model response.

        Default adds ``playbook_guidance_added: True`` when a playbook fired;
        Lite overrides to also run ``_clean_empty_response_retry_meta``.
        """
        meta = self.get_metadata(state)
        if playbook_fired:
            return {**meta, "playbook_guidance_added": True}
        return meta

    async def classify_auto_continue(
        self, state: Any, model: Any, content: str, reasoning: Optional[str]
    ) -> bool:
        """Return ``True`` when the NL response should loop back automatically.
        Default: ``False`` (Supervisor never auto-continues).
        Lite overrides with ``classify_nl_auto_continue``."""
        return False


def append_chat_messages_with_step_limit(
    adapter: CoreGraphAdapter,
    state: Any,
    new_messages: List[BaseMessage],
    max_steps: Optional[int] = None,
) -> Tuple[List[BaseMessage], Optional[AIMessage]]:
    """Append messages, counting a step and enforcing the limit.

    Returns ``(updated_messages, error_message_or_None)``. On limit breach
    the error AIMessage is also appended to the returned list.
    """
    limit = adapter.resolve_max_steps(state, max_steps)
    new_step_count = state.step_count + 1
    base = adapter.get_messages(state)

    if new_step_count > limit:
        error_msg = (
            f"Maximum step limit ({limit}) reached. "
            f"The task has exceeded the allowed number of execution cycles. "
            f"Please simplify your request or break it into smaller tasks."
        )
        logger.warning(f"Step limit reached: {new_step_count} > {limit}")
        error_ai_message = AIMessage(content=error_msg)
        return base + new_messages + [error_ai_message], error_ai_message

    logger.debug(f"Step count: {new_step_count}/{limit}")
    return base + new_messages, None


def execution_output_text(output: str) -> str:
    """The execution-feedback message body shared by both execute nodes."""
    return f"Execution output:\n{output}"


def enforce_step_limit(
    adapter: CoreGraphAdapter,
    *,
    state: Any,
    messages: List[BaseMessage],
    new_step_count: int,
    limit: int,
) -> Optional[Command]:
    """In-``call_model`` step guard for the code / no-code branches.

    Unlike :func:`append_chat_messages_with_step_limit` (which rebuilds the
    base list from the adapter), the call_model branches have already built
    the post-response message list, so it is passed in explicitly. ``limit``
    is also passed explicitly so each callsite keeps its exact current
    resolution (Lite and Supervisor's call_model checks differ). Returns an
    END error Command (with the canned error appended) when the limit is
    exceeded, else ``None`` so the caller proceeds with its own routing.
    """
    if new_step_count > limit:
        error_msg = (
            f"Maximum step limit ({limit}) reached. "
            f"The task has exceeded the allowed number of execution cycles. "
            f"Please simplify your request or break it into smaller tasks."
        )
        logger.warning(f"Step limit reached: {new_step_count} > {limit}")
        error_ai_message = AIMessage(content=error_msg)
        return create_error_command(
            adapter, messages + [error_ai_message], error_ai_message, state.step_count
        )
    return None


def inject_playbook_guidance(
    messages: List[BaseMessage],
    metadata: Optional[dict],
) -> List[BaseMessage]:
    """Inject playbook guidance into the last HumanMessage (first call only).

    Returns the original list unchanged when conditions are not met.
    When injection fires, returns a new list so the caller's original is
    not mutated.
    """
    if not metadata:
        return messages
    if not metadata.get("policy_matched"):
        return messages
    if metadata.get("policy_type") != "playbook":
        return messages
    if metadata.get("playbook_guidance_added"):
        return messages
    guidance = metadata.get("playbook_guidance")
    if not guidance:
        return messages

    last_human_idx: Optional[int] = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, HumanMessage) or getattr(msg, "type", None) in ("human", "user"):
            last_human_idx = i
            break

    if last_human_idx is None:
        return messages

    result = list(messages)
    original = result[last_human_idx]
    result[last_human_idx] = original.model_copy(
        update={"content": f"{original.content}\n\n## Task Guidance\n{guidance}"}
    )
    return result


def create_error_command(
    adapter: CoreGraphAdapter,
    updated_messages: List[BaseMessage],
    error_message: AIMessage,
    step_count: int,
    additional_updates: Optional[Dict[str, Any]] = None,
) -> Command:
    """Create a Command routing to END with error state."""
    updates: Dict[str, Any] = {
        adapter.messages_key: updated_messages,
        "script": None,
        "final_answer": error_message.content,
        "execution_complete": True,
        "error": error_message.content,
        "step_count": step_count + 1,
    }
    if additional_updates:
        updates.update(additional_updates)

    return Command(goto=END, update=updates)
