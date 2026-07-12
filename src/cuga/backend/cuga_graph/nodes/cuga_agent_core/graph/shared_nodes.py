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
from cuga.backend.cuga_graph.utils.context_management_utils import (
    apply_context_summarization,
    truncate_text_for_context,
)


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

        # ── Variables summary (count toward context before summarization) ──
        var_manager = adapter.get_variable_manager(state)
        variables_summary_text: Optional[str] = None
        variables_addendum = ""
        if var_manager is not None:
            existing_var_names = var_manager.get_variable_names()
            if existing_var_names:
                variables_summary_text = var_manager.get_variables_summary(variable_names=existing_var_names)
                variables_summary_text = truncate_text_for_context(
                    variables_summary_text,
                    settings.advanced_features.variables_summary_max_length,
                    label="Variables summary",
                )
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

        summarization_system_prompt = system_content + variables_addendum
        if playbook_guidance:
            summarization_system_prompt += f"\n\n## Task Guidance\n{playbook_guidance}"

        # ── Context summarisation ──────────────────────────────────────────
        effective_messages = await apply_context_summarization(
            adapter.get_messages(state) or [],
            active_model,
            system_prompt=summarization_system_prompt,
            tools=None,
            tracker=adapter.get_tracker(),
            variables_storage=adapter.get_variables_storage(state),
            variable_counter_state=getattr(state, "variable_counter_state", None),
            variable_creation_order=getattr(state, "variable_creation_order", None),
            message_list_name=adapter.messages_key,
        )

        # ── Build messages_for_model: [system] + few-shot + conversation ───
        messages_for_model: list = [{"role": "system", "content": system_content}]

        for example in adapter.get_few_shot_messages(state):
            if isinstance(example, dict):
                role = (example.get("role") or "").strip().lower()
                ex_content = example.get("content") or ""
                if role in {"user", "assistant"} and ex_content:
                    messages_for_model.append({"role": role, "content": ex_content})

        # ── Process messages: inject PI / playbook / variables ─────────────
        pi = adapter.get_pi(state)
        pi_added = False
        playbook_fired = False
        modified_messages: list = []

        for i, msg in enumerate(effective_messages):
            is_last = i == len(effective_messages) - 1
            msg_role = getattr(msg, "type", None)
            is_human = isinstance(msg, HumanMessage) or msg_role in ("human", "user")
            is_ai = isinstance(msg, AIMessage) or msg_role in ("ai", "assistant")

            if is_human:
                content = msg.content if hasattr(msg, "content") else (msg.get("content") or "")
                modified = False

                if pi and not pi_added and "## User Context" not in content and len(effective_messages) == 1:
                    content = f"{content}\n\n## User Context\n{pi}"
                    pi_added = True
                    modified = True

                if playbook_guidance and is_last:
                    content = f"{content}\n\n## Task Guidance\n{playbook_guidance}"
                    modified = True
                    playbook_fired = True

                if variables_summary_text and is_last:
                    content = content + variables_addendum
                    modified = True

                modified_messages.append(msg.model_copy(update={"content": content}) if modified else msg)
                messages_for_model.append({"role": "user", "content": content})

            elif is_ai:
                modified_messages.append(msg)
                ai_content = msg.content if hasattr(msg, "content") else (msg.get("content") or "")
                messages_for_model.append({"role": "assistant", "content": ai_content})

            else:
                modified_messages.append(msg)
                logger.warning("call_model: skipping message {} with unknown role: {}", i, msg_role)

        logger.info(
            "call_model: {} messages → model ({})",
            len(messages_for_model),
            adapter.sender_name,
        )

        # ── Resolve bound model (bind-tools, Lite-only) ────────────────────
        bound = await adapter.resolve_bind_tools(state, active_model, configurable, config) or active_model

        # ── Model invocation ───────────────────────────────────────────────
        # Pass the full node config so LangChain keeps parent_run_id linkage for
        # Langfuse. Passing only {"callbacks": [...]} starts orphan root traces.
        invoke_config = config if config is not None else {}
        response = await adapter.ainvoke_model(bound, messages_for_model, invoke_config)

        # ── Normalise response ─────────────────────────────────────────────
        content, reasoning = adapter.normalize_response(response)

        # ── Extract code ───────────────────────────────────────────────────
        code = extract_code_from_model_response(content, reasoning)

        adapter.on_response_processed(state, code, content)

        # ── Build final message list + step count ──────────────────────────
        final_messages: list = modified_messages + [AIMessage(content=content)]
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
            return limit_cmd

        # ── Tool-approval interrupt for generated code ─────────────────────
        if code and settings.policy.enabled:
            approval_command = await ToolApprovalHandler.check_and_create_approval_interrupt(
                adapter, state, code, content, config
            )
            if approval_command:
                return approval_command

        # ── Metadata update ────────────────────────────────────────────────
        meta_update = {
            adapter.metadata_key: adapter.build_metadata_update(state, playbook_fired=playbook_fired)
        }

        # ── Route: code → execute node; text → END or auto-continue ────────
        if code:
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
            logger.info(f"{adapter.sender_name}: NL response classified as interim — auto-continuing")
            return Command(
                goto="call_model",
                update={
                    adapter.messages_key: final_messages + [HumanMessage(content="continue")],
                    "script": None,
                    "final_answer": "",
                    "execution_complete": False,
                    "step_count": new_step_count,
                    **meta_update,
                },
            )

        # ponytail: reasoning-only models may finalize with empty visible content
        final_answer = content
        if not (final_answer or "").strip() and reasoning:
            final_answer = (reasoning or "").strip()
        if not (final_answer or "").strip():
            exec_prefix = "Execution output:\n"
            for msg in reversed(modified_messages):
                if isinstance(msg, HumanMessage):
                    text = msg.content or ""
                    if text.startswith(exec_prefix):
                        body = text[len(exec_prefix) :].strip()
                        if body:
                            final_answer = body
                            break
        if not (content or "").strip() and final_answer:
            final_messages = modified_messages + [AIMessage(content=final_answer)]

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
