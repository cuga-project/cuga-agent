from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
    extract_and_combine_codeblocks,
)


class TestExtractAndCombineCodeblocks:
    """Test suite for extract_and_combine_codeblocks function."""

    def test_plain_identifier(self):
        """Plain identifier should be considered data output."""
        result = extract_and_combine_codeblocks('sami')
        assert result == ''

    def test_plain_number(self):
        """Plain number should be considered data output."""
        result = extract_and_combine_codeblocks('123')
        assert result == ''

    def test_invalid_syntax(self):
        """Invalid syntax should return empty string."""
        result = extract_and_combine_codeblocks('hello world')
        assert result == ''

    def test_function_call(self):
        """Function call without markdown should compile and return."""
        result = extract_and_combine_codeblocks('print("hello")')
        assert result == 'print("hello")'

    def test_assignment(self):
        """Assignment statement should compile and return."""
        result = extract_and_combine_codeblocks('x = 5\nprint(x)')
        assert result == 'x = 5\nprint(x)'

    def test_multiple_statements(self):
        """Multiple statements should compile and return."""
        result = extract_and_combine_codeblocks('x = 5\nprint(x)')
        assert result == 'x = 5\nprint(x)'

    def test_function_definition(self):
        """Function definition should compile and return."""
        result = extract_and_combine_codeblocks('def foo():\n    pass\nprint("function defined")')
        assert result == 'def foo():\n    pass\nprint("function defined")'

    def test_simple_markdown_block(self):
        """Simple markdown code block should extract code."""
        result = extract_and_combine_codeblocks('```python\nprint("hello")\n```')
        assert result == 'print("hello")'

    def test_text_before_markdown(self):
        """Text before markdown should extract only code."""
        result = extract_and_combine_codeblocks('Here is some code:\n```python\nx = 5\nprint(x)\n```')
        assert result == 'x = 5\nprint(x)'

    def test_text_before_markdown_with_newline(self):
        """Text before markdown should extract only code."""
        result = extract_and_combine_codeblocks('Here is some code:\n\n```python\n\n\nx = 5\nprint(x)\n\n```')
        assert result == 'x = 5\nprint(x)'

    def test_text_and_markdown(self):
        """Text with markdown should extract only code."""
        result = extract_and_combine_codeblocks('Let me show you:\n```python\nprint("test")\n```')
        assert result == 'print("test")'

    def test_markdown_with_text_after(self):
        """Markdown with text after should extract only code."""
        result = extract_and_combine_codeblocks('```python\nx = 10\nprint(x)\n```\nThat was the code')
        assert result == 'x = 10\nprint(x)'

    def test_markdown_with_text(self):
        """Markdown with text should extract only code."""
        result = extract_and_combine_codeblocks('```python\nprint("hi")\n```\nEnd of example')
        assert result == 'print("hi")'

    def test_text_markdown_text(self):
        """Text before and after markdown should extract only code."""
        result = extract_and_combine_codeblocks(
            'Here is code:\n```python\ny = 20\nprint(y)\n```\nThat was it'
        )
        assert result == 'y = 20\nprint(y)'

    def test_full_example_with_loop(self):
        """Full example with loop in markdown should extract code."""
        result = extract_and_combine_codeblocks(
            'Example:\n```python\nfor i in range(5):\n    print(i)\n```\nDone!'
        )
        assert result == 'for i in range(5):\n    print(i)'

    def test_two_markdown_blocks(self):
        """Two markdown blocks should combine with double newline."""
        result = extract_and_combine_codeblocks(
            '```python\nx = 1\nprint(x)\n```\n```python\ny = 2\nprint(y)\n```'
        )
        assert result == 'x = 1\nprint(x)\n\ny = 2\nprint(y)'

    def test_two_blocks_with_text(self):
        """Two blocks with text between should combine code only."""
        result = extract_and_combine_codeblocks(
            'Code 1:\n```python\na = 5\nprint(a)\n```\nCode 2:\n```python\nb = 10\nprint(b)\n```'
        )
        assert result == 'a = 5\nprint(a)\n\nb = 10\nprint(b)'

    def test_function_in_markdown(self):
        """Complex multi-line function in markdown should extract properly."""
        result = extract_and_combine_codeblocks(
            '```python\ndef calculate(x, y):\n    result = x + y\n    return result\nprint(calculate(2, 3))\n```'
        )
        assert result == 'def calculate(x, y):\n    result = x + y\n    return result\nprint(calculate(2, 3))'

    def test_empty_string(self):
        """Empty string should return empty."""
        result = extract_and_combine_codeblocks('')
        assert result == ''

    def test_whitespace_only(self):
        """Whitespace only should return empty."""
        result = extract_and_combine_codeblocks('   \n  \n  ')
        assert result == ''

    def test_markdown_without_python_tag(self):
        """Markdown without python tag should not match (requires python tag)."""
        result = extract_and_combine_codeblocks('```\nprint("test")\n```')
        assert result == ''

    def test_incomplete_markdown_block_with_valid_code_recovered(self):
        """Unclosed fence with valid code returns the code."""
        result = extract_and_combine_codeblocks('```python\nprint("test")')
        assert result == 'print("test")'

    def test_incomplete_markdown_block_recovers_multiline_code(self):
        """Unclosed fence with multi-line valid Python preceded by prose returns the code."""
        text = (
            "Config is available, so the scan can proceed. "
            "I'll list all Gmail messages...\n"
            "```python\n"
            "import json, re\n"
            "from datetime import datetime, timedelta\n"
            "print('starting scan')\n"
        )
        result = extract_and_combine_codeblocks(text)
        assert result.startswith("import json, re")
        assert "print('starting scan')" in result

    def test_incomplete_markdown_block_with_invalid_code_returns_empty(self):
        """Unclosed fence with non-compilable text returns empty."""
        result = extract_and_combine_codeblocks('```python\nthis is not python at all $$$')
        assert result == ''

    def test_incomplete_markdown_block_strips_partial_trailing_fence(self):
        """Unclosed fence with 1 or 2 trailing backticks still returns the code."""
        result_one_tick = extract_and_combine_codeblocks('```python\nprint("test")\n`')
        result_two_ticks = extract_and_combine_codeblocks('```python\nprint("test")\n``')
        assert result_one_tick == 'print("test")'
        assert result_two_ticks == 'print("test")'

    def test_incomplete_markdown_block_recovers_valid_code_with_trailing_prose(self):
        """Unclosed fence with valid code followed by prose returns just the code."""
        text = '```python\nprint("test")\n\nHope that helps!\n'
        result = extract_and_combine_codeblocks(text)
        assert result == 'print("test")'

    def test_incomplete_same_line_python_fence_recovered(self):
        """Unclosed fence with code on the same line as ```python returns the code."""
        result = extract_and_combine_codeblocks('```python print("x")')
        assert result == 'print("x")'

    def test_nested_code_structures(self):
        """Nested code structures should be preserved."""
        code = '''def outer():
    def inner():
        return 42
    return inner()
print(outer())'''
        result = extract_and_combine_codeblocks(code)
        assert result == code

    def test_code_with_strings_containing_backticks(self):
        """Code with strings containing backticks should work."""
        result = extract_and_combine_codeblocks('x = "some `text` here"\nprint(x)')
        assert result == 'x = "some `text` here"\nprint(x)'

    def test_markdown_with_empty_code_block(self):
        """Markdown with empty code block should return empty string."""
        result = extract_and_combine_codeblocks('```python\n```')
        assert result == ''

    def test_class_definition(self):
        """Class definition should compile and return."""
        code = '''class MyClass:
    def __init__(self):
        self.value = 10
obj = MyClass()
print(obj.value)'''
        result = extract_and_combine_codeblocks(code)
        assert result == code

    def test_import_statements(self):
        """Import statements should compile and return."""
        result = extract_and_combine_codeblocks('import os\nfrom sys import path\nprint("imports done")')
        assert result == 'import os\nfrom sys import path\nprint("imports done")'

    def test_markdown_with_class_definition(self):
        """Class definition in markdown should extract properly."""
        result = extract_and_combine_codeblocks(
            '```python\nclass Test:\n    pass\nobj = Test()\nprint("class created")\n```'
        )
        assert result == 'class Test:\n    pass\nobj = Test()\nprint("class created")'

    def test_multiple_functions_in_markdown(self):
        """Multiple functions in one markdown block should extract all."""
        code = '''def func1():
    return 1

def func2():
    return 2
print(func1(), func2())'''
        result = extract_and_combine_codeblocks(f'```python\n{code}\n```')
        assert result == code

    def test_async_await_statement(self):
        """Async await statement should compile and return."""
        result = extract_and_combine_codeblocks(
            'accounts_data = await get_accounts_accounts()\nprint(accounts_data)'
        )
        assert result == 'accounts_data = await get_accounts_accounts()\nprint(accounts_data)'

    def test_async_await_statement_without_variable(self):
        """Async await statement without variable should compile and return."""
        result = extract_and_combine_codeblocks('await get_accounts_accounts()\nprint(accounts_data)')
        assert result == 'await get_accounts_accounts()\nprint(accounts_data)'

    def test_async_await_in_mid_of_code(self):
        """Async await statement in mid of code should compile and return."""
        result = extract_and_combine_codeblocks(
            'accounts_data = await get_accounts_accounts()\nprint(accounts_data)\nawait get_accounts_accounts()'
        )
        assert (
            result
            == 'accounts_data = await get_accounts_accounts()\nprint(accounts_data)\nawait get_accounts_accounts()'
        )

    def test_string_array_should_not_be_code(self):
        """Array of strings should not be considered code."""
        string_array = "['item1', 'item2', 'item3']"
        result = extract_and_combine_codeblocks(string_array)
        assert result == ''

    def test_email_array_should_not_be_code(self):
        """Array of email addresses (strings) should not be considered code."""
        email_array = "['sarah.bell@gammadeltainc.partners.org', 'sharon.jimenez@upsiloncorp.innovation.org']"
        result = extract_and_combine_codeblocks(email_array)
        assert result == ''

    def test_number_array_should_not_be_code(self):
        """Array of numbers should not be considered code."""
        number_array = "[1, 2, 3, 4, 5]"
        result = extract_and_combine_codeblocks(number_array)
        assert result == ''

    def test_mixed_constant_array_should_not_be_code(self):
        """Array with mixed constants should not be considered code."""
        mixed_array = "['string', 42, True, False]"
        result = extract_and_combine_codeblocks(mixed_array)
        assert result == ''

    def test_single_boolean_true_should_not_be_code(self):
        """Single boolean True should not be considered code."""
        result = extract_and_combine_codeblocks('True')
        assert result == ''

    def test_single_boolean_false_should_not_be_code(self):
        """Single boolean False should not be considered code."""
        result = extract_and_combine_codeblocks('False')
        assert result == ''

    def test_single_string_constant_should_not_be_code(self):
        """Single string constant should not be considered code."""
        result = extract_and_combine_codeblocks("'hello world'")
        assert result == ''

    def test_single_word_without_spaces_should_not_be_code(self):
        """Single word without spaces should not be considered code."""
        result = extract_and_combine_codeblocks('hello')
        assert result == ''

    def test_decimal_number_should_not_be_code(self):
        """Decimal number should not be considered code."""
        result = extract_and_combine_codeblocks('123.45')
        assert result == ''

    def test_multiline_json_array_with_script_true(self):
        """Multiline JSON array output with 'Has script: True' should not be considered code."""
        json_output = '''Has script: True, script value: [
    "sarah.bell@gammadeltainc.partners.org",
    "sharon.jimenez@upsiloncorp.innovation.org",
    "ruth.ross@sigmasystems.operations.com",
    "dorothy.richardson@nextgencorp.gmail.com",
    "james.richardson@technovate.com",
    "michael.torres@pinnacle-solutions.net",
    "emma.larsson@nexus-digital.co"
]'''
        result = extract_and_combine_codeblocks(json_output)
        assert result == ''

    def test_multiline_json_array_should_not_be_code(self):
        """Multiline JSON array should not be considered code."""
        json_array = '''[
    "item1@example.com",
    "item2@example.com",
    "item3@example.com"
]'''
        result = extract_and_combine_codeblocks(json_array)
        assert result == ''

    def test_multiline_emails(self):
        """Multiline JSON array should not be considered code."""
        json_array = '''
item1@example.com
item2@example.com
item3@example.com
'''
        result = extract_and_combine_codeblocks(json_array)
        assert result == ''

    def test_multiline_json_object_should_not_be_code(self):
        """Multiline JSON object should not be considered code."""
        json_obj = '''{
    "name": "John Doe",
    "email": "john@example.com",
    "age": 30
}'''
        result = extract_and_combine_codeblocks(json_obj)
        assert result == ''

    def test_print_with_mismatched_brackets(self):
        """Code with mismatched brackets/parentheses should return empty string."""
        malformed_code = """print(variable_contacts)
print('sarah.bell@gammadeltainc.partners.org')
print('sharon.jimenez@upsiloncorp.innovation.org')
print('ruth.ross@sigmasystems.operations.com')
print('dorothy.richardson@nextgencorp.gmail.com')
print('james.richardson@technovate.com')
print('michael.torres@pinnacle-solutions.net')
print('emma.larsson@nexus-digital.co'])"""
        result = extract_and_combine_codeblocks(malformed_code)
        assert result == ''
