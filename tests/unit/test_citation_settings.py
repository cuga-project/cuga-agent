# tests/unit/test_citation_settings.py
import pytest

from cuga.backend.knowledge.awareness import CITATIONS_CONTRACT
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.sources import citations_enabled_for, set_session_override_lookup


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
