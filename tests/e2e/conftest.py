"""Shared fixtures and helpers for e2e tests.

Provides:
- CaptureChatModel: hermetic mock LLM that records inputs and replays scripted responses
- KnowledgeToolProvider: minimal tool provider that exposes a stub knowledge tool,
  triggering the knowledge-awareness injection path in prepare_tools_and_apps
- knowledge_engine fixture: isolated KnowledgeEngine (fastembed + sqlite-vec, tmp_path)
- Helpers: write_skill, poll_task, extract_system_content
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field, PrivateAttr

from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


# ---------------------------------------------------------------------------
# CaptureChatModel
# ---------------------------------------------------------------------------


class CaptureChatModel(BaseChatModel):
    """Scripted mock chat model for hermetic e2e tests.

    Records every message list it receives and replays a pre-loaded queue of
    AIMessage responses. Raises AssertionError if the graph makes more LLM
    calls than there are scripted responses — surfacing unexpected loops early.

    bind_tools() records the tools it was called with and returns self. This
    works correctly for graphs that call bind_tools once; if a future graph
    ever calls bind_tools in a loop, captured_tools will reflect all calls
    because we extend rather than replace the list on each call.
    """

    captured_inputs: list[list[Any]] = Field(default_factory=list)
    captured_tools: list[Any] = Field(default_factory=list)
    _queue: list[AIMessage] = PrivateAttr(default_factory=list)

    def __init__(self, responses: list[AIMessage] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._queue = list(responses or [AIMessage(content="Done.")])

    @property
    def _llm_type(self) -> str:
        return "capture"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "CaptureChatModel":
        self.captured_tools.extend(list(tools))
        return self

    def _pop_response(self) -> AIMessage:
        assert self._queue, (
            "CaptureChatModel queue exhausted — the graph made more LLM calls than expected. "
            "Pass more scripted responses to CaptureChatModel() or investigate unexpected loops."
        )
        return self._queue.pop(0)

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.captured_inputs.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._pop_response())])

    async def _agenerate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.captured_inputs.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._pop_response())])


# ---------------------------------------------------------------------------
# Tool providers
# ---------------------------------------------------------------------------


class MinimalToolProvider(ToolProviderInterface):
    """No-op tool provider (copied from policy test helpers to avoid cross-import)."""

    async def initialize(self) -> None:
        pass

    async def get_apps(self) -> list:
        return []

    async def get_all_tools(self) -> list:
        return []

    async def get_tools(self, app_name: str) -> list:
        return []




class KnowledgeToolProvider(MinimalToolProvider):
    """Tool provider that exposes a stub 'knowledge_search_knowledge' tool.

    CugaLite's prepare_tools_and_apps detects tools whose names start with
    'knowledge_' in tools_for_execution (cuga_lite_graph.py:1493-1495) and
    activates the knowledge-awareness injection path. This provider makes that
    detection fire without requiring a full MCP server or HTTP routes.

    If the detection predicate changes (e.g., moves to a decorator or type
    annotation), this provider may stop triggering awareness injection —
    update the tool name to match the new predicate.
    """

    async def get_all_tools(self) -> list:
        @tool
        def knowledge_search_knowledge(query: str) -> str:
            """Search the knowledge base for relevant documents."""
            return "[]"

        return [knowledge_search_knowledge]


class RealSearchKnowledgeToolProvider(MinimalToolProvider):
    """Tool provider with a real knowledge_search_knowledge tool backed by KnowledgeEngine.

    Unlike KnowledgeToolProvider (stub that always returns '[]'), this provider calls
    engine.search() so the RAG retrieval path — ingest → search → return chunks — can
    be tested at the tool boundary without a running MCP server or HTTP routes.

    The tool name intentionally matches the detection predicate in cuga_lite_graph.py
    (startswith 'knowledge_') so it also activates the awareness injection path when
    used in Tier 2 graph tests.
    """

    def __init__(self, engine: KnowledgeEngine, collection: str) -> None:
        self._engine = engine
        self._collection = collection

    async def get_all_tools(self) -> list:
        engine = self._engine
        collection = self._collection

        @tool
        async def knowledge_search_knowledge(query: str) -> str:
            """Search the knowledge base for relevant documents."""
            results = await engine.search(collection, query, limit=5)
            return "\n".join(r.text for r in results) if results else "[]"

        return [knowledge_search_knowledge]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_skill(
    root: Path,
    name: str,
    description: str,
    body: str,
    requirements: str = "",
) -> Path:
    """Write a SKILL.md under root/.agents/skills/<name>/SKILL.md."""
    skill_dir = root / ".agents" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    req_line = f"requirements: {requirements}\n" if requirements else ""
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n{req_line}---\n{body}\n",
        encoding="utf-8",
    )
    return skill_file


async def poll_task(engine: KnowledgeEngine, task_id: str, max_iters: int = 60) -> dict:
    """Poll an ingest task until it completes or times out."""
    for _ in range(max_iters):
        task = await engine.get_task(task_id)
        if task and task.get("status") in ("completed", "failed", "error"):
            return task
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Task {task_id} did not complete within {max_iters * 0.5}s")


def extract_system_content(messages: list) -> str:
    """Return the content of the first system message from a LangChain message list."""
    for msg in messages:
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role == "system":
            return msg.content or ""
        if isinstance(msg, dict) and msg.get("role") == "system":
            return msg.get("content", "")
    return ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def knowledge_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeEngine:
    """Isolated KnowledgeEngine backed by fastembed + sqlite-vec in a temp dir."""
    isolated_db = str(tmp_path / "cuga_storage.db")
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.get_storage_connection_params",
        lambda: ("local", isolated_db, ""),
    )
    config = KnowledgeConfig(
        enabled=True,
        persist_dir=tmp_path / "knowledge",
        embedding_provider="fastembed",
        embedding_model="",
        chunk_size=200,
        chunk_overlap=50,
        max_ingest_workers=1,
        max_pending_tasks=5,
    )
    engine = KnowledgeEngine(config)
    await engine.warmup()
    yield engine
    await engine.aclose()
    engine.shutdown()
