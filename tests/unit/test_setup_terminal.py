"""Exercise the real prompt_toolkit keyboard handling without a live terminal."""

import asyncio
import io

from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output.vt100 import Vt100_Output
import pytest

from cuga.setup_cli import PROVIDERS
from cuga.setup_terminal import BACK, SetupField, TerminalUI


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def bounded_keyboard_test(monkeypatch):
    def run(app):
        async def bounded():
            return await asyncio.wait_for(app.run_async(), timeout=3)

        return asyncio.run(bounded())

    monkeypatch.setattr(Application, "run", run)


def test_arrow_keys_select_provider_and_enter_continues():
    with create_pipe_input() as keyboard, create_app_session(input=keyboard, output=DummyOutput()):
        keyboard.send_text("\x1b[B\r")
        assert TerminalUI().choose("Provider", "Choose", [(0, "OpenAI"), (1, "watsonx")], default=0) == 1


@pytest.mark.parametrize("rows,columns", [(18, 72), (24, 80), (40, 140)])
@pytest.mark.parametrize("exit_keys,result", [("\r", 0), ("\x03", None)])
def test_arrow_redraws_stay_fixed_and_restore_terminal(monkeypatch, rows, columns, exit_keys, result):
    """Exercise VT100 rendering, not only key handling with DummyOutput."""
    output_text = io.StringIO()
    output = Vt100_Output(output_text, lambda: Size(rows=rows, columns=columns), term="xterm-256color")
    frames = []

    def run(app):
        redraw = asyncio.Event()

        def rendered(application):
            screen = application.renderer._last_screen
            if screen is not None:
                for y, row in screen.data_buffer.items():
                    line = "".join(row[x].char for x in range(columns))
                    if "CUGA · Provider" in line:
                        frames.append((y, line))
            redraw.set()

        app.after_render += rendered

        async def bounded():
            task = asyncio.create_task(app.run_async())
            try:
                # Separate events force actual redraws after each arrow key.
                for keys in ["\x1b[B", "\x1b[B", "\x1b[A", "\x1b[A", exit_keys]:
                    await asyncio.wait_for(redraw.wait(), timeout=3)
                    redraw.clear()
                    keyboard.send_text(keys)
                return await asyncio.wait_for(task, timeout=3)
            finally:
                if not task.done():
                    task.cancel()

        return asyncio.run(bounded())

    monkeypatch.setattr(Application, "run", run)
    with create_pipe_input() as keyboard, create_app_session(input=keyboard, output=output):
        assert (
            TerminalUI().choose(
                "Provider",
                "Choose",
                [(index, provider[0]) for index, provider in enumerate(PROVIDERS)],
                default=0,
            )
            == result
        )
    assert len(frames) >= 5
    assert len(set(frames)) == 1, "Arrow redraws moved the dialog frame"
    rendered_output = output_text.getvalue()
    assert rendered_output.count("\x1b[?1049h") == 1, "Setup must use an isolated terminal screen"
    assert rendered_output.count("\x1b[?1049l") == 1, "Setup must restore the previous terminal screen"
    assert "\x1b[6n" not in rendered_output, "Setup must not depend on inline cursor-position reports"


def test_invalid_endpoint_can_be_corrected_in_same_screen():
    with create_pipe_input() as keyboard, create_app_session(input=keyboard, output=DummyOutput()):
        keyboard.send_text("ftp://wrong\r\x01\x0bhttps://valid.example/v1\r")
        value = TerminalUI().edit(SetupField("OPENAI_BASE_URL", "Endpoint URL", ""), 1, 1)
        assert value == "https://valid.example/v1"


def test_enter_keeps_saved_secret_and_summary_hides_it():
    item = SetupField("OPENAI_API_KEY", "API key", "private-value", secret=True)
    assert "private-value" not in repr(item)
    assert "private-value" not in item.summary()
    with create_pipe_input() as keyboard, create_app_session(input=keyboard, output=DummyOutput()):
        keyboard.send_text("\r")
        assert TerminalUI().edit(item, 1, 1) == "private-value"


@pytest.mark.parametrize("keys,result", [("\x1b", BACK), ("\x03", None)])
def test_back_and_cancel_keyboard_controls(keys, result):
    with create_pipe_input() as keyboard, create_app_session(input=keyboard, output=DummyOutput()):
        keyboard.send_text(keys)
        assert TerminalUI().edit(SetupField("MODEL_NAME", "Model", "model"), 1, 1) is result


@pytest.mark.parametrize("back_keys", ["\x1b", "\t\t\r"])
def test_back_retains_unsubmitted_draft(back_keys):
    item = SetupField("MODEL_NAME", "Model", "previous-model")
    with create_pipe_input() as keyboard, create_app_session(input=keyboard, output=DummyOutput()):
        keyboard.send_text("\x01\x0bnew-draft" + back_keys)
        assert TerminalUI().edit(item, 1, 1) is BACK
    assert item.value == "new-draft"


@pytest.mark.parametrize("value", ["", "value\nother", "value\x1b[31m"])
def test_configuration_fields_reject_empty_and_control_characters(value):
    assert SetupField("MODEL_NAME", "Model", "").error(value)
