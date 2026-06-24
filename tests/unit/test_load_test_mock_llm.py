"""Unit tests for LoadTestMockChatModel response selection."""

from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.llm.load_test_mock import (
    LoadTestMockChatModel,
    _ACCOUNTS_QUERY_CODE,
    is_mock_llm_enabled,
)


def test_is_mock_llm_enabled_reads_env(monkeypatch):
    monkeypatch.delenv("CUGA_MOCK_LLM", raising=False)
    assert is_mock_llm_enabled() is False
    monkeypatch.setenv("CUGA_MOCK_LLM", "true")
    assert is_mock_llm_enabled() is True


def test_create_llm_from_config_uses_mock_when_enabled(monkeypatch):
    monkeypatch.setenv("CUGA_MOCK_LLM", "true")
    from cuga.backend.llm.load_test_mock import LoadTestMockChatModel
    from cuga.backend.llm.models import create_llm_from_config

    model = create_llm_from_config({"provider": "openai", "model": "gpt-4o"})
    assert isinstance(model, LoadTestMockChatModel)


def test_mock_returns_code_when_few_shots_contain_execution_output():
    """Few-shot prefix must not trigger post-execution response on first live turn."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
        extract_code_from_model_response,
    )

    model = LoadTestMockChatModel()
    messages = [
        HumanMessage(content="Execution output:\n# Found 3 Matching Tool(s)"),
        AIMessage(content="```python\nprint('discover')\n```"),
        HumanMessage(content="list all my accounts, how many are there?"),
    ]
    content = model._pick_content(messages)
    assert "```python" in content
    assert "digital_sales_my_accounts" in content
    assert extract_code_from_model_response(content, None)


def test_mock_returns_code_for_accounts_query():
    model = LoadTestMockChatModel()
    content = model._pick_content([HumanMessage(content="list all my accounts, how many are there?")])
    assert "```python" in content
    assert "digital_sales_my_accounts" in content
    assert "print(account_count)" in content


def test_mock_retries_code_after_execution_error():
    model = LoadTestMockChatModel()
    content = model._pick_content(
        [
            HumanMessage(content="list all my accounts, how many are there?"),
            AIMessage(content=_ACCOUNTS_QUERY_CODE),
            HumanMessage(content="Execution output:\nError during execution: ValueError('bad tool')"),
        ]
    )
    assert "```python" in content
    assert "digital_sales_my_accounts" in content


def test_mock_followup_not_confused_by_variables_summary_in_prompt():
    model = LoadTestMockChatModel()
    content = model._pick_content(
        [
            HumanMessage(
                content=(
                    "how many accounts did we retrieve?\n\n"
                    "## Available Variables\n\n# Variables Summary\n\n## my_accounts_response"
                )
            ),
        ]
    )
    assert content == "We retrieved 50 accounts."


def test_mock_returns_final_answer_after_execution_output():
    model = LoadTestMockChatModel()
    content = model._pick_content(
        [
            HumanMessage(content="list all my accounts, how many are there?"),
            AIMessage(content="```python\nprint(50)\n```"),
            HumanMessage(content="Execution output:\n\nNew Variables Created:\naccount_count = 50"),
        ]
    )
    assert "50" in content
    assert "```python" not in content


def test_mock_returns_followup_answer():
    model = LoadTestMockChatModel()
    content = model._pick_content([HumanMessage(content="how many accounts did we retrieve?")])
    assert "50" in content.lower()


def test_mock_bind_tools_does_not_raise():
    from langchain_core.tools import tool

    @tool
    def sample_tool(x: int) -> int:
        """Sample tool."""
        return x

    model = LoadTestMockChatModel()
    bound = model.bind_tools([sample_tool])
    assert bound.bound_tools is not None


def test_mock_with_structured_output_returns_valid_model():
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
        TaskDecompositionPlan,
    )

    model = LoadTestMockChatModel()
    chain = model.with_structured_output(TaskDecompositionPlan, method="json_schema")
    result = chain.invoke([])
    assert isinstance(result, TaskDecompositionPlan)
    assert result.task_decomposition
