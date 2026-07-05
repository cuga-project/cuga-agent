#!/usr/bin/env python3
"""
DEPRECATED: This test is no longer needed as we have moved to using the E2B sandbox.
Test for sandbox local execution with simple code

Tests basic functionality of running code in the local sandbox environment.
"""

import pytest

from cuga.backend.tools_env.code_sandbox.sandbox import run_local, get_premable

pytestmark = pytest.mark.skip(reason="Deprecated: superseded by cuga_lite executor tests")


def test_basic_code_execution():
    """Test basic Python code execution in sandbox"""
    code = """
print("Hello, World!")
result = 2 + 3
print(f"2 + 3 = {result}")
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "Hello, World!" in result.stdout
    assert "2 + 3 = 5" in result.stdout
    assert result.stderr == ""


def test_variable_assignment_and_print():
    """Test variable assignment and printing"""
    code = """
x = 42
y = "test"
z = [1, 2, 3]
print(f"x = {x}")
print(f"y = {y}")
print(f"z = {z}")
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "x = 42" in result.stdout
    assert "y = test" in result.stdout
    assert "z = [1, 2, 3]" in result.stdout
    assert result.stderr == ""


def test_function_definition_and_call():
    """Test function definition and execution"""
    code = """
def add_numbers(a, b):
    return a + b

result = add_numbers(10, 20)
print(f"10 + 20 = {result}")
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "10 + 20 = 30" in result.stdout
    assert result.stderr == ""


def test_exception_handling():
    """Test exception handling in executed code"""
    code = """
try:
    result = 1 / 0
except ZeroDivisionError as e:
    print(f"Caught exception: {e}")
finally:
    print("Finally block executed")
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "Caught exception: division by zero" in result.stdout
    assert "Finally block executed" in result.stdout
    assert result.stderr == ""


def test_import_and_usage():
    """Test importing and using standard library modules"""
    code = """
import json
import datetime

data = {"name": "test", "value": 123}
json_str = json.dumps(data)
print(f"JSON: {json_str}")

now = datetime.datetime.now()
print(f"Current time: {now}")
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "JSON:" in result.stdout
    assert '"name": "test"' in result.stdout
    assert '"value": 123' in result.stdout
    assert "Current time:" in result.stdout
    assert result.stderr == ""


def test_system_exit_handling():
    """Test handling of SystemExit calls"""
    code = """
print("About to exit")
exit(42)
print("This should not print")
"""

    result = run_local(code)

    assert result.exit_code == 42
    # Note: PythonREPL doesn't capture stdout before SystemExit due to internal redirection
    assert "This should not print" not in result.stdout
    assert "SystemExit: 42" in result.stderr


def test_call_api_function_definition():
    """Test that call_api function is properly defined in preamble"""
    preamble = get_premable(is_local=True)

    assert "def call_api(app_name, api_name, args=None):" in preamble
    assert "if args is None:" in preamble
    assert "args = {}" in preamble


def test_complex_data_structures():
    """Test working with complex data structures"""
    code = """
# Dictionary operations
user = {"name": "Alice", "age": 30, "active": True}
user["city"] = "New York"
print(f"User: {user}")

# List operations
numbers = [1, 2, 3, 4, 5]
squares = [x**2 for x in numbers]
print(f"Squares: {squares}")

# Set operations
unique_items = set([1, 2, 2, 3, 3, 3])
print(f"Unique items: {unique_items}")
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "User:" in result.stdout
    assert "Alice" in result.stdout
    assert "New York" in result.stdout
    assert "Squares:" in result.stdout
    assert "[1, 4, 9, 16, 25]" in result.stdout
    assert "Unique items:" in result.stdout
    assert "{1, 2, 3}" in result.stdout
    assert result.stderr == ""


def test_string_operations():
    """Test various string operations"""
    code = """
text = "Hello, World!"
print(f"Original: {text}")
print(f"Upper: {text.upper()}")
print(f"Lower: {text.lower()}")
print(f"Length: {len(text)}")
print(f"Split: {text.split(', ')}")

# String formatting
name = "Bob"
age = 25
formatted = f"Name: {name}, Age: {age}"
print(formatted)
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "Original: Hello, World!" in result.stdout
    assert "Upper: HELLO, WORLD!" in result.stdout
    assert "Lower: hello, world!" in result.stdout
    assert "Length: 13" in result.stdout
    assert "['Hello', 'World!']" in result.stdout
    assert "Name: Bob, Age: 25" in result.stdout
    assert result.stderr == ""


def test_control_flow():
    """Test control flow statements"""
    code = """
# If-elif-else
x = 10
if x < 5:
    result = "small"
elif x < 15:
    result = "medium"
else:
    result = "large"
print(f"Size: {result}")

# For loop
total = 0
for i in range(1, 6):
    total += i
print(f"Sum 1-5: {total}")

# While loop
count = 0
while count < 3:
    print(f"Count: {count}")
    count += 1
"""

    result = run_local(code)

    assert result.exit_code == 0
    assert "Size: medium" in result.stdout
    assert "Sum 1-5: 15" in result.stdout
    assert "Count: 0" in result.stdout
    assert "Count: 1" in result.stdout
    assert "Count: 2" in result.stdout
    assert result.stderr == ""
