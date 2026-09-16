"""Exercise runtime helper gating through the real prepare node and prompt template."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import FILESYSTEM_TOOL_NAMES
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor import (
    LocalSandboxExecutor,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (
    OpenSandboxExecutor,
)
from cuga.backend.llm.utils.helpers import load_one_prompt

pytestmark = pytest.mark.unit


@pytest.fixture
def prepare_runtime(monkeypatch):
    """Keep real backend resolution, injection and rendering; isolate external services."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import prepare_node
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
    from cuga.backend.evolve import memory

    advanced = SimpleNamespace(
        enable_todos=False,
        shortlisting_tool_threshold=35,
        enable_filesystem_tools=False,
        enable_shell_tool=False,
        sandbox_mode="native",
        opensandbox_sandbox=False,
        force_autonomous_mode=False,
    )
    monkeypatch.setattr(
        prepare_node,
        "settings",
        SimpleNamespace(
            advanced_features=advanced,
            policy=SimpleNamespace(enabled=False, cuga_folder=""),
            skills=SimpleNamespace(enabled=False),
            agent_spawn=SimpleNamespace(enabled=False),
            evolve=SimpleNamespace(timeout=1),
        ),
    )
    monkeypatch.setattr(memory, "build_evolve_special_instructions_extension", AsyncMock(return_value=""))
    executor = LocalSandboxExecutor()
    monkeypatch.setattr(executor, "_copy_skills_to_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(CodeExecutor, "_get_native_executor", staticmethod(lambda: executor))
    monkeypatch.setattr(CodeExecutor, "_get_local_sandbox_executor", staticmethod(lambda: executor))
    remote_executor = OpenSandboxExecutor()
    monkeypatch.setattr(remote_executor, "_skills_config", {})
    monkeypatch.setattr(remote_executor, "_active_skills_config", {})
    monkeypatch.setattr(remote_executor, "_sandboxes", {})
    monkeypatch.setattr(CodeExecutor, "_get_opensandbox_executor", staticmethod(lambda: remote_executor))
    monkeypatch.setattr(prepare_node, "get_sandbox_env_description", lambda: "Test environment")
    monkeypatch.delenv("CUGA_POLICIES_CONTENT", raising=False)

    async def run(
        *, filesystem, shell, mode="native", settings_fs=False, static=False, examples=None, opensandbox=False
    ):
        advanced.enable_filesystem_tools = settings_fs
        advanced.enable_shell_tool = shell
        advanced.sandbox_mode = mode
        advanced.opensandbox_sandbox = opensandbox
        adapter = MagicMock()
        adapter._task_todos_ref = []
        adapter._tools_context = {}
        adapter._instructions = ""
        adapter._special_instructions = None
        adapter._static_prompt = "Custom static prompt" if static else None
        adapter._thread_id = "runtime-gating-test"
        adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=[])
        adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])
        adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
        template = Path(__file__).resolve().parents[1] / "prompts" / "mcp_prompt.jinja2"
        adapter._prompt_template = load_one_prompt(str(template), relative_to_caller=False)
        state = SimpleNamespace(
            chat_messages=[HumanMessage(content="Prepare a report")],
            task_todos=None,
            sub_task=None,
            sub_task_app=None,
            api_intent_relevant_apps=None,
            cuga_lite_metadata=None,
            thread_id="runtime-gating-test",
        )
        config = {
            "enable_filesystem_tools": filesystem,
            "skills_enabled": False,
            "knowledge_engine": SimpleNamespace(_config=None),
            "cuga_lite_enable_few_shots": True,
            "mcp_few_shot_examples": examples or [],
        }
        node = prepare_node.create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        result = await node(state, config={"configurable": config})
        return adapter._tools_context, result.update

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("filesystem", [False, True])
@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("mode", ["native", "local", "opensandbox"])
async def test_prepare_prompt_matches_injected_helpers(prepare_runtime, filesystem, shell, mode):
    """Per-invocation filesystem overrides also control instructions and the split note."""
    context, update = await prepare_runtime(
        filesystem=filesystem, shell=shell, settings_fs=not filesystem, mode=mode, opensandbox=True
    )
    prompt = update["prepared_prompt"]
    assert ("run_command" in context) is shell
    assert ("run_command" in prompt) is shell
    for name in FILESYSTEM_TOOL_NAMES:
        assert (name in context) is filesystem
        assert (name in prompt) is filesystem


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["e2b", "opensandbox"])
async def test_prepare_does_not_advertise_unavailable_shell_backend(prepare_runtime, mode):
    """Raw shell=true cannot advertise run_command if injection resolves to none."""
    context, update = await prepare_runtime(filesystem=False, shell=True, mode=mode)
    assert "run_command" not in context
    assert "run_command" not in update["prepared_prompt"]
    assert "Split-execution mode" not in update["prepared_prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("static", [False, True])
@pytest.mark.parametrize("filesystem", [False, True])
async def test_prepare_filters_few_shots_for_static_and_dynamic_prompts(prepare_runtime, static, filesystem):
    """The returned chat prefix must pass through the real filter in both prompt paths."""
    turns = [
        {"role": "user", "content": "Save a report"},
        {"role": "assistant", "content": 'await write_file("./report.md", "Report")'},
        {"role": "user", "content": "Execution output: success"},
        {"role": "assistant", "content": "The report was saved."},
    ]
    _, update = await prepare_runtime(filesystem=filesystem, shell=False, static=static, examples=turns)
    assert update["mcp_few_shot_messages"] == (turns if filesystem else [])
    if static:
        assert update["prepared_prompt"] == "Custom static prompt"
