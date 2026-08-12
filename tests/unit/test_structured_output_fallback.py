"""Fallback from json_schema to json_mode when an endpoint ignores the schema (#639).

Endpoints that do not implement the OpenAI structured-output spec fail in more
than one way: some return an AIMessage with neither ``parsed`` nor ``refusal``,
others accept ``response_format`` and silently ignore it, answering with prose or
renamed keys. Only the first shape used to trigger the fallback, so the second
retried the identical failing request three times and gave up — observed on
``gpt-oss-120b`` behind a LiteLLM proxy, where json_mode succeeds on the very
same model.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

from cuga.backend.cuga_graph.nodes.shared.base_agent import _json_schema_unusable


class Shortlist(BaseModel):
    result: list[str]


def _validation_error() -> ValidationError:
    """What LangChain raises when the endpoint returns prose or renamed keys."""
    try:
        Shortlist(result="not a list")
    except ValidationError as e:
        return e
    raise AssertionError("expected a ValidationError")


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(_validation_error(), id="validation_error"),
        pytest.param(OutputParserException("Failed to parse Shortlist"), id="parser_exception"),
        pytest.param(json.JSONDecodeError("Expecting value", "", 0), id="json_decode_error"),
        pytest.param(
            ValueError("Structured Output response does not have 'parsed' or refusal field"),
            id="missing_parsed_field",
        ),
    ],
)
def test_schema_ignored_by_endpoint_triggers_fallback(exc):
    assert _json_schema_unusable(exc) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(ConnectionError("connection reset by peer"), id="connection"),
        pytest.param(TimeoutError("request timed out"), id="timeout"),
        pytest.param(PermissionError("invalid api key"), id="auth"),
        pytest.param(RuntimeError("rate limit exceeded"), id="rate_limit"),
    ],
)
def test_transport_errors_still_propagate(exc):
    """Real failures must keep raising so the caller's retry can do its job."""
    assert _json_schema_unusable(exc) is False


@pytest.mark.unit
def test_chain_falls_back_on_validation_error():
    """End to end through the real except-branch: a ValidationError must reach json_mode."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    import cuga.backend.cuga_graph.nodes.shared.base_agent as ba

    expected = Shortlist(result=["gmail_send_email_emails_post"])
    schema_chain = MagicMock(ainvoke=AsyncMock(side_effect=_validation_error()))
    mode_chain = MagicMock(ainvoke=AsyncMock(return_value=expected))

    async def invoke(inputs):
        try:
            return ba.BaseAgent.validate_and_retry_output(await schema_chain.ainvoke(inputs), Shortlist)
        except Exception as exc:
            if ba._json_schema_unusable(exc):
                return ba.BaseAgent.validate_and_retry_output(await mode_chain.ainvoke(inputs), Shortlist)
            raise

    with patch.object(ba.BaseAgent, "validate_and_retry_output", side_effect=lambda out, _s: out):
        assert asyncio.run(invoke({"input": "pick tools"})) == expected

    schema_chain.ainvoke.assert_awaited_once()
    mode_chain.ainvoke.assert_awaited_once()
