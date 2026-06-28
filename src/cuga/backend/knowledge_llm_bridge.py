"""Host bridge: adapt cuga's LLM to the knowledge package's ChatGenerator Protocol.

Lives OUTSIDE ``knowledge/`` so the knowledge package stays standalone (it never
imports cuga internals — it only receives a duck-typed ``generate(prompt) -> str``).
Lazy: the chat model is built on first use, so KnowledgeEngine can be constructed
before cuga's LLM is ready.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CugaChatGenerator:
    """ChatGenerator over cuga's configured agent LLM (the same model that powers
    the code agent). Implements ``async generate(prompt) -> str`` for the
    knowledge engine's query-transform (multi_query / HyDE)."""

    def __init__(self) -> None:
        self._model = None

    async def generate(self, prompt: str) -> str:
        if self._model is None:
            from cuga.backend.llm.models import LLMManager
            from cuga.config import settings

            self._model = LLMManager().get_model(settings.agent.code.model)
        resp = await self._model.ainvoke(prompt)
        content = getattr(resp, "content", resp)
        return content if isinstance(content, str) else str(content)
