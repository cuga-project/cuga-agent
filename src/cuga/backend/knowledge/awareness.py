"""Knowledge awareness — injects document summaries into agent system prompt.

Shows both agent-level (permanent) and session-level (temporary) documents
so the agent knows what knowledge is available. The static "Knowledge Tool
Contract" (in ``knowledge_instructions.md``) carries the scope/query/result
rules; this module only emits the dynamic per-conversation doc list.

This module also exposes the **single seam** the rest of cuga uses to
inject knowledge guidance into a system prompt: :func:`assemble_system_prompt_section`.
That keeps the cross-cutting agent code (cuga_lite, chat_agent) detached
from the knowledge implementation — they import one function and stay out
of the contract / doc-list / hash plumbing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cuga.backend.knowledge.engine import KnowledgeEngine

logger = logging.getLogger("cuga.knowledge")

# Max characters of preview shown per document in the awareness summary
_AWARENESS_PREVIEW_MAX_CHARS = 200

# Sentinel for {{max_search_attempts}} in knowledge_instructions.md. Using
# a Jinja-style placeholder makes the template obvious to anyone editing
# the .md file without dragging in a templating engine.
_MAX_SEARCH_ATTEMPTS_PLACEHOLDER = "{{max_search_attempts}}"


def compose_knowledge_prompt(contract_text: str, doc_list_block: str, base_instructions: str) -> str:
    """Assemble the knowledge sections of the system prompt in the order
    that gives the LLM the best instruction-following:

      1. contract (the actionable policy — closest to the user message
         in most assemblers means highest weight)
      2. doc list (refers to "your KB" — meaningful only after the
         contract has defined what KB means)
      3. base_instructions (everything else)

    Empty sections are skipped so the joiners don't leave double-blank
    runs that some LLMs treat as content boundaries.
    """
    parts: list[str] = []
    for chunk in (contract_text, doc_list_block, base_instructions):
        if chunk:
            stripped = chunk.strip("\n")
            if stripped:
                parts.append(stripped)
    return "\n\n".join(parts)


def prompt_hash(text: str) -> str:
    """Short stable identifier for the assembled system prompt.

    Logged on every agent turn so future "the agent got worse since X"
    reports can be bucketed by exact prompt content and diffed
    side-by-side. 12 hex chars disambiguates ~10^14 variants — plenty
    for the small set of prompts a deployment actually runs.

    Centralized here (instead of inlined at each call site) so both
    cuga_lite and chat_agent compute hashes identically; an asymmetry
    here would make prompt drift in one path invisible to the other.
    """
    import hashlib as _hashlib

    return _hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_knowledge_instructions(max_search_attempts: int | None = None) -> str:
    """Load the canonical Knowledge Tool Contract from disk + substitute
    the dynamic search-attempt budget (from the RAG profile).

    Single source of truth used by every code path that injects knowledge
    guidance (cuga_lite, chat_agent). Returns ``""`` if the file is
    missing — caller decides whether that's an error.
    """
    # Walk up to <repo>/src/cuga/configurations/knowledge/knowledge_instructions.md.
    # This module lives at <repo>/src/cuga/backend/knowledge/awareness.py, so
    # three ``parents`` hops land us at ``<repo>/src/cuga``.
    instructions_path = (
        Path(__file__).resolve().parents[2] / "configurations" / "knowledge" / "knowledge_instructions.md"
    )
    try:
        text = instructions_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.debug(f"Failed to load knowledge_instructions.md: {exc}")
        return ""
    # The .md template is calibrated for "3 attempts" wording; when the
    # active profile sets a different budget we substitute. Falling back to
    # 3 keeps the prose readable if no value is supplied.
    attempts = max_search_attempts if (max_search_attempts and max_search_attempts > 0) else 3
    return text.replace(_MAX_SEARCH_ATTEMPTS_PLACEHOLDER, str(attempts))


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


def _render_glossary_table(glossary: list[dict] | None) -> str:
    """Render the glossary as a markdown table for prompt injection.

    The table sits inside the same ``<client_adaptation priority="high">``
    block as the freeform text so the LLM treats the canonical terms and
    their aliases as part of the deployment's contract.
    """
    if not glossary:
        return ""
    rows = []
    for e in glossary:
        term = e.get("term", "").strip()
        if not term:
            continue
        aliases = ", ".join(e.get("aliases", []) or []) or "—"
        definition = (e.get("definition", "") or "").strip() or "—"
        rows.append(f"| `{term}` | {aliases} | {definition} |")
    if not rows:
        return ""
    table = (
        "\n## Domain glossary\n\n"
        "When the user query mentions any *Term* or *Alias* below, treat the "
        "*Term* as the canonical token to echo verbatim; aliases are also "
        "expanded into the retrieval query.\n\n"
        "| Term | Aliases | Definition |\n"
        "|---|---|---|\n" + "\n".join(rows)
    )
    return table


def _render_client_adaptation_block(text: str, glossary: list[dict] | None = None) -> str:
    """Render the adaptation as an XML-tagged block.

    - When both ``text`` and ``glossary`` are empty, emit a structural
      sentinel ``<client_adaptation>none</client_adaptation>`` so the prompt
      shape is stable across set/unset (helpful for A/B comparison and for
      the model to recognise the slot exists even when empty).
    - When set, wrap in ``<client_adaptation priority="high">`` with explicit
      precedence framing. XML-delimited spans are weighted higher than
      markdown headers by Claude-family models, and the priority attribute
      gives the LLM a label to reason about.
    """
    body = (text or "").strip()
    table = _render_glossary_table(glossary)
    if not body and not table:
        return "<client_adaptation>none</client_adaptation>"
    # Stronger framing — the prior "These are operator-supplied rules…"
    # opener primed the LLM to treat the block as advisory, especially
    # when an operator rule sounded stylistic (e.g. "praise the user"
    # tone rules were observed being silently dropped in production).
    # The MANDATORY framing + "EVERY response" scope + explicit "apply
    # the rules below" handoff line measurably raises compliance on
    # mid-tier models. CRITICAL-safety carve-out kept verbatim so the
    # contract precedence is unambiguous.
    parts = [
        "<client_adaptation priority=\"high\">",
        "**MANDATORY operator rules for this deployment.** Apply these "
        "rules on EVERY response (especially when you used the knowledge "
        "tools or are answering from retrieved content). They are NOT "
        "stylistic suggestions — they override default phrasing, tone, "
        "and presentation. The only exception: CRITICAL safety rules "
        "above (no-fabrication, search-first, tool-permission) still "
        "win on conflict. Apply the rules below verbatim:",
        "",
    ]
    if body:
        parts.append(body)
    if table:
        parts.append(table)
    parts.append("</client_adaptation>")
    return "\n".join(parts)


async def get_knowledge_summary(
    engine: KnowledgeEngine,
    agent_collection: str | None = None,
    session_collection: str | None = None,
    max_docs: int = 10,
    max_search_attempts: int | None = None,
    default_limit: int | None = None,
    rag_profile: str = "standard",
    client_adaptation_text: str = "",
    client_adaptation_glossary: list[dict] | None = None,
) -> str | None:
    """Build a knowledge summary for injection into the agent's system prompt.

    Returns a formatted markdown string, or None if no documents exist.
    """
    sections: list[str] = []
    has_agent_docs = False
    has_session_docs = False

    # Agent documents (permanent)
    if agent_collection:
        try:
            agent_docs = await engine.list_documents(agent_collection)
            if agent_docs:
                has_agent_docs = True
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
                has_session_docs = True
                lines = [_format_doc_line(d) for d in session_docs[:max_docs]]
                if len(session_docs) > max_docs:
                    lines.append(f"- ... and {len(session_docs) - max_docs} more")
                # The just-in-time pointer is appended INSIDE the section
                # block so it stays visually anchored to the doc list.
                # Tier-3 by Agentic-Expert taxonomy.
                lines.append(
                    "\n⚠️ **The session documents above are LIVE and indexed** "
                    "(retrievable via `knowledge_search_knowledge(scope=\"session\")`). "
                    "If the user's question is plausibly about one of these files, "
                    "search them before answering."
                )
                sections.append("### Session Documents (this conversation only):\n" + "\n".join(lines))
        except Exception as e:
            logger.warning(f"Failed to list session docs for {session_collection}: {e}")

    if not sections:
        return None

    # Doc list = narrow summary by design (perf-branch intent: scope/query/
    # iterative-search/result-reading guidance lives in the single canonical
    # contract block — knowledge_instructions.md). The client_adaptation
    # block is the only exception: it's deployment-specific operator policy,
    # not a generic contract, and it deserves top placement above the doc
    # inventory so the rules sit with the policy framing instead of being
    # diluted by a 1-2KB preview block. When unset, the block degrades to
    # ``<client_adaptation>none</client_adaptation>`` — harmless one-liner.
    adapt_body = (client_adaptation_text or "").strip()
    glossary = client_adaptation_glossary or []
    adapt_block = _render_client_adaptation_block(adapt_body, glossary)
    has_adapt = bool(adapt_body) or bool(glossary)

    summary = f"{adapt_block}\n\n## Your Knowledge Base\n\n" + "\n\n".join(sections)

    # Cross-scope verbalization gate — fires only when BOTH agent AND
    # session have indexed docs. Third-round expert consensus: prior
    # enumeration ("both papers, both policies, both product specs" +
    # "the paper, the policy, this document") was list-overfit, not
    # principle-pattern. Forced verbalization (name the candidate on
    # each side, or assert none) converts an unforced "scan" — which
    # LLMs treat as flavor ~30-40% of the time per CoT literature —
    # into a commit step the model has to emit.
    if has_agent_docs and has_session_docs:
        summary += (
            "\n\n⚠️ **Cross-scope commit: BOTH sides have indexed docs above.** "
            "Before emitting `scope`, name the strongest candidate doc on EACH "
            "side for the user's topic-class (the kind of artifact they're "
            "asking about), or assert \"no competing doc on the other side.\" "
            "If you named a candidate on BOTH sides, `scope` MUST be `\"all\"`. "
            "Missing the better source in the other scope is the failure mode."
        )

    # Observability hash log — never log the text itself (PII / prompt-IP).
    # Hash + length + agent_collection lets SREs correlate complaints to
    # config versions. Both adaptation hashes logged so glossary changes
    # are independently traceable.
    if has_adapt:
        from cuga.backend.knowledge.config import (
            client_adaptation_hash,
            client_glossary_hash,
        )

        logger.info(
            "cuga.knowledge.adaptation_applied",
            extra={
                "cuga_knowledge_adaptation_hash": client_adaptation_hash(adapt_body),
                "cuga_knowledge_adaptation_len": len(adapt_body),
                "cuga_knowledge_glossary_hash": client_glossary_hash(glossary),
                "cuga_knowledge_glossary_entries": len(glossary),
                "cuga_knowledge_agent_collection": agent_collection or "",
                "cuga_knowledge_session_collection": session_collection or "",
            },
        )

    # Inject profile-specific instruction addendum (still owned by the
    # profile, not the canonical contract — profiles can layer custom
    # guidance like "be terse" or "always cite sources" without touching
    # the contract).
    if rag_profile and rag_profile != "standard":
        try:
            from cuga.backend.knowledge.config import load_profile

            profile_data = load_profile(rag_profile)
            addendum = profile_data.get("instructions", {}).get("addendum", "").strip()
            if addendum:
                summary += f"\n{addendum}\n"
        except Exception as e:
            logger.warning(f"Failed to load profile addendum for {rag_profile}: {e}")

    # Recency tail — strong imperative that lands right before the user
    # message in the composed prompt. The prior "Remember to apply…"
    # phrasing read as polite advice and was being silently ignored in
    # production traces. Phrasing this as a pre-response check ("BEFORE
    # you respond, verify…") triggers the model's self-review behavior
    # before output instead of after, which is where it actually matters.
    if has_adapt:
        summary += (
            "\n**BEFORE you respond**, verify you have applied EVERY rule "
            "in the <client_adaptation> block above. Those rules are "
            "mandatory and apply to every response on this deployment — "
            "they are not optional or stylistic.\n"
        )

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


# ----------------------------------------------------------------------
# Single seam for cross-cutting agent code
# ----------------------------------------------------------------------


@dataclass
class AssembledKnowledgePrompt:
    """Result of :func:`assemble_system_prompt_section` — every field the
    agent code needs in one object so cross-cutting consumers stay 1-2
    lines instead of 150.

    Fields:
      ``text``: the rendered system-prompt block to inject (contract +
        doc list + base, ordered for instruction-following). Empty
        string when knowledge is disabled / no docs / no engine.
      ``prompt_hash``: the audit hash, identical across consumer paths
        for byte-identical ``text``.
      ``knowledge_block_chars``: doc-list length (for logging only).
      ``contract_chars``: contract length (for logging only).
      ``has_knowledge``: convenience flag — ``True`` iff the agent
        should consider its knowledge tools live.
    """

    text: str
    prompt_hash: str
    knowledge_block_chars: int
    contract_chars: int
    has_knowledge: bool


async def assemble_system_prompt_section(
    engine: KnowledgeEngine | None,
    agent_id: str | None,
    thread_id: str | None,
    base_instructions: str = "",
    *,
    agent_config_hash: str | None = None,
    search_config: Any | None = None,
) -> AssembledKnowledgePrompt:
    """Assemble the entire knowledge section of a system prompt.

    THE single seam between cuga's agent loops and the knowledge module —
    cross-cutting code calls this and gets back a ready-to-inject
    ``text`` plus the audit ``prompt_hash``. Returning a dataclass (not
    a tuple) means future fields (e.g. metrics dimensions, prompt
    version) extend cleanly without breaking call sites.

    ``search_config`` overrides the engine's config for search-time
    knobs (RAG profile, max_search_attempts) — used by the draft / "Try
    It Out" agent in the manage UI. When ``None`` falls back to
    ``engine._config``.
    """
    if engine is None:
        return AssembledKnowledgePrompt(
            text=base_instructions,
            prompt_hash=prompt_hash(base_instructions),
            knowledge_block_chars=0,
            contract_chars=0,
            has_knowledge=False,
        )

    cfg = search_config if search_config is not None else getattr(engine, "_config", None)
    if cfg is None or not getattr(cfg, "enabled", False):
        return AssembledKnowledgePrompt(
            text=base_instructions,
            prompt_hash=prompt_hash(base_instructions),
            knowledge_block_chars=0,
            contract_chars=0,
            has_knowledge=False,
        )

    # Resolve collections and pull the doc-list summary.
    kb_ctx = format_knowledge_context(
        agent_id,
        thread_id,
        engine=engine,
        agent_config_hash=agent_config_hash,
    )
    knowledge_block = await get_knowledge_summary(
        engine,
        agent_collection=kb_ctx.get("agent_collection"),
        session_collection=kb_ctx.get("session_collection"),
        max_search_attempts=getattr(cfg, "max_search_attempts", None),
        default_limit=getattr(cfg, "default_limit", None),
        rag_profile=getattr(cfg, "rag_profile", "standard"),
        client_adaptation_text=getattr(cfg, "client_adaptation_text", ""),
        client_adaptation_glossary=getattr(cfg, "client_adaptation_glossary", []),
    )
    if not knowledge_block:
        return AssembledKnowledgePrompt(
            text=base_instructions,
            prompt_hash=prompt_hash(base_instructions),
            knowledge_block_chars=0,
            contract_chars=0,
            has_knowledge=False,
        )

    # Load the contract with the configured budget substituted in.
    attempts = getattr(cfg, "max_search_attempts", None)
    contract_text = load_knowledge_instructions(max_search_attempts=attempts)

    composed = compose_knowledge_prompt(contract_text, knowledge_block, base_instructions)
    _hash = prompt_hash(composed)
    logger.info(
        "Knowledge prompt assembled: knowledge_block=%d chars, "
        "contract=%d chars, max_search_attempts=%s, prompt_hash=%s",
        len(knowledge_block),
        len(contract_text),
        attempts or 3,
        _hash,
    )
    return AssembledKnowledgePrompt(
        text=composed,
        prompt_hash=_hash,
        knowledge_block_chars=len(knowledge_block),
        contract_chars=len(contract_text),
        has_knowledge=True,
    )
