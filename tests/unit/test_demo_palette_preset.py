"""The `demo_palette` preset: the skills environment, tuned for a slow task.

The preset is deliberately thin. Palette's SKILL.md says how to build a deck;
all CUGA does is turn on skills and the shell tool, give a step long enough to
not cut anything short, and warn early about the two environment variables
whose absence otherwise surfaces minutes into a build. Each of those is set in
a different place, so they are asserted together here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cuga.backend.server.demo_manage_setup import get_default_apps_for_preset
from cuga.cli.main import (
    _apply_demo_skills_env,
    _apply_palette_env,
    validate_service,
)

PRESET = "demo_palette"


def palette_only_env() -> set[str]:
    """The variables the preset's own layer sets, isolated from demo_skills'."""
    before = set(os.environ)
    _apply_palette_env()
    return set(os.environ) - before | {
        k for k in os.environ if k.startswith("DYNACONF_SUPERVISOR__")
    }


@pytest.fixture
def palette_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Apply the preset's env layers into an isolated environment."""
    for key in list(os.environ):
        if key.startswith(("DYNACONF_SKILLS__", "DYNACONF_SUPERVISOR__", "DYNACONF_ADVANCED_FEATURES__")):
            monkeypatch.delenv(key, raising=False)
    _apply_demo_skills_env()
    _apply_palette_env()
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
        """Skill loading is gated on settings.skills.enabled and nothing else."""
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
        assert any("PALETTE_HOME" in m for m in self._warnings_from(_apply_palette_env))

    def test_it_warns_when_palette_home_points_somewhere_wrong(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PALETTE_HOME", str(tmp_path))
        messages = self._warnings_from(_apply_palette_env)
        assert any("no palette.py" in m for m in messages)

    def test_it_warns_when_the_model_key_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sandbox inherits this process's env, not ~/.config/palette/env.

        Without it every model call fails several minutes into a build, with an
        error that looks like Palette's fault rather than a missing export.
        """
        monkeypatch.delenv("RITS_API_KEY", raising=False)
        assert any("RITS_API_KEY" in m for m in self._warnings_from(_apply_palette_env))

    def test_a_configured_environment_warns_about_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warnings people see on every correct launch are warnings they stop reading."""
        monkeypatch.setenv("PALETTE_HOME", str(Path(__file__).resolve().parents[2]))
        monkeypatch.setenv("RITS_API_KEY", "x")
        monkeypatch.setattr(
            "os.path.isfile", lambda p: True if p.endswith("palette.py") else os.path.exists(p)
        )
        messages = self._warnings_from(_apply_palette_env)
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
        from cuga.cli.main import _apply_palette_env

        monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT", "600")
        _apply_palette_env()
        assert os.environ["DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT"] == "600"

    def test_auto_continue_defers_to_an_explicit_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """setdefault, not assignment — someone debugging turn-by-turn keeps control."""
        from cuga.cli.main import _apply_palette_env

        monkeypatch.setenv("DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE", "false")
        _apply_palette_env()
        assert os.environ["DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE"] == "false"

    def test_the_preset_does_not_restate_the_skill(self) -> None:
        """CUGA configures the environment. It never explains how to build a deck.

        There used to be a supervisor config here carrying a deck-builder
        persona: load the skill, honour the confirmation gate, start the build
        detached and poll. Every line of that already lived in Palette's
        SKILL.md, and this copy went stale the moment the skill changed — it
        still described the deck build detaching after the plan started
        detaching too.

        One source of truth, and it ships with Palette.
        """
        repo = Path(__file__).resolve().parents[2]
        stray = list(repo.glob("src/**/supervisor_palette.yaml"))
        assert not stray, f"a palette persona config is back: {stray}"

        # The env layer is config only. Prose for the model would have to reach
        # it through a supervisor config, so there must not be one.
        assert "DYNACONF_SUPERVISOR__CONFIG_PATH" not in palette_only_env(), (
            "the preset points the supervisor at a config, which is where "
            "instructions duplicating SKILL.md lived last time"
        )

    def test_a_misconfigured_environment_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Everything the preset checks is recoverable, so warn and continue.

        Refusing to launch would strand the user with no way to fix it from
        inside the app.
        """
        monkeypatch.delenv("PALETTE_HOME", raising=False)
        monkeypatch.delenv("RITS_API_KEY", raising=False)
        _apply_palette_env()  # must not raise


@pytest.mark.unit
class TestPresetApps:
    def test_deck_content_and_workspace_apps_are_on(self) -> None:
        apps = get_default_apps_for_preset(PRESET)
        assert apps["filesystem"] is True, "built decks land in the workspace"

    def test_unrelated_demo_apps_stay_off(self) -> None:
        apps = get_default_apps_for_preset(PRESET)
        assert not apps["crm"] and not apps["email"] and not apps["oak_health"]

    def test_preset_is_known_to_the_apps_resolver(self) -> None:
        """An unknown preset silently falls through to a generic default."""
        assert get_default_apps_for_preset(PRESET) != get_default_apps_for_preset("not_a_preset")


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
