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
    monkeypatch.delenv("PALETTE_URL", raising=False)
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

    def test_natural_language_auto_continue_is_on(self, palette_env: dict[str, str]) -> None:
        """A deck is minutes of polling, so the model narrates progress a lot.

        With auto-continue off, the first "still rendering, I'll keep checking"
        is prose with no code, the run ends there, and the build finishes on
        the server with nobody downloading it. Seen twice, both at roughly
        step 40 of 100 — nowhere near the step limit.
        """
        assert palette_env["DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE"] == "true"

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

    def test_missing_palette_url_warns_rather_than_failing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A missing URL is recoverable — the user can start a server."""
        monkeypatch.delenv("PALETTE_URL", raising=False)
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

    def test_supervisor_is_told_not_to_start_the_server(self, config: dict) -> None:
        """The agent cannot start Palette from a sandbox; it must say so instead."""
        assert "cannot start it" in config["supervisor"]["special_instructions"]


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
