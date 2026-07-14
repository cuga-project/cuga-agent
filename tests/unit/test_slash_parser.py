import pytest

from cuga.backend.slash_commands.parser import parse

pytestmark = pytest.mark.unit


def test_empty_input_passes_through():
    assert parse("") is None
    assert parse(None) is None


def test_bare_slash_is_not_a_command():
    assert parse("/") is None


def test_leading_whitespace_before_slash_passes_through():
    assert parse(" /help") is None
    assert parse("\t/help") is None


def test_non_leading_slash_passes_through():
    assert parse("hello /help") is None


def test_plain_text_passes_through():
    assert parse("how do I run the agent?") is None


def test_slash_with_no_args():
    parsed = parse("/help")
    assert parsed is not None
    assert parsed.name == "help"
    assert parsed.raw_args == ""
    assert parsed.raw_input == "/help"


def test_slash_with_args():
    parsed = parse("/foo bar baz")
    assert parsed is not None
    assert parsed.name == "foo"
    assert parsed.raw_args == "bar baz"


def test_args_are_stripped():
    parsed = parse("/foo   spaced   ")
    assert parsed is not None
    assert parsed.raw_args == "spaced"


def test_namespaced_names_allowed():
    parsed = parse("/mcp:github:list")
    assert parsed is not None
    assert parsed.name == "mcp:github:list"


def test_hyphen_and_underscore_in_name():
    assert parse("/cuga-create-pr").name == "cuga-create-pr"
    assert parse("/load_skill").name == "load_skill"


def test_multiline_args_preserved_after_strip():
    parsed = parse("/foo line1\nline2")
    assert parsed is not None
    assert parsed.raw_args == "line1\nline2"
