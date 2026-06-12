import pytest

from cuga.backend.browser_env.tools import playwright_commands


class FakeKeyboard:
    def __init__(self):
        self.presses = []

    async def press(self, key):
        self.presses.append(key)


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None


class FakeInput:
    def __init__(self):
        self.filled = []

    async def fill(self, value, **_kwargs):
        self.filled.append(value)


class FakeSearchInput(FakeInput):
    async def evaluate(self, _script):
        return True


class FakeClickable:
    def __init__(self):
        self.click_kwargs = None

    async def click(self, **kwargs):
        self.click_kwargs = kwargs


class HiddenAnonymousElement:
    async def is_visible(self, **_kwargs):
        return False

    async def evaluate(self, _script):
        return {
            "tag": "button",
            "id": "",
            "name": "",
            "type": "",
            "placeholder": "",
            "ariaLabel": "",
            "value": "",
            "title": "",
            "text": "",
            "label": "",
        }


class CounterpartLookupPage:
    def __init__(self):
        self.looked_up = False

    async def evaluate_handle(self, *_args, **_kwargs):
        self.looked_up = True
        raise AssertionError("anonymous hidden elements should not pick a visible counterpart")


@pytest.mark.asyncio
async def test_type_impl_respects_press_enter_false(monkeypatch):
    elem = FakeInput()
    page = FakePage()

    async def get_elem_by_bid_async(*_args, **_kwargs):
        return elem

    async def resolve_visible_counterpart(_page, candidate):
        return candidate

    async def add_animation(*_args, **_kwargs):
        return None

    async def check_for_alert(_page):
        return None

    monkeypatch.setattr(playwright_commands, "get_elem_by_bid_async", get_elem_by_bid_async)
    monkeypatch.setattr(playwright_commands, "_resolve_visible_counterpart", resolve_visible_counterpart)
    monkeypatch.setattr(playwright_commands, "add_animation", add_animation)
    monkeypatch.setattr(playwright_commands, "check_for_alert", check_for_alert)
    monkeypatch.setattr(playwright_commands, "schedule_clear_animations", lambda _page: None)

    await playwright_commands.type_impl(
        bid="1",
        value="Carnegie Mellon",
        press_enter=False,
        config={"configurable": {"page": page, "demo_mode": "off"}},
    )

    assert elem.filled == ["Carnegie Mellon"]
    assert page.keyboard.presses == []


@pytest.mark.asyncio
async def test_type_impl_submits_search_input(monkeypatch):
    elem = FakeSearchInput()
    page = FakePage()

    async def get_elem_by_bid_async(*_args, **_kwargs):
        return elem

    async def resolve_visible_counterpart(_page, candidate):
        return candidate

    async def add_animation(*_args, **_kwargs):
        return None

    async def check_for_alert(_page):
        return None

    monkeypatch.setattr(playwright_commands, "get_elem_by_bid_async", get_elem_by_bid_async)
    monkeypatch.setattr(playwright_commands, "_resolve_visible_counterpart", resolve_visible_counterpart)
    monkeypatch.setattr(playwright_commands, "add_animation", add_animation)
    monkeypatch.setattr(playwright_commands, "check_for_alert", check_for_alert)
    monkeypatch.setattr(playwright_commands, "schedule_clear_animations", lambda _page: None)

    await playwright_commands.type_impl(
        bid="1",
        value="Carnegie Mellon",
        press_enter=False,
        config={"configurable": {"page": page, "demo_mode": "off"}},
    )

    assert elem.filled == ["Carnegie Mellon"]
    assert page.keyboard.presses == ["Enter"]


@pytest.mark.asyncio
async def test_click_impl_passes_requested_button(monkeypatch):
    elem = FakeClickable()
    page = FakePage()

    async def get_elem_by_bid_async(*_args, **_kwargs):
        return elem

    async def resolve_visible_counterpart(_page, candidate):
        return candidate

    async def add_animation(*_args, **_kwargs):
        return None

    async def check_for_alert(_page):
        return None

    monkeypatch.setattr(playwright_commands, "get_elem_by_bid_async", get_elem_by_bid_async)
    monkeypatch.setattr(playwright_commands, "_resolve_visible_counterpart", resolve_visible_counterpart)
    monkeypatch.setattr(playwright_commands, "add_animation", add_animation)
    monkeypatch.setattr(playwright_commands, "check_for_alert", check_for_alert)
    monkeypatch.setattr(playwright_commands, "schedule_clear_animations", lambda _page: None)

    await playwright_commands.click_impl(
        bid="1",
        button="right",
        modifiers=["Shift"],
        config={"configurable": {"page": page}},
    )

    assert elem.click_kwargs == {
        "button": "right",
        "modifiers": ["Shift"],
        "timeout": 5000,
        "force": True,
    }


@pytest.mark.asyncio
async def test_visible_counterpart_requires_stable_identifier():
    page = CounterpartLookupPage()
    elem = HiddenAnonymousElement()

    result = await playwright_commands._resolve_visible_counterpart(page, elem)

    assert result is elem
    assert page.looked_up is False
