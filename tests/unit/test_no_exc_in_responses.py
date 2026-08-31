"""Tests for the static check that keeps exception detail out of server responses.

The checker (``scripts/checks/no_exc_in_responses.py``) enforces the rule established by
#681: nothing taken from an exception may reach a caller-facing response. It flags the
places a response is built — ``HTTPException(detail=...)``, ``JSONResponse(...)``, the A2A
``_rpc_error(...)`` helper, and streaming ``StreamEvent(data=...)`` — when the value
placed there is derived from an exception (``str(exc)``, ``repr(exc)``, an f-string that
interpolates the exception, ``exc.args`` / ``exc.errors()`` / ``exc.json()``, or
``traceback.format_exc()``).

The class name alone (``type(exc).__name__``) is safe and must not be flagged. Which
identifiers count as "the exception" is resolved from real ``except ... as x:`` bindings
(and direct aliases of them), not from a guessed list of common names — so an arbitrary
alias like ``failure`` is caught, and an unrelated variable that just happens to be named
``error`` is not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "no_exc_in_responses.py"
_spec = importlib.util.spec_from_file_location("no_exc_in_responses", _MODULE_PATH)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module under `from __future__
# import annotations` (it looks the module up in sys.modules by __module__).
sys.modules["no_exc_in_responses"] = checker
_spec.loader.exec_module(checker)

pytestmark = pytest.mark.unit


def _codes(src: str) -> list[int]:
    """Return the 1-based line numbers flagged in ``src``."""
    return sorted(v.line for v in checker.find_violations(src, "<test>"))


def _in_except(*stmts: str, alias: str = "e") -> str:
    """Wrap ``stmts`` in a minimal ``except ... as alias:`` block.

    Every real leak site has this shape — the bound name only exists inside the handler —
    so tests exercise the checker the way it actually runs against server code, instead of
    a bare, out-of-context statement.
    """
    body = "".join(f"    {stmt}\n" for stmt in stmts)
    return f"try:\n    pass\nexcept Exception as {alias}:\n{body}"


# --- SHOULD flag -----------------------------------------------------------------


def test_flags_httpexception_detail_str_exc():
    src = _in_except("raise HTTPException(status_code=500, detail=str(e))")
    assert _codes(src) == [4]


def test_flags_httpexception_detail_fstring_bare_exc():
    src = _in_except('raise HTTPException(status_code=401, detail=f"token validation failed: {e}")')
    assert _codes(src) == [4]


def test_flags_httpexception_detail_fstring_str_exc():
    src = _in_except('raise HTTPException(status_code=500, detail=f"Failed to load config: {str(e)}")')
    assert _codes(src) == [4]


def test_flags_str_exc_with_or_fallback():
    src = _in_except('raise HTTPException(status_code=503, detail=str(e) or "unavailable")')
    assert _codes(src) == [4]


def test_flags_jsonresponse_dict_message_str_exc():
    src = _in_except('return JSONResponse({"status": "error", "message": str(e)}, status_code=500)')
    assert _codes(src) == [4]


def test_flags_jsonresponse_arbitrary_key():
    # The value is exception-derived regardless of what key it's filed under.
    src = _in_except('return JSONResponse({"debug": str(e)}, status_code=500)')
    assert _codes(src) == [4]


def test_flags_rpc_error_positional_message():
    src = _in_except("return _rpc_error(rpc_id, _INTERNAL_ERROR, str(e))")
    assert _codes(src) == [4]


def test_flags_stream_event_data_str_exc():
    src = _in_except('yield StreamEvent(name="Error", data=str(e))')
    assert _codes(src) == [4]


def test_flags_traceback_format_exc():
    src = _in_except('return JSONResponse({"traceback": traceback.format_exc()}, status_code=500)')
    assert _codes(src) == [4]


def test_flags_pydantic_errors():
    src = _in_except("raise HTTPException(status_code=422, detail=exc.errors())", alias="exc")
    assert _codes(src) == [4]


def test_flags_exc_args():
    src = _in_except('raise HTTPException(status_code=500, detail=f"{e.args}")')
    assert _codes(src) == [4]


def test_flags_multiple_sites():
    src = _in_except(
        "raise HTTPException(status_code=400, detail=str(e))",
        'raise HTTPException(status_code=404, detail="not found")',
        "raise HTTPException(status_code=500, detail=str(e))",
    )
    assert _codes(src) == [4, 6]


def test_flags_str_of_exception_with_arbitrary_alias():
    # The heuristic is real `except ... as x:` bindings, not a guessed spelling list —
    # an alias like `failure` (not `e`/`exc`/`err`) must still be caught.
    src = _in_except("raise HTTPException(status_code=500, detail=str(failure))", alias="failure")
    assert _codes(src) == [4]


def test_flags_aliased_exception_variable():
    # `err = e` then using `err` is still the caught exception.
    src = _in_except(
        "err = e",
        "raise HTTPException(status_code=500, detail=str(err))",
    )
    assert _codes(src) == [5]


# --- SHOULD NOT flag -------------------------------------------------------------


def test_ok_fixed_message():
    src = 'raise HTTPException(status_code=500, detail="Internal server error")\n'
    assert _codes(src) == []


def test_ok_type_name_is_safe():
    src = _in_except(
        'return _rpc_error(rpc_id, _INTERNAL_ERROR, f"Internal error: {type(exc).__name__}")',
        alias="exc",
    )
    assert _codes(src) == []


def test_ok_non_exception_name_in_fstring():
    src = 'raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")\n'
    assert _codes(src) == []


def test_ok_fixed_message_variable():
    src = 'return JSONResponse({"status": "error", "message": message}, status_code=500)\n'
    assert _codes(src) == []


def test_ok_str_exc_outside_sink_is_ignored():
    src = _in_except("logger.error(str(e))")
    assert _codes(src) == []


def test_ok_type_name_alongside_other_text():
    src = _in_except('raise HTTPException(status_code=500, detail=f"Failed ({type(e).__name__})")')
    assert _codes(src) == []


def test_ok_unrelated_variable_named_error():
    # A parameter or local literally named `error` that was never bound by `except ... as`
    # is not the caught exception — flagging it would be a false positive.
    src = "def handler(error):\n    raise HTTPException(status_code=400, detail=str(error))\n"
    assert _codes(src) == []


def test_ok_exception_name_used_outside_its_handler():
    # `e` is only "the exception" inside its own handler; a same-named variable in another
    # function (or after the handler ends) is unrelated.
    src = (
        "def other(e):\n"
        "    raise HTTPException(status_code=400, detail=str(e))\n"
        "def handler():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:\n"
        "        log_error_ref(e)\n"
    )
    assert _codes(src) == []


# --- inline suppression ----------------------------------------------------------


def test_noqa_pragma_suppresses():
    src = _in_except("raise HTTPException(status_code=500, detail=str(e))  # noqa: exc-in-response")
    assert _codes(src) == []


def test_noqa_pragma_on_later_line_of_multiline_call_suppresses():
    src = (
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    raise HTTPException(\n"
        "        status_code=500,\n"
        "        detail=str(e),  # noqa: exc-in-response\n"
        "    )\n"
    )
    assert _codes(src) == []


# --- baseline behavior -----------------------------------------------------------


def test_baseline_allowlists_known_snippet():
    src = _in_except("raise HTTPException(status_code=500, detail=str(e))")
    violations = checker.find_violations(src, "server/foo.py")
    baseline = checker.build_baseline({"server/foo.py": violations})
    # A known snippet in the baseline is not a *new* violation.
    new = checker.new_violations(violations, "server/foo.py", baseline)
    assert new == []


def test_baseline_does_not_allowlist_new_snippet():
    old = checker.find_violations(
        _in_except("raise HTTPException(status_code=500, detail=str(e))"), "server/foo.py"
    )
    baseline = checker.build_baseline({"server/foo.py": old})
    # A different offending line in the same file is still flagged.
    current = checker.find_violations(
        _in_except("raise HTTPException(status_code=503, detail=str(e))"), "server/foo.py"
    )
    new = checker.new_violations(current, "server/foo.py", baseline)
    assert len(new) == 1


def test_baseline_flags_extra_duplicate_beyond_baselined_count():
    # Occurrence-specific matching: baselining one instance of a snippet must not silently
    # allow a second, later-added copy of that exact same call in the same file.
    single = _in_except("raise HTTPException(status_code=500, detail=str(e))")
    original = checker.find_violations(single, "server/foo.py")
    baseline = checker.build_baseline({"server/foo.py": original})

    duplicated = _in_except(
        "raise HTTPException(status_code=500, detail=str(e))",
        "raise HTTPException(status_code=500, detail=str(e))",
    )
    current = checker.find_violations(duplicated, "server/foo.py")
    new = checker.new_violations(current, "server/foo.py", baseline)
    assert len(new) == 1


def test_baseline_distinguishes_multiline_calls_with_same_first_line():
    # A baseline keyed on only the call's first line would let *any* other multiline
    # HTTPException call through, no matter what it leaks. The baseline identity must be
    # the full call, not just where it starts.
    src = (
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    raise HTTPException(\n"
        "        status_code=500,\n"
        "        detail=str(e),\n"
        "    )\n"
    )
    violations = checker.find_violations(src, "server/foo.py")
    baseline = checker.build_baseline({"server/foo.py": violations})

    other = (
        "try:\n"
        "    pass\n"
        "except Exception as exc:\n"
        "    raise HTTPException(\n"
        "        status_code=503,\n"
        "        detail=str(exc),\n"
        "    )\n"
    )
    other_violations = checker.find_violations(other, "server/foo.py")
    new = checker.new_violations(other_violations, "server/foo.py", baseline)
    assert len(new) == 1


# --- CLI ---------------------------------------------------------------------------


def test_main_reports_multiple_findings_in_one_file(tmp_path, capsys):
    # Regression: two new findings in the same file must not crash the reporter's sort.
    target = tmp_path / "routes.py"
    target.write_text(
        "from fastapi import HTTPException\n"
        "def a():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:\n"
        "        raise HTTPException(status_code=400, detail=str(e))\n"
        "def b():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        raise HTTPException(status_code=500, detail=str(exc))\n"
    )
    rc = checker.main([str(tmp_path), "--baseline", str(tmp_path / "absent.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "2 new finding(s)" in err


def test_main_update_then_check_passes(tmp_path):
    target = tmp_path / "routes.py"
    target.write_text(
        "from fastapi import HTTPException\n"
        "def a():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:\n"
        "        raise HTTPException(status_code=400, detail=str(e))\n"
    )
    baseline = tmp_path / "baseline.json"
    assert checker.main([str(tmp_path), "--baseline", str(baseline), "--update"]) == 0
    assert baseline.exists()
    # With the baseline in place, the same source is no longer a new finding.
    assert checker.main([str(tmp_path), "--baseline", str(baseline)]) == 0
