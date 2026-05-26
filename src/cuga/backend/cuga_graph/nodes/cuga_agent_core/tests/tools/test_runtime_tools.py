"""Tests for the shared runtime tool-injection orchestrator.

Two concerns, kept separate:

1. ``resolve_runtime_backends`` must reproduce *exactly* the legacy gating
   currently inlined in ``cuga_lite_graph`` (the
   ``_sandbox_mode/_shell_tool_on/_fs_tool_on/_opensandbox_on/_use_sandbox``
   block). These are the behavior-preservation guards.
2. ``build_runtime_tools`` only *orchestrates* the existing
   ``create_filesystem_tools`` + shell ``create_sandbox_tools`` packages —
   it must not re-implement any tool. We assert the wiring (which backend,
   which executor, prompt vs execution namespace) with fakes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.tools.runtime_tools import (
    RuntimeBackends,
    ToolBundle,
    build_runtime_tools,
    prompt_tool_dicts,
    resolve_runtime_backends,
)


# ─── prompt_tool_dicts (Phase 5: expose runtime tools in Supervisor prompt) ──


class _PT:
    def __init__(self, name, description, args):
        self.name = name
        self.description = description
        self.args = args


def test_prompt_tool_dicts_shape_for_supervisor_template():
    out = prompt_tool_dicts([_PT("read_file", "Read a file.", {"path": {}, "start": {}})])
    assert out == [
        {
            "name": "read_file",
            "description": "Read a file.",
            "params_str": "path, start",
            "params_doc": "- path\n- start",
            "response_doc": "",
        }
    ]


def test_prompt_tool_dicts_no_args_and_empty_list():
    assert prompt_tool_dicts([]) == []
    out = prompt_tool_dicts([_PT("run_command", "", {})])
    assert out[0]["params_str"] == ""
    assert out[0]["params_doc"] == "No parameters required"
    assert out[0]["description"] == ""


def test_prompt_tool_dicts_survives_bad_args():
    class Bad:
        name = "x"
        description = "d"

        @property
        def args(self):
            raise RuntimeError("schema build failed")

    out = prompt_tool_dicts([Bad()])
    assert out[0]["params_str"] == ""
    assert out[0]["name"] == "x"


def _settings(**adv) -> SimpleNamespace:
    defaults = dict(
        e2b_sandbox=False,
        opensandbox_sandbox=True,
        enable_shell_tool=False,
        sandbox_mode="native",
        enable_filesystem_tools=False,
    )
    defaults.update(adv)
    return SimpleNamespace(advanced_features=SimpleNamespace(**defaults))


# ─── resolve_runtime_backends: exact legacy-gating parity ───────────────────


def test_defaults_inject_nothing():
    b = resolve_runtime_backends(_settings(), {})
    assert b == RuntimeBackends(filesystem="none", shell="none")


def test_fs_only_uses_host_backend():
    b = resolve_runtime_backends(_settings(enable_filesystem_tools=True), {})
    assert b.filesystem == "host"
    assert b.shell == "none"


def test_configurable_override_enables_fs():
    b = resolve_runtime_backends(_settings(enable_filesystem_tools=False), {"enable_filesystem_tools": True})
    assert b.filesystem == "host"


def test_shell_native_when_enabled():
    b = resolve_runtime_backends(_settings(enable_shell_tool=True, sandbox_mode="native"), {})
    assert b.shell == "native"


def test_shell_local_when_enabled():
    b = resolve_runtime_backends(_settings(enable_shell_tool=True, sandbox_mode="local"), {})
    assert b.shell == "local"


def test_shell_opensandbox_requires_opensandbox_flag():
    on = resolve_runtime_backends(
        _settings(enable_shell_tool=True, sandbox_mode="opensandbox", opensandbox_sandbox=True), {}
    )
    off = resolve_runtime_backends(
        _settings(enable_shell_tool=True, sandbox_mode="opensandbox", opensandbox_sandbox=False), {}
    )
    assert on.shell == "opensandbox"
    assert off.shell == "none"  # _use_sandbox is False without the flag


def test_fs_uses_sandbox_remote_only_with_opensandbox_shell():
    remote = resolve_runtime_backends(
        _settings(
            enable_filesystem_tools=True,
            enable_shell_tool=True,
            sandbox_mode="opensandbox",
            opensandbox_sandbox=True,
        ),
        {},
    )
    host = resolve_runtime_backends(
        _settings(enable_filesystem_tools=True, enable_shell_tool=True, sandbox_mode="native"),
        {},
    )
    assert remote.filesystem == "sandbox_remote"
    assert host.filesystem == "host"


# ─── build_runtime_tools: orchestration only ────────────────────────────────


class _FakeTool:
    def __init__(self, name, coroutine=None, func=None):
        self.name = name
        self.coroutine = coroutine
        self.func = func


@pytest.fixture
def patch_packages(monkeypatch):
    created = {}

    def fake_create_fs(thread_id=None, *, backend=None):
        created["fs_thread_id"] = thread_id
        created["fs_backend"] = backend

        async def _rf():
            return "x"

        return [_FakeTool("read_file", coroutine=_rf), _FakeTool("write_file", func=lambda: None)]

    class FakeRemoteBackend:
        def __init__(self, executor, thread_id=None):
            created["remote_executor"] = executor
            created["remote_thread_id"] = thread_id

    import cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem as fs_pkg

    monkeypatch.setattr(fs_pkg, "create_filesystem_tools", fake_create_fs)
    monkeypatch.setattr(fs_pkg, "RemoteSandboxBackend", FakeRemoteBackend)

    class FakeShellExecutor:
        def __init__(self, label):
            self.label = label

        def create_sandbox_tools(self, thread_id=None):
            created["shell_thread_id"] = thread_id
            created["shell_label"] = self.label

            async def _rc():
                return "ran"

            return [_FakeTool("run_command", coroutine=_rc)]

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor

    monkeypatch.setattr(
        CodeExecutor, "_get_native_executor", classmethod(lambda cls: FakeShellExecutor("native"))
    )
    monkeypatch.setattr(
        CodeExecutor,
        "_get_local_sandbox_executor",
        classmethod(lambda cls: FakeShellExecutor("local")),
    )
    monkeypatch.setattr(
        CodeExecutor,
        "_get_opensandbox_executor",
        classmethod(lambda cls: FakeShellExecutor("opensandbox")),
    )
    return created


def test_none_none_produces_empty_bundle(patch_packages):
    bundle = build_runtime_tools(thread_id="t1", backends=RuntimeBackends("none", "none"))
    assert isinstance(bundle, ToolBundle)
    assert bundle.prompt_tools == []
    assert bundle.execution_callables == {}
    assert bundle.app_definitions == []


def test_host_fs_orchestrates_create_filesystem_tools(patch_packages):
    bundle = build_runtime_tools(thread_id="t1", backends=RuntimeBackends("host", "none"))
    assert patch_packages["fs_backend"] is None  # host default
    assert {t.name for t in bundle.prompt_tools} == {"read_file", "write_file"}
    assert set(bundle.execution_callables) == {"read_file", "write_file"}
    assert [a.name for a in bundle.app_definitions] == ["filesystem"]


def test_sandbox_remote_fs_builds_remote_backend(patch_packages):
    build_runtime_tools(thread_id="t9", backends=RuntimeBackends("sandbox_remote", "opensandbox"))
    # RemoteSandboxBackend wired with the opensandbox executor + thread_id
    assert patch_packages["fs_backend"] is not None
    assert patch_packages["remote_thread_id"] == "t9"


def test_shell_native_uses_native_executor_only(patch_packages):
    bundle = build_runtime_tools(thread_id="t1", backends=RuntimeBackends("none", "native"))
    assert patch_packages["shell_label"] == "native"
    assert set(bundle.execution_callables) == {"run_command"}
    assert bundle.app_definitions == []  # shell-only: no filesystem app def


def test_callable_is_coroutine_or_func_and_skips_empty(patch_packages):
    bundle = build_runtime_tools(thread_id="t1", backends=RuntimeBackends("host", "none"))
    # write_file had only .func, read_file had .coroutine — both captured
    assert callable(bundle.execution_callables["read_file"])
    assert callable(bundle.execution_callables["write_file"])


# ─── Skills parity: prompt_tool_dicts on real StructuredTool (Supervisor) ────


def test_skill_tool_is_prompt_dict_compatible():
    """Real StructuredTool from create_skill_tools must survive prompt_tool_dicts
    and produce a load_skill dict with the correct keys for Supervisor's template.

    This is the integration seam the skills-parity wiring uses:
      create_skill_tools(registry) → prompt_tool_dicts(tools) → agent_tools_for_prompt
    """
    from cuga.backend.skills.registry import SkillEntry, SkillRegistry
    from cuga.backend.skills.tools import create_skill_tools

    entry = SkillEntry(name="demo", description="A demo skill.", body="# demo", source="test")
    registry = SkillRegistry([entry])
    skill_tools = create_skill_tools(registry)

    dicts = prompt_tool_dicts(skill_tools)

    assert len(dicts) == 1
    d = dicts[0]
    assert d["name"] == "load_skill"
    assert "name" in d["params_str"]
    assert d["description"] != ""


@pytest.mark.asyncio
async def test_skill_tool_func_is_awaitable_via_make_tool_awaitable():
    """Skill tools use .func (sync) — make_tool_awaitable must wrap it so
    Supervisor's execute_agent_tool can ``await load_skill(...)`` in code blocks."""
    from cuga.backend.skills.registry import SkillEntry, SkillRegistry
    from cuga.backend.skills.tools import create_skill_tools
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable

    entry = SkillEntry(name="my_skill", description="Test.", body="instructions", source="t")
    registry = SkillRegistry([entry])
    skill_tools = create_skill_tools(registry)

    load_skill_tool = skill_tools[0]
    wrapped = make_tool_awaitable(load_skill_tool.func)

    result = await wrapped(name="my_skill")
    assert "instructions" in result
