"""Knowledge awareness — injects document summaries into agent system prompt.

Shows both agent-level (permanent) and session-level (temporary) documents
so the agent knows what knowledge is available and how to search it.
"""

from __future__ import annotations

import logging
from typing import Any

from cuga.backend.knowledge.engine import KnowledgeEngine

logger = logging.getLogger("cuga.knowledge")

# Max characters of preview shown per document in the awareness summary
_AWARENESS_PREVIEW_MAX_CHARS = 200


def _agent_collection_name(agent_id: str, config_hash: str | None = None) -> str:
    import re

    sanitized_agent_id = re.sub(r"[^a-zA-Z0-9_]", "_", agent_id)
    base = f"kb_agent_{sanitized_agent_id}"
    return f"{base}_{config_hash}" if config_hash else base


def _format_doc_line(doc: Any) -> str:
    """Format a single document entry for the awareness summary."""
    line = f"- {doc.filename} ({doc.chunk_count} chunks)"
    preview = getattr(doc, "preview", "") or ""
    if preview:
        truncated = preview[:_AWARENESS_PREVIEW_MAX_CHARS]
        if len(preview) > _AWARENESS_PREVIEW_MAX_CHARS:
            truncated = truncated.rsplit(" ", 1)[0] + "..."
        line += f"\n  Preview: {truncated}"
    return line


async def get_knowledge_summary(
    engine: KnowledgeEngine,
    agent_collection: str | None = None,
    session_collection: str | None = None,
    max_docs: int = 10,
    max_search_attempts: int | None = None,
    default_limit: int | None = None,
    rag_profile: str = "standard",
) -> str | None:
    """Build a knowledge summary for injection into the agent's system prompt.

    Returns a formatted markdown string, or None if no documents exist.
    """
    sections: list[str] = []

    # Agent documents (permanent)
    if agent_collection:
        try:
            agent_docs = await engine.list_documents(agent_collection)
            if agent_docs:
                lines = [_format_doc_line(d) for d in agent_docs[:max_docs]]
                if len(agent_docs) > max_docs:
                    lines.append(f"- ... and {len(agent_docs) - max_docs} more")
                sections.append("### Agent Documents (permanent):\n" + "\n".join(lines))
        except Exception as e:
            logger.warning(f"Failed to list agent docs for {agent_collection}: {e}")

    # Session documents (temporary)
    if session_collection:
        try:
            session_docs = await engine.list_documents(session_collection)
            logger.info(f"Session docs in {session_collection}: {len(session_docs) if session_docs else 0}")
            if session_docs:
                lines = [_format_doc_line(d) for d in session_docs[:max_docs]]
                if len(session_docs) > max_docs:
                    lines.append(f"- ... and {len(session_docs) - max_docs} more")
                sections.append("### Session Documents (this conversation only):\n" + "\n".join(lines))
        except Exception as e:
            logger.warning(f"Failed to list session docs for {session_collection}: {e}")

    if not sections:
        return None

    summary = "## Your Knowledge Base\n\n" + "\n\n".join(sections)

    # Build scope-aware directive
    has_agent = agent_collection and any(s.startswith("### Agent Documents") for s in sections)
    has_session = session_collection and any(s.startswith("### Session Documents") for s in sections)

    summary += "\n\n"

    # Scope guidance — compact
    if has_agent and has_session:
        summary += "Search with `knowledge_search_knowledge(query=..., scope=\"agent\")` for permanent docs "
        summary += "or `scope=\"session\"` for this conversation's docs.\n"
    elif has_agent:
        summary += "Search with `knowledge_search_knowledge(query=..., scope=\"agent\")`.\n"
    elif has_session:
        summary += "Search with `knowledge_search_knowledge(query=..., scope=\"session\")`.\n"

    limit = max_search_attempts or 3
    summary += f"\nSearch limit: {limit}. Answer from your first search if possible.\n"

    # Inject profile-specific instruction addendum
    if rag_profile and rag_profile != "standard":
        try:
            from cuga.backend.knowledge.config import load_profile

            profile_data = load_profile(rag_profile)
            addendum = profile_data.get("instructions", {}).get("addendum", "").strip()
            if addendum:
                summary += f"\n{addendum}\n"
        except Exception as e:
            logger.warning("Failed to load profile addendum for %s: %s", rag_profile, e)

    return summary


def get_engine_from_app_state() -> KnowledgeEngine | None:
    """Try to get the knowledge engine from the FastAPI app state singleton.

    This avoids needing to pass the engine through LangGraph's configurable dict.
    Returns None if not available (e.g., running outside FastAPI context).
    """
    try:
        from cuga.backend.server.main import app

        app_state = getattr(app.state, "app_state", None)
        return getattr(app_state, "knowledge_engine", None) if app_state else None
    except Exception:
        return None


def format_knowledge_context(
    agent_id: str | None = None,
    thread_id: str | None = None,
    engine: KnowledgeEngine | None = None,
    agent_config_hash: str | None = None,
) -> dict[str, str | None]:
    """Build collection names from agent/session context.

    Returns dict with agent_collection and session_collection names.
    """
    import re

    def _sanitize(v: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]", "_", v)

    config = getattr(engine, "_config", None) if engine else None
    agent_enabled = getattr(config, "agent_level_enabled", True) if config else True
    session_enabled = getattr(config, "session_level_enabled", True) if config else True

    return {
        "agent_collection": (
            _agent_collection_name(agent_id, agent_config_hash) if agent_id and agent_enabled else None
        ),
        "session_collection": f"kb_sess_{_sanitize(thread_id)}" if thread_id and session_enabled else None,
    }
