import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.benchmark_mode import (
    reset_skills_relaxed_execution,
    set_skills_relaxed_execution,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.security import SecurityValidator


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        'result = await create_ticket(description="User requests 4 GB more RAM")',
        'result = await create_ticket(description="Check socket connection status")',
        'msg = "please open the ticket queue"',
        'note = "Use urllib docs or ctypes examples in the writeup"',
        'text = "Avoid eval() and exec() in production; prefer open communication"',
        'x = "pathlib and shutil were mentioned in the training"',
        'payload = {"hint": "import requests carefully"}',
    ],
)
def test_validate_wrapped_code_allows_flagged_words_in_string_literals(code):
    SecurityValidator.validate_wrapped_code(code)


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,match",
    [
        ("import requests", "requests"),
        ("from requests import get", "requests"),
        ("requests.get('http://example.com')", "requests"),
        ("import socket", "socket"),
        ("socket.socket()", "socket"),
        ("import urllib", "urllib"),
        ("import ctypes", "ctypes"),
        ("open('/tmp/x')", "file access|open"),
        ("eval('1+1')", "eval"),
        ("exec('x=1')", "exec"),
        ("compile('x=1', '<x>', 'exec')", "compile"),
        ("__import__('os')", "__import__"),
        ("breakpoint()", "breakpoint"),
        ("setattr(obj, 'x', 1)", "setattr"),
        ("delattr(obj, 'x')", "delattr"),
        ("hasattr(obj, 'x')", "hasattr"),
        ("os.system('ls')", "os"),
        ("sys.exit(1)", "sys"),
        ("subprocess.run(['ls'])", "subprocess"),
        ("pathlib.Path('/')", "pathlib"),
        ("shutil.rmtree('/tmp/x')", "shutil"),
        ("glob.glob('*')", "glob"),
        ("pickle.loads(b'')", "pickle"),
        ("obj.__class__", "dunder|__class__"),
        ("obj.__subclasses__()", "dunder|__subclasses__"),
        ("frame.f_globals", "f_globals"),
    ],
)
def test_validate_wrapped_code_blocks_dangerous_code(code, match):
    with pytest.raises(PermissionError, match=match):
        SecurityValidator.validate_wrapped_code(code)


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,match",
    [
        ('__import__("req" + "uests")', "__import__"),
        ('__import__("".join(["req", "uests"]))', "__import__"),
        ('getattr(__import__("o" + "s"), "system")("id")', "__import__|getattr"),
        ("import importlib\nimportlib.import_module('requests')", "importlib|requests"),
    ],
)
def test_validate_wrapped_code_blocks_import_bypasses(code, match):
    with pytest.raises(PermissionError, match=match):
        SecurityValidator.validate_wrapped_code(code)


@pytest.mark.unit
def test_validate_wrapped_code_allows_safe_tool_call_shape():
    code = '''
async def _async_main():
    result = await create_ticket(description="User requests 4 GB more RAM")
    return locals()
'''
    SecurityValidator.validate_wrapped_code(code)


@pytest.mark.unit
def test_validate_wrapped_code_allows_allowed_imports():
    SecurityValidator.validate_wrapped_code("import json\ndata = json.dumps({'a': 1})")


@pytest.mark.unit
def test_skills_relaxed_skips_wrapped_code_validation():
    token = set_skills_relaxed_execution(True)
    try:
        SecurityValidator.validate_wrapped_code("open('/tmp/x')")
        SecurityValidator.validate_wrapped_code("import requests")
    finally:
        reset_skills_relaxed_execution(token)


@pytest.mark.unit
def test_wrapped_code_validation_active_without_skills():
    token = set_skills_relaxed_execution(False)
    try:
        with pytest.raises(PermissionError, match="Security violation"):
            SecurityValidator.validate_wrapped_code("open('/tmp/x')")
    finally:
        reset_skills_relaxed_execution(token)


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        'x = "import os please"',
        'doc = "from subprocess import run"',
    ],
)
def test_validate_dangerous_modules_ignores_import_text_in_strings(code):
    SecurityValidator.validate_dangerous_modules(code)


@pytest.mark.unit
@pytest.mark.parametrize(
    "code,module",
    [
        ("import os", "os"),
        ("from sys import path", "sys"),
        ("import subprocess", "subprocess"),
        ("from pathlib import Path", "pathlib"),
        ("import shutil", "shutil"),
    ],
)
def test_validate_dangerous_modules_blocks_real_imports(code, module):
    with pytest.raises(PermissionError, match=module):
        SecurityValidator.validate_dangerous_modules(code)
