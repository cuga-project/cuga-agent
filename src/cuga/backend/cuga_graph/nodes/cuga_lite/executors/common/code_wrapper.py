from typing import Optional


class CodeWrapper:
    """Handles wrapping user code for async execution."""

    @staticmethod
    def build_async_main(indented_body: str, fake_datetime: Optional[str] = None) -> str:
        """Build the ``async def _async_main():`` block wrapping the user code.

        When ``fake_datetime`` is set (benchmark mode), freeze the **wall clock**
        that AppWorld freezes for the agent's own code — ``datetime.datetime``,
        ``datetime.date`` and the ``time`` module readouts (``time.time`` /
        ``localtime`` / ``gmtime`` / ``strftime``) — to the task datetime.

        Crucially it does NOT touch ``time.monotonic`` / ``perf_counter`` /
        ``sleep``. freezegun freezes those too, and asyncio's event loop uses
        ``monotonic`` for every timer — so freezing it makes any ``await`` inside
        the user code (e.g. the LLM-backed ``find_tools``) hang until the outer
        sandbox timeout. A monotonic-safe hand-rolled patch avoids that while
        still giving the agent the frozen date/time it needs. (Trade-off vs
        freezegun: ``isinstance(x, datetime)`` isn't preserved — the same
        limitation the original datetime-only shim had — which agent code
        effectively never depends on.)

        The patch is scoped with try/finally: originals are restored the moment
        the user code finishes, so nothing leaks into cuga's own clock.

        ``indented_body`` must already be indented 4 spaces (function-body level).
        """
        if not fake_datetime:
            return f"async def _async_main():\n{indented_body}\n    return locals()"

        # One extra indent level: the user body now lives inside `try:` inside the function.
        deeper = "\n".join("    " + line for line in indented_body.split("\n"))
        # `_cuga_*` names are underscore-prefixed so VariableUtils.filter_new_variables
        # drops them from the returned agent variables. `time.time()` uses the UTC
        # interpretation of the naive datetime (timegm), matching AppWorld/freezegun.
        return (
            "async def _async_main():\n"
            "    import datetime as _cuga_dtmod, time as _cuga_tmod, calendar as _cuga_cal\n"
            "    _cuga_odt = _cuga_dtmod.datetime\n"
            "    _cuga_odate = _cuga_dtmod.date\n"
            f'    _cuga_ft = _cuga_odt.fromisoformat("{fake_datetime}")\n'
            "    _cuga_epoch = float(_cuga_cal.timegm(_cuga_ft.timetuple()))\n"
            "    _cuga_tt = _cuga_ft.timetuple()\n"
            "    _cuga_otime = (_cuga_tmod.time, _cuga_tmod.localtime, _cuga_tmod.gmtime, _cuga_tmod.strftime)\n"
            "    class _CugaDatetime:\n"
            "        def __new__(cls, *a, **k): return _cuga_odt(*a, **k)\n"
            "        @staticmethod\n"
            "        def now(tz=None): return _cuga_ft.replace(tzinfo=tz) if tz else _cuga_ft\n"
            "        @staticmethod\n"
            "        def today(): return _cuga_ft\n"
            "        @staticmethod\n"
            "        def utcnow(): return _cuga_ft\n"
            "        @staticmethod\n"
            "        def fromisoformat(s): return _cuga_odt.fromisoformat(s)\n"
            "        @staticmethod\n"
            "        def strptime(s, f): return _cuga_odt.strptime(s, f)\n"
            "        @staticmethod\n"
            "        def combine(*a, **k): return _cuga_odt.combine(*a, **k)\n"
            "        @staticmethod\n"
            "        def fromtimestamp(ts, tz=None): return _cuga_odt.fromtimestamp(ts, tz)\n"
            "        @staticmethod\n"
            "        def utcfromtimestamp(ts): return _cuga_odt.utcfromtimestamp(ts)\n"
            "        @staticmethod\n"
            "        def fromordinal(o): return _cuga_odt.fromordinal(o)\n"
            "    _CugaDatetime.min = _cuga_odt.min\n"
            "    _CugaDatetime.max = _cuga_odt.max\n"
            "    _CugaDatetime.resolution = _cuga_odt.resolution\n"
            "    class _CugaDate:\n"
            "        def __new__(cls, *a, **k): return _cuga_odate(*a, **k)\n"
            "        @staticmethod\n"
            "        def today(): return _cuga_odate(_cuga_ft.year, _cuga_ft.month, _cuga_ft.day)\n"
            "        @staticmethod\n"
            "        def fromisoformat(s): return _cuga_odate.fromisoformat(s)\n"
            "        @staticmethod\n"
            "        def fromtimestamp(ts): return _cuga_odate.fromtimestamp(ts)\n"
            "        @staticmethod\n"
            "        def fromordinal(o): return _cuga_odate.fromordinal(o)\n"
            "    _CugaDate.min = _cuga_odate.min\n"
            "    _CugaDate.max = _cuga_odate.max\n"
            "    _CugaDate.resolution = _cuga_odate.resolution\n"
            "    _cuga_dtmod.datetime = _CugaDatetime\n"
            "    _cuga_dtmod.date = _CugaDate\n"
            "    _cuga_tmod.time = lambda: _cuga_epoch\n"
            "    _cuga_tmod.localtime = lambda secs=None: (_cuga_tt if secs is None else _cuga_otime[1](secs))\n"
            "    _cuga_tmod.gmtime = lambda secs=None: (_cuga_tt if secs is None else _cuga_otime[2](secs))\n"
            "    _cuga_tmod.strftime = lambda fmt, t=None: _cuga_otime[3](fmt, _cuga_tt if t is None else t)\n"
            "    try:\n"
            f"{deeper}\n"
            "        return locals()\n"
            "    finally:\n"
            "        _cuga_dtmod.datetime = _cuga_odt\n"
            "        _cuga_dtmod.date = _cuga_odate\n"
            "        _cuga_tmod.time, _cuga_tmod.localtime, _cuga_tmod.gmtime, _cuga_tmod.strftime = _cuga_otime\n"
        )

    @staticmethod
    def wrap_code(code: str, fake_datetime: Optional[str] = None) -> str:
        """Wrap user code in an async function for execution.

        Args:
            code: User's Python code
            fake_datetime: Optional ISO format date string to freeze time to
                (see build_async_main — freezes datetime, date and time module)

        Returns:
            Wrapped code ready for execution

        Note:
            Workspace CWD is managed externally (shell subprocess `cwd=`,
            filesystem MCP server `cwd=`, sandbox-tool path resolution) — not
            injected into the wrapped user code. Keeping `os` out of the user
            namespace means SecurityValidator.validate_imports() remains the
            sole gate on what imports the wrapped code can reach.
        """
        indented_code = '\n'.join('    ' + line for line in code.split('\n'))
        lines = [line.strip() for line in code.split('\n') if line.strip()]

        if not lines:
            body = CodeWrapper.build_async_main(indented_code, fake_datetime)
            return f"""
import asyncio
{body}

# Execute the wrapped function
"""

        # Check if the last statement is already a print, return, or assignment
        # Look backwards through lines to find the start of the last statement
        last_line = lines[-1]
        has_print = False
        has_return = False

        # Check if any line contains print( - handles multi-line print statements
        # Also check for print statements that span multiple lines
        code_text = '\n'.join(lines)
        if 'print(' in code_text:
            # More sophisticated check: look for print( that might span multiple lines
            # Check if we're in the middle of a print statement by counting brackets
            has_print = True

        # Check for return statements
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith('return '):
                has_return = True
                break
            if '=' in stripped and not stripped.startswith('#'):
                # If assignment is on last line, don't auto-print
                if line == last_line:
                    break

        # Check if last line is just closing brackets (part of multi-line statement)
        is_closing_only = last_line in ('}', ')', '})', '])', '))', ']}', ')}')

        # Only auto-print if:
        # 1. Last line doesn't start with print/return/#
        # 2. No print statement found in any line
        # 3. Last line is not an assignment
        # 4. Last line is not just closing brackets (part of multi-line statement)
        should_auto_print = (
            not last_line.startswith(('print', 'return', '#'))
            and not has_print
            and not has_return
            and '=' not in last_line
            and not is_closing_only
        )

        if should_auto_print:
            indented_code += f"\n    print({last_line})"

        body = CodeWrapper.build_async_main(indented_code, fake_datetime)

        wrapped_code = f"""
import asyncio
{body}

# Execute the wrapped function
"""
        return wrapped_code
