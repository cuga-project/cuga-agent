"""Exercise the real prompt_toolkit keyboard handling without a live terminal."""

import asyncio

from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
import pytest

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
