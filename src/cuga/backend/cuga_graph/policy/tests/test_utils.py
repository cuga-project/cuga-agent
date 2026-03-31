"""Tests for policy utils."""

from cuga.backend.cuga_graph.policy.utils import validate_output_formatter


def test_validate_output_formatter_valid():
    valid = {
        "id": "of_1",
        "name": "Test",
        "description": "Desc",
        "triggers": [{"type": "always"}],
        "format_type": "markdown",
        "format_config": "Format as markdown",
    }
    assert validate_output_formatter(valid) == []


def test_validate_output_formatter_missing_required():
    missing_name = {"id": "of_1", "description": "D", "triggers": [{"type": "always"}]}
    errs = validate_output_formatter(missing_name)
    assert any("name" in e.lower() for e in errs)


def test_validate_output_formatter_empty_triggers():
    no_triggers = {
        "id": "of_1",
        "name": "T",
        "description": "D",
        "triggers": [],
        "format_config": "x",
    }
    errs = validate_output_formatter(no_triggers)
    assert any("triggers" in e.lower() for e in errs)


def test_validate_output_formatter_invalid_format_type():
    bad_type = {
        "id": "of_1",
        "name": "T",
        "description": "D",
        "triggers": [{"type": "always"}],
        "format_type": "invalid",
        "format_config": "x",
    }
    errs = validate_output_formatter(bad_type)
    assert any("format_type" in e.lower() for e in errs)


def test_validate_output_formatter_json_schema_invalid_json():
    bad_json = {
        "id": "of_1",
        "name": "T",
        "description": "D",
        "triggers": [{"type": "always"}],
        "format_type": "json_schema",
        "format_config": "not valid json {{{",
    }
    errs = validate_output_formatter(bad_json)
    assert any("json" in e.lower() for e in errs)


def test_validate_output_formatter_json_schema_valid():
    good_json = {
        "id": "of_1",
        "name": "T",
        "description": "D",
        "triggers": [{"type": "always"}],
        "format_type": "json_schema",
        "format_config": '{"type": "object", "properties": {"x": {"type": "string"}}}',
    }
    assert validate_output_formatter(good_json) == []
