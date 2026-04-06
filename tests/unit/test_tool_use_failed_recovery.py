"""Tests for LLM tool_use_failed error recovery in errors.py."""

from cuga.backend.llm.errors import (
    extract_code_from_tool_use_failed,
    failed_gen_to_code,
    parse_tool_use_failed_generation,
)


# ---------------------------------------------------------------------------
# parse_tool_use_failed_generation
# ---------------------------------------------------------------------------


class TestParseToolUseFailedGeneration:
    def test_groq_python_tool_single_quotes(self):
        err = (
            "Error code: 400 - {'error': {'message': 'Failed to call a function. "
            "tool_use_failed', 'type': 'invalid_request_error', "
            "'failed_generation': '{\"name\": \"python\", \"arguments\": \"print(42)\"}'}}"
        )
        result = parse_tool_use_failed_generation(err)
        assert result is not None
        assert result["name"] == "python"
        assert result["arguments"] == "print(42)"

    def test_groq_named_tool_single_quotes(self):
        err = (
            "Error code: 400 - {'error': {'message': 'Failed to call a function. "
            "tool_use_failed', 'type': 'invalid_request_error', "
            "'failed_generation': '{\"name\": \"knowledge_search_knowledge\", "
            "\"arguments\": {\"query\": \"test\"}}'}}"
        )
        result = parse_tool_use_failed_generation(err)
        assert result is not None
        assert result["name"] == "knowledge_search_knowledge"

    def test_double_quotes_variant(self):
        err = (
            'Error code: 400 - {"error": {"message": "Failed to call a function. '
            'tool_use_failed", "type": "invalid_request_error", '
            '"failed_generation": "{\\"name\\": \\"python\\", \\"arguments\\": \\"x = 1\\"}"}}'
        )
        result = parse_tool_use_failed_generation(err)
        # May or may not parse depending on escaping — just ensure no crash
        assert result is None or isinstance(result, dict)

    def test_malformed_json_python_fallback(self):
        """When JSON parsing fails but 'name': 'python' is present, regex fallback kicks in."""
        err = "tool_use_failed 'failed_generation': '{\"name\": \"python\", \"arguments\": some_broken_json}'"
        result = parse_tool_use_failed_generation(err)
        assert result is not None
        assert result["name"] == "python"
        assert "some_broken_json" in result["arguments"]

    def test_missing_tool_use_failed_keyword(self):
        err = "'failed_generation': '{\"name\": \"python\", \"arguments\": \"print(1)\"}'"
        assert parse_tool_use_failed_generation(err) is None

    def test_missing_failed_generation_keyword(self):
        err = "Error code: 400 - tool_use_failed some other error"
        assert parse_tool_use_failed_generation(err) is None

    def test_unparseable_error(self):
        err = "tool_use_failed failed_generation totally_random_garbage"
        assert parse_tool_use_failed_generation(err) is None

    def test_empty_string(self):
        assert parse_tool_use_failed_generation("") is None

    def test_parses_exception_body_when_string_format_is_unhelpful(self):
        class FakeErr(Exception):
            def __init__(self):
                self.body = {
                    "error": {
                        "message": "Tool choice is none, but model called a tool",
                        "type": "invalid_request_error",
                        "code": "tool_use_failed",
                        "failed_generation": (
                            '{"name": "knowledge_search_knowledge", '
                            '"arguments": {"query": "GPA", "scope": "session"}}'
                        ),
                    }
                }
                super().__init__("400 bad request")

        result = parse_tool_use_failed_generation(FakeErr())
        assert result is not None
        assert result["name"] == "knowledge_search_knowledge"
        assert result["arguments"]["query"] == "GPA"


# ---------------------------------------------------------------------------
# failed_gen_to_code
# ---------------------------------------------------------------------------


class TestFailedGenToCode:
    def test_python_tool_unwraps_code(self):
        result = failed_gen_to_code({"name": "python", "arguments": "x = 1\\nprint(x)"})
        assert result == "x = 1\nprint(x)"

    def test_python_tool_strips_whitespace(self):
        result = failed_gen_to_code({"name": "python", "arguments": "  print(1)  "})
        assert result == "print(1)"

    def test_named_tool_dict_args(self):
        result = failed_gen_to_code(
            {
                "name": "knowledge_search_knowledge",
                "arguments": {"query": "hello", "top_k": 5},
            }
        )
        assert result == "result = await knowledge_search_knowledge(query='hello', top_k=5)\nprint(result)"

    def test_named_tool_string_args_json(self):
        result = failed_gen_to_code(
            {
                "name": "web_search",
                "arguments": '{"query": "test"}',
            }
        )
        assert result == "result = await web_search(query='test')\nprint(result)"

    def test_named_tool_string_args_invalid_json(self):
        result = failed_gen_to_code(
            {
                "name": "web_search",
                "arguments": "not_valid_json",
            }
        )
        # Falls back to empty args
        assert result == "result = await web_search()\nprint(result)"

    def test_no_name_returns_none(self):
        assert failed_gen_to_code({}) is None
        assert failed_gen_to_code({"arguments": "x"}) is None


# ---------------------------------------------------------------------------
# extract_code_from_tool_use_failed (end-to-end)
# ---------------------------------------------------------------------------


class TestExtractCodeFromToolUseFailed:
    def test_groq_python_e2e(self):
        err = (
            "Error code: 400 - {'error': {'message': 'Failed to call a function. "
            "tool_use_failed', 'type': 'invalid_request_error', "
            "'failed_generation': '{\"name\": \"python\", \"arguments\": \"print(42)\"}'}}"
        )
        code = extract_code_from_tool_use_failed(err)
        assert code == "print(42)"

    def test_groq_named_tool_e2e(self):
        err = (
            "Error code: 400 - {'error': {'message': 'Failed to call a function. "
            "tool_use_failed', 'type': 'invalid_request_error', "
            "'failed_generation': '{\"name\": \"knowledge_search_knowledge\", "
            "\"arguments\": {\"query\": \"docs\"}}'}}"
        )
        code = extract_code_from_tool_use_failed(err)
        assert code is not None
        assert "knowledge_search_knowledge" in code
        assert "query=" in code
        assert "print(result)" in code

    def test_non_recoverable_returns_none(self):
        assert extract_code_from_tool_use_failed("some random error") is None

    def test_empty_string_returns_none(self):
        assert extract_code_from_tool_use_failed("") is None

    def test_reads_failed_generation_from_exception_body(self):
        class FakeErr(Exception):
            def __init__(self):
                self.body = {
                    "error": {
                        "message": "Tool choice is none, but model called a tool",
                        "type": "invalid_request_error",
                        "code": "tool_use_failed",
                        "failed_generation": (
                            '{"name": "knowledge_search_knowledge", '
                            '"arguments": {"query": "GPA", "scope": "session", "limit": 5}}'
                        ),
                    }
                }
                super().__init__("400 bad request")

        code = extract_code_from_tool_use_failed(FakeErr())
        assert code is not None
        assert "knowledge_search_knowledge" in code
        assert "query='GPA'" in code
