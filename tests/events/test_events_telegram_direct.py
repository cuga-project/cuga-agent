"""Offline unit tests for telegram_direct — the pure message-gating logic of the AP-free Telegram
backend (long-poll). No network: we only exercise should_process / mention_gate / chat_mode, the
functions that decide whether a Telegram update becomes a concierge turn."""
import os
import sys

_EVENTS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "src", "cuga", "backend", "events"))
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import telegram_direct as T          # noqa: E402


def _msg(text="hi", chat_type="private", is_bot=False, chat_id=42):
    return {"message_id": 1, "text": text, "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": 7, "is_bot": is_bot, "username": "alice"}}


def test_should_process_accepts_human_text():
    assert T.should_process(_msg()) is True


def test_should_process_rejects_bot_empty_and_no_chat():
    assert T.should_process(_msg(is_bot=True)) is False
    assert T.should_process(_msg(text="")) is False
    assert T.should_process({"text": "hi", "from": {"id": 7}}) is False   # no chat id


def test_chat_mode_default_all(monkeypatch):
    monkeypatch.delenv("EVENTS_TELEGRAM_CHAT", raising=False)
    assert T.chat_mode() == "all"


def test_mention_gate_private_always_passes(monkeypatch):
    monkeypatch.setenv("EVENTS_TELEGRAM_CHAT", "mention")
    ok, text = T.mention_gate(_msg(text="hello", chat_type="private"))
    assert ok is True and text == "hello"


def test_mention_gate_group_requires_mention(monkeypatch):
    monkeypatch.setenv("EVENTS_TELEGRAM_CHAT", "mention")
    monkeypatch.setenv("EVENTS_TELEGRAM_BOT_USERNAME", "mybot")
    # a bare group message is gated OUT of chat…
    ok, _ = T.mention_gate(_msg(text="random chatter", chat_type="group"))
    assert ok is False
    # …but an @mention passes and the mention is stripped from the lead
    ok2, text2 = T.mention_gate(_msg(text="@mybot what's up", chat_type="group"))
    assert ok2 is True and "@mybot" not in text2


def test_mention_gate_all_mode_passes_group(monkeypatch):
    monkeypatch.setenv("EVENTS_TELEGRAM_CHAT", "all")
    ok, _ = T.mention_gate(_msg(text="hi all", chat_type="group"))
    assert ok is True


def test_mention_gate_group_fails_open_without_username(monkeypatch):
    monkeypatch.setenv("EVENTS_TELEGRAM_CHAT", "mention")
    monkeypatch.delenv("EVENTS_TELEGRAM_BOT_USERNAME", raising=False)
    ok, _ = T.mention_gate(_msg(text="anything", chat_type="group"))
    assert ok is True          # can't detect a mention → never silently drop
