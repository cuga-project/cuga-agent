"""High-level memory helpers built on top of EvolveIntegration.

Wraps the guidelines + user-facts (store/retrieve) flow so call sites in the
graph can compose a single string section to append to the system prompt
instead of inlining the orchestration.
"""

import asyncio
import os
from typing import Any, Optional

from loguru import logger

from cuga.backend.evolve.compliance_poc import record_memory_usage
from cuga.backend.evolve.formatting import (
    build_evolve_guidelines_section,
    build_evolve_user_preference_section,
    get_first_human_message_content,
    get_latest_memory_query,
)
from cuga.backend.evolve.integration import EvolveIntegration, normalize_evolve_identifier
from cuga.config import settings


def _memory_user_id(state: Any) -> Optional[str]:
    raw_user_id = str(getattr(state, "user_id", "") or "").strip()
    normalized = normalize_evolve_identifier(raw_user_id)
    if normalized:
        return normalized
    if (
        raw_user_id == "default_user"
        and os.getenv("CUGA_COMPLIANCE_POC_SEED_ENABLED", "").lower() in {"1", "true", "yes"}
    ):
        return raw_user_id
    return None


async def build_evolve_special_instructions_extension(
    *,
    state: Any,
    configurable: dict,
    timeout: Optional[float] = None,
) -> str:
    """Compose Evolve guidelines + user-preference sections to append to the prompt.

    Returns "" when Evolve is disabled or nothing applies. Both reads
    (`get_guidelines`, `retrieve_user_facts`) are bounded by `timeout` and fail
    open. The user-fact write is fire-and-forget.
    """
    if not EvolveIntegration.is_enabled():
        return ""

    if timeout is None:
        timeout = settings.evolve.timeout

    extra = ""
    used_entity_ids: list[str] = []
    service_scope = getattr(state, "service_scope", {}) or {}

    task_description = state.sub_task or get_first_human_message_content(state.chat_messages)
    if task_description:
        try:
            # Extract multi-user parameters from state for Evolve attribution
            _evolve_user_id = _memory_user_id(state)
            _evolve_namespace_id = service_scope.get("tenant_id") or None
            _evolve_session_id = getattr(state, "thread_id", None)

            attributed_guidelines = await asyncio.wait_for(
                EvolveIntegration.get_guidelines_with_attribution(
                    task_description,
                    user_id=_evolve_user_id,
                    namespace_id=_evolve_namespace_id,
                    session_id=_evolve_session_id,
                ),
                timeout=timeout,
            )
            if attributed_guidelines:
                evolve_guidelines = attributed_guidelines["text"]
                used_entity_ids.extend(attributed_guidelines["entity_ids"])
            else:
                evolve_guidelines = await asyncio.wait_for(
                    EvolveIntegration.get_guidelines(
                        task_description,
                        user_id=_evolve_user_id,
                        namespace_id=_evolve_namespace_id,
                        session_id=_evolve_session_id,
                    ),
                    timeout=timeout,
                )
        except Exception:
            logger.warning("Evolve: get_guidelines timed out or failed; continuing without guidelines")
            evolve_guidelines = None
        evolve_section = build_evolve_guidelines_section(str(evolve_guidelines) if evolve_guidelines else "")
        if evolve_section:
            extra += evolve_section
            logger.info("Evolve: Injected guidelines into system prompt")
            logger.debug(f"Evolve: Injected guidelines section ({len(evolve_section)} chars)")

    memory_query = state.sub_task or get_latest_memory_query(state.chat_messages)
    current_user_id = _memory_user_id(state)
    if current_user_id and memory_query:
        current_agent_id = str(configurable.get("agent_id") or "").strip()
        thread_id_for_memory = str(
            configurable.get("thread_id") or getattr(state, "thread_id", "") or ""
        ).strip()

        try:
            retrieved_preferences = await asyncio.wait_for(
                EvolveIntegration.retrieve_user_facts(current_user_id, memory_query),
                timeout=timeout,
            )
        except Exception:
            logger.warning("Evolve: retrieve_user_facts timed out or failed; continuing without preferences")
            retrieved_preferences = None

        def _log_store_error(task: asyncio.Task) -> None:
            exc = task.exception() if not task.cancelled() else None
            if exc:
                logger.warning(f"Evolve: store_user_facts failed (non-blocking): {exc}")

        _store_task = asyncio.create_task(
            EvolveIntegration.store_user_facts(
                current_user_id,
                memory_query,
                metadata={
                    "thread_id": thread_id_for_memory,
                    "agent_id": current_agent_id,
                    "source": "cuga-lite",
                },
            )
        )
        _store_task.add_done_callback(_log_store_error)

        categories = (
            retrieved_preferences.get("categories") if isinstance(retrieved_preferences, dict) else None
        )
        if isinstance(categories, dict):
            used_entity_ids.extend(
                str(fact["id"])
                for facts in categories.values()
                if isinstance(facts, list)
                for fact in facts
                if isinstance(fact, dict) and fact.get("id")
            )
        preference_section = build_evolve_user_preference_section(categories)
        if preference_section:
            extra += preference_section
            logger.info("Evolve: Injected user preference context into system prompt")
            logger.debug(f"Evolve: Injected user preference section ({len(preference_section)} chars)")

    turn_id = str(service_scope.get("memory_turn_id") or "").strip()
    raw_user_id = str(getattr(state, "user_id", "") or "").strip()
    agent_id = str(configurable.get("agent_id") or service_scope.get("agent_id") or "").strip()
    if used_entity_ids and turn_id and raw_user_id and agent_id:
        try:
            await record_memory_usage(
                turn_id=turn_id,
                agent_id=agent_id,
                user_id=raw_user_id,
                entity_ids=used_entity_ids,
                thread_id=str(getattr(state, "thread_id", "") or ""),
                conversation_label=str(getattr(state, "input", "") or memory_query or "")[:120],
            )
        except Exception as exc:
            logger.warning(f"Evolve: failed to record memory usage (non-fatal): {exc}")

    return extra
