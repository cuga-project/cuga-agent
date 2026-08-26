"""Tests for the static check that keeps exception detail out of server responses.

The checker (``scripts/checks/no_exc_in_responses.py``) enforces the rule established by
#681: nothing taken from an exception may reach a caller-facing response. It flags the
places a response is built — ``HTTPException(detail=...)``, ``JSONResponse(...)``, the A2A
``_rpc_error(...)`` helper, and streaming ``StreamEvent(data=...)`` — when the value
placed there is derived from an exception (``str(exc)``, ``repr(exc)``, an f-string that
interpolates the exception, ``exc.args`` / ``exc.errors()`` / ``exc.json()``, or
``traceback.format_exc()``).

The class name alone (``type(exc).__name__``) is safe and must not be flagged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "checks" / "no_exc_in_responses.py"
_spec = importlib.util.spec_from_file_location("no_exc_in_responses", _MODULE_PATH)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module under `from __future__
# import annotations` (it looks the module up in sys.modules by __module__).
sys.modules["no_exc_in_responses"] = checker
_spec.loader.exec_module(checker)


def _codes(src: str) -> list[int]:
    """Return the 1-based line numbers flagged in ``src``."""
    return sorted(v.line for v in checker.find_violations(src, "<test>"))


# --- SHOULD flag -----------------------------------------------------------------


def test_flags_httpexception_detail_str_exc():
    src = "raise HTTPException(status_code=500, detail=str(e))\n"
    assert _codes(src) == [1]


def test_flags_httpexception_detail_fstring_bare_exc():
    src = 'raise HTTPException(status_code=401, detail=f"token validation failed: {e}")\n'
    assert _codes(src) == [1]


def test_flags_httpexception_detail_fstring_str_exc():
    src = 'raise HTTPException(status_code=500, detail=f"Failed to load config: {str(e)}")\n'
    assert _codes(src) == [1]


def test_flags_str_exc_with_or_fallback():
    src = 'raise HTTPException(status_code=503, detail=str(e) or "unavailable")\n'
    assert _codes(src) == [1]


def test_flags_jsonresponse_dict_message_str_exc():
    src = 'return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)\n'
    assert _codes(src) == [1]


def test_flags_rpc_error_positional_message():
    src = "return _rpc_error(rpc_id, _INTERNAL_ERROR, str(e))\n"
    assert _codes(src) == [1]


def test_flags_stream_event_data_str_exc():
    src = 'yield StreamEvent(name="Error", data=str(e))\n'
    assert _codes(src) == [1]


def test_flags_traceback_format_exc():
    src = 'return JSONResponse({"traceback": traceback.format_exc()}, status_code=500)\n'
    assert _codes(src) == [1]


def test_flags_pydantic_errors():
    src = 'raise HTTPException(status_code=422, detail=exc.errors())\n'
    assert _codes(src) == [1]


def test_flags_exc_args():
    src = 'raise HTTPException(status_code=500, detail=f"{e.args}")\n'
    assert _codes(src) == [1]


def test_flags_multiple_sites():
    src = (
        "raise HTTPException(status_code=400, detail=str(e))\n"
        'raise HTTPException(status_code=404, detail="not found")\n'
        "raise HTTPException(status_code=500, detail=str(exc))\n"
    )
    assert _codes(src) == [1, 3]


# --- SHOULD NOT flag -------------------------------------------------------------


def test_ok_fixed_message():
    src = 'raise HTTPException(status_code=500, detail="Internal server error")\n'
    assert _codes(src) == []


def test_ok_type_name_is_safe():
    src = 'return _rpc_error(rpc_id, _INTERNAL_ERROR, f"Internal error: {type(exc).__name__}")\n'
    assert _codes(src) == []


def test_ok_non_exception_name_in_fstring():
    src = 'raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")\n'
    assert _codes(src) == []


def test_ok_fixed_message_variable():
    src = 'return JSONResponse({"status": "error", "message": message}, status_code=500)\n'
    assert _codes(src) == []


def test_ok_str_exc_outside_sink_is_ignored():
    src = "logger.error(str(e))\n"
    assert _codes(src) == []


def test_ok_type_name_alongside_other_text():
    src = 'raise HTTPException(status_code=500, detail=f"Failed ({type(e).__name__})")\n'
    assert _codes(src) == []


# --- inline suppression ----------------------------------------------------------


def test_noqa_pragma_suppresses():
    src = "raise HTTPException(status_code=500, detail=str(e))  # noqa: exc-in-response\n"
    assert _codes(src) == []


# --- baseline behavior -----------------------------------------------------------


def test_baseline_allowlists_known_snippet(tmp_path):
    src = "raise HTTPException(status_code=500, detail=str(e))\n"
    violations = checker.find_violations(src, "server/foo.py")
    baseline = checker.build_baseline({"server/foo.py": violations})
    # A known snippet in the baseline is not a *new* violation.
    new = checker.new_violations(violations, "server/foo.py", baseline)
    assert new == []


def test_main_reports_multiple_findings_in_one_file(tmp_path, capsys):
    # Regression: two new findings in the same file must not crash the reporter's sort.
    target = tmp_path / "routes.py"
    target.write_text(
        "from fastapi import HTTPException\n"
        "def a(e):\n"
        "    raise HTTPException(status_code=400, detail=str(e))\n"
        "def b(exc):\n"
        "    raise HTTPException(status_code=500, detail=str(exc))\n"
    )
    rc = checker.main([str(tmp_path), "--baseline", str(tmp_path / "absent.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "2 new finding(s)" in err


def test_main_update_then_check_passes(tmp_path):
    target = tmp_path / "routes.py"
    target.write_text(
        "from fastapi import HTTPException\n"
        "def a(e):\n"
        "    raise HTTPException(status_code=400, detail=str(e))\n"
    )
    baseline = tmp_path / "baseline.json"
    assert checker.main([str(tmp_path), "--baseline", str(baseline), "--update"]) == 0
    assert baseline.exists()
    # With the baseline in place, the same source is no longer a new finding.
    assert checker.main([str(tmp_path), "--baseline", str(baseline)]) == 0


def test_baseline_does_not_allowlist_new_snippet(tmp_path):
    old = checker.find_violations("raise HTTPException(status_code=500, detail=str(e))\n", "server/foo.py")
    baseline = checker.build_baseline({"server/foo.py": old})
    # A different offending line in the same file is still flagged.
    current = checker.find_violations(
        "raise HTTPException(status_code=503, detail=str(exc))\n", "server/foo.py"
    )
    new = checker.new_violations(current, "server/foo.py", baseline)
    assert len(new) == 1
