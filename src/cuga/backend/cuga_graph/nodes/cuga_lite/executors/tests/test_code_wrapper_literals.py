"""CodeWrapper must not rewrite the contents of string literals.

Text-indent via ``'\\n'.join('    ' + line ...)`` prepends spaces to every
source line, including the interior of triple-quoted / implicit-join strings.
These checks pin the runtime value of those literals in both wrap modes, plus
join-adjacent edge cases (blank lines, leading spaces, await, auto-print).

Loaded directly (not via the cuga package) to stay fast/import-light.
"""

import asyncio
import importlib.util
import pathlib
import threading

import pytest

pytestmark = pytest.mark.unit

_CW = pathlib.Path(__file__).resolve().parents[1] / "common" / "code_wrapper.py"
_spec = importlib.util.spec_from_file_location("code_wrapper_literals", _CW)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
CodeWrapper = _mod.CodeWrapper

FT = "2021-03-14T15:09:26"


def _run(user_code: str, fake_datetime=None, limit: float = 8.0) -> dict:
    wrapped = CodeWrapper.wrap_code(user_code, fake_datetime=fake_datetime)
    compile(wrapped, "<wrapped>", "exec")
    box: dict = {}

    def target():
        try:
            g: dict = {}
            exec(wrapped, g)
            box["res"] = asyncio.run(g["_async_main"]())
        except Exception as e:  # noqa: BLE001 — surfaced below
            box["err"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(limit)
    assert not t.is_alive(), "wrapped async code did not finish"
    if "err" in box:
        raise box["err"]
    return box["res"]


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_triple_double_quoted_multiline_keeps_source_value(fake_datetime):
    code = 'body = """line1\nline2\nline3"""\n'
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "line1\nline2\nline3"


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_triple_single_quoted_multiline_keeps_source_value(fake_datetime):
    code = "body = '''line1\nline2\nline3'''\n"
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "line1\nline2\nline3"


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_blank_line_inside_triple_quoted_string_is_not_filled_with_spaces(fake_datetime):
    code = 'body = """line1\n\nline3"""\n'
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "line1\n\nline3"
    assert "    " not in res["body"]


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_intentional_leading_spaces_inside_string_are_preserved_exactly(fake_datetime):
    code = 'body = """line1\n    indented\nline3"""\n'
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "line1\n    indented\nline3"


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_multiline_string_inside_indented_block_keeps_value(fake_datetime):
    code = "if True:\n    body = \"\"\"line1\nline2\nline3\"\"\"\n"
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "line1\nline2\nline3"


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_multiline_fstring_interpolates_without_indent_corruption(fake_datetime):
    code = 'name = "Ada"\nbody = f"""Hello {name}\nline2"""\n'
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "Hello Ada\nline2"


@pytest.mark.parametrize("fake_datetime", [None, FT], ids=["no_freeze", "benchmark"])
def test_raw_multiline_string_keeps_backslashes_and_newlines(fake_datetime):
    code = 'body = r"""line1\\n\nline2"""\n'
    res = _run(code, fake_datetime=fake_datetime)
    assert res["body"] == "line1\\n\nline2"


def test_implicit_concat_across_parentheses_is_not_corrupted():
    code = 'body = (\n    "line1\\n"\n    "line2"\n)\n'
    res = _run(code, fake_datetime=None)
    assert res["body"] == "line1\nline2"


def test_escaped_newlines_in_single_line_literal_unchanged():
    code = 'body = "line1\\nline2\\nline3"\n'
    res = _run(code, fake_datetime=None)
    assert res["body"] == "line1\nline2\nline3"


def test_string_containing_print_call_text_does_not_block_auto_print():
    code = 'note = """please print(this)"""\n42'
    wrapped = CodeWrapper.wrap_code(code, fake_datetime=None)
    g: dict = {}
    exec(wrapped, g)
    # last expression must still be auto-printed; "print(" inside the string
    # is not a real print() statement.
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        res = asyncio.run(g["_async_main"]())
    assert res["note"] == "please print(this)"
    assert "42" in buf.getvalue()


def test_await_inside_wrapped_multiline_block_still_runs():
    code = "import asyncio\nbody = \"\"\"line1\nline2\"\"\"\nawait asyncio.sleep(0)\ndone = True\n"
    res = _run(code, fake_datetime=FT)
    assert res["body"] == "line1\nline2"
    assert res["done"] is True


def test_nested_def_with_multiline_docstring_keeps_doc():
    code = (
        "def helper():\n    \"\"\"first\n    second\n    \"\"\"\n    return helper.__doc__\ndoc = helper()\n"
    )
    res = _run(code, fake_datetime=None)
    assert res["doc"] == "first\n    second\n    "


def test_future_import_is_lifted_out_of_the_function():
    code = "from __future__ import annotations\nx = 1\n"
    wrapped = CodeWrapper.wrap_code(code, fake_datetime=None)
    compile(wrapped, "<wrapped>", "exec")
    res = _run(code, fake_datetime=None)
    assert res["x"] == 1


def test_empty_and_comment_only_code_still_wrap():
    assert "return locals()" in CodeWrapper.wrap_code("", fake_datetime=None)
    res = _run("# only a comment\n", fake_datetime=None)
    assert isinstance(res, dict)


def test_assignment_last_statement_is_not_auto_printed():
    wrapped = CodeWrapper.wrap_code('body = """a\nb"""\n', fake_datetime=None)
    assert "print(" not in wrapped


def test_wrap_in_async_def_preserves_multiline_and_blank_lines():
    wrapped = CodeWrapper.wrap_in_async_def(
        'body = """line1\n\nline3"""\nreturn body\n',
        "__cuga_async_wrapper__",
    )
    g: dict = {}
    exec(wrapped, g)
    assert asyncio.run(g["__cuga_async_wrapper__"]()) == "line1\n\nline3"


def test_code_agent_wrap_preserves_multiline_without_auto_print():
    """CodeAgent wrap adds json and must not text-indent literals."""
    wrap = getattr(CodeWrapper, "wrap_code_for_code_agent", None)
    assert wrap is not None, "CodeAgent wrap must live on CodeWrapper so it cannot reintroduce join-indent"
    for fake in (None, FT):
        wrapped = wrap('body = """line1\nline2\nline3"""\n', fake_datetime=fake)
        assert "import json" in wrapped
        g: dict = {}
        exec(wrapped, g)
        res = asyncio.run(g["_async_main"]())
        assert res["body"] == "line1\nline2\nline3"
