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

    def allowed_scopes(self) -> tuple[str, ...]:
        config = getattr(self._engine, "_config", None)
        if not config or not getattr(config, "enabled", False):
            return ()

        scopes: list[str] = []
        if getattr(config, "agent_level_enabled", True):
            scopes.append("agent")
        if getattr(config, "session_level_enabled", True):
            scopes.append("session")
        return tuple(scopes)

    def _require_scope_enabled(self, scope: str) -> None:
        allowed_scopes = self.allowed_scopes()
        if scope not in allowed_scopes:
            if scope == "session":
                raise ValueError("Session-level knowledge is disabled for this agent")
            raise ValueError("Agent-level knowledge is disabled for this agent")

    def _resolve_collection(self, scope: str, thread_id: str | None = None) -> str:
        self._require_scope_enabled(scope)

        def sanitize(v: str) -> str:
            return re.sub(r"[^a-zA-Z0-9_]", "_", v)

        if scope == "session":
            if not thread_id:
                raise ValueError("thread_id required for session scope")
            return f"kb_sess_{sanitize(thread_id)}"
        base = f"kb_agent_{sanitize(self._default_agent_id)}"
        return f"{base}_{self._agent_collection_hash}" if self._agent_collection_hash else base

    async def search(
        self,
        query: str,
        scope: str = "agent",
        limit: int = 10,
        score_threshold: float = 0.0,
        thread_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search documents in the knowledge base."""
        collection = self._resolve_collection(scope, thread_id)
        results = await self._engine.search(collection, query, limit, score_threshold)
        return [{"text": r.text, "filename": r.filename, "page": r.page, "score": r.score} for r in results]

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
        allowed_scopes = client.allowed_scopes()
        if "session" in allowed_scopes and not _thread_id:
            allowed_scopes = tuple(scope for scope in allowed_scopes if scope != "session")
        if not allowed_scopes:
            return []
        default_scope = "agent" if "agent" in allowed_scopes else allowed_scopes[0]
        scope_help = (
            'Only use `scope="agent"`.'
            if allowed_scopes == ("agent",)
            else 'Only use `scope="session"`.'
            if allowed_scopes == ("session",)
            else 'Use `scope="agent"` for permanent documents and `scope="session"` for conversation documents.'
        )

        async def knowledge_search_knowledge(
            query: str,
            scope: str = default_scope,
        ) -> dict:
            results = await client.search(
                query, scope, _default_limit, _default_threshold, thread_id=_thread_id
            )
            return {"results": results}

        async def knowledge_ingest_knowledge(
            file_path: str,
            scope: str = default_scope,
            replace_duplicates: bool = True,
        ) -> dict:
            return await client.ingest(file_path, scope, replace_duplicates, thread_id=_thread_id)

        async def knowledge_ingest_knowledge_url(url: str, scope: str = default_scope) -> dict:
            return await client.ingest_url(url, scope, thread_id=_thread_id)

        async def knowledge_list_knowledge_documents(scope: str = default_scope) -> dict:
            docs = await client.list_documents(scope, thread_id=_thread_id)
            return {"documents": docs}

        async def knowledge_delete_knowledge_document(
            filename: str,
            scope: str = default_scope,
        ) -> dict:
            return await client.delete_document(filename, scope, thread_id=_thread_id)

        async def knowledge_get_ingestion_status(task_id: str) -> dict:
            """Check the status of a document ingestion task.

            Returns progress information including per-file status.
            """
            task = await client._engine.get_task(task_id)
            return task or {"error": "task not found"}

        async def knowledge_get_knowledge_status() -> dict:
            """Check if the knowledge service is healthy and get current settings.

            Returns health status and configuration details.
            """
            health = await client._engine.health(collection=None)
            settings = client.get_settings()
            return {"healthy": health.get("status") == "healthy", "settings": settings}

        knowledge_search_knowledge.__doc__ = (
            "Search documents in the knowledge base.\n\n"
            f"Search only in enabled knowledge scopes. {scope_help}\n"
            "Returns results with text, filename, and page number."
        )
        knowledge_ingest_knowledge.__doc__ = (
            "Upload a document file to the knowledge base.\n\n"
            f"Supports PDF, DOCX, XLSX, PPTX, HTML, Markdown, images, and more. {scope_help}"
        )
        knowledge_ingest_knowledge_url.__doc__ = (
            f"Ingest a document from a URL into the knowledge base.\n\n{scope_help}"
        )
        knowledge_list_knowledge_documents.__doc__ = (
            f"List all documents in the knowledge base.\n\n{scope_help}"
        )
        knowledge_delete_knowledge_document.__doc__ = (
            f"Delete a document from the knowledge base by filename.\n\n{scope_help}"
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
