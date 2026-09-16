"""Prompt guidance and few-shots must match the injected runtime helpers."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
    ExecutionPlan,
    split_execution_note,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem import (
    create_filesystem_tools,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor import (
    LocalSandboxExecutor,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.native.native_sandbox_executor import (
    NativeSandboxExecutor,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (
    OpenSandboxExecutor,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import (
    FILESYSTEM_TOOL_NAMES,
    create_mcp_prompt,
    drop_examples_using_absent_helpers,
)
from cuga.backend.llm.utils.helpers import load_one_prompt

pytestmark = pytest.mark.unit

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "mcp_prompt.jinja2"


def _render(**kwargs) -> str:
    return create_mcp_prompt(
        prompt_template=load_one_prompt(str(_PROMPT), relative_to_caller=False),
        **kwargs,
    )


@pytest.mark.parametrize("shell_enabled", [False, True])
@pytest.mark.parametrize("filesystem_enabled", [False, True])
def test_runtime_guidance_is_independently_gated(shell_enabled, filesystem_enabled):
    """Each capability has its own guidance, with write-before-run requiring both."""
    rendered = _render(tools=[], enable_filesystem_tools=filesystem_enabled, enable_shell_tool=shell_enabled)
    assert ("run_command" in rendered) is shell_enabled
    assert ("write_file" in rendered) is filesystem_enabled
    assert ("column 0" in rendered) is filesystem_enabled
    assert ("**Workspace paths**" in rendered) is filesystem_enabled
    assert ("**Write before run**" in rendered) is (shell_enabled and filesystem_enabled)
    if not filesystem_enabled:
        assert all(helper not in rendered for helper in FILESYSTEM_TOOL_NAMES)


@pytest.mark.parametrize("helper", FILESYSTEM_TOOL_NAMES)
def test_absent_helper_drops_entire_transcript(helper):
    """Preserve conversation integrity: no orphaned outputs or unsupported final claims."""
    examples = [
        {"role": "user", "content": "Prepare a report"},
        {"role": "assistant", "content": f'output = await {helper}("./a.txt")'},
        {"role": "user", "content": "Execution output: success"},
        {"role": "assistant", "content": "The report was saved."},
    ]
    original = list(examples)
    assert drop_examples_using_absent_helpers(examples, filesystem_enabled=False) == []
    assert examples == original
    assert drop_examples_using_absent_helpers(examples, filesystem_enabled=True) == examples


@pytest.mark.parametrize(
    "content",
    ["do not use read_file", 'await s3_read_files_batch("q")', 'await client.read_file("q")'],
)
def test_prose_and_other_tool_names_are_kept(content):
    """Mentioning a helper or calling another tool is not an absent-helper call."""
    examples = [{"role": "assistant", "content": content}]
    assert drop_examples_using_absent_helpers(examples, filesystem_enabled=False) == examples


def test_bundled_find_tools_transcript_is_dropped_or_preserved_whole():
    """Run the real filter against the shipped paired conversation."""
    raw = json.loads((_PROMPT.parent / "find_tools_few_shot_examples.json").read_text(encoding="utf-8"))
    turns = raw if isinstance(raw, list) else raw["examples"]
    assert turns
    assert drop_examples_using_absent_helpers(turns, filesystem_enabled=False) == []
    assert drop_examples_using_absent_helpers(turns, filesystem_enabled=True) == turns


def test_shell_few_shots_follow_shell_availability():
    """Custom examples cannot call run_command when its runtime backend is absent."""
    turns = [{"role": "assistant", "content": 'await run_command("ls")'}]
    assert drop_examples_using_absent_helpers(turns, filesystem_enabled=True, shell_enabled=False) == []
    assert drop_examples_using_absent_helpers(turns, filesystem_enabled=False, shell_enabled=True) == turns


def test_filtered_names_match_filesystem_tool_surface():
    """The filter must cover exactly the eight injected tools and their schemas."""
    tools = create_filesystem_tools(backend=MagicMock())
    assert len(tools) == 8
    assert tuple(t.name for t in tools) == FILESYSTEM_TOOL_NAMES
    assert set(next(t for t in tools if t.name == "read_file").args) == {
        "path",
        "start_line",
        "end_line",
        "grep_pattern",
    }
    assert set(next(t for t in tools if t.name == "write_file").args) == {"path", "content"}


@pytest.mark.parametrize("filesystem_enabled", [False, True])
@pytest.mark.parametrize("executor_type", [LocalSandboxExecutor, NativeSandboxExecutor, OpenSandboxExecutor])
def test_shell_description_only_removes_disabled_filesystem_helpers(
    executor_type, filesystem_enabled, monkeypatch
):
    """Render the actual shell tool description together with the split-execution note."""
    executor = executor_type()
    if isinstance(executor, OpenSandboxExecutor):
        monkeypatch.setattr(executor, "_skills_config", {})
        monkeypatch.setattr(executor, "_active_skills_config", {})
        monkeypatch.setattr(executor, "_sandboxes", {})
    else:
        monkeypatch.setattr(executor, "_copy_skills_to_workspace", lambda *args, **kwargs: None)
    plan = ExecutionPlan(
        requested_backend="local", python_backend="local", shell_backend="native", filesystem_backend="none"
    )
    tools = executor.create_sandbox_tools()
    original_description = tools[0].description
    rendered = _render(
        tools=tools,
        enable_filesystem_tools=filesystem_enabled,
        enable_shell_tool=True,
        special_instructions=split_execution_note(plan),
    )
    assert "run_command" in rendered
    assert tools[0].description == original_description
    if filesystem_enabled:
        assert original_description in rendered
    else:
        assert all(helper not in rendered for helper in FILESYSTEM_TOOL_NAMES)


@pytest.mark.parametrize("shell", ["none", "local", "native", "opensandbox", "e2b"])
@pytest.mark.parametrize("filesystem", ["none", "host", "sandbox_remote"])
def test_split_note_names_only_remote_capabilities(shell, filesystem):
    """The note must not invent helpers or describe host filesystem tools as remote."""
    plan = ExecutionPlan(
        requested_backend="local", python_backend="local", shell_backend=shell, filesystem_backend=filesystem
    )
    note = split_execution_note(plan)
    assert ("run_command" in note) is (shell in ("native", "opensandbox", "e2b"))
    assert ("read_file" in note) is (filesystem == "sandbox_remote")
