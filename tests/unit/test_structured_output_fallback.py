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
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda
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


class _FakeLLM(Runnable):
    """Minimal LLM stand-in that composes with `|` like the real thing.

    ``with_structured_output`` yields a runnable that fails the way an endpoint
    ignoring ``json_schema`` does; the model itself answers with plain JSON, which
    is what the json_mode branch's PydanticOutputParser expects.
    """

    def __init__(self, schema_failure: BaseException, json_text: str):
        self.schema_failure = schema_failure
        self.json_text = json_text
        self.calls: list[str] = []
        self.json_mode_prompt_text = ""

    def with_structured_output(self, schema, method=None, **kwargs):
        def _fail(_inputs):
            self.calls.append(f"json_schema({method})")
            raise self.schema_failure

        return RunnableLambda(_fail)

    def invoke(self, input, config=None, **kwargs):
        self.calls.append("json_mode")
        # capture what the fallback prompt actually rendered, so the test can
        # assert the format instructions really reached the model
        self.json_mode_prompt_text = "\n".join(m.content for m in input.to_messages())
        return AIMessage(content=self.json_text)

    async def ainvoke(self, input, config=None, **kwargs):
        return self.invoke(input, config, **kwargs)


@pytest.mark.unit
def test_production_chain_falls_back_and_carries_format_instructions():
    """Drive the real create_validated_structured_output_chain, not a copy of it.

    Exercises the actual wiring: the json_schema attempt, the `_json_schema_unusable`
    branch, and the json_mode prompt built with the `cuga_format_instructions`
    partial. A broken partial raises KeyError here; a rewired chain changes the
    recorded call order.
    """
    import asyncio

    from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent

    llm = _FakeLLM(_validation_error(), '{"result": ["gmail_send_email_emails_post"]}')
    prompt = ChatPromptTemplate.from_messages([("system", "You select tools."), ("human", "{input}")])

    chain = BaseAgent.create_validated_structured_output_chain(llm, Shortlist, prompt)
    out = asyncio.run(chain.ainvoke({"input": "pick tools for sending mail"}))

    assert isinstance(out, Shortlist)
    assert out.result == ["gmail_send_email_emails_post"]
    # json_schema attempted exactly once, then json_mode exactly once — no retry storm
    assert llm.calls == ["json_schema(json_schema)", "json_mode"], llm.calls
    # the fallback prompt must actually tell the model what JSON to produce
    assert "result" in llm.json_mode_prompt_text
    assert "schema" in llm.json_mode_prompt_text.lower()


@pytest.mark.unit
def test_production_chain_does_not_fall_back_on_transport_error():
    """A connection failure must propagate, not silently become a json_mode call."""
    import asyncio

    from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent

    llm = _FakeLLM(ConnectionError("connection reset by peer"), "{}")
    prompt = ChatPromptTemplate.from_messages([("system", "You select tools."), ("human", "{input}")])
    chain = BaseAgent.create_validated_structured_output_chain(llm, Shortlist, prompt)

    with pytest.raises(ConnectionError):
        asyncio.run(chain.ainvoke({"input": "pick tools"}))
    assert "json_mode" not in llm.calls
