import asyncio
import pytest
from unittest.mock import Mock

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor


@pytest.fixture
def mock_state():
    """Create a mock AgentState with VariablesManager."""
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    return state


@pytest.mark.asyncio
async def test_basic_execution_local(mock_state):
    """Test basic code execution in local mode."""
    code = "x = 5 + 3\nprint(x)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "8" in result
    assert 'x' in new_vars
    assert new_vars['x'] == 8


@pytest.mark.asyncio
async def test_execution_with_variables(mock_state):
    """Test execution with existing variables."""
    code = "y = x * 2\nprint(y)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'x': 5},
        state=mock_state,
        mode='local',
    )

    assert "10" in result
    assert 'y' in new_vars
    assert new_vars['y'] == 10


@pytest.mark.asyncio
async def test_async_tool_execution(mock_state):
    """Test execution with async tools."""

    async def my_tool(value: int) -> int:
        await asyncio.sleep(0.01)
        return value * 2

    code = "result = await my_tool(5)\nprint(result)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'my_tool': my_tool},
        state=mock_state,
        mode='local',
    )

    assert "10" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 10


@pytest.mark.asyncio
async def test_unknown_tool_name_gets_correction(mock_state):
    """A fabricated tool name returns a correction listing the closest real tools.

    Regression guard for the gpt-oss tool-name hallucination loop: calling a
    non-existent tool used to surface only a raw NameError, so the agent kept
    re-inventing the same bogus name until the step limit. The correction must
    name the closest real tool so the agent can recover in one step.
    """

    async def mondial_geo_get_top_country_by_gdp_and_agriculture() -> int:
        return 0

    async def mondial_geo_get_mountain_count_by_country(country: str) -> int:
        return 0

    # References a real tool (so the context-usage guard passes) then calls a
    # fabricated one — mirrors the real trajectory where the agent invents
    # generic CRUD names after a valid find_tools/real-tool reference.
    code = (
        "mc = mondial_geo_get_mountain_count_by_country\n"
        "result = await mondial_geo_get_countries_countries_get()\n"
        "print(result)"
    )
    result, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={
            'mondial_geo_get_top_country_by_gdp_and_agriculture': (
                mondial_geo_get_top_country_by_gdp_and_agriculture
            ),
            'mondial_geo_get_mountain_count_by_country': mondial_geo_get_mountain_count_by_country,
        },
        state=mock_state,
        mode='local',
    )

    assert "tool-name correction" in result
    assert "mondial_geo_get_countries_countries_get" in result
    assert "Did you mean" in result
    # the real tool the shortlister meant must be offered as a candidate
    assert "mondial_geo_get_top_country_by_gdp_and_agriculture" in result


@pytest.mark.asyncio
async def test_unknown_name_no_close_match_points_to_find_tools(mock_state):
    """With no similar tool loaded, the correction tells the agent to re-query find_tools."""

    async def authors_get_author_details(author_id: int) -> dict:
        return {}

    code = "ad = authors_get_author_details\nresult = await repo_browser()\nprint(result)"
    result, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={'authors_get_author_details': authors_get_author_details},
        state=mock_state,
        mode='local',
    )

    assert "tool-name correction" in result
    assert "find_tools" in result


@pytest.mark.asyncio
async def test_dangerous_import_blocked(mock_state):
    """Test that dangerous imports are blocked."""
    code = "import os\nos.system('echo hello')"

    with pytest.raises(ImportError) as exc_info:
        result, new_vars = await CodeExecutor.eval_with_tools_async(
            code=code,
            _locals={},
            state=mock_state,
            mode='local',
        )

    assert "not allowed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_allowed_import_works(mock_state):
    """Test that allowed imports work."""
    code = "import json\ndata = json.dumps({'key': 'value'})\nprint(data)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert '"key"' in result or "'key'" in result
    assert 'data' in new_vars


@pytest.mark.asyncio
async def test_pandas_support(mock_state):
    """Test pandas support if available."""
    try:
        code = "import pandas as pd\ndf = pd.DataFrame({'a': [1, 2, 3]})\nprint(len(df))"
        result, new_vars = await CodeExecutor.eval_with_tools_async(
            code=code,
            _locals={},
            state=mock_state,
            mode='local',
        )

        assert "3" in result
        assert 'df' in new_vars
    except ImportError:
        pytest.skip("pandas not installed")


@pytest.mark.asyncio
async def test_variable_reordering(mock_state):
    """Test that printed variables are moved to end."""
    code = "x = 5\ny = 10\nz = 15\nprint(y)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    # y should be moved to the end since it appears in print statement
    # and is more than 3 characters (actually it's 1, so it won't be moved)
    # Let's test with a longer variable name
    assert 'x' in new_vars and 'y' in new_vars and 'z' in new_vars


@pytest.mark.asyncio
async def test_variable_reordering_long_name(mock_state):
    """Test that printed variables with long names are moved to end."""
    code = "short = 5\nlonger_name = 10\nanother = 15\nprint(longer_name)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    var_names = list(new_vars.keys())
    # longer_name should be moved to the end since it's > 3 chars and in print
    assert var_names[-1] == 'longer_name'


@pytest.mark.asyncio
async def test_timeout_handling(mock_state):
    """Test that timeouts are handled properly."""
    code = "import asyncio\nawait asyncio.sleep(100)"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "timeout" in result.lower() or "error" in result.lower()


@pytest.mark.asyncio
async def test_expression_auto_print(mock_state):
    """Test that final expressions are auto-printed."""
    code = "x = 5\nx * 2"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
        mode='local',
    )

    assert "10" in result


@pytest.mark.asyncio
async def test_mode_auto_detection(mock_state):
    """Test that mode is auto-detected from settings."""
    code = "x = 42"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={},
        state=mock_state,
    )

    assert 'x' in new_vars
    assert new_vars['x'] == 42
