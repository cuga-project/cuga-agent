from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.skills.sandbox_uv import SANDBOX_UV_COMMAND_NORMALIZATION


def test_sandbox_uv_guidance_forbids_bare_uv_run() -> None:
    assert "Never use bare `uv run`" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "uv pip install" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "python -c" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "python -m pip" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "retry once with" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "shell_path" not in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "workspace-relative" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "plain **string**" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "write_file" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "can't open file" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "head: None" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "infer prefixes" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "split('\\n[stderr]\\n', 1)" in SANDBOX_UV_COMMAND_NORMALIZATION
    assert "only on failure" in SANDBOX_UV_COMMAND_NORMALIZATION


def test_load_skill_includes_sandbox_uv_guidance() -> None:
    registry = SkillRegistry(
        [SkillEntry(name="x", description="d", body="Do work.", source="/skills/x/SKILL.md")]
    )
    loaded = registry.load_skill("x")
    assert SANDBOX_UV_COMMAND_NORMALIZATION in loaded
    assert "uv run --no-project" in loaded
