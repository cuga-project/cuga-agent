"""Interactive terminal setup. Imported only when provider configuration is needed."""

from dataclasses import dataclass, field as dataclass_field
import os

from cuga import setup_cli


BACK = object()


@dataclass
class SetupField:
    key: str
    label: str
    value: str = dataclass_field(repr=False)
    secret: bool = False
    hint: str = ""

    def error(self, value: str) -> str:
        if not value.strip():
            return "A value is required."
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            return "Use a single line without control characters."
        if self.key == "scope" and value not in ("project", "space"):
            return "Choose project or space."
        if self.key.endswith(("URL", "ENDPOINT")) and not setup_cli.valid_endpoint(value):
            return "Enter an http:// or https:// URL without embedded credentials."
        return ""

    def summary(self) -> str:
        return "[set — hidden]" if self.secret else self.value


def current_provider() -> int:
    filename = setup_cli.profile_name()
    for index, (_, name, _, endpoint, default) in enumerate(setup_cli.PROVIDERS):
        if filename == f"settings.{name}.toml":
            # Both public OpenAI and private compatible endpoints use the same profile.
            if name == "openai" and os.getenv(endpoint, "").rstrip("/") not in ("", default):
                return 4
            return index
    return 0


def provider_fields(index: int) -> list[SetupField]:
    _, provider, credential, endpoint, default_endpoint = setup_cli.PROVIDERS[index]
    filename = f"settings.{provider}.toml"
    profile = setup_cli.read_profile(filename)
    same_provider = filename == setup_cli.profile_name()
    model = os.getenv("MODEL_NAME", "") if same_provider else ""
    fields = [SetupField("MODEL_NAME", "Model identifier", model or profile.get("model_name", ""))]
    if endpoint:
        current = os.getenv(endpoint, "") if same_provider else ""
        fields.append(
            SetupField(endpoint, "Endpoint URL", current or default_endpoint or profile.get("url", ""))
        )
    if credential:
        current_key = os.getenv(credential, "")
        if provider == "watsonx":
            current_key = current_key or os.getenv("WATSONX_APIKEY", "")
        fields.append(SetupField(credential, "API key", current_key, secret=True))
    if provider == "watsonx":
        scope = "space" if os.getenv("WATSONX_SPACE_ID") else "project"
        fields.extend(
            [
                SetupField("scope", "watsonx scope", scope, hint="project or space"),
                SetupField("scope_id", "Project or space ID", os.getenv(f"WATSONX_{scope.upper()}_ID", "")),
            ]
        )
    return fields


def connection_values(index: int, fields: list[SetupField]) -> dict[str, str]:
    _, provider, credential, _, _ = setup_cli.PROVIDERS[index]
    values = {item.key: item.value.strip() for item in fields}
    values.update(AGENT_SETTING_CONFIG=f"settings.{provider}.toml", DYNACONF_SECRETS__FORCE_ENV="true")
    for item in fields:
        error = item.error(values[item.key])
        if error:
            raise ValueError(f"{item.label}: {error}")
    if not credential:
        values["OPENAI_API_KEY"] = "ollama"  # pragma: allowlist secret (Local server placeholder.)
    if provider in ("openai", "ollama"):
        values["LLM_AUTH_HEADER"] = ""
    if provider == "watsonx":
        scope = values.pop("scope")
        values[f"WATSONX_{scope.upper()}_ID"] = values.pop("scope_id")
        values["WATSONX_PROJECT_ID" if scope == "space" else "WATSONX_SPACE_ID"] = ""
        values["WATSONX_APIKEY"] = values[credential]
    return values


class TerminalUI:
    """Fixed terminal dialogs; drafts and secret inputs never enter command history."""

    def _application(self, dialog, focus, *, back=False, on_back=None):
        from prompt_toolkit import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.widgets import Box
        from prompt_toolkit.styles import Style

        keys = KeyBindings()

        @keys.add("c-c", is_global=True)
        def cancel(event):
            event.app.exit(result=None)

        @keys.add("escape", is_global=True)
        def go_back(event):
            if on_back:
                on_back()
            event.app.exit(result=BACK if back else None)

        @keys.add("tab", is_global=True)
        def next_control(event):
            event.app.layout.focus_next()

        @keys.add("s-tab", is_global=True)
        def previous_control(event):
            event.app.layout.focus_previous()

        return Application(
            layout=Layout(Box(dialog, style="class:dialog"), focused_element=focus),
            key_bindings=keys,
            # The alternate screen anchors redraws and restores prior terminal output on exit.
            full_screen=True,
            erase_when_done=True,
            mouse_support=True,
            style=Style.from_dict(
                {
                    "dialog": "bg:#161616 #e0e0e0",
                    "dialog.body": "bg:#161616 #e0e0e0",
                    "frame.border": "#4589ff",
                    "frame.label": "#4589ff bold",
                    "button.focused": "bg:#0f62fe #ffffff",
                    "button": "bg:#262626 #e0e0e0",
                    "radio-selected": "#4589ff bold",
                    "radio-checked": "#4589ff bold",
                    "help": "#8d8d8d",
                    "text-area": "bg:#262626 #ffffff",
                    "error": "#ff8389",
                }
            ),
        )

    def choose(self, title, text, choices, default=None, *, back=False):
        from prompt_toolkit.application import get_app
        from prompt_toolkit.layout import HSplit
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.widgets import Button, Dialog, Label, RadioList

        menu = RadioList(
            choices,
            default=default,
            select_on_focus=True,
            open_character=" ",
            select_character="›",
            close_character=" ",
            show_scrollbar=False,
        )
        buttons = [Button("Continue", handler=lambda: get_app().exit(result=menu.current_value))]
        if back:
            buttons.append(Button("Back", handler=lambda: get_app().exit(result=BACK)))
        buttons.append(Button("Cancel", handler=lambda: get_app().exit(result=None)))
        dialog = Dialog(
            title=f"CUGA · {title}",
            body=HSplit(
                [
                    Label(text),
                    menu,
                    Label(
                        "↑↓ Select · Enter Continue · Tab Buttons · Esc Back · Ctrl+C Cancel",
                        style="class:help",
                    ),
                ],
                padding=1,
            ),
            buttons=buttons,
            width=Dimension(preferred=88, max=88),
        )
        app = self._application(dialog, menu, back=back)

        @menu.control.key_bindings.add("enter")
        def accept(event):
            event.app.exit(result=menu.current_value)

        return app.run()

    def edit(self, item: SetupField, step: int, count: int):
        from prompt_toolkit.application import get_app
        from prompt_toolkit.layout import HSplit
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.widgets import Button, Dialog, Label, TextArea

        error = Label("", style="class:error")
        entry = TextArea(text=item.value, multiline=False, password=item.secret)

        def accept():
            value = entry.text.strip()
            error.text = item.error(value)
            if error.text:
                get_app().layout.focus(entry)
            else:
                get_app().exit(result=value)
            return True  # Keep invalid input available for correction.

        entry.buffer.accept_handler = lambda buffer: accept()

        def retain_draft():
            item.value = entry.text.strip()

        def go_back():
            retain_draft()
            get_app().exit(result=BACK)

        hint = "Hidden input; keep or replace the saved key." if item.secret else item.hint
        dialog = Dialog(
            title=f"CUGA · Connection details {step}/{count}",
            body=HSplit(
                [
                    Label(item.label),
                    Label(hint, style="class:help"),
                    entry,
                    error,
                    Label("Enter Continue · Esc Back · Ctrl+C Cancel", style="class:help"),
                ],
                padding=1,
            ),
            buttons=[
                Button("Continue", handler=accept),
                Button("Back", handler=go_back),
                Button("Cancel", handler=lambda: get_app().exit(result=None)),
            ],
            width=Dimension(preferred=88, max=88),
        )
        return self._application(dialog, entry, back=True, on_back=retain_draft).run()

    def test(self, values):
        from rich.console import Console

        with Console().status("Testing the connection with a short inference request…", spinner="dots"):
            return setup_cli.test_connection(values)


def run_setup(path, ui=None):
    """Return verified values, or None on cancellation; never persist partial drafts."""
    ui = ui or TerminalUI()
    selected = current_provider()
    drafts = {}
    while True:
        selected = ui.choose(
            "Inference provider",
            f"Configuration file: {path}\nChoose where CUGA runs inference.",
            [(index, provider[0]) for index, provider in enumerate(setup_cli.PROVIDERS)],
            default=selected,
        )
        if selected is None:
            return None
        if selected not in drafts:
            drafts[selected] = provider_fields(selected)
        fields = drafts[selected]
        step = 0
        while 0 <= step < len(fields):
            response = ui.edit(fields[step], step + 1, len(fields))
            if response is None:
                return None
            if response is BACK:
                step -= 1
            else:
                fields[step].value = response
                step += 1
        if step < 0:
            continue
        status = "Review your connection. Testing sends a short inference request; successful tests save it."
        while True:
            action = ui.choose(
                "Review & test",
                f"Provider: {setup_cli.PROVIDERS[selected][0]}\n{status}",
                [("test", "Test connection & save")]
                + [(index, f"Edit {item.label}: {item.summary()}") for index, item in enumerate(fields)]
                + [("provider", "Change provider")],
                default="test",
                back=True,
            )
            if action is None:
                return None
            if action == "provider":
                break
            if action == "test":
                try:
                    values = connection_values(selected, fields)
                except ValueError as error:
                    status = f"{error}\nEdit the field before testing. Your answers are retained."
                    continue
                result = ui.test(values)
                if result:
                    return values
                message = getattr(result, "message", "Check your model, endpoint and credentials.")
                status = (
                    f"Connection test failed. {message}\nYour answers are retained. Edit a field or retry."
                )
            else:
                index = len(fields) - 1 if action is BACK else action
                response = ui.edit(fields[index], index + 1, len(fields))
                if response is None:
                    return None
                if response is not BACK:
                    fields[index].value = response
                status = "Connection updated. Test again to save."
