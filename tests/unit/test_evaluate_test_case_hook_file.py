"""`cuga evaluate --test-case-hook-file` writes the running test case's name to
a file (and clears it afterward) so an external test double, e.g. a mock MCP
server, can scope its replay to that test case."""

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cuga.cli import main as cli

pytestmark = pytest.mark.unit

EVALUATION_DIR = Path(cli.PACKAGE_ROOT) / "evaluation"


@pytest.fixture
def evaluate_cuga(monkeypatch):
    # evaluate_cuga.py runs as a script and imports its sibling modules bare.
    monkeypatch.syspath_prepend(str(EVALUATION_DIR))
    return importlib.import_module("evaluate_cuga")


def test_hook_writes_and_clears_test_case_name(evaluate_cuga, tmp_path):
    hook = tmp_path / "current_test_case"
    evaluate_cuga._run_test_case_hook(str(hook), "case-1")
    assert hook.read_text(encoding="utf-8") == "case-1"
    evaluate_cuga._run_test_case_hook(str(hook), None)
    assert hook.read_text(encoding="utf-8") == ""


def test_hook_is_a_no_op_without_a_path(evaluate_cuga, tmp_path):
    evaluate_cuga._run_test_case_hook(None, "case-1")
    assert list(tmp_path.iterdir()) == []


def test_unwritable_hook_path_does_not_fail(evaluate_cuga, tmp_path):
    evaluate_cuga._run_test_case_hook(str(tmp_path / "missing-dir" / "hook"), "case-1")


@pytest.mark.parametrize(
    "extra_args, expected_tail",
    [
        ([], ["-r", "results.json"]),
        (["--test-case-hook-file", "/tmp/hook"], ["--test-case-hook-file", "/tmp/hook"]),
    ],
)
def test_cli_forwards_hook_file_to_evaluator(monkeypatch, extra_args, expected_tail):
    commands = {}
    monkeypatch.setattr(
        cli, "run_direct_service", lambda name, command, **kwargs: commands.__setitem__(name, command)
    )
    monkeypatch.setattr(cli, "direct_processes", {"registry": object()})
    monkeypatch.setattr(cli, "wait_for_registry_server", lambda port: None)
    monkeypatch.setattr(cli, "wait_for_direct_processes", lambda: None)

    result = CliRunner().invoke(cli.app, ["evaluate", "tests.json", "results.json", *extra_args])

    assert result.exit_code == 0, result.output
    assert commands["evaluation"][-len(expected_tail) :] == expected_tail
