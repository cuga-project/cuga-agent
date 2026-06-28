"""Knowledge SDK client.

Provides a clean Python API for knowledge operations.
Endorsed usage: agent.knowledge.search("query", scope="session")
"""

from __future__ import annotations

import re
from typing import Any

from cuga.backend.knowledge.engine import KnowledgeEngine


class KnowledgeClient:
    """SDK client for knowledge operations."""

    def __init__(
        self,
        engine: KnowledgeEngine,
        default_agent_id: str = "default",
        agent_collection_hash: str | None = None,
    ):
        self._engine = engine
        self._default_agent_id = default_agent_id
        self._agent_collection_hash = agent_collection_hash

    @staticmethod
    def scope_context(
        engine: Any | None,
        thread_id: str | None,
    ) -> tuple[tuple[str, ...], str | None]:
        """Compute ``(allowed_scopes, default_scope)`` for a given run.

        Static (takes engine explicitly) so cross-cutting code can call
        without instantiating a client just to ask "what scopes are
        wired for this turn?". Both cuga_lite_graph and chat_agent need
        this same answer; centralizing here means a future scope change
        (e.g. adding ``"project"``) updates one place instead of three.
        """
        config = getattr(engine, "_config", None) if engine else None
        if not config or not getattr(config, "enabled", False):
            return (), None

        scopes: list[str] = []
        if getattr(config, "agent_level_enabled", True):
            scopes.append("agent")
        if getattr(config, "session_level_enabled", True) and thread_id:
            scopes.append("session")

        if "agent" in scopes and "session" in scopes:
            # Both available → expose "all" + both narrow scopes. Default
            # to "session" (narrowest plausible scope) — uploaded docs are
            # almost always topical, and the engine auto-fallbacks
            # session→all on 0 hits so the default is risk-free.
            # Matches client.py:get_langchain_tools which defaults the
            # search tool to "session" for the same reason.
            return ("all", "agent", "session"), "session"
        if scopes:
            return tuple(scopes), scopes[0]
        return (), None

    @staticmethod
    def scope_instruction(allowed_scopes: tuple[str, ...], thread_id: str | None) -> str:
        """One-line per-run scope gate.

        The FULL scope/query/result rules live in the canonical Knowledge
        Tool Contract (``knowledge_instructions.md``); this only tells
        the LLM **which scopes are wired this turn** so it can't ask for
        one that isn't there. Kept terse to avoid drift with the contract.
        """
        if allowed_scopes == ("agent",):
            return (
                "Knowledge scopes available this run: agent only. "
                "Never call knowledge tools with `scope=\"session\"` or `scope=\"all\"`."
            )
        if allowed_scopes == ("session",):
            return (
                "Knowledge scopes available this run: session only. "
                "Never call knowledge tools with `scope=\"agent\"` or `scope=\"all\"`. "
                "The thread context is injected automatically."
            )
        if "all" in allowed_scopes:
            return (
                "Knowledge scopes available this run: agent + session. "
                "Pick the NARROWEST scope that could plausibly contain the "
                "answer. If a doc in your '### Session Documents (this "
                "conversation only):' list looks topical (filename or "
                "preview matches the query), use `scope=\"session\"`. "
                "Otherwise use `scope=\"agent\"`. Use `scope=\"all\"` only "
                "when you genuinely can't tell which side has it. If "
                "session returns nothing, the engine auto-fallbacks to "
                "'all' for you — do NOT retry yourself."
            )
        if thread_id:
            return "Knowledge tools are unavailable in this run."
        return (
            "Knowledge tools are unavailable in this run "
            "(no conversation thread, session scope cannot be used)."
        )

    def allowed_scopes(self) -> tuple[str, ...]:
        """Return the scope tokens this client accepts.

        Includes the synthetic ``"all"`` scope when both agent and session
        are enabled — mirrors the HTTP route's contract so SDK and HTTP
        callers see the same surface. ``"all"`` is only meaningful for
        ``search``; ingest/list/delete still require an explicit scope.
        """
        config = getattr(self._engine, "_config", None)
        if not config or not getattr(config, "enabled", False):
            return ()

        scopes: list[str] = []
        if getattr(config, "agent_level_enabled", True):
            scopes.append("agent")
        if getattr(config, "session_level_enabled", True):
            scopes.append("session")
        if "agent" in scopes and "session" in scopes:
            # Expose "all" as a first-class scope when both are usable.
            scopes.append("all")
        return tuple(scopes)

    def _require_scope_enabled(self, scope: str) -> None:
        allowed_scopes = self.allowed_scopes()
        if scope not in allowed_scopes:
            if scope == "session":
                raise ValueError("Session-level knowledge is disabled for this agent")
            if scope == "all":
                raise ValueError("scope='all' requires both agent-level and session-level knowledge enabled")
            raise ValueError("Agent-level knowledge is disabled for this agent")

    @staticmethod
    def _sanitize(v: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]", "_", v)

    def _agent_collection(self) -> str:
        base = f"kb_agent_{self._sanitize(self._default_agent_id)}"
        return f"{base}_{self._agent_collection_hash}" if self._agent_collection_hash else base

    def _resolve_collection(self, scope: str, thread_id: str | None = None) -> str:
        """Resolve a SINGLE collection. Not valid for scope='all' — callers
        of ``search()`` go through ``_scoped_collections_for_all`` instead.
        """
        self._require_scope_enabled(scope)
        if scope == "all":
            raise ValueError("_resolve_collection cannot resolve scope='all' — use search() directly")
        if scope == "session":
            # Strip before truthiness + before sanitize so the SDK mirrors
            # the HTTP route's canonicalization in auth.resolve_collection.
            # Without this, " abc" passes the truthiness check but routes
            # to ``kb_sess__abc`` while the HTTP path uses ``kb_sess_abc``
            # — same logical session, two physical collections. Closes
            # CodeRabbit M1.
            canonical = (thread_id or "").strip()
            if not canonical:
                raise ValueError("thread_id required for session scope")
            return f"kb_sess_{self._sanitize(canonical)}"
        return self._agent_collection()

    def _scoped_collections_for_all(self, thread_id: str | None) -> list[tuple[str, str]]:
        """Build the ``[(scope, collection), ...]`` list for scope='all'.

        Silently drops scopes that are disabled or that the caller can't
        use (e.g. session without thread_id) — same gentle semantics as the
        HTTP route. Returning an empty list yields an empty result, not an
        error.
        """
        allowed = self.allowed_scopes()
        out: list[tuple[str, str]] = []
        if "agent" in allowed:
            out.append(("agent", self._agent_collection()))
        # Strip BEFORE truthiness + before sanitize — same canonicalization
        # rule as ``_resolve_collection`` (CodeRabbit M1).
        canonical_thread_id = (thread_id or "").strip()
        if "session" in allowed and canonical_thread_id:
            out.append(("session", f"kb_sess_{self._sanitize(canonical_thread_id)}"))
        return out

    async def search(
        self,
        query: str,
        scope: str = "agent",
        limit: int = 10,
        score_threshold: float = 0.0,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search documents in the knowledge base — flat list of result
        dicts (back-compat). For the full envelope (``filtered_count``,
        ``partial``, ``scope``, etc.) call :meth:`search_envelope`.
        """
        envelope = await self.search_envelope(
            query=query,
            scope=scope,
            limit=limit,
            score_threshold=score_threshold,
            thread_id=thread_id,
        )
        return envelope["results"]

    async def search_envelope(
        self,
        query: str,
        scope: str = "agent",
        limit: int = 10,
        score_threshold: float = 0.0,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Search with the full HTTP-route response shape — SDK consumers
        get the same envelope as MCP / HTTP callers.

        Envelope shape lives in
        :func:`cuga.backend.knowledge.envelope.build_retrieval_envelope`.
        Both this SDK method and the HTTP ``/search`` route call that
        helper so the two surfaces can't drift. Includes the
        ``retrieval`` block (per-scope candidates / filtered /
        reasons / partial / fallback_from / recommendation).

        Session→all auto-fallback: when ``scope='session'`` returns 0
        results above threshold AND agent is wired, the engine
        internally retries as ``scope='all'`` and the envelope carries
        ``retrieval.fallback_from = "session"``. Same behavior as the
        HTTP route.

        Wire-shape note: SDK callers always get ``include_scores=True``
        in the returned chunks. HTTP callers default to
        ``include_scores=False`` (toggled per-request via the body
        param). The asymmetry is intentional — SDK consumers are
        usually the in-process LangChain tool wrapper which the LLM
        reads, and the score field is load-bearing for the
        ``recommendation`` LLM cue. HTTP consumers are LLMs reading
        across a wire boundary and typically don't need scores; making
        them opt-in keeps responses tight.
        """
        from cuga.backend.knowledge.envelope import build_retrieval_envelope

        filter_mode = getattr(self._engine._config, "search_junk_filter", "dry_run")

        async def _run_multi(scope_requested: str, fallback_from: str | None) -> dict[str, Any]:
            scoped_collections = self._scoped_collections_for_all(thread_id)
            results, multi_stats = await self._engine.search_multi(
                scoped_collections=scoped_collections,
                query=query,
                limit=limit,
                score_threshold=score_threshold,
            )
            return build_retrieval_envelope(
                results=results,
                scope_requested=scope_requested,
                multi_stats=multi_stats,
                single_stats=None,
                single_scope_name=None,
                filter_mode=filter_mode,
                fallback_from=fallback_from,
                include_scores=True,
            )

        if scope == "all":
            self._require_scope_enabled("all")
            return await _run_multi(scope_requested="all", fallback_from=None)

        collection = self._resolve_collection(scope, thread_id)
        results, single_stats = await self._engine.search_with_stats(
            collection=collection,
            query=query,
            limit=limit,
            score_threshold=score_threshold,
            scope=scope,
        )
        for r in results:
            if not r.scope:
                r.scope = scope

        # Session→all auto-fallback. Symmetric with the HTTP route's
        # behavior so SDK consumers get the same safety net. Log at INFO
        # parity with the route so SREs see fallback events regardless of
        # whether traffic came in via HTTP or the in-process SDK path.
        if scope == "session" and not results:
            allowed = self.allowed_scopes()
            agent_available = "agent" in allowed
            if agent_available:
                import logging as _stdlib_logging

                _stdlib_logging.getLogger("cuga.knowledge").info(
                    "search session via SDK: 0 hits → auto-fallback to scope='all' (thread_id=%s query=%r)",
                    (thread_id or "")[:12] + "..." if thread_id else "-",
                    (query or "")[:60],
                )
                return await _run_multi(scope_requested="all", fallback_from="session")

        return build_retrieval_envelope(
            results=results,
            scope_requested=scope,
            multi_stats=None,
            single_stats=single_stats,
            single_scope_name=scope,
            filter_mode=filter_mode,
            fallback_from=None,
            include_scores=True,
        )

    async def ingest(
        self,
        file_path: str,
        scope: str = "agent",
        replace_duplicates: bool = True,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a document file."""
        from pathlib import Path

        collection = self._resolve_collection(scope, thread_id)
        return await self._engine.ingest(collection, Path(file_path), replace_duplicates)

    async def ingest_url(
        self,
        url: str,
        scope: str = "agent",
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a document from URL."""
        collection = self._resolve_collection(scope, thread_id)
        return await self._engine.ingest_url(collection, url)

    async def list_documents(
        self,
        scope: str = "agent",
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List documents in the knowledge base."""
        collection = self._resolve_collection(scope, thread_id)
        docs = await self._engine.list_documents(collection)
        return [
            {
                "filename": d.filename,
                "chunk_count": d.chunk_count,
                "status": d.status,
                "ingested_at": d.ingested_at,
                "preview": d.preview,
            }
            for d in docs
        ]

    async def delete_document(
        self,
        filename: str,
        scope: str = "agent",
        thread_id: str | None = None,
    ) -> dict[str, str]:
        """Delete a document by filename."""
        collection = self._resolve_collection(scope, thread_id)
        await self._engine.delete_document(collection, filename)
        return {"status": "ok"}

    def get_settings(self) -> dict[str, Any]:
        """Get knowledge settings."""
        return self._engine.get_settings()

    def update_settings(self, **kwargs) -> dict[str, Any]:
        """Update knowledge settings."""
        return self._engine.update_settings(**kwargs)

    def get_langchain_tools(self, thread_id: str | None = None) -> list:
        """Create LangChain StructuredTool wrappers for knowledge operations.

        These tools can be passed to CugaAgent or added to a tool provider,
        making knowledge operations available in the agent's code sandbox.

        Args:
            thread_id: Optional thread ID for session-scoped operations.
        """
        from langchain_core.tools import StructuredTool

        client = self
        _thread_id = thread_id
        _default_limit = client._engine._config.default_limit
        _default_threshold = client._engine._config.default_score_threshold
        # ``allowed_scopes()`` includes "all" when both agent+session are
        # enabled — but "all" requires session, which requires a thread_id.
        # Without one, drop both "session" and "all".
        allowed_scopes = client.allowed_scopes()
        if "session" in allowed_scopes and not _thread_id:
            allowed_scopes = tuple(scope for scope in allowed_scopes if scope not in ("session", "all"))
        if not allowed_scopes:
            return []

        # Default the search tool to the NARROWEST scope available.
        # Prefer "session" when wired (matches the awareness signal — if
        # the user uploaded a doc in this conversation, it's almost
        # always topical). Fall back to "agent" otherwise. We deliberately
        # avoid defaulting to "all" because "all" was the noisiest call
        # in production — a thin session collection got drowned by 15
        # noise chunks from a fat agent collection, and the LLM picked
        # "all" because the contract said default to it. The engine
        # provides session→all auto-fallback so this default is safe.
        # Non-search tools (ingest/list/delete) still need an explicit
        # single concrete scope — that's handled separately below.
        search_default_scope = (
            "session"
            if "session" in allowed_scopes
            else "agent"
            if "agent" in allowed_scopes
            else allowed_scopes[0]
        )
        # For mutation tools (ingest/list/delete) "all" is invalid —
        # ``resolve_collection`` would 400. Filter it out so the SDK never
        # tempts the LLM into a guaranteed-to-fail call.
        _mutation_scopes = tuple(s for s in allowed_scopes if s != "all")
        single_default_scope = (
            "agent"
            if "agent" in _mutation_scopes
            else _mutation_scopes[0]
            if _mutation_scopes
            else allowed_scopes[0]
        )
        # Decision rules live at the point of decision (tool-call frame)
        # AND in the Knowledge Tool Contract. Duplication is intentional:
        # the docstring is read at the moment the LLM fills in `scope=`,
        # and ~80 tokens here beats a contract paragraph 6,000 tokens
        # upstream. Updated when the contract's Scope section is updated.
        scope_help_search = (
            'Pick narrowest scope. "session" if a Session Documents entry '
            'matches the query (filename or preview); "agent" for '
            'institutional/policy/permanent questions; "all" only when '
            "you can't tell which side has it. Session→all auto-fallback "
            'is automatic — do NOT retry yourself. See the Knowledge Tool '
            'Contract for worked examples.'
        )
        scope_help_single = (
            'Only use `scope="agent"`.'
            if _mutation_scopes == ("agent",)
            else 'Only use `scope="session"`.'
            if _mutation_scopes == ("session",)
            else (
                'Pass `scope="agent"` for permanent documents or `scope="session"` '
                'for documents in this conversation. `scope="all"` is NOT valid here — '
                'this tool operates on one collection at a time.'
            )
        )

        # NOTE: **_ absorbs extra kwargs (e.g. `thread_id`) injected by the
        # cuga_lite runner's knowledge-tool wrapper. StructuredTool.from_function
        # ignores **kwargs when building the JSON schema, so the LLM cannot set
        # these fields — they are only available to internal callers.
        async def knowledge_search_knowledge(
            query: str,
            scope: str = search_default_scope,
            **_: Any,
        ) -> dict:
            # Use the full envelope so the tool surfaces the new
            # ``retrieval`` block (per-scope candidates / filtered /
            # reasons / fallback_from / recommendation) to the LLM in
            # the same shape as the HTTP route. Building a partial
            # payload here would re-introduce the SDK-vs-route drift
            # that envelope.py exists to prevent.
            return await client.search_envelope(
                query,
                scope,
                _default_limit,
                _default_threshold,
                thread_id=_thread_id,
            )

        async def knowledge_ingest_knowledge(
            file_path: str,
            scope: str = single_default_scope,
            replace_duplicates: bool = True,
            **_: Any,
        ) -> dict:
            return await client.ingest(file_path, scope, replace_duplicates, thread_id=_thread_id)

        async def knowledge_ingest_knowledge_url(
            url: str, scope: str = single_default_scope, **_: Any
        ) -> dict:
            return await client.ingest_url(url, scope, thread_id=_thread_id)

        async def knowledge_list_knowledge_documents(scope: str = single_default_scope, **_: Any) -> dict:
            docs = await client.list_documents(scope, thread_id=_thread_id)
            return {"documents": docs}

        async def knowledge_delete_knowledge_document(
            filename: str,
            scope: str = single_default_scope,
            **_: Any,
        ) -> dict:
            return await client.delete_document(filename, scope, thread_id=_thread_id)

        async def knowledge_get_ingestion_status(task_id: str, **_: Any) -> dict:
            """Check the status of a document ingestion task.

            Returns progress information including per-file status.
            """
            task = await client._engine.get_task(task_id)
            return task or {"error": "task not found"}

        async def knowledge_get_knowledge_status(**_: Any) -> dict:
            """Check if the knowledge service is healthy and get current settings.

            Returns health status and configuration details.
            """
            health = await client._engine.health(collection=None)
            settings = client.get_settings()
            return {"healthy": health.get("status") == "healthy", "settings": settings}

        knowledge_search_knowledge.__doc__ = (
            "Search documents in the knowledge base.\n\n"
            f"{scope_help_search}\n"
            "Returns `{scope, results: [...]}` — each result has `source` (\"agent\" or "
            "\"session\"), `text`, `filename`, `page`, `score`. When `scope=\"all\"` the "
            "response also includes `scope_legend` explaining what each source means."
        )
        knowledge_ingest_knowledge.__doc__ = (
            "Upload a document file to the knowledge base.\n\n"
            f"Supports PDF, DOCX, XLSX, PPTX, HTML, Markdown, images, and more. {scope_help_single}"
        )
        knowledge_ingest_knowledge_url.__doc__ = (
            f"Ingest a document from a URL into the knowledge base.\n\n{scope_help_single}"
        )
        knowledge_list_knowledge_documents.__doc__ = (
            f"List all documents in the knowledge base.\n\n{scope_help_single}"
        )
        knowledge_delete_knowledge_document.__doc__ = (
            f"Delete a document from the knowledge base by filename.\n\n{scope_help_single}"
        )

        tools = [
            StructuredTool.from_function(
                coroutine=knowledge_search_knowledge,
                name="knowledge_search_knowledge",
                description=knowledge_search_knowledge.__doc__,
            ),
            StructuredTool.from_function(
                coroutine=knowledge_ingest_knowledge,
                name="knowledge_ingest_knowledge",
                description=knowledge_ingest_knowledge.__doc__,
            ),
            StructuredTool.from_function(
                coroutine=knowledge_ingest_knowledge_url,
                name="knowledge_ingest_knowledge_url",
                description=knowledge_ingest_knowledge_url.__doc__,
            ),
            StructuredTool.from_function(
                coroutine=knowledge_list_knowledge_documents,
                name="knowledge_list_knowledge_documents",
                description=knowledge_list_knowledge_documents.__doc__,
            ),
            StructuredTool.from_function(
                coroutine=knowledge_delete_knowledge_document,
                name="knowledge_delete_knowledge_document",
                description=knowledge_delete_knowledge_document.__doc__,
            ),
            StructuredTool.from_function(
                coroutine=knowledge_get_ingestion_status,
                name="knowledge_get_ingestion_status",
                description=knowledge_get_ingestion_status.__doc__,
            ),
            StructuredTool.from_function(
                coroutine=knowledge_get_knowledge_status,
                name="knowledge_get_knowledge_status",
                description=knowledge_get_knowledge_status.__doc__,
            ),
        ]
        return tools

    async def close(self) -> None:
        """Shutdown the knowledge engine."""
        if self._engine:
            await self._engine.aclose()
            self._engine.shutdown()
            self._engine = None
