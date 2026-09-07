"""Shared call_model node factory.

``create_call_model_node`` produces the async ``call_model`` node used by
both CugaLite and CugaSupervisor graphs.  Graph-specific behaviour is
delegated through :class:`CoreGraphAdapter` hooks so the factory itself
contains only logic that is identical across both graphs.

Differences handled via hooks:
- Few-shot messages: ``adapter.get_few_shot_messages``
- Personal Instructions: ``adapter.get_pi``
- System content augmentation (todos): ``adapter.prepare_system_content``
- Variable storage key: ``adapter.get_variables_storage``
- Activity tracker: ``adapter.get_tracker``
- Langfuse: pass full LangGraph ``config`` into ``ainvoke`` (preserves parent run ids)
- Bind-tools model: ``adapter.resolve_bind_tools``
- Response normalisation: ``adapter.normalize_response``
- Tracker side-effects: ``adapter.on_response_processed``
- Metadata update: ``adapter.build_metadata_update``
- NL auto-continue: ``adapter.classify_auto_continue``
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
    extract_code_from_model_response,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    CoreGraphAdapter,
    enforce_step_limit,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.tool_approval_handler import ToolApprovalHandler
from cuga.backend.cuga_graph.utils.context_management_utils import apply_context_summarization

from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification.prompt_verifier import (
    commit_reasoning_trace_to_state,
    reset_reasoning_trace,
    verify_candidate,
)

AUTO_CONTINUE_MSG = (
    "Your previous assistant response was classified as non-terminal/interim. "
    "Do not return another progress or status message.\n\n"
    "Continue by taking the next valid step under your current output contract. "
    "If an internal action, tool call, lookup, verification, search, calculation, "
    "or subtask is needed, proceed with that action now.\n\n"
    "Only return plain text if you are asking for genuinely missing user input, "
    "refusing, reporting an error, or giving a final answer based on already "
    "observed data."
)

PROMPT_VERIFICATION_REASONING_HISTORY_KEY = (
    "prompt_verification_reasoning_history"
)
MAX_AUTO_CONTINUE_RECOVERY_ATTEMPTS = 3
AUTO_CONTINUE_RECOVERY_COUNT_KEY = "auto_continue_recovery_count"
AUTO_CONTINUE_FAILED_KEY = "auto_continue_failed"
AUTO_CONTINUE_FAILURE_MESSAGE = (
    "Auto-continue recovery failed: the agent repeatedly produced "
    "non-terminal natural-language responses instead of a valid next output."
)

PROMPT_VERIFICATION_RETRY_MSG = (
    "Your previous proposed reasoning step or output was rejected because it "
    "was not fully supported by the current context or policy.\n\n"
    "Verifier feedback:\n"
    "{reason}\n\n"
    "Generate a corrected next step that resolves this issue. "
    "Follow your current output contract. "
    "Do not mention the verifier or the rejected output."
)


REASONING_STEP_BUDGET_MSG = (
    "You have at most {remaining} reasoning step(s) remaining before you must "
    "commit to either a terminal user-facing response or a tool call. "
    "If you choose to reason, produce exactly one intermediate reasoning step "
    "prefixed with `REASONING:`. "
    "If {remaining} is 0, you must not produce another reasoning step."
)

def _is_reasoning_message(content: str) -> bool:
    """Whether the visible model output declares one reasoning step."""
    stripped = (content or "").lstrip()

    if ":" not in stripped:
        return False

    prefix, _ = stripped.split(":", 1)
    return prefix.strip().lower() == "reasoning"

def _strip_reasoning_prefix(content: str) -> str:
    """Return the body of a REASONING: response."""
    stripped = (content or "").lstrip()

    if ":" not in stripped:
        return stripped.strip()

    prefix, body = stripped.split(":", 1)

    if prefix.strip().lower() == "reasoning":
        return body.strip()

    return stripped.strip()


def _build_reasoning_history_message(
    steps: list[str],
) -> dict[str, str]:
    """Build temporary non-authoritative reasoning context for generation."""
    numbered_steps = "\n".join(
        f"R{i}: {step}"
        for i, step in enumerate(steps, start=1)
    )

    return {
        "role": "user",
        "content": (
            "## Accepted Reasoning History\n\n"
            "The following reasoning steps were accepted earlier in the "
            "current internal trajectory. They are provided only for "
            "reasoning continuity and are NOT authoritative facts. "
            "Later user messages, tool observations, and policy evidence "
            "take precedence if they conflict with any earlier reasoning.\n\n"
            f"{numbered_steps}"
        ),
    }

def _snapshot_runtime_variables_for_verifier(
    adapter: CoreGraphAdapter,
    state: Any,
) -> dict[str, Any]:
    """Snapshot current CUGA variable values for one verifier call.

    This is execution-context normalization only. The returned mapping is passed
    directly to ``verify_candidate`` so generated Python can be interpreted with
    the same variable values CUGA would use at execution time. It is not appended
    to canonical conversation history and therefore does not become verifier STATE.
    """
    manager = adapter.get_variable_manager(state)
    if manager is None:
        return {}

    try:
        names = list(manager.get_variable_names() or [])
    except Exception as exc:
        logger.warning(
            "Could not enumerate CUGA variables for prompt verification: {}: {}",
            type(exc).__name__,
            exc,
        )
        return {}

    snapshot: dict[str, Any] = {}
    for raw_name in names:
        name = str(raw_name)
        if not name.isidentifier():
            continue
        try:
            snapshot[name] = manager.get_variable(name)
        except Exception as exc:
            logger.warning(
                "Could not read CUGA variable {} for prompt verification: {}: {}",
                name,
                type(exc).__name__,
                exc,
            )

    logger.debug(
        "Prompt verification runtime-variable snapshot from current state: names={}",
        sorted(snapshot),
    )
    return snapshot


def create_call_model_node(
    adapter: CoreGraphAdapter,
    base_model: Any,
    settings: Any,
) -> Callable:
    """Return an async ``call_model`` node function parameterised by *adapter*.

    Args:
        adapter: Graph-specific seam providing hook implementations.
        base_model: Default LLM; can be overridden at runtime via
            ``config["configurable"]["llm"]``.
        settings: Application settings object (policy, advanced_features, …).
    """

    async def call_model(state: Any, config: RunnableConfig = None) -> Command:
        configurable: dict = config.get("configurable", {}) if config else {}

        from cuga.backend.cuga_graph.utils.langfuse_tracing import sync_langfuse_callbacks_from_config

        sync_langfuse_callbacks_from_config(config)

        # ── Tool-approval HITL resumption ──────────────────────────────────
        if settings.policy.enabled and ToolApprovalHandler.is_returning_from_approval(adapter, state):
            return ToolApprovalHandler.handle_approval_resumption(adapter, state)

        # ── Resolve active model ───────────────────────────────────────────
        active_model = configurable.get("llm") or base_model

        # ── System content (may be augmented, e.g. with todos) ─────────────
        base_prompt: str = getattr(state, "prepared_prompt", "") or ""
        system_content: str = adapter.prepare_system_content(state, configurable, base_prompt)

        # ── Context summarisation ──────────────────────────────────────────
        effective_messages = await apply_context_summarization(
            adapter.get_messages(state) or [],
            active_model,
            system_prompt=base_prompt,
            tools=None,
            tracker=adapter.get_tracker(),
            variables_storage=adapter.get_variables_storage(state),
            variable_counter_state=getattr(state, "variable_counter_state", None),
            variable_creation_order=getattr(state, "variable_creation_order", None),
            message_list_name=adapter.messages_key,
        )

        # ``canonical_messages`` is the committed conversation view that may be
        # persisted back into LangGraph state.  Everything injected below
        # (Playbook guidance, PI, variable summaries, verifier retry/control
        # messages) is model-only rendering and must never be written back into
        # ``chat_messages``.  This separation is what prevents authority text
        # from leaking into the verifier's dynamic STATE graph.
        canonical_messages = list(effective_messages)

        # ── Build messages_for_model: [system] + few-shot + conversation ───
        messages_for_model: list = [{"role": "system", "content": system_content}]

        for example in adapter.get_few_shot_messages(state):
            if isinstance(example, dict):
                role = (example.get("role") or "").strip().lower()
                ex_content = example.get("content") or ""
                if role in {"user", "assistant"} and ex_content:
                    messages_for_model.append({"role": role, "content": ex_content})

        # ── Variables summary ──────────────────────────────────────────────
        var_manager = adapter.get_variable_manager(state)
        variables_summary_text: Optional[str] = None
        variables_addendum = ""
        if var_manager is not None:
            existing_var_names = var_manager.get_variable_names()
            if existing_var_names:
                variables_summary_text = var_manager.get_variables_summary(variable_names=existing_var_names)
                variables_addendum = (
                    f"\n\n## Available Variables\n\n{variables_summary_text}"
                    f"\n\nYou can use these variables directly by their names."
                )

        # ── Playbook guidance (first call only) ────────────────────────────
        metadata = adapter.get_metadata(state)
        playbook_guidance: Optional[str] = None
        if (
            settings.policy.enabled
            and metadata.get("policy_matched")
            and metadata.get("policy_type") == "playbook"
            and not metadata.get("playbook_guidance_added")
        ):
            playbook_guidance = metadata.get("playbook_guidance")
            if playbook_guidance:
                logger.info("Will inject playbook guidance into last user message (first time only)")

        # ── Process messages: inject PI / playbook / variables ─────────────
        # These augmentations are intentionally model-only.  Do not persist the
        # rendered copies into state.
        pi = adapter.get_pi(state)
        pi_added = False
        playbook_fired = False

        for i, msg in enumerate(canonical_messages):
            is_last = i == len(canonical_messages) - 1
            msg_role = getattr(msg, "type", None)
            is_human = isinstance(msg, HumanMessage) or msg_role in ("human", "user")
            is_ai = isinstance(msg, AIMessage) or msg_role in ("ai", "assistant")

            if is_human:
                content = msg.content if hasattr(msg, "content") else (msg.get("content") or "")

                if pi and not pi_added and "## User Context" not in content and len(canonical_messages) == 1:
                    content = f"{content}\n\n## User Context\n{pi}"
                    pi_added = True

                if playbook_guidance and is_last:
                    content = f"{content}\n\n## Task Guidance\n{playbook_guidance}"
                    playbook_fired = True

                if variables_summary_text and is_last:
                    content = content + variables_addendum

                messages_for_model.append({"role": "user", "content": content})

            elif is_ai:
                ai_content = msg.content if hasattr(msg, "content") else (msg.get("content") or "")
                messages_for_model.append({"role": "assistant", "content": ai_content})

            else:
                logger.warning("call_model: skipping message {} with unknown role: {}", i, msg_role)

        logger.info(
            "call_model: {} messages → model ({})",
            len(messages_for_model),
            adapter.sender_name,
        )

        print("==== CALL_MODEL TOOL DEBUG ====", flush=True)
        print("adapter type:", type(adapter), flush=True)

        for name in [
            "tools",
            "lc_bind_tools",
            "lc_bind_tools_meta",
            "tools_context",
            "available_tools",
            "tool_schemas",
        ]:
            value = locals().get(name, "<not in locals>")
            try:
                length = len(value)
            except Exception:
                length = "n/a"
            print(f"{name}: type={type(value)} len={length} value={value}", flush=True)

        print("state type:", type(state), flush=True)
        print("state:", state, flush=True)
        print("==== END CALL_MODEL TOOL DEBUG ====", flush=True)

        # ── Resolve bound model (bind-tools, Lite-only) ────────────────────
        bound = await adapter.resolve_bind_tools(state, active_model, configurable, config) or active_model

        # ── Model invocation ───────────────────────────────────────────────
        # Pass the full node config so LangChain keeps parent_run_id linkage for
        # Langfuse. Passing only {"callbacks": [...]} starts orphan root traces.
        invoke_config = config if config is not None else {}


        # ── Model invocation + optional prompt verification ────────────────
        verification_enabled = bool(
            getattr(
                settings.advanced_features,
                "prompt_verification_enabled",
                False,
            )
        )

        external_reasoning_enabled = verification_enabled and bool(
            getattr(
                settings.advanced_features,
                "prompt_verification_external_reasoning",
                True,
            )
        )

        max_verification_attempts = int(
            getattr(
                settings.advanced_features,
                "prompt_verification_max_attempts",
                3,
            )
        )

        max_reasoning_steps = int(
            getattr(
                settings.advanced_features,
                "prompt_verification_max_reasoning_steps",
                5,
            )
        )

        if max_verification_attempts < 1:
            raise ValueError(
                "prompt_verification_max_attempts must be at least 1"
            )

        if external_reasoning_enabled and max_reasoning_steps < 0:
            raise ValueError(
                "prompt_verification_max_reasoning_steps cannot be negative"
            )

        content = ""
        reasoning = None
        accepted_reasoning_steps: list[str] = []

        generation_model = bound

        # Normal CUGA behavior is unchanged when verification is disabled.
        if not verification_enabled:
            response = await adapter.ainvoke_model(
                generation_model,
                messages_for_model,
                invoke_config,
            )

            content, reasoning = adapter.normalize_response(response)

        elif external_reasoning_enabled:
            # External reasoning mode deliberately minimizes provider-side
            # reasoning and exposes one REASONING: proposition at a time so
            # each intermediate step can be verified before it is reused.
            generation_model = bound.bind(
                reasoning_effort="low",
            )

            # Reasoning history persists across:
            #
            # call_model -> sandbox -> tool observation -> call_model
            #
            # Do NOT reset the verifier reasoning graph here.
            verification_metadata = adapter.get_metadata(state)

            accepted_reasoning_steps = list(
                verification_metadata.get(
                    PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                    [],
                )
                or []
            )

            reasoning_steps_used = len(accepted_reasoning_steps)

            # Only the latest rejected candidate + feedback are retained for
            # agent regeneration. Separately, keep exactly one immediately prior
            # verifier rejection so the next verifier call can receive it as one
            # temporary Q statement. Neither survives a tool-execution round trip.
            rejection_pair: list[dict[str, str]] = []
            previous_verifier_rejection: tuple[str, str] | None = None

            rejection_attempt = 0

            logger.info(
                "{}: prompt verification using EXTERNAL reasoning; loaded {} "
                "persisted reasoning step(s)",
                adapter.sender_name,
                reasoning_steps_used,
            )

            try:
                while True:
                    remaining_reasoning_steps = (
                        max_reasoning_steps - reasoning_steps_used
                    )

                    budget_message = {
                        "role": "user",
                        "content": REASONING_STEP_BUDGET_MSG.format(
                            remaining=remaining_reasoning_steps,
                        ),
                    }

                    # IMPORTANT:
                    #
                    # Base CUGA history remains immutable during this internal
                    # generation cycle. Accepted external reasoning accumulates
                    # separately, and only the latest rejected candidate +
                    # verifier feedback are supplied during a correction attempt.
                    generation_messages = [
                        *messages_for_model,
                    ]

                    if accepted_reasoning_steps:
                        generation_messages.append(
                            _build_reasoning_history_message(
                                accepted_reasoning_steps
                            )
                        )

                    generation_messages.append(budget_message)
                    generation_messages.extend(rejection_pair)

                    response = await adapter.ainvoke_model(
                        generation_model,
                        generation_messages,
                        invoke_config,
                    )

                    content, reasoning = adapter.normalize_response(response)

                    # Provider-side reasoning is deliberately not propagated.
                    # Explicit REASONING: messages are our observable reasoning
                    # channel while external reasoning is enabled.
                    reasoning = None

                    candidate_kind = (
                        "reasoning"
                        if _is_reasoning_message(content)
                        else "terminal"
                    )

                    # Track whether this rejection came from the verifier itself.
                    # Local protocol/budget rejections must not be mislabeled as
                    # verifier history in the next verifier prompt.
                    verifier_was_called = False

                    # Enforce the reasoning budget in Python as well as in the
                    # prompt. Rejected reasoning does not consume the reasoning
                    # budget because it never enters the accepted trajectory.
                    if (
                        candidate_kind == "reasoning"
                        and remaining_reasoning_steps <= 0
                    ):
                        verification_valid = False
                        verification_reason = (
                            "The reasoning-step budget is exhausted. "
                            "Produce either a terminal user-facing response "
                            "or a tool call now."
                        )

                    else:
                        verifier_was_called = True
                        verification = await verify_candidate(
                            # Verify against committed conversation state only.
                            # ``messages_for_model`` also contains Playbook/PI/
                            # variable/control injections and is therefore not a
                            # valid STATE source.
                            current_context=canonical_messages,
                            candidate=content,
                            candidate_kind=candidate_kind,
                            runtime_variables=_snapshot_runtime_variables_for_verifier(
                                adapter,
                                state,
                            ),
                            previous_rejection=previous_verifier_rejection,
                        )

                        verification_valid = verification.valid
                        verification_reason = verification.reason

                    logger.info(
                        "{}: prompt verification mode=external "
                        "candidate_kind={} reasoning_steps={}/{} "
                        "rejection_attempt={}/{} valid={} reason={!r}",
                        adapter.sender_name,
                        candidate_kind,
                        reasoning_steps_used,
                        max_reasoning_steps,
                        rejection_attempt + 1,
                        max_verification_attempts,
                        verification_valid,
                        verification_reason,
                    )

                    if verification_valid:
                        # Once a replacement is accepted, the rejected candidate
                        # and its feedback disappear from temporary history.
                        rejection_pair = []
                        previous_verifier_rejection = None
                        rejection_attempt = 0

                        if candidate_kind == "reasoning":
                            accepted_reasoning_steps.append(
                                _strip_reasoning_prefix(content)
                            )

                            reasoning_steps_used = len(
                                accepted_reasoning_steps
                            )

                            logger.info(
                                "{}: accepted external reasoning step {}/{}; "
                                "continuing generation loop",
                                adapter.sender_name,
                                reasoning_steps_used,
                                max_reasoning_steps,
                            )

                            # Do NOT leave call_model and do NOT commit this
                            # reasoning message to normal CUGA state.
                            continue

                        # Accepted terminal response or tool execution.
                        #
                        # prompt_verifier automatically upgrades a terminal
                        # candidate containing awaited calls to tool_execution.
                        # The reasoning trajectory remains alive here. The
                        # orchestrator finalizes it only when this graph
                        # trajectory actually ends.
                        break

                    # ── Candidate rejected ──────────────────────────────────
                    rejection_attempt += 1

                    if rejection_attempt >= max_verification_attempts:
                        raise RuntimeError(
                            "Prompt verification failed after "
                            f"{max_verification_attempts} attempts for the "
                            "current reasoning/output step. "
                            f"Last verifier reason: {verification_reason}"
                        )

                    retry_reason = (
                        verification_reason.strip()
                        if verification_reason
                        else "The previous output did not pass verification."
                    )

                    previous_verifier_rejection = (
                        (content, retry_reason)
                        if verifier_was_called
                        else None
                    )

                    # This pair is intentionally NOT appended to
                    # accepted_reasoning_steps. If the replacement passes, this
                    # entire pair disappears.
                    rejection_pair = [
                        {
                            "role": "assistant",
                            "content": content,
                        },
                        {
                            "role": "user",
                            "content": PROMPT_VERIFICATION_RETRY_MSG.format(
                                reason=retry_reason,
                            ),
                        },
                    ]

            except Exception:
                # Prevent a failed/aborted external reasoning cycle from leaving
                # accepted temporary reasoning attached to the verifier session.
                reset_reasoning_trace()

                failed_metadata = dict(
                    adapter.get_metadata(state) or {}
                )
                failed_metadata.pop(
                    PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                    None,
                )
                adapter.set_metadata(
                    state,
                    failed_metadata,
                )

                raise

        else:
            # Internal reasoning mode leaves the bound model's provider-side
            # reasoning configuration untouched. The verifier sees only the
            # visible tool/final candidate, never the hidden reasoning channel.
            generation_model = bound

            # If a session was previously run in external mode, do not carry its
            # temporary explicit reasoning trajectory into internal mode.
            reset_reasoning_trace()
            verification_metadata = dict(adapter.get_metadata(state) or {})
            if PROMPT_VERIFICATION_REASONING_HISTORY_KEY in verification_metadata:
                verification_metadata.pop(
                    PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                    None,
                )
                adapter.set_metadata(state, verification_metadata)

            rejection_pair: list[dict[str, str]] = []
            previous_verifier_rejection: tuple[str, str] | None = None
            rejection_attempt = 0

            logger.info(
                "{}: prompt verification using INTERNAL provider reasoning",
                adapter.sender_name,
            )

            try:
                while True:
                    generation_messages = [
                        *messages_for_model,
                        *rejection_pair,
                    ]

                    response = await adapter.ainvoke_model(
                        generation_model,
                        generation_messages,
                        invoke_config,
                    )

                    content, reasoning = adapter.normalize_response(response)

                    # Track whether this rejection came from the verifier itself.
                    verifier_was_called = False

                    # Internal mode must not accidentally fall back to the old
                    # visible REASONING: protocol. Hidden/provider reasoning is
                    # preserved in ``reasoning`` for normal CUGA processing.
                    if _is_reasoning_message(content):
                        verification_valid = False
                        verification_reason = (
                            "External REASONING: messages are disabled. Reason "
                            "internally and return either the next executable "
                            "Python action or a terminal user-facing response."
                        )
                    else:
                        verifier_was_called = True
                        verification = await verify_candidate(
                            current_context=canonical_messages,
                            candidate=content,
                            candidate_kind="terminal",
                            runtime_variables=_snapshot_runtime_variables_for_verifier(
                                adapter,
                                state,
                            ),
                            previous_rejection=previous_verifier_rejection,
                        )
                        verification_valid = verification.valid
                        verification_reason = verification.reason

                    logger.info(
                        "{}: prompt verification mode=internal "
                        "rejection_attempt={}/{} valid={} reason={!r}",
                        adapter.sender_name,
                        rejection_attempt + 1,
                        max_verification_attempts,
                        verification_valid,
                        verification_reason,
                    )

                    if verification_valid:
                        rejection_pair = []
                        previous_verifier_rejection = None
                        break

                    rejection_attempt += 1
                    if rejection_attempt >= max_verification_attempts:
                        raise RuntimeError(
                            "Prompt verification failed after "
                            f"{max_verification_attempts} attempts for the "
                            "current output. "
                            f"Last verifier reason: {verification_reason}"
                        )

                    retry_reason = (
                        verification_reason.strip()
                        if verification_reason
                        else "The previous output did not pass verification."
                    )
                    previous_verifier_rejection = (
                        (content, retry_reason)
                        if verifier_was_called
                        else None
                    )
                    rejection_pair = [
                        {
                            "role": "assistant",
                            "content": content,
                        },
                        {
                            "role": "user",
                            "content": PROMPT_VERIFICATION_RETRY_MSG.format(
                                reason=retry_reason,
                            ),
                        },
                    ]

            except Exception:
                reset_reasoning_trace()

                failed_metadata = dict(adapter.get_metadata(state) or {})
                failed_metadata.pop(
                    PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                    None,
                )
                adapter.set_metadata(state, failed_metadata)
                raise

        if verification_enabled and external_reasoning_enabled:
            # Persist accepted explicit reasoning across:
            # call_model -> execute/sandbox -> tool observation -> call_model.
            # This metadata is temporary reasoning continuity, not grounding STATE.
            updated_verification_metadata = {
                **(adapter.get_metadata(state) or {}),
                PROMPT_VERIFICATION_REASONING_HISTORY_KEY: list(
                    accepted_reasoning_steps
                ),
            }
            adapter.set_metadata(
                state,
                updated_verification_metadata,
            )

        # ── Extract code ───────────────────────────────────────────────────
        # When verification is enabled, execute only code that appeared in the
        # visible candidate that was actually verified. In internal reasoning
        # mode, provider-side reasoning is intentionally hidden and must never
        # become an unverified fallback source of executable code.
        code = extract_code_from_model_response(
            content,
            None if verification_enabled else reasoning,
        )

        adapter.on_response_processed(state, code, content)

        # ── Build final message list + step count ──────────────────────────
        # Persist only the canonical conversation plus the accepted assistant
        # output.  CodeAct execution messages are tagged so the verifier can
        # advance over them without promoting generated code to grounding STATE.
        assistant_additional_kwargs = (
            {"cuga_internal_tool_execution": True}
            if code
            else {}
        )
        final_messages: list = canonical_messages + [
            AIMessage(
                content=content,
                additional_kwargs=assistant_additional_kwargs,
            )
        ]
        new_step_count: int = state.step_count + 1

        # ── Step limit enforcement ─────────────────────────────────────────
        max_steps = adapter.resolve_max_steps(state, configurable.get("cuga_lite_max_steps"))
        limit_cmd = enforce_step_limit(
            adapter,
            state=state,
            messages=final_messages,
            new_step_count=new_step_count,
            limit=max_steps,
        )
        if limit_cmd is not None:
            if verification_enabled:
                reset_reasoning_trace()

                limit_metadata = dict(
                    adapter.get_metadata(state) or {}
                )
                limit_metadata.pop(
                    PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                    None,
                )
                adapter.set_metadata(
                    state,
                    limit_metadata,
                )

            return limit_cmd

        # ── Tool-approval interrupt for generated code ─────────────────────
        if code and settings.policy.enabled:
            approval_command = await ToolApprovalHandler.check_and_create_approval_interrupt(
                adapter, state, code, content, config
            )
            if approval_command:
                return approval_command

        # ── Metadata update ────────────────────────────────────────────────
        meta_value = adapter.build_metadata_update(state, playbook_fired=playbook_fired)
        meta_update = {adapter.metadata_key: meta_value}

        auto_continue_count = int(
            (adapter.get_metadata(state) or {}).get(AUTO_CONTINUE_RECOVERY_COUNT_KEY, 0) or 0
        )

        # ── Route: code → execute node; text → END or auto-continue ────────
        if code:
            # Recovered: the model moved from NL interim text to executable code.
            meta_value[AUTO_CONTINUE_RECOVERY_COUNT_KEY] = 0

            return Command(
                goto=adapter.execute_node_name,
                update={
                    adapter.messages_key: final_messages,
                    "script": code,
                    "step_count": new_step_count,
                    **meta_update,
                },
            )

        should_continue = await adapter.classify_auto_continue(state, active_model, content, reasoning)
        if should_continue:
            next_auto_continue_count = auto_continue_count + 1
            meta_value[AUTO_CONTINUE_RECOVERY_COUNT_KEY] = next_auto_continue_count

            if next_auto_continue_count >= MAX_AUTO_CONTINUE_RECOVERY_ATTEMPTS:
                logger.warning(
                    "{}: auto-continue recovery failed after {} attempts; stopping graph",
                    adapter.sender_name,
                    next_auto_continue_count,
                )

                meta_value[AUTO_CONTINUE_FAILED_KEY] = True

                failure_message = AUTO_CONTINUE_FAILURE_MESSAGE
                failure_messages = final_messages + [AIMessage(content=failure_message)]

                if verification_enabled:
                    reset_reasoning_trace()
                    meta_value.pop(
                        PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                        None,
                    )

                return Command(
                    goto=END,
                    update={
                        adapter.messages_key: failure_messages,
                        "script": None,
                        "final_answer": failure_message,
                        "execution_complete": True,
                        "step_count": new_step_count,
                        **meta_update,
                    },
                )

            logger.info(
                "{}: NL response classified as interim — auto-continuing ({}/{})",
                adapter.sender_name,
                next_auto_continue_count,
                MAX_AUTO_CONTINUE_RECOVERY_ATTEMPTS,
            )

            return Command(
                goto="call_model",
                update={
                    adapter.messages_key: final_messages + [
                        HumanMessage(
                            content=AUTO_CONTINUE_MSG,
                            additional_kwargs={"cuga_internal_control": True},
                        )
                    ],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    **meta_update,
                },
            )

        # ponytail: reasoning-only models may finalize with empty visible content
        final_answer = content
        # Provider-side reasoning is private in verified internal-reasoning mode.
        # Never expose it as a fallback user-facing answer.
        if (
            not verification_enabled
            and not (final_answer or "").strip()
            and reasoning
        ):
            final_answer = (reasoning or "").strip()
        if not (final_answer or "").strip():
            exec_prefix = "Execution output:\n"
            for msg in reversed(canonical_messages):
                if isinstance(msg, HumanMessage):
                    text = msg.content or ""
                    if text.startswith(exec_prefix):
                        body = text[len(exec_prefix) :].strip()
                        if body:
                            final_answer = body
                            break
        if not (content or "").strip() and final_answer:
            final_messages = canonical_messages + [AIMessage(content=final_answer)]

        meta_value[AUTO_CONTINUE_RECOVERY_COUNT_KEY] = 0

        if verification_enabled:
            if external_reasoning_enabled:
                commit_reasoning_trace_to_state()
            else:
                reset_reasoning_trace()
            meta_value.pop(
                PROMPT_VERIFICATION_REASONING_HISTORY_KEY,
                None,
            )

        return Command(
            goto=END,
            update={
                adapter.messages_key: final_messages,
                "script": None,
                "final_answer": final_answer,
                "execution_complete": True,
                "step_count": new_step_count,
                **meta_update,
            },
        )

    return call_model