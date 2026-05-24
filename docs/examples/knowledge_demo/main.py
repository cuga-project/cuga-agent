"""
Knowledge Engine Demo — HR Benefits Assistant.

Demonstrates the `agent.knowledge.*` SDK surface against a realistic HR corpus:

1. Ingests four **agent-level** documents (employee handbook, health insurance,
   PTO policy, 401(k) plan) that persist across conversations.
2. Asks an agent-level RAG question via `agent.invoke(...)` and prints the answer.
3. Runs a direct `agent.knowledge.search(...)` call to show the raw search API
   (no LLM involved).
4. Ingests three **session-level** documents for a single conversation
   (benefits enrollment, PTO balance, pay stub) and searches them directly via
   the session-scoped client API.

End-to-end session RAG (where the agent itself reasons over session docs) is
demonstrated via `cuga start demo_knowledge` — see `README.md` "Path B".
"""

import os
import sys

# Load `.env` before importing cuga (matches cuga_with_runtime_tools/main.py).
# `.env` must set DYNACONF_KNOWLEDGE__ENABLED=true to override the repo default.
os.environ["ENV_FILE"] = os.path.join(os.path.dirname(__file__), ".env")

import asyncio
from pathlib import Path

from loguru import logger

from cuga import CugaAgent

HERE = Path(__file__).parent
AGENT_DOCS = HERE / "sample_data" / "agent_level"
SESSION_DOCS = HERE / "sample_data" / "session_level"

SESSION_THREAD_ID = "sarah-demo-session"


async def reset_agent_scope(agent: CugaAgent) -> None:
    """Delete every existing agent-level document so re-runs are idempotent."""
    existing = await agent.knowledge.list_documents(scope="agent")
    for doc in existing:
        filename = doc["filename"]
        logger.info(f"Reset: deleting agent doc {filename}")
        await agent.knowledge.delete_document(filename, scope="agent")


async def ingest_all(agent: CugaAgent, folder: Path, scope: str, thread_id: str | None = None) -> None:
    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".md", ".pdf"})
    for path in paths:
        logger.info(f"Ingesting [{scope}] {path.name} ...")
        result = await agent.knowledge.ingest(str(path), scope=scope, thread_id=thread_id)
        status = result.get("status") if isinstance(result, dict) else result
        logger.info(f"  → {path.name}: {status}")


def print_hits(header: str, hits: list) -> None:
    logger.info(header)
    if not hits:
        logger.warning("  (no results)")
        return
    for r in hits[:3]:
        preview = (r.get("text") or "")[:120].replace("\n", " ")
        logger.info(f"  • {r.get('filename')}  score={r.get('score'):.3f}  preview={preview!r}")


async def main() -> None:
    reset = "--reset" in sys.argv[1:]

    logger.info("Building CugaAgent with enable_knowledge=True ...")
    agent = CugaAgent(enable_knowledge=True)

    try:
        # ---------- Agent-level: ingest + agent.invoke() RAG ----------
        if reset:
            await reset_agent_scope(agent)

        logger.info("=== 1. Ingest agent-level documents ===")
        await ingest_all(agent, AGENT_DOCS, scope="agent")

        logger.info("=== 2. Agent-level RAG via agent.invoke() ===")
        question = "What's the PTO carryover limit if I don't use all my days before year-end?"
        logger.info(f"Q: {question}")
        result = await agent.invoke(question)
        logger.info(f"A: {result.answer}")

        logger.info("=== 3. Direct search API (no LLM) ===")
        hits = await agent.knowledge.search("HSA contribution limit family coverage", scope="agent", limit=3)
        print_hits("Top agent-level hits for 'HSA contribution limit family coverage':", hits)

        # ---------- Session-level: direct client API only ----------
        logger.info("=== 4. Ingest session-level documents ===")
        logger.info(f"Session thread_id: {SESSION_THREAD_ID}")
        await ingest_all(agent, SESSION_DOCS, scope="session", thread_id=SESSION_THREAD_ID)

        logger.info("=== 5. Session-scoped search API ===")
        hits = await agent.knowledge.search(
            "current PTO balance and usage this year",
            scope="session",
            limit=3,
            thread_id=SESSION_THREAD_ID,
        )
        print_hits(
            f"Top session-level hits (thread={SESSION_THREAD_ID}) for 'current PTO balance and usage this year':",
            hits,
        )

        logger.info(
            "=== Done. For end-to-end session RAG where the agent reasons over "
            "session docs, run `cuga start demo_knowledge` (see README Path B). ==="
        )
    finally:
        await agent.aclose()


if __name__ == "__main__":
    asyncio.run(main())
