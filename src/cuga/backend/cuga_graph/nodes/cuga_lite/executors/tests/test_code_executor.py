import asyncio
import pytest
from unittest.mock import Mock

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.benchmark_mode import (
    reset_skills_relaxed_execution,
    set_skills_relaxed_execution,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.security import SecurityValidator
from cuga.config import settings


@pytest.fixture
def mock_state():
    """Create a mock AgentState with VariablesManager."""
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    state.reflection_skills_enabled = False
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
async def test_dangerous_import_blocked(mock_state, monkeypatch):
    """Test that dangerous imports are blocked."""
    monkeypatch.setattr(settings.skills, "enabled", False)
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
async def test_skills_mode_allows_non_allowlisted_import(mock_state, monkeypatch):
    """Skills mode skips import allowlist so skill workflows can use extra packages."""
    mock_state.reflection_skills_enabled = True
    code = "import pathlib\np = pathlib.Path('.')\nprint(p.name or '.')"
    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={"x": 1},
        state=mock_state,
        mode="local",
    )
    assert "." in result


def test_skills_relaxed_skips_wrapped_code_validation():
    token = set_skills_relaxed_execution(True)
    try:
        SecurityValidator.validate_wrapped_code("open('/tmp/x')")
    finally:
        reset_skills_relaxed_execution(token)


def test_wrapped_code_validation_active_without_skills():
    token = set_skills_relaxed_execution(False)
    try:
        with pytest.raises(PermissionError, match="Security violation"):
            SecurityValidator.validate_wrapped_code("open('/tmp/x')")
    finally:
        reset_skills_relaxed_execution(token)


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
