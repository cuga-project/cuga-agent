"""AppWorld OpenAPI ops must not lose important schema facts when trim omits Input Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tests.corpus_loss import (
    facts_visible_in_params_doc,
    important_facts,
    params_doc_from_schema,
    render_with_schema,
    trim_drops_important_facts,
)

_FIXTURE = Path(__file__).parent / "corpus" / "appworld_ops.json"

pytestmark = pytest.mark.skipif(
    not _FIXTURE.is_file(),
    reason="AppWorld corpus is not vendored; regenerate with extract_openapi_operations",
)


@pytest.mark.unit
def test_appworld_fixture_covers_all_ops():
    data = json.loads(_FIXTURE.read_text())
    ops = data["operations"]
    assert len(ops) == 473
    sources = {op["source"].removesuffix(".json") for op in ops}
    assert "amazon" in sources and "gmail" in sources and "todoist" in sources


@pytest.mark.unit
def test_appworld_trim_does_not_drop_important_facts():
    ops = json.loads(_FIXTURE.read_text())["operations"]
    failures = []
    for op in ops:
        schema = op["input_schema"]
        dropped = trim_drops_important_facts(schema)
        if dropped:
            failures.append(f"{op['id']}: {dropped}")
    assert failures == [], "trim hid important facts:\n" + "\n".join(failures[:40])


@pytest.mark.unit
def test_appworld_render_keeps_input_schema_when_facts_would_be_lost():
    ops = json.loads(_FIXTURE.read_text())["operations"]
    checked = 0
    for op in ops:
        schema = op["input_schema"]
        params = params_doc_from_schema(schema)
        facts = important_facts(schema)
        if not (facts - facts_visible_in_params_doc(facts, params)):
            continue
        checked += 1
        md = render_with_schema(op["operation_id"], schema, params)
        assert "**Input Schema:**" in md, op["id"]
    assert checked > 0
