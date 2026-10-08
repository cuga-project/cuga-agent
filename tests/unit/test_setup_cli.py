import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from dotenv import dotenv_values
import pytest

from cuga import setup_cli
from cuga import setup_terminal

pytestmark = pytest.mark.unit


@pytest.fixture
def environment(monkeypatch, tmp_path):
    variables = {
        "AGENT_SETTING_CONFIG",
        "MODEL_NAME",
        "ENV_FILE",
        "CI",
        "CUGA_CONFIGURATIONS_DIR",
        "WATSONX_APIKEY",
        "LLM_AUTH_HEADER",
        "DYNACONF_SECRETS__MODE",
        "DYNACONF_SECRETS__FORCE_ENV",
        "WATSONX_PROJECT_ID",
        "WATSONX_SPACE_ID",
        "GOOGLE_API_KEY",
        "WXO_API_KEY",
        "WXO_INSTANCE_URL",
        "CUGA_LOCAL_MANAGER",
        "CUGA_WORKSPACE_PATH",
        "CUGA_SECRET_KEY",
        "CUGA_DBS_DIR",
        "CUGA_LOGGING_DIR",
        "DYNACONF_KNOWLEDGE__PERSIST_DIR",
        "DYNACONF_STORAGE__PRESERVE_CONFIGS_ON_STARTUP",
    }
    for provider in setup_cli.PROVIDERS:
        variables.update(filter(None, provider[2:4]))
    for name in variables:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setenv("CUGA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("dotenv.find_dotenv", lambda **kwargs: "")
    return tmp_path / "data"


@pytest.fixture
def scripted_ui():
    def create(monkeypatch, *, choices, edits):
        class UI:
            def __init__(self):
                self.choices = iter(choices)
                self.edits = iter(edits)
                self.screens = []
                self.fields = []

            def choose(self, title, text, choices, default=None, **kwargs):
                self.screens.append((title, text, choices, default))
                return next(self.choices)

            def edit(self, item, step, count):
                self.fields.append((item.key, item.value))
                return next(self.edits)

            def test(self, values):
                return setup_cli.test_connection(values)

        ui = UI()
        monkeypatch.setattr(setup_terminal, "TerminalUI", lambda: ui)
        return ui

    return create


def test_stored_environment_loads_outside_checkout_and_shell_takes_precedence(environment, monkeypatch):
    environment.mkdir()
    (environment / ".env").write_text(
        "AGENT_SETTING_CONFIG=settings.openrouter.toml\nOPENROUTER_API_KEY=stored\nMODEL_NAME=stored-model\n"
    )
    monkeypatch.setenv("MODEL_NAME", "shell-model")
    assert setup_cli.load_environment(environment) == environment / ".env"
    assert os.environ["MODEL_NAME"] == "shell-model"
    assert setup_cli.missing_configuration() == []


def test_explicit_env_file_retains_existing_loader_precedence(environment, monkeypatch, tmp_path):
    selected = tmp_path / "selected.env"
    selected.write_text("MODEL_NAME=file-model\n")
    monkeypatch.setenv("ENV_FILE", str(selected))
    monkeypatch.setenv("MODEL_NAME", "shell-model")
    assert setup_cli.load_environment(environment) == selected
    assert os.environ["MODEL_NAME"] == "file-model"


def test_automatic_setup_skips_existing_configuration_without_prompt_or_test(environment, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "existing-value")
    monkeypatch.setenv("MODEL_NAME", "existing-model")
    configure = Mock()
    validate = Mock()
    monkeypatch.setattr(setup_cli, "configure", configure)
    monkeypatch.setattr(setup_cli, "test_connection", validate)
    assert setup_cli.ensure_provider(environment)
    configure.assert_not_called()
    validate.assert_not_called()


def test_noninteractive_start_reports_missing_fields_without_writing(environment, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert not setup_cli.ensure_provider(environment)
    assert not environment.exists()
    assert "OPENAI_API_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("variable", ["WATSONX_PROJECT_ID", "WATSONX_SPACE_ID"])
def test_watsonx_accepts_either_scope(environment, monkeypatch, variable):
    monkeypatch.setenv("AGENT_SETTING_CONFIG", "settings.watsonx.toml")
    monkeypatch.setenv("WATSONX_APIKEY", "existing-value")
    assert "WATSONX_PROJECT_ID or WATSONX_SPACE_ID" in setup_cli.missing_configuration()
    monkeypatch.setenv(variable, "scope-id")
    assert setup_cli.missing_configuration() == []


@pytest.mark.parametrize("provider", setup_cli.PROVIDERS)
def test_supported_provider_uses_packaged_profile(environment, monkeypatch, provider):
    _, name, credential, endpoint, _ = provider
    monkeypatch.setenv("AGENT_SETTING_CONFIG", f"settings.{name}.toml")
    monkeypatch.setenv("MODEL_NAME", "selected-model")
    monkeypatch.setenv(credential or "OPENAI_API_KEY", "existing-value")
    if endpoint:
        monkeypatch.setenv(endpoint, "https://provider.example/v1")
    if name == "watsonx":
        monkeypatch.setenv("WATSONX_PROJECT_ID", "project-id")
    assert setup_cli.missing_configuration() == []


def test_env_update_preserves_comments_unknown_keys_and_private_permissions(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# Keep this comment\nTOOL_ENDPOINT=https://tools.example\nMODEL_NAME=old\n")
    replacement = {
        "MODEL_NAME": "new",
        "OPENAI_API_KEY": "private-value",  # pragma: allowlist secret (Test fixture.)
    }
    setup_cli.save_environment(path, replacement)
    values = dotenv_values(path)
    assert values["MODEL_NAME"] == "new"
    assert values["TOOL_ENDPOINT"] == "https://tools.example"
    assert "# Keep this comment" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_test_preserves_original_file_and_never_prints_key(
    environment, monkeypatch, capsys, scripted_ui
):
    environment.mkdir()
    path = environment / ".env"
    before = b"# Original\nMODEL_NAME=old\n"
    path.write_bytes(before)
    scripted_ui(
        monkeypatch,
        choices=[1, "test", None],
        edits=["selected-model", "https://provider.example/v1", "private-value"],
    )
    monkeypatch.setattr(setup_cli, "test_connection", lambda values: False)
    assert not setup_cli.configure(path)
    assert path.read_bytes() == before
    assert "private-value" not in capsys.readouterr().out


def test_watsonx_wizard_saves_selected_scope_after_connection_test(
    environment, monkeypatch, capsys, scripted_ui
):
    scripted_ui(
        monkeypatch,
        choices=[2, "test"],
        edits=["selected-model", "https://watsonx.example", "private-value", "project", "project-id"],
    )
    captured = {}

    def validate(values):
        captured.update(values)
        assert not (environment / ".env").exists()
        return True

    monkeypatch.setattr(setup_cli, "test_connection", validate)
    assert setup_cli.configure(environment / ".env")
    values = dotenv_values(environment / ".env")
    assert values["AGENT_SETTING_CONFIG"] == "settings.watsonx.toml"
    assert values["MODEL_NAME"] == "selected-model"
    assert values["WATSONX_PROJECT_ID"] == "project-id"
    assert values["WATSONX_SPACE_ID"] == ""
    assert values["WATSONX_APIKEY"] == values["WATSONX_API_KEY"] == captured["WATSONX_API_KEY"]
    assert "private-value" not in capsys.readouterr().out


def test_candidate_validation_does_not_reload_previous_explicit_file(environment, monkeypatch):
    monkeypatch.setenv("ENV_FILE", "/existing/.env")
    runner = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(subprocess, "run", runner)
    assert setup_cli.test_connection({"MODEL_NAME": "candidate"})
    kwargs = runner.call_args.kwargs
    assert "ENV_FILE" not in kwargs["env"]
    assert kwargs["env"]["MODEL_NAME"] == "candidate"
    assert kwargs["timeout"] == 60


def test_setup_dispatch_runs_before_importing_manager_settings(monkeypatch):
    from cuga import cli

    command = Mock(return_value=0)
    monkeypatch.setattr(setup_cli, "main", command)
    monkeypatch.setattr(sys, "argv", ["cuga", "setup", "--check"])
    with pytest.raises(SystemExit) as result:
        cli.run_cli()
    assert result.value.code == 0
    command.assert_called_once_with(["--check"])


def test_manager_help_does_not_prompt(monkeypatch):
    from cuga import cli

    prepare = Mock()
    app = Mock()
    monkeypatch.setattr("cuga.local_setup.prepare_local_manager", prepare)
    monkeypatch.setitem(sys.modules, "cuga.cli.main", SimpleNamespace(app=app))
    monkeypatch.setattr(sys, "argv", ["cuga", "start", "manager", "--help"])
    cli.run_cli()
    prepare.assert_not_called()
    app.assert_called_once()


@pytest.mark.parametrize("credential", ["WATSONX_API_KEY", "WATSONX_APIKEY"])
def test_watsonx_recognizes_both_credential_names(environment, monkeypatch, credential):
    monkeypatch.setenv("AGENT_SETTING_CONFIG", "settings.watsonx.toml")
    monkeypatch.setenv(credential, "stored-key")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "stored-project")
    assert setup_cli.missing_configuration() == []


def test_new_key_neutralizes_old_authorization_header(environment, monkeypatch, scripted_ui):
    monkeypatch.setenv("LLM_AUTH_HEADER", "Bearer old-key")
    scripted_ui(
        monkeypatch, choices=[4, "test"], edits=["selected-model", "https://provider.example/v1", "new-key"]
    )
    captured = {}
    monkeypatch.setattr(setup_cli, "test_connection", lambda values: not captured.update(values))
    assert setup_cli.configure(environment / ".env")
    assert captured["LLM_AUTH_HEADER"] == ""
    assert captured["OPENAI_API_KEY"] == "new-key"


def test_malformed_environment_is_reported_without_modification(environment, monkeypatch, capsys):
    environment.mkdir()
    path = environment / ".env"
    path.write_bytes(b'OPENAI_API_KEY=\xff\xfe\n')
    before = path.read_bytes()
    assert not setup_cli.ensure_provider(environment)
    assert path.read_bytes() == before
    assert "Could not read configuration" in capsys.readouterr().err


def test_profile_without_platform_reports_missing_configuration(environment, monkeypatch):
    models = environment / "models"
    models.mkdir(parents=True)
    (models / "settings.openai.toml").write_text('[agent.code.model]\nmodel_name="example"\n')
    monkeypatch.setenv("CUGA_CONFIGURATIONS_DIR", str(environment))
    assert setup_cli.missing_configuration() == ["AGENT_SETTING_CONFIG (model profile unavailable)"]


def test_read_failure_does_not_leave_temporary_credentials(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b'\xff')
    with pytest.raises(UnicodeDecodeError):
        setup_cli.save_environment(path, {"MODEL_NAME": "new"})
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("name,variable", [("ollama", "OPENAI_BASE_URL"), ("minimax", "MINIMAX_BASE_URL")])
def test_profile_defaults_yield_to_selected_endpoint(environment, monkeypatch, name, variable):
    from cuga.backend.llm.models import LLMManager

    profile = setup_cli.read_profile(f"settings.{name}.toml")
    monkeypatch.setenv(variable, "http://127.0.0.1:12345/v1")
    assert LLMManager()._get_base_url(profile, profile["platform"]) == "http://127.0.0.1:12345/v1"
    assert (
        LLMManager()._get_base_url({"url": "https://explicit.example/v1"}, profile["platform"])
        == "https://explicit.example/v1"
    )


@pytest.mark.asyncio
async def test_saved_agent_connection_does_not_replace_terminal_selection(environment, monkeypatch):
    from cuga.backend.llm import models
    from cuga.backend.server.manage_routes import apply
    import cuga.config

    selected = {"platform": "openrouter", "model_name": "new-model"}
    configured = SimpleNamespace(
        secrets=SimpleNamespace(force_env=True), agent=SimpleNamespace(code=SimpleNamespace(model=selected))
    )
    monkeypatch.setattr(cuga.config, "settings", configured)
    monkeypatch.setattr(models, "settings", configured)
    monkeypatch.setenv("CUGA_LOCAL_MANAGER", "true")
    monkeypatch.setenv("MODEL_NAME", "new-model")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://new-endpoint.example/v1")
    manager = Mock()
    monkeypatch.setattr(models, "LLMManager", lambda: manager)
    old = {
        "provider": "openai",
        "model": "old-model",
        "base_url": "https://old-endpoint.example/v1",
        "api_key": "old-key",  # pragma: allowlist secret (Test fixture.)
    }
    state = SimpleNamespace(agent=None, policy_system=None)
    config = {"llm": old, "tools": [{"name": "crm", "include": ["list_contacts"]}]}
    await apply.apply_published_config(state, config)
    apply.apply_llm_to_state(state, old)
    apply.apply_llm_to_draft_state(state, old)
    assert os.environ["MODEL_NAME"] == "new-model"
    assert state.tools_include_by_app == {"crm": ["list_contacts"]}
    manager.get_model.assert_called_once_with(selected)
    assert config["llm"] == old


@pytest.mark.asyncio
async def test_supervisor_model_config_uses_terminal_profile_not_saved_connection(environment, monkeypatch):
    from cuga.backend.cuga_graph import entry_graph

    selected = {"platform": "openrouter", "model_name": "new-model", "url": "https://new-endpoint.example/v1"}
    monkeypatch.setattr(
        entry_graph,
        "settings",
        SimpleNamespace(
            secrets=SimpleNamespace(force_env=True),
            agent=SimpleNamespace(code=SimpleNamespace(model=selected)),
        ),
    )
    monkeypatch.setenv("CUGA_LOCAL_MANAGER", "true")
    manager = Mock()
    monkeypatch.setattr(entry_graph, "LLMManager", lambda: manager)
    graph = entry_graph.CugaEntryGraph.__new__(entry_graph.CugaEntryGraph)
    graph.llm_config = {
        "provider": "openai",
        "model": "old-model",
        "base_url": "https://old-endpoint.example/v1",
        "api_key": "old-key",  # pragma: allowlist secret (Test fixture.)
    }
    model, config, _ = await graph._build_model_and_config()
    assert config == {**selected, "streaming": False}
    assert "api_key" not in config
    manager.get_model.assert_called_once_with(config)
    assert model is manager.get_model.return_value


def test_retry_edits_only_wrong_key_and_keeps_other_answers(environment, monkeypatch, scripted_ui):
    ui = scripted_ui(
        monkeypatch,
        choices=[1, "test", 2, "test"],
        edits=["chosen-model", "https://provider.example/v1", "wrong-key", "correct-key"],
    )
    seen = []

    def check(values):
        seen.append(dict(values))
        assert not (environment / ".env").exists()
        return setup_cli.ConnectionResult(len(seen) == 2, "authentication")

    monkeypatch.setattr(setup_cli, "test_connection", check)
    assert setup_cli.configure(environment / ".env")
    assert seen[0]["MODEL_NAME"] == seen[1]["MODEL_NAME"] == "chosen-model"
    assert seen[0]["OPENROUTER_BASE_URL"] == seen[1]["OPENROUTER_BASE_URL"]
    assert seen[1]["OPENROUTER_API_KEY"] == "correct-key"
    assert ui.fields[-1] == ("OPENROUTER_API_KEY", "wrong-key")
    review = str(ui.screens)
    assert "Authentication was rejected" in review
    assert "wrong-key" not in review and "correct-key" not in review


def test_retry_without_edits_preserves_entire_candidate(environment, monkeypatch, scripted_ui):
    scripted_ui(monkeypatch, choices=[3, "test", "test"], edits=["local-model", "http://localhost:11434/v1"])
    seen = []

    def check(values):
        seen.append(dict(values))
        return len(seen) == 2

    monkeypatch.setattr(setup_cli, "test_connection", check)
    assert setup_cli.configure(environment / ".env")
    assert seen[0] == seen[1]


def test_back_keeps_draft_and_never_tests_before_review(environment, monkeypatch, scripted_ui):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, "test"],
        edits=[
            "model-typo",
            setup_terminal.BACK,
            "corrected-model",
            "https://api.openai.com/v1",
            "private-key",
        ],
    )
    check = Mock(return_value=True)
    monkeypatch.setattr(setup_cli, "test_connection", check)
    assert setup_cli.configure(environment / ".env")
    assert ui.fields[2] == ("MODEL_NAME", "model-typo")
    check.assert_called_once()
    assert check.call_args.args[0]["MODEL_NAME"] == "corrected-model"


def test_switch_provider_and_return_restores_unsaved_draft(environment, monkeypatch, scripted_ui):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, "provider", 3, "provider", 0, "test"],
        edits=[
            "openai-model",
            "https://api.openai.com/v1",
            "private-key",
            "local-model",
            "http://localhost:11434/v1",
            "openai-model",
            "https://api.openai.com/v1",
            "private-key",
        ],
    )
    monkeypatch.setattr(setup_cli, "test_connection", lambda values: True)
    assert setup_cli.configure(environment / ".env")
    assert ui.fields[-3:] == [
        ("MODEL_NAME", "openai-model"),
        ("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        ("OPENAI_API_KEY", "private-key"),
    ]
    saved = dotenv_values(environment / ".env")
    assert saved["AGENT_SETTING_CONFIG"] == "settings.openai.toml"
    assert saved["OPENAI_BASE_URL"] == "https://api.openai.com/v1"


def test_current_provider_defaults_to_existing_watsonx(environment, monkeypatch):
    monkeypatch.setenv("AGENT_SETTING_CONFIG", "settings.watsonx.toml")
    monkeypatch.setenv("MODEL_NAME", "existing-model")
    monkeypatch.setenv("WATSONX_APIKEY", "saved-key")
    monkeypatch.setenv("WATSONX_SPACE_ID", "space-id")
    assert setup_terminal.current_provider() == 2
    values = setup_terminal.connection_values(2, setup_terminal.provider_fields(2))
    assert values["MODEL_NAME"] == "existing-model"
    assert values["WATSONX_API_KEY"] == values["WATSONX_APIKEY"] == "saved-key"
    assert values["WATSONX_SPACE_ID"] == "space-id"
    assert values["WATSONX_PROJECT_ID"] == ""


def test_private_endpoint_defaults_to_compatible_provider(environment, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://inference.internal:8000/v1")
    assert setup_terminal.current_provider() == 4


@pytest.mark.parametrize("stage", ["provider", "details", "review"])
def test_cancel_never_tests_or_changes_existing_config(environment, monkeypatch, scripted_ui, stage):
    environment.mkdir()
    path = environment / ".env"
    before = b"# Keep\nMODEL_NAME=old\nTOOL_ENDPOINT=https://tools.example\n"
    path.write_bytes(before)
    choices = [None] if stage == "provider" else [0, None]
    edits = [None] if stage == "details" else ["model", "https://api.openai.com/v1", "private-key"]
    scripted_ui(monkeypatch, choices=choices, edits=edits)
    check = Mock()
    monkeypatch.setattr(setup_cli, "test_connection", check)
    assert not setup_cli.configure(path)
    assert path.read_bytes() == before
    check.assert_not_called()


@pytest.mark.parametrize(
    "status,reason",
    [(401, "authentication"), (403, "authentication"), (404, "not_found"), (429, "quota"), (400, "request")],
)
def test_provider_error_status_is_classified_without_response_text(status, reason):
    error = RuntimeError("private-key and internal provider response")
    error.status_code = status
    assert setup_cli.connection_failure_reason(error) == reason
    assert "private-key" not in setup_cli.ConnectionResult(False, reason).message


def test_wrapped_certificate_error_keeps_specific_guidance():
    import ssl

    class APIConnectionError(Exception):
        pass

    error = APIConnectionError("private provider response")
    error.__cause__ = ssl.SSLCertVerificationError("certificate details")
    assert setup_cli.connection_failure_reason(error) == "tls"


def test_unsubmitted_invalid_draft_can_be_corrected_from_review(environment, monkeypatch, scripted_ui):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, 1, "test", 1, "test"],
        edits=[
            "model",
            "https://api.openai.com/v1",
            "private-key",
            setup_terminal.BACK,
            "https://fixed.example/v1",
        ],
    )
    original_edit = ui.edit

    def edit(item, step, count):
        response = original_edit(item, step, count)
        if response is setup_terminal.BACK:
            item.value = "ftp://unfinished"
        return response

    ui.edit = edit
    check = Mock(return_value=True)
    monkeypatch.setattr(setup_cli, "test_connection", check)
    assert setup_cli.configure(environment / ".env")
    check.assert_called_once()
    assert check.call_args.args[0]["OPENAI_BASE_URL"] == "https://fixed.example/v1"
    assert any("Enter an http:// or https:// URL" in screen[1] for screen in ui.screens)


def test_unsubmitted_model_draft_survives_provider_round_trip(environment, monkeypatch, scripted_ui):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, 3, 0, "test"],
        edits=[
            setup_terminal.BACK,
            setup_terminal.BACK,
            "new-draft",
            "https://api.openai.com/v1",
            "private-key",
        ],
    )
    original_edit = ui.edit

    def edit(item, step, count):
        response = original_edit(item, step, count)
        if response is setup_terminal.BACK and len(ui.fields) == 1:
            item.value = "new-draft"
        return response

    ui.edit = edit
    monkeypatch.setattr(setup_cli, "test_connection", lambda values: True)
    assert setup_cli.configure(environment / ".env")
    assert ui.fields[2] == ("MODEL_NAME", "new-draft")


def test_check_does_not_echo_raw_provider_output(environment, monkeypatch, capsys):
    runner = Mock(
        return_value=SimpleNamespace(
            returncode=1,
            stdout=b'private-key\nCUGA_SETUP_RESULT:{"reason":"authentication"}\n',
            stderr=b'private-key',
        )
    )
    monkeypatch.setattr(subprocess, "run", runner)
    result = setup_cli.test_connection({})
    assert not result and result.reason == "authentication"
    assert "private-key" not in capsys.readouterr().out


def test_check_timeout_is_actionable(environment, monkeypatch):
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("validation", 60)))
    assert setup_cli.test_connection({}).reason == "timeout"


def test_keyboard_interrupt_preserves_previous_config(environment, monkeypatch, scripted_ui):
    environment.mkdir()
    path = environment / ".env"
    before = b"MODEL_NAME=old\n"
    path.write_bytes(before)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    scripted_ui(monkeypatch, choices=[3, "test"], edits=["model", "http://localhost:11434/v1"])
    monkeypatch.setattr(setup_cli, "test_connection", Mock(side_effect=KeyboardInterrupt))
    assert not setup_cli.ensure_provider(environment)
    assert path.read_bytes() == before


@pytest.fixture
def interactive_setup(environment, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("cuga.local_setup.prepare_local_manager", lambda: environment)
    monkeypatch.setattr(setup_cli, "test_connection", lambda values: True)
    return environment


def test_ready_screen_starts_same_installation_with_saved_connection(
    interactive_setup, monkeypatch, scripted_ui, tmp_path
):
    installation = tmp_path / "isolated tools" / "bin"
    installation.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(installation / "python"))
    ui = scripted_ui(
        monkeypatch,
        choices=[0, "test", "start"],
        edits=["selected-model", "https://api.openai.com/v1", "private-key"],
    )
    saved = interactive_setup / ".env"
    captured = {}

    class ManagerStarted(Exception):
        pass

    def execute(command, args, env):
        assert dotenv_values(saved)["MODEL_NAME"] == "selected-model"
        captured.update(command=command, args=args, env=env)
        raise ManagerStarted

    monkeypatch.setattr(os, "execvpe", execute)
    with pytest.raises(ManagerStarted):
        setup_cli.main([])
    assert captured["args"] == [str(installation / "python"), "-m", "cuga.cli", "start", "manager"]
    assert "ENV_FILE" not in captured["env"]
    assert captured["env"]["MODEL_NAME"] == "selected-model"
    assert captured["env"]["CUGA_DATA_DIR"] == str(interactive_setup)
    ready = ui.screens[-1]
    assert ready[0] == "Connection ready" and ready[3] == "start"
    assert "selected-model" in ready[1] and str(saved) in ready[1]
    assert "private-key" not in ready[1]


@pytest.mark.parametrize("action", ["finish", None])
def test_finishing_ready_screen_prints_exact_command_without_credentials(
    interactive_setup, monkeypatch, scripted_ui, capsys, action
):
    scripted_ui(
        monkeypatch,
        choices=[0, "test", action],
        edits=["model", "https://api.openai.com/v1", "private-key"],
    )
    execute = Mock()
    monkeypatch.setattr(os, "execvpe", execute)
    assert setup_cli.main([]) == 0
    execute.assert_not_called()
    output = capsys.readouterr().out
    command_line = output.split("Start the manager later with:\n")[1].splitlines()[0]
    tokens = shlex.split(command_line)
    assert tokens[:5] == ["cd", str(Path.cwd()), "&&", "env", f"CUGA_DATA_DIR={interactive_setup}"]
    assert tokens[5] == f"ENV_FILE={interactive_setup / '.env'}"
    assert tokens[-2:] == ["start", "manager"]
    assert "private-key" not in output


def test_edit_from_ready_prefills_saved_values_and_returns_after_save(
    interactive_setup, monkeypatch, scripted_ui
):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, "test", "edit", 0, "test", "finish"],
        edits=[
            "first-model",
            "https://api.openai.com/v1",
            "private-key",
            "updated-model",
            "https://api.openai.com/v1",
            "private-key",
        ],
    )
    assert setup_cli.main([]) == 0
    assert ui.fields[3] == ("MODEL_NAME", "first-model")
    assert dotenv_values(interactive_setup / ".env")["MODEL_NAME"] == "updated-model"
    assert [screen[0] for screen in ui.screens].count("Connection ready") == 2


def test_cancelling_edit_returns_to_ready_with_saved_connection(interactive_setup, monkeypatch, scripted_ui):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, "test", "edit", None, "finish"],
        edits=["saved-model", "https://api.openai.com/v1", "private-key"],
    )
    assert setup_cli.main([]) == 0
    assert dotenv_values(interactive_setup / ".env")["MODEL_NAME"] == "saved-model"
    assert [screen[0] for screen in ui.screens].count("Connection ready") == 2


def test_keeping_existing_connection_offers_start_without_retesting(
    interactive_setup, monkeypatch, scripted_ui
):
    monkeypatch.setenv("MODEL_NAME", "existing-model")
    monkeypatch.setenv("OPENAI_API_KEY", "existing-key")
    ui = scripted_ui(monkeypatch, choices=["keep", "finish"], edits=[])
    check = Mock()
    monkeypatch.setattr(setup_cli, "test_connection", check)
    assert setup_cli.main([]) == 0
    check.assert_not_called()
    assert ui.screens[-1][0] == "Connection configured"
    assert "verified" not in ui.screens[-1][1]


def test_manager_launch_fallback_and_quoted_paths(environment, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Python runtime" / "python"))
    root = tmp_path / "data with spaces"
    path = root / "chosen connection.env"
    command, env = setup_cli.manager_launch(path, root)
    assert command == [sys.executable, "-m", "cuga.cli", "start", "manager"]
    assert "ENV_FILE" not in env
    assert shlex.split(setup_cli.manager_launch_hint(path, root)) == [
        "cd",
        str(Path.cwd()),
        "&&",
        "env",
        f"CUGA_DATA_DIR={root}",
        *command,
    ]


def test_failed_manager_start_preserves_saved_file_and_gives_launch_command(
    interactive_setup, monkeypatch, scripted_ui, capsys
):
    scripted_ui(
        monkeypatch,
        choices=[0, "test", "start"],
        edits=["model", "https://api.openai.com/v1", "private-key"],
    )
    monkeypatch.setattr(os, "execvpe", Mock(side_effect=OSError("private provider response")))
    assert setup_cli.main([]) == 1
    assert dotenv_values(interactive_setup / ".env")["MODEL_NAME"] == "model"
    error = capsys.readouterr().err
    assert "Could not start the manager" in error
    assert "CUGA_DATA_DIR=" in error
    assert "private provider response" not in error
    assert "private-key" not in error


def test_start_from_existing_connection_preserves_shell_precedence(environment, monkeypatch):
    environment.mkdir()
    path = environment / ".env"
    path.write_text("MODEL_NAME=file-model\nOPENAI_API_KEY=file-key\n")
    monkeypatch.setenv("MODEL_NAME", "shell-model")
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    _, env = setup_cli.manager_launch(path, environment)
    assert "ENV_FILE" not in env
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import os; "
            "from cuga.setup_cli import load_environment; "
            "load_environment(Path(os.environ['CUGA_DATA_DIR'])); "
            "from cuga.config import settings; "
            "assert os.environ['MODEL_NAME'] == 'shell-model'; "
            "assert os.environ['OPENAI_API_KEY'] == 'shell-key'",
        ],
        cwd=environment,
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_launch_preserves_explicit_env_file(environment, monkeypatch):
    path = environment / "selected.env"
    monkeypatch.setenv("ENV_FILE", "relative-selected.env")
    _, env = setup_cli.manager_launch(path, environment)
    assert env["ENV_FILE"] == str(path)
    assert f"ENV_FILE={path}" in setup_cli.manager_launch_hint(path, environment)


def test_interrupting_edit_validation_returns_to_saved_completion(
    interactive_setup, monkeypatch, scripted_ui
):
    ui = scripted_ui(
        monkeypatch,
        choices=[0, "test", "edit", 0, "test", "finish"],
        edits=[
            "saved-model",
            "https://api.openai.com/v1",
            "private-key",
            "draft-model",
            "https://api.openai.com/v1",
            "draft-key",
        ],
    )
    monkeypatch.setattr(setup_cli, "test_connection", Mock(side_effect=[True, KeyboardInterrupt]))
    assert setup_cli.main([]) == 0
    assert dotenv_values(interactive_setup / ".env")["MODEL_NAME"] == "saved-model"
    assert os.environ["MODEL_NAME"] == "saved-model"
    assert [screen[0] for screen in ui.screens].count("Connection ready") == 2


@pytest.mark.skipif(os.name == "nt", reason="Exercises the POSIX launch command; Windows uses PowerShell.")
def test_printed_module_fallback_bootstraps_storage_in_fresh_shell(environment, monkeypatch, tmp_path):
    import dotenv

    environment.mkdir()
    (environment / ".env").write_text("MODEL_NAME=model\nOPENAI_API_KEY=local-test-value\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "executable", str(Path(sys.executable).resolve()))
    # Stop before services start, then inspect the real module entry point's bootstrap.
    (tmp_path / "sitecustomize.py").write_text(
        "import os, sys, types\n"
        "from pathlib import Path\n"
        "def app():\n"
        "    root = Path(os.environ['CUGA_DATA_DIR'])\n"
        "    assert os.environ['CUGA_LOCAL_MANAGER'] == 'true'\n"
        "    assert os.environ['CUGA_DBS_DIR'] == str(root / 'dbs')\n"
        "    assert os.environ['CUGA_WORKSPACE_PATH'] == str(root / 'workspace')\n"
        "    assert os.environ['DYNACONF_STORAGE__PRESERVE_CONFIGS_ON_STARTUP'] == 'any'\n"
        "    assert (root / 'secret.key').is_file()\n"
        "    print('storage bootstrapped')\n"
        "sys.modules['cuga.cli.main'] = types.SimpleNamespace(app=app)\n"
    )
    hint = setup_cli.manager_launch_hint(environment / ".env", environment)
    assert "-m cuga.cli start manager" in hint
    _, env = setup_cli.manager_launch(environment / ".env", environment)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(Path(setup_cli.__file__).parents[1]), str(Path(dotenv.__file__).parents[1])]
    )
    env.pop("CUGA_DEMO_MODE", None)
    result = subprocess.run(
        hint, shell=True, executable="/bin/bash", env=env, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"storage bootstrapped" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Exercises the POSIX launch command.")
def test_finish_after_save_replays_new_connection_over_old_parent_shell(
    interactive_setup, monkeypatch, scripted_ui, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODEL_NAME", "old-model")
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")
    parent_environment = dict(os.environ)
    scripted_ui(
        monkeypatch,
        choices=["change", 0, "test", "finish"],
        edits=["saved-model", "https://api.openai.com/v1", "saved-key"],
    )
    assert setup_cli.main([]) == 0
    hint = capsys.readouterr().out.split("Start the manager later with:\n")[1].splitlines()[0]
    assert f"ENV_FILE={interactive_setup / '.env'}" in hint
    (tmp_path / "sitecustomize.py").write_text(
        "import os, sys, types\n"
        "def app():\n"
        "    from cuga.config import settings\n"
        "    assert os.environ['MODEL_NAME'] == 'saved-model'\n"
        "    assert os.environ['OPENAI_API_KEY'] == 'saved-key'\n"
        "    assert os.environ['CUGA_LOCAL_MANAGER'] == 'true'\n"
        "    print('saved connection replayed')\n"
        "sys.modules['cuga.cli.main'] = types.SimpleNamespace(app=app)\n"
    )
    parent_environment["PYTHONPATH"] = os.pathsep.join(
        [str(tmp_path), str(Path(setup_cli.__file__).parents[1])]
    )
    parent_environment.pop("CUGA_DEMO_MODE", None)
    result = subprocess.run(
        hint, shell=True, executable="/bin/bash", env=parent_environment, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"saved connection replayed" in result.stdout
