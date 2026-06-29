from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.run_output import (
    format_run_command_output,
)


def test_format_run_command_output_appends_stderr_on_failure() -> None:
    out = format_run_command_output("oops", "detail", failed=True)
    assert out == "oops\n[stderr]\ndetail"


def test_format_run_command_output_omits_stderr_on_success() -> None:
    out = format_run_command_output('{"ok": true}\n', "DeprecationWarning: x", failed=False)
    assert out == '{"ok": true}\n'
    assert "[stderr]" not in out


def test_format_run_command_output_empty_stdout() -> None:
    assert format_run_command_output("", "", failed=False) == "(command completed with no output)"
