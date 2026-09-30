"""Compact signatures and minified wire schemas (fc-deferred-tool-search §3.10)."""

from __future__ import annotations

import json

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.minify import (
    minified_tools,
    minify_openai_tool,
    minify_parameters,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.signature_render import (
    first_sentence,
    render_shape,
    render_signature,
    render_type,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict
from cuga.backend.tools_env.registry.utils.schema_model import build_input_model

pytestmark = pytest.mark.unit

_GAMES = {
    "description": (
        "Retrieve the list of games played in a given hockey season. Results are ordered by date. "
        "Supports pagination."
    ),
    "parameters": [
        {
            "name": "season",
            "type": "integer",
            "required": True,
            "description": "Season year.",
            "constraints": [">= 1917"],
        },
        {
            "name": "order",
            "type": "string",
            "required": False,
            "default": "desc",
            "constraints": ["must be one of: [asc, desc]"],
        },
        {
            "name": "limit",
            "type": "integer",
            "required": False,
            "default": 50,
            "constraints": [">= 1", "<= 100"],
        },
        {"name": "tags", "type": "array", "required": False, "schema": {"type": "array", "items": "string"}},
    ],
    "response_schemas": {
        "success": [{"game_id": "integer", "home": {"team_id": "integer", "score": "integer"}}]
    },
}


def _games_tool():
    return create_tool_from_api_dict("hockey_get_games", _GAMES, "hockey")


# ── signatures ───────────────────────────────────────────────────────────────


def test_signature_golden():
    assert render_signature(_games_tool()) == (
        'hockey_get_games(season: int (1917..), order?: "asc" | "desc" = "desc", '
        "limit?: int (1..100) = 50, tags?: string[]) -> {game_id: int, home: {team_id: int, score: int}}[]\n"
        "  Retrieve the list of games played in a given hockey season."
    )


def test_signature_without_description_is_one_line():
    assert "\n" not in render_signature(_games_tool(), include_description=False)


def test_signature_respects_the_char_budget():
    sig = render_signature(_games_tool(), max_chars=80, include_description=False)
    assert len(sig) <= 80


def test_signature_omits_the_return_type_for_weak_schema_placeholders():
    tool = create_tool_from_api_dict(
        "get_state",
        {
            "description": "d",
            "parameters": [],
            "response_schemas": {"success": {"type": "string"}, "_synthetic_placeholder": True},
        },
        "browser",
    )
    assert render_signature(tool, include_description=False) == "get_state()"


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, "string"),
        ({"type": "string", "format": "date"}, "string<date>"),
        ({"type": "string", "maxLength": 3}, "string (len ≤3)"),
        ({"type": "integer", "minimum": 1, "maximum": 5}, "int (1..5)"),
        ({"type": "array", "items": {"type": "integer"}, "minItems": 1}, "int[] (items ≥1)"),
        ({"type": "array", "items": {"enum": ["a", "b"]}}, '("a" | "b")[]'),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "int"),
        ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, "int | string"),
        ({"type": ["string", "null"]}, "string"),
        ({"type": "object", "additionalProperties": {"type": "integer"}}, "{[key: string]: int}"),
        ({"type": "object"}, "object"),
        ({}, "any"),
    ],
)
def test_render_type(schema, expected):
    assert render_type(schema) == expected


def test_render_type_resolves_refs_and_caps_depth():
    defs = {"Team": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}
    assert render_type({"$ref": "#/$defs/Team"}, defs) == "{id: int}"
    deep = {
        "type": "object",
        "properties": {
            "a": {
                "type": "object",
                "properties": {"b": {"type": "object", "properties": {"c": {"type": "integer"}}}},
            }
        },
    }
    assert render_type(deep) == "{a?: {b?: {…}}}"


def test_render_shape_reads_registry_summaries_and_examples():
    assert render_shape({"id": "integer", "names": ["string"], "ok": True, "score": 1.5}) == (
        "{id: int, names: string[], ok: boolean, score: number}"
    )
    assert render_shape({"type": "object", "properties": {"id": {"type": "integer"}}}) == "{id?: int}"


def test_first_sentence():
    assert first_sentence("One. Two.") == "One."
    assert first_sentence("  no   period ") == "no period"
    assert first_sentence("x" * 300, max_chars=10) == "x" * 9 + "…"


# ── minified wire schemas ────────────────────────────────────────────────────


def test_minify_strips_noise_and_keeps_constraints():
    spec = minified_tools([_games_tool()], description_max_chars=300)[0]
    params = spec["function"]["parameters"]
    dumped = json.dumps(params)
    assert '"title"' not in dumped
    assert '"null"' not in dumped, "optional-null unions collapse; required carries optionality"
    assert params["required"] == ["season"]
    assert params["properties"]["order"]["enum"] == ["asc", "desc"]
    assert params["properties"]["limit"]["maximum"] == 100
    assert params["properties"]["tags"]["items"] == {"type": "string"}


def test_minify_is_smaller_than_the_default_wire_schema():
    tool = _games_tool()
    full = json.dumps(convert_to_openai_tool(tool))
    mini = json.dumps(minified_tools([tool], description_max_chars=300)[0])
    assert len(mini) < len(full)


def test_minified_schema_accepts_and_rejects_what_the_model_does():
    jsonschema = pytest.importorskip("jsonschema")
    tool = _games_tool()
    params = minified_tools([tool], description_max_chars=300)[0]["function"]["parameters"]
    model = tool.args_schema
    for payload, ok in (
        ({"season": 2020}, True),
        ({"season": 2020, "order": "asc", "limit": 10, "tags": ["x"]}, True),
        ({"season": 2020, "order": "sideways"}, False),
        ({"season": 2020, "limit": 0}, False),
        ({"order": "asc"}, False),
    ):
        wire_ok = jsonschema.Draft202012Validator(params).is_valid(payload)
        try:
            model.model_validate(payload)
            model_ok = True
        except Exception:
            model_ok = False
        assert wire_ok is ok and model_ok is ok, payload


def test_minify_keeps_a_property_literally_named_title():
    model = build_input_model(
        "create_note",
        {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        mode="lax",
    )
    params = minify_parameters(model.model_json_schema())
    assert params["properties"]["title"] == {"type": "string"}


def test_minify_inlines_defs():
    model = build_input_model(
        "create_order",
        {
            "type": "object",
            "properties": {
                "customer": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}
            },
            "required": ["customer"],
        },
        mode="lax",
    )
    params = minify_parameters(model.model_json_schema())
    assert "$defs" not in params and "$ref" not in json.dumps(params)
    assert params["properties"]["customer"]["properties"]["id"]["type"] == "integer"


def test_minify_truncates_long_descriptions_to_the_first_sentence():
    spec = minify_openai_tool(
        {
            "type": "function",
            "function": {"name": "t", "description": "Short lead. " + "x" * 400, "parameters": {}},
        },
        description_max_chars=100,
    )
    assert spec["function"]["description"] == "Short lead."
    assert spec["function"]["parameters"] == {"type": "object", "properties": {}}


def test_minify_does_not_mutate_its_input():
    original = convert_to_openai_tool(_games_tool())
    snapshot = json.dumps(original, sort_keys=True)
    minify_openai_tool(original)
    assert json.dumps(original, sort_keys=True) == snapshot


# ── CodeAct find_tools signature rendering ───────────────────────────────────


def test_find_tools_signature_render_is_compact_and_lists_each_candidate():
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlistCandidate
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.render import (
        NO_MATCH_MESSAGE,
        render_tools_markdown,
        render_tools_signatures,
    )

    tool = _games_tool()
    candidates = [ShortlistCandidate(name=tool.name), ShortlistCandidate(name="missing")]
    compact = render_tools_signatures(candidates, [tool])
    assert compact.splitlines()[1].startswith("hockey_get_games(season: int (1917..)")
    assert "missing" not in compact
    assert len(compact) < len(render_tools_markdown(candidates, [tool], "q")) / 2
    assert render_tools_signatures([], [tool]) == NO_MATCH_MESSAGE


@pytest.mark.asyncio
async def test_find_tools_render_mode_switch(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite import prompt_utils
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlistCandidate, ShortlistResult
    from cuga.config import settings

    async def fake_run(plan, request):
        return ShortlistResult(candidates=[ShortlistCandidate(name="hockey_get_games")])

    monkeypatch.setattr("cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.run_shortlister", fake_run)
    tool = _games_tool()

    monkeypatch.setattr(settings.advanced_features, "cuga_lite_find_tools_render", "signature", raising=False)
    compact = await prompt_utils.PromptUtils.find_tools("games", [tool], [], llm=None)
    assert compact.startswith("Matching tools")

    monkeypatch.setattr(settings.advanced_features, "cuga_lite_find_tools_render", "markdown", raising=False)
    markdown = await prompt_utils.PromptUtils.find_tools("games", [tool], [], llm=None)
    assert not markdown.startswith("Matching tools")
