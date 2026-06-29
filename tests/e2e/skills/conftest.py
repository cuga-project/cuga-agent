"""Shared fixtures and helpers for e2e tests.

Provides:
- CaptureChatModel: hermetic mock LLM that records inputs and replays scripted responses
- Helpers: write_skill, extract_system_content
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface


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
    """Write a SKILL.md under root/.cuga/skills/<name>/SKILL.md."""
    skill_dir = root / ".cuga" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    req_line = f"requirements: {requirements}\n" if requirements else ""
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n{req_line}---\n{body}\n",
        encoding="utf-8",
    )
    return skill_file


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


@pytest.fixture
def real_llm():
    """Real LLM model for Tier 3 e2e tests, built from the project's own settings.

    Uses LLMManager with settings.agent.code.model — the same model and credentials
    that cuga uses in production.  The platform, model name, base URL, and API key
    are all read from the AGENT_SETTING_CONFIG toml loaded by config.py (which also
    loads .env from the repo root).  If credentials are absent the test will fail
    at inference time with an auth error, not at collection time.
    """
    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings

    return LLMManager().get_model(settings.agent.code.model)


# ---------------------------------------------------------------------------
# End-of-session summary for Tier 3 LLM e2e tests
# ---------------------------------------------------------------------------


def _find_module(name: str):
    return (
        sys.modules.get(f"tests.e2e.skills.{name}")
        or sys.modules.get(name)
        or next((v for k, v in sys.modules.items() if k.endswith(name)), None)
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    """Print a summary table of all Tier 3 e2e skill test results."""
    results: list[dict] = []
    for mod_name in ("test_skills_llm_e2e", "test_skills_sdk_e2e"):
        mod = _find_module(mod_name)
        if mod:
            results.extend(getattr(mod, "_RESULTS", []))
    if not results:
        return

    width = 84
    sep = "=" * width
    print(f"\n{sep}")
    print("  TIER 3 E2E SKILLS — SUMMARY")
    print(sep)
    print(f"  {'Skill':<36} {'Expected':<16}  {'':6}  Actual (excerpt)")
    print(f"  {'─' * (width - 2)}")
    for r in results:
        skill = r["skill"][:35]
        expected = repr(r["expected"])[:15]
        tag = "NOT in" if r["negative"] else "in    "
        verdict = "PASS" if r["passed"] else "FAIL"
        actual_excerpt = r["actual"][:45].replace("\n", " ")
        print(f"  {skill:<36} {expected:<16}  {tag}  {verdict}  {actual_excerpt}")
    print(sep)
