"""The `demo_palette` preset: supervisor mode + the palette agent skill.

The preset is only correct if four things line up — skills on, shell tool on,
supervisor pointed at a real config, and the deck-content app enabled. Each is
set in a different file, so this asserts them together.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from cuga.backend.server.demo_manage_setup import get_default_apps_for_preset
from cuga.cli.main import (
    _apply_demo_skills_env,
    _apply_palette_supervisor_env,
    validate_service,
)

PRESET = "demo_palette"


@pytest.fixture
def palette_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Apply the preset's env layers into an isolated environment."""
    for key in list(os.environ):
        if key.startswith(("DYNACONF_SKILLS__", "DYNACONF_SUPERVISOR__", "DYNACONF_ADVANCED_FEATURES__")):
            monkeypatch.delenv(key, raising=False)
    _apply_demo_skills_env()
    _apply_palette_supervisor_env()
    return dict(os.environ)


@pytest.mark.unit
class TestPresetRegistration:
    def test_service_name_is_accepted(self) -> None:
        validate_service(PRESET)  # raises typer.Exit if unknown

    def test_unknown_service_still_rejected(self) -> None:
        import typer

        with pytest.raises(typer.Exit):
            validate_service("demo_not_a_thing")


@pytest.mark.unit
class TestPresetEnvironment:
    def test_skills_are_enabled(self, palette_env: dict[str, str]) -> None:
        """The supervisor gates skill loading on settings.skills.enabled alone."""
        assert palette_env["DYNACONF_SKILLS__ENABLED"] == "true"

    def test_shell_tool_is_enabled(self, palette_env: dict[str, str]) -> None:
        """The palette skill drives its CLI through run_command."""
        assert palette_env["DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL"] == "true"

    @staticmethod
    def _warnings_from(action) -> list[str]:
        """Run `action` and return loguru's warnings.

        CUGA logs through loguru, which pytest's caplog does not see — a test
        asserting on caplog here passes vacuously whatever the code does. Add a
        real sink instead.
        """
        from loguru import logger

        captured: list[str] = []
        sink = logger.add(captured.append, level="WARNING", format="{message}")
        try:
            action()
        finally:
            logger.remove(sink)
        return captured

    def test_it_warns_when_palette_home_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The skill shells out to palette.py; without PALETTE_HOME it cannot start.

        Warn at launch rather than letting the agent discover it mid-deck,
        which costs a model call and reads as a skill bug.
        """
        monkeypatch.delenv("PALETTE_HOME", raising=False)
        assert any("PALETTE_HOME" in m for m in self._warnings_from(_apply_palette_supervisor_env))

    def test_it_warns_when_palette_home_points_somewhere_wrong(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PALETTE_HOME", str(tmp_path))
        messages = self._warnings_from(_apply_palette_supervisor_env)
        assert any("no palette.py" in m for m in messages)

    def test_it_warns_when_the_model_key_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sandbox inherits this process's env, not ~/.config/palette/env.

        Without it every model call fails several minutes into a build, with an
        error that looks like Palette's fault rather than a missing export.
        """
        monkeypatch.delenv("RITS_API_KEY", raising=False)
        assert any("RITS_API_KEY" in m for m in self._warnings_from(_apply_palette_supervisor_env))

    def test_a_configured_environment_warns_about_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warnings people see on every correct launch are warnings they stop reading."""
        monkeypatch.setenv("PALETTE_HOME", str(Path(__file__).resolve().parents[2]))
        monkeypatch.setenv("RITS_API_KEY", "x")
        monkeypatch.setattr(
            "os.path.isfile", lambda p: True if p.endswith("palette.py") else os.path.exists(p)
        )
        messages = self._warnings_from(_apply_palette_supervisor_env)
        assert not [m for m in messages if "PALETTE_HOME" in m or "RITS_API_KEY" in m]

    def test_natural_language_auto_continue_is_on(self, palette_env: dict[str, str]) -> None:
        """A deck is minutes of polling, so the model narrates progress a lot.

        With auto-continue off, the first "still rendering, I'll keep checking"
        is prose with no code, the run ends there, and the build finishes on
        the server with nobody downloading it. Seen twice, both at roughly
        step 40 of 100 — nowhere near the step limit.
        """
        assert palette_env["DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE"] == "true"

    def test_a_step_is_long_enough_to_draft_a_plan(self, palette_env: dict[str, str]) -> None:
        """The default 30s step cannot hold `build-plan`, which is ~40s of one model call.

        This is the limit the skill's start-and-poll helper works around, and
        it belongs here rather than in the skill: Palette ships one SKILL.md for
        every host, and Claude Code's Bash tool allows ten minutes. A number
        that is right for CUGA would be wrong there.

        The deck build itself never blocks a step at all — no step limit will
        ever cover a ten-minute render, so `deck.py` starts it detached.
        """
        assert palette_env["DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT"] == "120"

    def test_the_step_limit_defers_to_an_explicit_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """setdefault, like auto-continue — a longer limit set for debugging survives."""
        from cuga.cli.main import _apply_palette_supervisor_env

        monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT", "600")
        _apply_palette_supervisor_env()
        assert os.environ["DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT"] == "600"

    def test_auto_continue_defers_to_an_explicit_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """setdefault, not assignment — someone debugging turn-by-turn keeps control."""
        from cuga.cli.main import _apply_palette_supervisor_env

        monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE", "false")
        _apply_palette_supervisor_env()
        assert os.environ["DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE"] == "false"

    def test_supervisor_is_enabled_and_configured(self, palette_env: dict[str, str]) -> None:
        assert palette_env["DYNACONF_SUPERVISOR__ENABLED"] == "true"
        assert Path(palette_env["DYNACONF_SUPERVISOR__CONFIG_PATH"]).is_file()

    def test_config_path_is_absolute(self, palette_env: dict[str, str]) -> None:
        """The demo server is spawned from a different cwd than the CLI."""
        assert Path(palette_env["DYNACONF_SUPERVISOR__CONFIG_PATH"]).is_absolute()

    def test_a_misconfigured_environment_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Everything the preset checks is recoverable, so warn and continue.

        Refusing to launch would strand the user with no way to fix it from
        inside the app.
        """
        monkeypatch.delenv("PALETTE_HOME", raising=False)
        monkeypatch.delenv("RITS_API_KEY", raising=False)
        _apply_palette_supervisor_env()  # must not raise


@pytest.mark.unit
class TestPresetApps:
    def test_deck_content_and_workspace_apps_are_on(self) -> None:
        apps = get_default_apps_for_preset(PRESET)
        assert apps["digital_sales"] is True, "the supervisor's content sub-agent needs it"
        assert apps["filesystem"] is True, "built decks land in the workspace"

    def test_unrelated_demo_apps_stay_off(self) -> None:
        apps = get_default_apps_for_preset(PRESET)
        assert not apps["crm"] and not apps["email"] and not apps["oak_health"]

    def test_preset_is_known_to_the_apps_resolver(self) -> None:
        """An unknown preset silently falls through to a generic default."""
        assert get_default_apps_for_preset(PRESET) != get_default_apps_for_preset("not_a_preset")


@pytest.mark.unit
class TestSupervisorConfig:
    @pytest.fixture
    def config(self, palette_env: dict[str, str]) -> dict:
        return yaml.safe_load(Path(palette_env["DYNACONF_SUPERVISOR__CONFIG_PATH"]).read_text())

    def test_parses_into_the_shape_the_loader_expects(self, config: dict) -> None:
        assert isinstance(config.get("agents"), list)
        for agent in config["agents"]:
            assert agent.get("name") and agent.get("description")

    def test_sub_agent_apps_match_the_preset(self, config: dict) -> None:
        """An app named here but disabled in the preset yields an agent with no tools."""
        enabled = {name for name, on in get_default_apps_for_preset(PRESET).items() if on}
        for agent in config["agents"]:
            for app in agent.get("apps", []):
                assert app in enabled, f"{agent['name']} wants {app!r}, which the preset does not start"

    def test_supervisor_instructions_route_through_the_skill(self, config: dict) -> None:
        instructions = config["supervisor"]["special_instructions"]
        assert "palette" in instructions.lower()
        assert "pptxgenjs" in instructions, "must forbid hand-building decks"

    def test_supervisor_states_the_confirmation_gate(self, config: dict) -> None:
        """Building an unapproved plan burns minutes on the wrong deck."""
        instructions = config["supervisor"]["special_instructions"]
        assert "explicit agreement before building" in instructions
        assert "never a courtesy" in instructions or "required, not a courtesy" in instructions

    def test_supervisor_knows_builds_outlast_a_step(self, config: dict) -> None:
        """A blocking build is killed part-way and the work is thrown away."""
        assert "detached and poll" in config["supervisor"]["special_instructions"]


@pytest.mark.unit
class TestSeedingBranches:
    """Source-level: demo_palette must share demo_skills' seeding branches.

    These flags are set inside setup_demo_manage_config, which resets the
    config database — too destructive to call in a unit test, so the branch
    membership is asserted directly.
    """

    @pytest.fixture
    def source(self) -> str:
        import cuga.backend.server.demo_manage_setup as module

        return Path(module.__file__).read_text()

    def test_shell_tool_flag_covers_the_preset(self, source: str) -> None:
        assert 'if demo_type in ("demo_skills", "demo_palette"):' in source

    def test_preset_has_its_own_agent_identity(self, source: str) -> None:
        assert '"name": "Deck Builder"' in source
        assert "DEMO_PALETTE_STARTERS" in source
