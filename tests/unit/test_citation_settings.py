# tests/unit/test_citation_settings.py
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.knowledge.awareness import CITATIONS_CONTRACT, assemble_system_prompt_section
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.sources import citations_enabled_for, set_session_override_lookup


# ---------------------------------------------------------------------------
# Stub helpers (mirror the harness in test_knowledge_client_adaptation.py)
# ---------------------------------------------------------------------------


@dataclass
class _StubDoc:
    filename: str
    chunk_count: int = 1
    preview: str = ""


def _stub_engine(docs):
    """Minimal engine stub that satisfies assemble_system_prompt_section."""
    engine = SimpleNamespace()
    engine.list_documents = AsyncMock(return_value=docs)
    return engine


def teardown_function():
    set_session_override_lookup(None)


def test_default_is_enabled():
    assert KnowledgeConfig().citations_enabled is True


def test_round_trips_through_to_dict_and_coerce():
    cfg = KnowledgeConfig.coerce_and_validate({"citations_enabled": False})
    assert cfg.citations_enabled is False
    assert cfg.to_dict()["citations_enabled"] is False
    # string coercion parity with other bool fields
    cfg2 = KnowledgeConfig.coerce_and_validate({"citations_enabled": "true"})
    assert cfg2.citations_enabled is True


def test_not_in_vector_config_hash():
    a = KnowledgeConfig(citations_enabled=True)
    b = KnowledgeConfig(citations_enabled=False)
    assert a.vector_config_hash() == b.vector_config_hash()


def test_effective_enablement_prefers_session_override():
    cfg = KnowledgeConfig(citations_enabled=True)
    assert citations_enabled_for(cfg, "t-1") is True
    set_session_override_lookup(lambda tid: {"citations_enabled": False} if tid == "t-1" else {})
    assert citations_enabled_for(cfg, "t-1") is False
    assert citations_enabled_for(cfg, "t-2") is True
    # override can also force ON over an agent-level OFF
    cfg_off = KnowledgeConfig(citations_enabled=False)
    set_session_override_lookup(lambda tid: {"citations_enabled": True})
    assert citations_enabled_for(cfg_off, "t-1") is True
    assert citations_enabled_for(cfg_off, None) is False  # no thread -> agent default


def test_lookup_errors_fall_back_to_config():
    def boom(tid):
        raise RuntimeError("provider down")
    set_session_override_lookup(boom)
    assert citations_enabled_for(KnowledgeConfig(citations_enabled=True), "t") is True


def test_validate_rejects_non_bool():
    cfg = KnowledgeConfig()
    cfg.citations_enabled = "yes"
    with pytest.raises(ValueError, match="citations_enabled"):
        cfg.validate()


def test_lookup_returning_none_falls_back():
    set_session_override_lookup(lambda tid: None)
    assert citations_enabled_for(KnowledgeConfig(citations_enabled=True), "t") is True


def test_string_overrides_coerced_not_truthiness():
    cfg = KnowledgeConfig(citations_enabled=True)
    set_session_override_lookup(lambda tid: {"citations_enabled": "false"})
    assert citations_enabled_for(cfg, "t") is False
    set_session_override_lookup(lambda tid: {"citations_enabled": "on"})
    assert citations_enabled_for(KnowledgeConfig(citations_enabled=False), "t") is True
    # junk values fall back to the agent-level flag
    set_session_override_lookup(lambda tid: {"citations_enabled": "banana"})
    assert citations_enabled_for(cfg, "t") is True
    set_session_override_lookup(lambda tid: {"citations_enabled": 3.14})
    assert citations_enabled_for(KnowledgeConfig(citations_enabled=False), "t") is False


def test_citations_contract_content():
    assert "[s3]" in CITATIONS_CONTRACT
    assert "cite_id" in CITATIONS_CONTRACT
    assert "earlier turns" in CITATIONS_CONTRACT.lower()
    assert "never write bare numeric" in CITATIONS_CONTRACT.lower()


def test_contract_present_iff_enabled():
    """CITATIONS_CONTRACT appears iff citations_enabled=True; legacy
    '## Citing sources' section is suppressed when citations are enabled
    and retained when they are not."""

    def _search_cfg(citations_enabled: bool) -> SimpleNamespace:
        return SimpleNamespace(
            enabled=True,
            citations_enabled=citations_enabled,
            client_adaptation_text="",
            client_adaptation_glossary=[],
            max_search_attempts=3,
            default_limit=10,
            rag_profile="standard",
        )

    engine = _stub_engine([_StubDoc("report.pdf")])

    # --- citations ON ---
    assembled_on = asyncio.run(
        assemble_system_prompt_section(
            engine,
            agent_id="test_agent",
            thread_id=None,
            base_instructions="BASE",
            search_config=_search_cfg(citations_enabled=True),
        )
    )
    assert assembled_on.has_knowledge is True
    assert assembled_on.text.count("## Citations (use [sN] markers") == 1
    assert "## Citing sources" not in assembled_on.text  # legacy section suppressed

    # --- citations OFF ---
    assembled_off = asyncio.run(
        assemble_system_prompt_section(
            engine,
            agent_id="test_agent",
            thread_id=None,
            base_instructions="BASE",
            search_config=_search_cfg(citations_enabled=False),
        )
    )
    assert assembled_off.has_knowledge is True
    assert "## Citations (use [sN] markers" not in assembled_off.text
    assert "## Citing sources" in assembled_off.text  # legacy section retained
