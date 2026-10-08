import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from dotenv import dotenv_values
import pytest

from cuga import setup_cli

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


def test_failed_test_preserves_original_file_and_never_prints_key(environment, monkeypatch, capsys):
    environment.mkdir()
    path = environment / ".env"
    before = b"# Original\nMODEL_NAME=old\n"
    path.write_bytes(before)
    answers = iter(["2", "selected-model", "https://provider.example/v1"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(setup_cli.getpass, "getpass", lambda prompt: "private-value")
    monkeypatch.setattr(setup_cli, "test_connection", lambda values: False)
    assert not setup_cli.configure(path)
    assert path.read_bytes() == before
    assert "private-value" not in capsys.readouterr().out


def test_watsonx_wizard_saves_selected_scope_after_connection_test(environment, monkeypatch, capsys):
    answers = iter(["3", "selected-model", "https://watsonx.example", "project", "project-id"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(setup_cli.getpass, "getpass", lambda prompt: "private-value")
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


def test_new_key_neutralizes_old_authorization_header(environment, monkeypatch):
    monkeypatch.setenv("LLM_AUTH_HEADER", "Bearer old-key")
    answers = iter(["5", "selected-model", "https://provider.example/v1"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(setup_cli.getpass, "getpass", lambda prompt: "new-key")
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
