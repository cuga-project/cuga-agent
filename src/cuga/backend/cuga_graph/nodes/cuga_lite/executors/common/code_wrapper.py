from typing import Optional


class CodeWrapper:
    """Handles wrapping user code for async execution."""

    @staticmethod
    def create_datetime_mock(fake_datetime: Optional[str] = None) -> str:
        """Create datetime mocking code string.

        Args:
            fake_datetime: Optional ISO format date string to fake datetime.now()

        Returns:
            String containing datetime mocking code, or empty string if fake_datetime is None
        """
        if not fake_datetime:
            return ""

        return f"""
import datetime
_original_datetime = datetime.datetime

def _create_fake_datetime_class(_orig=None):
    if _orig is None:
        _orig = globals()['_original_datetime']
    
    class _FakeDatetime:
        def __new__(cls, *args, **kwargs):
            return _orig(*args, **kwargs)
        
        @staticmethod
        def now(tz=None):
            _fake_date = _orig.fromisoformat("{fake_datetime}")
            if tz:
                return _fake_date.replace(tzinfo=tz)
            return _fake_date
        
        @staticmethod
        def today():
            _fake_date = _orig.fromisoformat("{fake_datetime}")
            return _fake_date.date()
        
        @staticmethod
        def utcnow():
            _fake_date = _orig.fromisoformat("{fake_datetime}")
            return _fake_date
        
        @staticmethod
        def fromisoformat(date_string):
            return _orig.fromisoformat(date_string)
        
        @staticmethod
        def strptime(date_string, format):
            return _orig.strptime(date_string, format)
        
        @staticmethod
        def combine(date, time):
            return _orig.combine(date, time)
        
        @staticmethod
        def fromtimestamp(timestamp, tz=None):
            return _orig.fromtimestamp(timestamp, tz)
        
        @staticmethod
        def fromordinal(ordinal):
            return _orig.fromordinal(ordinal)
    
    return _FakeDatetime

_FakeDatetime = _create_fake_datetime_class(_original_datetime)
_fake_dt = _FakeDatetime
_fake_dt.min = _original_datetime.min
_fake_dt.max = _original_datetime.max
_fake_dt.resolution = _original_datetime.resolution

datetime.datetime = _fake_dt
"""

    @staticmethod
    def wrap_code(code: str, fake_datetime: Optional[str] = None) -> str:
        """Wrap user code in an async function for execution.

        Args:
            code: User's Python code
            fake_datetime: Optional ISO format date string to fake datetime.now()

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
            datetime_mock = CodeWrapper.create_datetime_mock(fake_datetime)
            wrapped_code = f"""
import asyncio
{datetime_mock}
async def _async_main():
{indented_code}
    return locals()

# Execute the wrapped function
"""
            return wrapped_code

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

        datetime_mock = CodeWrapper.create_datetime_mock(fake_datetime)

        wrapped_code = f"""
import asyncio
{datetime_mock}
async def _async_main():
{indented_code}
    return locals()

# Execute the wrapped function
"""
        return wrapped_code
