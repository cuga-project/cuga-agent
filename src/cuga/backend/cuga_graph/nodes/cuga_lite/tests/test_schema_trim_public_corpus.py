"""Public OpenAPI and MCP ops must not lose important schema facts when trim omits Input Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tests.corpus_loss import (
    trim_drops_important_facts,
)

_FIXTURE = Path(__file__).parent / "corpus" / "public_ops.json"


@pytest.mark.unit
def test_public_openapi_corpus_size():
    data = json.loads(_FIXTURE.read_text())
    sources = {op["source"] for op in data["openapi"]}
    assert len(sources) >= 12
    assert len(data["mcp"]) >= 10


@pytest.mark.unit
def test_public_openapi_trim_does_not_drop_important_facts():
    data = json.loads(_FIXTURE.read_text())
    failures = []
    for op in data["openapi"] + data["mcp"]:
        dropped = trim_drops_important_facts(op["input_schema"])
        if dropped:
            failures.append(f"{op['id']}: {dropped}")
    assert failures == [], "\n".join(failures)


@pytest.mark.unit
def test_mcp_empty_object_additional_properties_false_may_trim():
    schema = {"type": "object", "additionalProperties": False}
    assert trim_drops_important_facts(schema) == []
