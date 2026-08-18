"""Unit tests for corpus_loss helpers."""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tests.corpus_loss import (
    important_facts,
    input_schema_adds_detail,
    params_doc_from_schema,
    trim_drops_important_facts,
)


@pytest.mark.unit
def test_default_is_an_important_fact_not_in_params_doc():
    schema = {
        "type": "object",
        "properties": {"page": {"type": "integer", "default": 1, "description": "page"}},
    }
    facts = important_facts(schema)
    assert any(f.startswith("default@") and "1" in f for f in facts)
    assert "1" not in params_doc_from_schema(schema)


@pytest.mark.unit
def test_additional_properties_false_is_not_important():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    assert not any("additionalProperties" in f for f in important_facts(schema))


@pytest.mark.unit
def test_additional_properties_schema_is_important():
    schema = {
        "type": "object",
        "properties": {
            "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    facts = important_facts(schema)
    assert any("additionalProperties.schema" in f for f in facts)


@pytest.mark.unit
def test_trim_drops_important_facts_for_default_only():
    schema = {
        "type": "object",
        "properties": {"page": {"type": "integer", "default": 1}},
    }
    assert trim_drops_important_facts(schema) == []
    assert input_schema_adds_detail(schema) is True
