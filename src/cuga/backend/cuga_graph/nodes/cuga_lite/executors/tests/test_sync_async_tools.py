"""
Test that both sync and async LangChain tools work correctly with CodeExecutor
when called with await (as the agent always does).
"""

import asyncio
import pytest
from unittest.mock import Mock
from pydantic import BaseModel, Field
from langchain_core.tools import tool, StructuredTool

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable


@pytest.fixture
def mock_state():
    """Create a mock AgentState with VariablesManager."""
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    return state


@pytest.fixture
def sync_tool():
    """Create a synchronous LangChain tool."""

    @tool
    def sync_add(a: int, b: int) -> int:
        """Add two numbers together (synchronous tool)."""
        return a + b

    return sync_add


@pytest.fixture
def async_tool():
    """Create an asynchronous LangChain tool."""

    @tool
    async def async_multiply(a: int, b: int) -> int:
        """Multiply two numbers together (asynchronous tool)."""
        await asyncio.sleep(0.01)  # Simulate async operation
        return a * b

    return async_multiply


@pytest.mark.asyncio
async def test_sync_tool_with_await(mock_state, sync_tool):
    """Test that a synchronous tool works when called with await."""
    # Extract tool function similar to how CUGA does it
    tool_func = sync_tool.func if hasattr(sync_tool, 'func') else sync_tool._run

    # Wrap sync function to make it awaitable (since agent always uses await)
    awaitable_tool_func = make_tool_awaitable(tool_func)

    # Prepare tools context similar to how CUGA does it
    tools_context = {
        'sync_add': awaitable_tool_func,
    }

    # Code that calls the tool with await (as agent always does)
    code = """
result = await sync_add(5, 3)
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "8" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 8


@pytest.mark.asyncio
async def test_async_tool_with_await(mock_state, async_tool):
    """Test that an asynchronous tool works when called with await."""
    # Extract tool function similar to how CUGA does it
    # For async tools, StructuredTool might use .coroutine instead of .func
    if hasattr(async_tool, 'func') and async_tool.func:
        tool_func = async_tool.func
    elif hasattr(async_tool, 'coroutine') and async_tool.coroutine:
        tool_func = async_tool.coroutine
    else:
        # Fallback: try to get the underlying function
        tool_func = getattr(async_tool, '_run', None)
        if not tool_func:
            raise ValueError(f"Could not extract function from async tool {async_tool.name}")

    # Async functions are already awaitable, but wrap for consistency
    awaitable_tool_func = make_tool_awaitable(tool_func)

    # Prepare tools context similar to how CUGA does it
    tools_context = {
        'async_multiply': awaitable_tool_func,
    }

    # Code that calls the tool with await (as agent always does)
    code = """
result = await async_multiply(4, 5)
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "20" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 20


@pytest.mark.asyncio
async def test_both_sync_and_async_tools_together(mock_state, sync_tool, async_tool):
    """Test that both sync and async tools work together when called with await."""
    # Extract tool functions similar to how CUGA does it
    sync_tool_func = sync_tool.func if hasattr(sync_tool, 'func') else sync_tool._run

    # For async tools, StructuredTool might use .coroutine instead of .func
    if hasattr(async_tool, 'func') and async_tool.func:
        async_tool_func = async_tool.func
    elif hasattr(async_tool, 'coroutine') and async_tool.coroutine:
        async_tool_func = async_tool.coroutine
    else:
        async_tool_func = getattr(async_tool, '_run', None)
        if not async_tool_func:
            raise ValueError(f"Could not extract function from async tool {async_tool.name}")

    # Wrap both to make them awaitable (sync needs wrapping, async is already awaitable)
    awaitable_sync_func = make_tool_awaitable(sync_tool_func)
    awaitable_async_func = make_tool_awaitable(async_tool_func)

    # Prepare tools context similar to how CUGA does it
    tools_context = {
        'sync_add': awaitable_sync_func,
        'async_multiply': awaitable_async_func,
    }

    # Code that calls both tools with await (as agent always does)
    code = """
sum_result = await sync_add(10, 5)
product_result = await async_multiply(3, 4)
total = sum_result + product_result
print(f"Sum: {sum_result}, Product: {product_result}, Total: {total}")
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "15" in result  # sum_result
    assert "12" in result  # product_result
    assert "27" in result  # total
    assert 'sum_result' in new_vars
    assert 'product_result' in new_vars
    assert 'total' in new_vars
    assert new_vars['sum_result'] == 15
    assert new_vars['product_result'] == 12
    assert new_vars['total'] == 27


@pytest.mark.asyncio
async def test_sync_tool_from_structured_tool_directly(mock_state):
    """Test that a sync function wrapped in StructuredTool works with await."""

    def sync_subtract(a: int, b: int) -> int:
        """Subtract b from a (synchronous function)."""
        return a - b

    # Create StructuredTool similar to how CUGA creates tools
    structured_tool = StructuredTool.from_function(
        func=sync_subtract,
        name="sync_subtract",
        description="Subtract two numbers",
    )

    # Extract tool function similar to how CUGA does it (tool.func)
    tool_func = structured_tool.func

    # Wrap sync function to make it awaitable
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'sync_subtract': awaitable_tool_func,
    }

    code = """
result = await sync_subtract(10, 3)
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "7" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 7


@pytest.mark.asyncio
async def test_async_tool_from_structured_tool_directly(mock_state):
    """Test that an async function wrapped in StructuredTool works with await."""

    async def async_divide(a: int, b: int) -> float:
        """Divide a by b (asynchronous function)."""
        await asyncio.sleep(0.01)
        return a / b

    # Create StructuredTool similar to how CUGA creates tools
    structured_tool = StructuredTool.from_function(
        func=async_divide,
        name="async_divide",
        description="Divide two numbers",
    )

    # Extract tool function similar to how CUGA does it (tool.func)
    tool_func = structured_tool.func

    tools_context = {
        'async_divide': tool_func,
    }

    code = """
result = await async_divide(20, 4)
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "5.0" in result or "5" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 5.0


@pytest.mark.asyncio
async def test_sync_tool_from_function_single_param(mock_state):
    """Test that a sync function works when StructuredTool.from_function is called with only the function."""

    def sync_power(base: int, exponent: int) -> int:
        """Calculate base raised to the power of exponent (synchronous function)."""
        return base**exponent

    # Create StructuredTool with only the function (no explicit parameter names)
    structured_tool = StructuredTool.from_function(sync_power)

    # Extract tool function similar to how CUGA does it (tool.func)
    tool_func = structured_tool.func

    # Wrap sync function to make it awaitable
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'sync_power': awaitable_tool_func,
    }

    code = """
result = await sync_power(2, 8)
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "256" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 256


@pytest.mark.asyncio
async def test_async_tool_from_function_single_param(mock_state):
    """Test that an async function works when StructuredTool.from_function is called with only the function."""

    async def async_modulo(a: int, b: int) -> int:
        """Calculate a modulo b (asynchronous function)."""
        await asyncio.sleep(0.01)
        return a % b

    # Create StructuredTool with only the function (no explicit parameter names)
    structured_tool = StructuredTool.from_function(async_modulo)

    # Extract tool function similar to how CUGA does it (tool.func)
    tool_func = structured_tool.func

    # Wrap with make_tool_awaitable
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'async_modulo': awaitable_tool_func,
    }

    code = """
result = await async_modulo(17, 5)
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    assert "2" in result
    assert 'result' in new_vars
    assert new_vars['result'] == 2


class UserData(BaseModel):
    """Example Pydantic model for testing."""

    name: str
    age: int
    email: str = Field(..., description="User email address")


@pytest.fixture
def sync_tool_with_pydantic():
    """Create a synchronous tool that returns a Pydantic model."""

    @tool
    def get_user_data(name: str, age: int, email: str) -> UserData:
        """Get user data as a Pydantic model (synchronous tool)."""
        return UserData(name=name, age=age, email=email)

    return get_user_data


@pytest.fixture
def async_tool_with_pydantic():
    """Create an asynchronous tool that returns a Pydantic model."""

    @tool
    async def get_user_data_async(name: str, age: int, email: str) -> UserData:
        """Get user data as a Pydantic model (asynchronous tool)."""
        await asyncio.sleep(0.01)
        return UserData(name=name, age=age, email=email)

    return get_user_data_async


@pytest.mark.asyncio
async def test_sync_tool_with_pydantic_model_conversion(mock_state, sync_tool_with_pydantic):
    """Test that a sync tool returning a Pydantic model is converted to dict."""
    # Extract tool function similar to how CUGA does it
    tool_func = (
        sync_tool_with_pydantic.func
        if hasattr(sync_tool_with_pydantic, 'func')
        else sync_tool_with_pydantic._run
    )

    # Wrap with make_tool_awaitable (which should convert Pydantic models to dicts)
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'get_user_data': awaitable_tool_func,
    }

    code = """
result = await get_user_data("Alice", 30, "alice@example.com")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is a dict (not a Pydantic model)
    assert 'result' in new_vars
    assert isinstance(new_vars['result'], dict), f"Expected dict, got {type(new_vars['result'])}"
    assert new_vars['result']['name'] == "Alice"
    assert new_vars['result']['age'] == 30
    assert new_vars['result']['email'] == "alice@example.com"

    # Verify it was printed as a dict representation
    assert "Alice" in result
    assert "30" in result


@pytest.mark.asyncio
async def test_async_tool_with_pydantic_model_conversion(mock_state, async_tool_with_pydantic):
    """Test that an async tool returning a Pydantic model is converted to dict."""
    # Extract tool function similar to how CUGA does it
    if hasattr(async_tool_with_pydantic, 'func') and async_tool_with_pydantic.func:
        tool_func = async_tool_with_pydantic.func
    elif hasattr(async_tool_with_pydantic, 'coroutine') and async_tool_with_pydantic.coroutine:
        tool_func = async_tool_with_pydantic.coroutine
    else:
        tool_func = getattr(async_tool_with_pydantic, '_run', None)
        if not tool_func:
            raise ValueError(f"Could not extract function from async tool {async_tool_with_pydantic.name}")

    # Wrap with make_tool_awaitable (which should convert Pydantic models to dicts)
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'get_user_data_async': awaitable_tool_func,
    }

    code = """
result = await get_user_data_async("Bob", 25, "bob@example.com")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is a dict (not a Pydantic model)
    assert 'result' in new_vars
    assert isinstance(new_vars['result'], dict), f"Expected dict, got {type(new_vars['result'])}"
    assert new_vars['result']['name'] == "Bob"
    assert new_vars['result']['age'] == 25
    assert new_vars['result']['email'] == "bob@example.com"

    # Verify it was printed as a dict representation
    assert "Bob" in result
    assert "25" in result


@pytest.mark.asyncio
async def test_tool_with_non_pydantic_return_value(mock_state):
    """Test that non-Pydantic return values are not affected."""

    @tool
    def get_simple_dict(key: str, value: str) -> dict:
        """Return a simple dict (not a Pydantic model)."""
        return {key: value}

    tool_func = get_simple_dict.func if hasattr(get_simple_dict, 'func') else get_simple_dict._run
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'get_simple_dict': awaitable_tool_func,
    }

    code = """
result = await get_simple_dict("status", "success")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is unchanged (still a dict)
    assert 'result' in new_vars
    assert isinstance(new_vars['result'], dict)
    assert new_vars['result'] == {"status": "success"}


@pytest.mark.asyncio
async def test_sync_tool_returns_regular_dict(mock_state):
    """Test that a sync tool returning a regular dict (not Pydantic) works correctly."""

    @tool
    def get_config(key: str, value: str) -> dict:
        """Return a configuration dict (regular dict, not Pydantic model)."""
        return {"key": key, "value": value, "type": "config"}

    tool_func = get_config.func if hasattr(get_config, 'func') else get_config._run
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'get_config': awaitable_tool_func,
    }

    code = """
result = await get_config("database", "postgresql")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is a regular dict (not converted)
    assert 'result' in new_vars
    assert isinstance(new_vars['result'], dict)
    assert new_vars['result'] == {"key": "database", "value": "postgresql", "type": "config"}
    assert "database" in result
    assert "postgresql" in result


@pytest.mark.asyncio
async def test_async_tool_returns_regular_dict(mock_state):
    """Test that an async tool returning a regular dict (not Pydantic) works correctly."""

    @tool
    async def get_metadata(name: str, version: str) -> dict:
        """Return metadata dict (regular dict, not Pydantic model)."""
        await asyncio.sleep(0.01)
        return {"name": name, "version": version, "timestamp": "2024-01-01"}

    if hasattr(get_metadata, 'func') and get_metadata.func:
        tool_func = get_metadata.func
    elif hasattr(get_metadata, 'coroutine') and get_metadata.coroutine:
        tool_func = get_metadata.coroutine
    else:
        tool_func = getattr(get_metadata, '_run', None)
        if not tool_func:
            raise ValueError(f"Could not extract function from async tool {get_metadata.name}")

    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'get_metadata': awaitable_tool_func,
    }

    code = """
result = await get_metadata("myapp", "1.0.0")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is a regular dict (not converted)
    assert 'result' in new_vars
    assert isinstance(new_vars['result'], dict)
    assert new_vars['result'] == {"name": "myapp", "version": "1.0.0", "timestamp": "2024-01-01"}
    assert "myapp" in result
    assert "1.0.0" in result


@pytest.mark.asyncio
async def test_sync_tool_returns_none(mock_state):
    """Test that a sync tool returning None works correctly."""

    @tool
    def clear_cache(key: str) -> None:
        """Clear cache for a key (returns None)."""
        # Simulate cache clearing
        pass

    tool_func = clear_cache.func if hasattr(clear_cache, 'func') else clear_cache._run
    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'clear_cache': awaitable_tool_func,
    }

    code = """
result = await clear_cache("user:123")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is None
    assert 'result' in new_vars
    assert new_vars['result'] is None
    assert "None" in result


@pytest.mark.asyncio
async def test_async_tool_returns_none(mock_state):
    """Test that an async tool returning None works correctly."""

    @tool
    async def delete_resource(resource_id: str) -> None:
        """Delete a resource (returns None)."""
        await asyncio.sleep(0.01)
        # Simulate resource deletion
        pass

    if hasattr(delete_resource, 'func') and delete_resource.func:
        tool_func = delete_resource.func
    elif hasattr(delete_resource, 'coroutine') and delete_resource.coroutine:
        tool_func = delete_resource.coroutine
    else:
        tool_func = getattr(delete_resource, '_run', None)
        if not tool_func:
            raise ValueError(f"Could not extract function from async tool {delete_resource.name}")

    awaitable_tool_func = make_tool_awaitable(tool_func)

    tools_context = {
        'delete_resource': awaitable_tool_func,
    }

    code = """
result = await delete_resource("res:456")
print(result)
"""

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals=tools_context,
        state=mock_state,
        mode='local',
    )

    # Verify the result is None
    assert 'result' in new_vars
    assert new_vars['result'] is None
    assert "None" in result
