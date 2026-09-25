"""CUGA knowledge-engine settings this app must pin before `cuga` is imported.

1. Turn the knowledge engine on. CugaAgent(enable_knowledge=True) only adds
   knowledge *tools* to the agent; agent.knowledge.ingest/search still refuse
   to run ("Agent-level knowledge is disabled") unless settings say
   knowledge.enabled = true, and that defaults to false.
2. Pin the store location. CUGA keeps the knowledge base at
   <cwd>/.cuga/knowledge and ignores CugaAgent(cuga_folder=...), so starting
   the app from another directory silently finds an empty store.
3. Honour the embedding model from .env. The active RAG profile (default
   "standard") owns the embedding model and overrides
   DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL, so the English-only
   BAAI/bge-small-en-v1.5 runs whatever .env says. Re-apply it here until
   CUGA lets the env var win.

ingest.py and main.py must both call configure(): the embedding model is part
of the collection name, so a mismatch makes search read an empty collection.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path


def configure(app_dir: Path) -> None:
    os.environ.setdefault("DYNACONF_KNOWLEDGE__ENABLED", "true")
    os.environ.setdefault("DYNACONF_KNOWLEDGE__PERSIST_DIR", str(app_dir / ".cuga" / "knowledge"))

    model = os.environ.get("DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL", "").strip()
    if not model:
        return
    from cuga.backend.knowledge.config import KnowledgeConfig

    original = KnowledgeConfig.from_settings.__func__

    def from_settings(cls, settings):
        return dataclasses.replace(original(cls, settings), embedding_model=model)

    KnowledgeConfig.from_settings = classmethod(from_settings)
