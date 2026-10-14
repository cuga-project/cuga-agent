"""Unit regression test: LiteLLM format-instructions prompt wiring.

Verifies four things required by the maintainer review (PR #776):
1. The prompt template renders without missing-variable errors (JSON schema braces
   in the format instructions are NOT misread as LangChain template variables).
2. The rendered messages include the Pydantic JSON schema instructions.
3. A response that selects the second candidate policy returns that policy, not
   the first candidate (guards against index-off-by-one regressions), exercising
   the real BaseAgent.get_chain → PydanticOutputParser path for LiteLLM.
4. The format instructions (containing `matched_policy_index`) are present in the
   messages actually delivered to the model during conflict resolution, so a future
   regression that removes them from the production resolver fails this test.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.policy.agent import (
    PolicyAgent,
    PolicyConflictResolution,
    PolicyContext,
)
from cuga.backend.cuga_graph.policy.models import NaturalLanguageTrigger, Playbook


def _make_playbook(playbook_id: str, name: str, trigger_value: str) -> Playbook:
    """Build a minimal Playbook fixture with one NL intent trigger."""
    return Playbook(
        id=playbook_id,
        name=name,
        description=f"Playbook for '{trigger_value}' queries",
        triggers=[
            NaturalLanguageTrigger(
                value=[trigger_value],
                target="intent",
                threshold=0.7,
            ),
        ],
        markdown_content=f"# {name}\n\nHandle {trigger_value} requests.",
        priority=50,
        enabled=True,
    )


@pytest.mark.unit
def test_format_instructions_render_without_template_error():
    """Pydantic JSON schema braces must not raise KeyError in ChatPromptTemplate.

    The fix passes format instructions via .partial() rather than f-string
    interpolation. This test exercises that wiring directly and asserts the
    prompt renders cleanly and the schema text is present in the output.
    """
    parser = PydanticOutputParser(pydantic_object=PolicyConflictResolution)
    system_prompt = (
        "You are a policy matching system that resolves conflicts when multiple policies could apply."
    )
    user_prompt = "get my daughter's claims"

    # Reproduce the exact template construction from _resolve_nl_trigger_conflicts
    prompt_template = (
        ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                ("human", "{user_prompt}"),
            ]
        )
        + ChatPromptTemplate.from_messages([("system", "{cuga_format_instructions}")])
    ).partial(cuga_format_instructions=BaseAgent.get_format_instructions(parser))

    # Invoking with only {user_prompt} must not raise KeyError / missing variable
    messages = prompt_template.invoke({"user_prompt": user_prompt})

    rendered_text = " ".join(m.content for m in messages.messages)

    # The JSON schema (produced by PydanticOutputParser) must be present
    assert "matched_policy_index" in rendered_text, (
        f"Format instructions should contain the JSON schema field names; got: {rendered_text[:500]}"
    )
    # The user query must also be present
    assert user_prompt in rendered_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_second_candidate_selected():
    """LLM selecting index 2 returns the second policy via the real LiteLLM chain.

    Uses a ChatLiteLLM test double whose ainvoke returns an AIMessage with valid
    JSON so the real BaseAgent.get_chain → PydanticOutputParser path executes.
    Storage is mocked — _resolve_nl_trigger_conflicts never reads self.storage.
    Also asserts that the messages delivered to the model contain the Pydantic
    format instructions (matched_policy_index), verifying schema propagation
    through the production resolver rather than a locally reconstructed template.
    """
    try:
        from langchain_litellm import ChatLiteLLM
    except ImportError:
        pytest.skip("langchain_litellm not installed")

    first = _make_playbook("playbook_first", "First Playbook", "first intent query")
    second = _make_playbook("playbook_second", "Second Playbook", "second intent query")

    # Build the JSON that PydanticOutputParser expects from the model
    llm_json = json.dumps(
        {
            "matched_policy_index": 2,
            "confidence": 0.88,
            "reasoning": "Second policy NL trigger is the closer match for this query",
        }
    )

    # ChatLiteLLM test double: real isinstance check passes, ainvoke returns AIMessage
    fake_llm = MagicMock(spec=ChatLiteLLM)
    fake_llm.ainvoke = AsyncMock(return_value=AIMessage(content=llm_json))
    # bind() is called internally by with_retry; return the same mock
    fake_llm.bind = MagicMock(return_value=fake_llm)

    agent = PolicyAgent(storage=MagicMock(), llm=fake_llm, embedding_function=None)
    context = PolicyContext(
        user_input="second intent query",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )

    resolution = await agent._resolve_nl_trigger_conflicts(
        [(first, first.triggers), (second, second.triggers)],
        context,
        target="intent",
        target_text=context.user_input,
    )

    assert resolution is not None, "Resolution should not be None when LLM returns index 2"
    resolved_policy, confidence, reasoning = resolution
    assert resolved_policy.name == "Second Playbook", (
        f"Expected second candidate to be selected; got '{resolved_policy.name}'"
    )
    assert confidence == 0.88
    assert "LLM conflict resolution" in reasoning

    # Verify schema propagation: format instructions must have reached the model.
    # fake_llm.ainvoke receives the rendered ChatPromptValue / message list from
    # the production resolver chain. Joining all message content and asserting on
    # the field name guards against a future regression that drops the .partial()
    # call in _resolve_nl_trigger_conflicts.
    fake_llm.ainvoke.assert_awaited_once()
    call_args = fake_llm.ainvoke.call_args
    received_messages = call_args.args[0] if call_args.args else call_args.kwargs.get("input")
    if hasattr(received_messages, "messages"):
        received_text = " ".join(m.content for m in received_messages.messages)
    else:
        received_text = " ".join(m.content if hasattr(m, "content") else str(m) for m in received_messages)
    assert "matched_policy_index" in received_text, (
        "Format instructions must be present in the messages delivered to the model; "
        f"got: {received_text[:500]}"
    )
