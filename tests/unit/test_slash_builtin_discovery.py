import asyncio
import importlib
import sys
import textwrap
from pathlib import Path

import cuga.backend.slash_commands.builtins as builtins_pkg
from cuga.backend.slash_commands import (
    SlashRegistry,
    build_slash_registry,
    discover_builtins,
    parse_and_dispatch,
)


def test_help_builtin_is_auto_discovered():
    names = {b.name for b in discover_builtins()}
    assert "help" in names


def test_dropping_a_module_registers_it(tmp_path: Path, monkeypatch):
    """Auto-discovery picks up any new module exporting a BUILTIN attribute."""
    pkg_dir = Path(builtins_pkg.__file__).parent
    new_module_path = pkg_dir / "_test_stub_builtin.py"
    new_module_path.write_text(
        textwrap.dedent(
            '''
            from dataclasses import dataclass
            from typing import Optional

            from cuga.backend.slash_commands.types import DispatchContext, DispatchResult


            @dataclass
            class _StubCommand:
                name: str = "stubcommand"
                description: str = "Test-only stub."
                argument_hint: Optional[str] = None

                async def handle(self, ctx: DispatchContext) -> DispatchResult:
                    return DispatchResult(kind="builtin", text="stub ran")


            BUILTIN = _StubCommand()
            '''
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    try:
        # Even though the module name starts with `_`, the discovery skips that prefix.
        # Rename to a non-underscore name for the discovery path:
        target_path = pkg_dir / "stub_builtin_for_test.py"
        new_module_path.rename(target_path)

        # Drop any stale cached import
        sys.modules.pop("cuga.backend.slash_commands.builtins.stub_builtin_for_test", None)

        names = {b.name for b in discover_builtins()}
        assert "stubcommand" in names
        assert "help" in names  # existing built-in still discovered
    finally:
        target_path.unlink(missing_ok=True)
        sys.modules.pop("cuga.backend.slash_commands.builtins.stub_builtin_for_test", None)
        importlib.reload(builtins_pkg)


def test_unknown_command_returns_unknown_kind():
    reg = build_slash_registry()
    result = asyncio.run(parse_and_dispatch("/no-such-command", slash_registry=reg))
    assert result.kind == "unknown"
    assert "no-such-command" in (result.text or "")
    assert result.resolved_name == "no-such-command"


def test_passthrough_for_plain_text():
    reg = build_slash_registry()
    result = asyncio.run(parse_and_dispatch("just a question", slash_registry=reg))
    assert result.kind == "passthrough"


def test_help_dispatch_returns_builtin_text():
    reg = build_slash_registry()
    result = asyncio.run(parse_and_dispatch("/help", slash_registry=reg))
    assert result.kind == "builtin"
    assert result.text is not None
    assert "/help" in result.text
    assert "Available slash commands" in result.text


def test_builtin_shadows_skill_with_same_name(caplog):
    class FakeSkillRegistry:
        def summaries(self):
            return [{"name": "help", "description": "fake clash"}]

    reg = SlashRegistry(builtins=discover_builtins(), skill_registry=FakeSkillRegistry())
    commands = reg.list_commands()
    help_entries = [c for c in commands if c.name == "help"]
    assert len(help_entries) == 1
    assert help_entries[0].kind == "builtin"
    assert reg.has_skill("help") is False
