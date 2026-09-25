"""The handbook search itself — the Farm Assistant team's retrieval, in one place.

Their ``main.py`` searched and formatted the passages inline. That code is below, unchanged. Three
ways to reach it, all sharing this function:

  1. IN-PROCESS (what the example uses): don't run this file at all — bind the events layer's
     ``retrieval`` capability to ``provider: cuga_knowledge`` pointed at the same folder. Fewest moving
     parts, and closest to how their app called it.
  2. AS A SERVICE: ``python handbook_service.py`` → POST /v1/search, for when the index lives on
     another box or their team owns it. Bind ``provider: http``.
  3. AS A TOOL: ``python handbook_mcp.py`` → an MCP tool, for when the AGENT should decide whether to
     search (only worth it once there are other sources to choose between).

Build the index first with ``python ingest.py <pdf>``; the embedding model must match at search time.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_DIR = Path(__file__).parent
load_dotenv(_DIR / ".env")
KB_DIR = Path(os.environ.get("FARM_KB_DIR") or _DIR)

sys.path.insert(0, str(_DIR))
import cuga_compat  # noqa: E402  — must run BEFORE `from cuga import …` (it sets env)

cuga_compat.configure(KB_DIR)

from cuga import CugaAgent  # noqa: E402

_agent = None


def _get_agent():
    """Their lazy singleton, with the review's fix: no agent tools (enable_knowledge=False);
    ``agent.knowledge.search`` works either way."""
    global _agent
    if _agent is None:
        _agent = CugaAgent(
            enable_knowledge=False, cuga_folder=str(KB_DIR / ".cuga"), auto_load_policies=False
        )
    return _agent


async def search_passages(question: str, limit: int = 5) -> dict:
    """Top passages for a question, in any language, as ``{context, sources}``."""
    results = await _get_agent().knowledge.search(question)

    # ── from their main.py, unchanged ──
    sources = []
    if results:
        top_score = max((r.get("score") or 0) for r in results[:limit]) or 1
        for r in results[:limit]:
            raw = r.get("score") or 0
            sources.append({"page": r.get("page", "Unknown"), "score": round((raw / top_score) * 100)})

    context = "\n\n".join(f"[Page {r.get('page', 'Unknown')}]: {r.get('text', '')}" for r in results[:limit])
    # ──
    return {"context": context, "sources": sources}
