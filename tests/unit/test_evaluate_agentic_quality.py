"""`cuga evaluate --agentic-quality`: the CLI hands the judge settings to the
evaluator, and tracked tool calls feed tool_call_score."""

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuga.cli import main as cli

pytestmark = pytest.mark.unit

EVALUATION_DIR = Path(cli.PACKAGE_ROOT) / "evaluation"


@pytest.fixture
def services(monkeypatch):
    """Records each service the CLI would start instead of starting it."""
    started = {}

    def fake_run_direct_service(service_name, command, env_vars=None, **kwargs):
        started[service_name] = {"command": command, "env_vars": env_vars}

    monkeypatch.setattr(cli, "run_direct_service", fake_run_direct_service)
    monkeypatch.setattr(cli, "direct_processes", {"registry": object()})
    monkeypatch.setattr(cli, "wait_for_registry_server", lambda port: None)
    monkeypatch.setattr(cli, "wait_for_direct_processes", lambda: None)
    for var in ("CUGA_JUDGE_BASE_URL", "CUGA_JUDGE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CUGA_JUDGE_API_KEY", "judge-key")
    return started


def _evaluate(*args):
    return CliRunner().invoke(cli.app, ["evaluate", "tests.json", "results.json", *args])


def test_agentic_quality_without_judge_url_fails_before_starting_services(services):
    result = _evaluate("--agentic-quality")
    assert result.exit_code == 1
    assert services == {}


def test_judge_settings_go_to_the_evaluator_via_env(services):
    result = _evaluate("--agentic-quality", "--judge-url", "http://judge:8000/v1", "--judge-model", "m")

    assert result.exit_code == 0, result.output
    evaluation = services["evaluation"]
    assert "--agentic-quality" in evaluation["command"]
    assert evaluation["env_vars"] == {"CUGA_JUDGE_BASE_URL": "http://judge:8000/v1", "CUGA_JUDGE_MODEL": "m"}
    assert "judge-key" not in " ".join(evaluation["command"])


def test_without_agentic_quality_nothing_changes(services):
    result = _evaluate()

    assert result.exit_code == 0, result.output
    assert "--agentic-quality" not in services["evaluation"]["command"]
    assert services["evaluation"]["env_vars"] is None


def test_tracked_tool_calls_are_rebuilt_for_tool_call_score(monkeypatch):
    monkeypatch.syspath_prepend(str(EVALUATION_DIR))
    evaluate_cuga = importlib.import_module("evaluate_cuga")

    calls = evaluate_cuga._corrected_tool_calls_for_scoring(
        [
            {"tool": "petstore_getPetById", "arguments": {"petId": 1}, "app_name": "petstore"},
            {"tool": "listPets", "arguments": None, "app_name": ""},
        ]
    )

    assert [(c.name, c.args) for c in calls] == [("getPetById", {"petId": 1}), ("listPets", {})]
