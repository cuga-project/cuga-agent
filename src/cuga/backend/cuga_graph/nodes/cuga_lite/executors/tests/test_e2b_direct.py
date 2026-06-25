"""Direct E2B integration test - exploring how to inject variables and tools."""

import os
import pytest


@pytest.mark.asyncio
@pytest.mark.skipif(
    not pytest.importorskip("e2b_code_interpreter", reason="e2b-code-interpreter not installed"),
    reason="E2B not available",
)
@pytest.mark.skipif(
    not os.environ.get("E2B_API_KEY"),
    reason="E2B_API_KEY environment variable not set",
)
async def test_e2b_direct_with_variables_and_tools():
    """
    Direct test of E2B sandbox with variables and async tool functions.

    This test explores how to properly inject:
    1. Simple variables (strings, numbers) from previous execution
    2. Async tool functions (as Python source code)
    """
    from e2b_code_interpreter import Sandbox

    # Define dummy async tool functions (like what would be in _locals)
    async def get_account_name(account_id: str) -> str:
        """Get account name by ID."""
        accounts = {"acc_1": "Acme Corp", "acc_2": "TechStart Inc"}
        return accounts.get(account_id, "Unknown")

    async def get_account_revenue(account_id: str) -> float:
        """Get account revenue by ID."""
        revenues = {"acc_1": 1500000.0, "acc_2": 850000.0}
        return revenues.get(account_id, 0.0)

    # Variables from previous execution (like what would be in _locals)
    previous_variables = {
        "target_account": "acc_1",
        "threshold": 1000000.0,
    }

    # Tools from _locals
    previous_tools = {
        "get_account_name": get_account_name,
        "get_account_revenue": get_account_revenue,
    }

    # Step 1: Serialize variables into Python code
    variables_code = _serialize_variables(previous_variables)

    # Step 2: Serialize tool functions into Python code
    tools_code = _serialize_tools(previous_tools)

    # Step 3: User's code that uses variables and tools
    user_code = """
# Use variable from previous execution
account_id = target_account

# Call async tools
name = await get_account_name(account_id)
revenue = await get_account_revenue(account_id)

# Compute result using another variable
is_high_value = revenue > threshold

print(f"Account: {name}")
print(f"Revenue: ${revenue:,.0f}")
print(f"High value: {is_high_value}")

result = {
    "account_id": account_id,
    "name": name,
    "revenue": revenue,
    "is_high_value": is_high_value
}
"""

    # Step 4: Combine everything into complete code for E2B
    complete_code = f"""
import asyncio

{tools_code}

{variables_code}

async def _async_main():
{_indent_code(user_code, 1)}
    return locals()

# Execute
_result_locals = await asyncio.wait_for(_async_main(), timeout=30)
print(_result_locals)
"""

    print("=" * 60)
    print("Complete code to send to E2B:")
    print("=" * 60)
    print(complete_code)
    print("=" * 60)

    # Step 5: Execute in E2B sandbox
    with Sandbox.create() as sandbox:
        execution = sandbox.run_code(complete_code)

        # Check for errors
        if execution.error:
            pytest.fail(f"E2B execution error: {execution.error}")

        # Get stdout
        stdout_lines = execution.logs.stdout
        stdout = "\n".join(map(str.strip, stdout_lines))

        print("\nStdout from E2B:")
        print(stdout)

        # Verify output
        assert "Account: Acme Corp" in stdout
        assert "Revenue: $1,500,000" in stdout or "Revenue: $1500000" in stdout
        assert "High value: True" in stdout

        # Parse returned locals from stdout (last line after the prints)
        # The dict is printed on a separate line
        lines = stdout.split('\n')
        dict_line = None
        for line in reversed(lines):
            if line.strip().startswith('{') and 'account_id' in line:
                dict_line = line.strip()
                break

        assert dict_line is not None, "Could not find result dict in output"

        import ast

        result_locals = ast.literal_eval(dict_line)

        print("\nReturned locals:")
        print(result_locals)

        # Verify new variables
        assert 'result' in result_locals
        assert result_locals['result']['name'] == "Acme Corp"
        assert result_locals['result']['revenue'] == 1500000.0
        assert result_locals['result']['is_high_value'] is True


def _serialize_variables(variables: dict) -> str:
    """Serialize variables into Python assignment statements."""
    import json

    lines = ["# Variables from previous execution"]

    for var_name, var_value in variables.items():
        # Skip internal variables
        if var_name.startswith('_'):
            continue

        # Serialize based on type
        if isinstance(var_value, str):
            lines.append(f"{var_name} = {repr(var_value)}")
        elif isinstance(var_value, (int, float, bool, type(None))):
            lines.append(f"{var_name} = {var_value}")
        elif isinstance(var_value, (list, dict, tuple)):
            json_str = json.dumps(var_value)
            lines.append(f"{var_name} = {json_str}")
        else:
            # Skip non-serializable
            continue

    return "\n".join(lines)


def _serialize_tools(tools: dict) -> str:
    """Serialize async tool functions into Python source code."""
    import inspect
    import textwrap

    lines = ["# Tool functions from previous execution"]

    for tool_name, tool_func in tools.items():
        # Skip non-functions
        if not callable(tool_func):
            continue

        # Skip internal functions
        if tool_name.startswith('_'):
            continue

        try:
            # Get the source code of the function and dedent it
            source = inspect.getsource(tool_func)
            dedented_source = textwrap.dedent(source)
            lines.append(dedented_source)
        except (OSError, TypeError) as e:
            print(f"Warning: Could not serialize tool '{tool_name}': {e}")
            continue

    return "\n".join(lines)


def _indent_code(code: str, levels: int = 1) -> str:
    """Indent code by specified number of levels (4 spaces per level)."""
    indent = "    " * levels
    return "\n".join(indent + line for line in code.split("\n"))
