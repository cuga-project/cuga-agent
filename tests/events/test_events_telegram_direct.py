"""Offline unit tests for telegram_direct — the pure message-gating logic of the AP-free Telegram
backend (long-poll). No network: we only exercise should_process / mention_gate / chat_mode, the
functions that decide whether a Telegram update becomes a concierge turn."""

import os
import sys

_EVENTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events")
)
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import telegram_direct as T  # noqa: E402


def _msg(text="hi", chat_type="private", is_bot=False, chat_id=42):
    return {
        "message_id": 1,
        "text": text,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": 7, "is_bot": is_bot, "username": "alice"},
    }


def test_should_process_accepts_human_text():
    assert T.should_process(_msg()) is True


def test_should_process_rejects_bot_empty_and_no_chat():
    assert T.should_process(_msg(is_bot=True)) is False
    assert T.should_process(_msg(text="")) is False
    assert T.should_process({"text": "hi", "from": {"id": 7}}) is False  # no chat id


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
    assert ok is True  # can't detect a mention → never silently drop


# ── non-200 back-off: the long-poll must never spin ──────────────────────────
class _Resp:
    """Minimal stand-in for an httpx response — backoff_seconds is duck-typed on purpose."""

    def __init__(self, status=429, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def test_backoff_honours_retry_after_from_the_body():
    """Telegram states the wait authoritatively in the 429 body. Retrying sooner earns another 429."""
    r = _Resp(body={"ok": False, "error_code": 429, "parameters": {"retry_after": 30}})
    assert T.backoff_seconds(r) == 30.0


def test_backoff_falls_back_to_the_retry_after_header():
    """A proxy in front of the API sets the header instead of the body."""
    assert T.backoff_seconds(_Resp(body={"ok": False}, headers={"Retry-After": "12"})) == 12.0


def test_backoff_defaults_when_the_body_is_not_json():
    """401/409 return an HTML error page. Default rather than crash — and never zero."""
    assert T.backoff_seconds(_Resp(status=401, text="<html>nope</html>")) == T.BACKOFF_DEFAULT


def test_backoff_defaults_on_a_nonsense_value():
    for bad in (0, -1, "soon", None):
        r = _Resp(body={"parameters": {"retry_after": bad}})
        assert T.backoff_seconds(r) == T.BACKOFF_DEFAULT, bad


def test_backoff_is_capped():
    """A hostile or fat-fingered retry_after must not wedge the poller for hours."""
    r = _Resp(body={"parameters": {"retry_after": 99999}})
    assert T.backoff_seconds(r) == T.BACKOFF_MAX


def test_non_200_sleeps_instead_of_hot_looping(monkeypatch):
    """The regression itself: a 429 used to complete the loop with updates=[] and NO sleep, so the
    poller re-requested at full speed — pinning a CPU core and deepening the rate limit.

    The loop is driven with a fake transport that always 429s; the assertion is that it slept the
    body's retry_after on every pass rather than spinning.
    """
    import asyncio

    slept, polls = [], []
    stop = asyncio.Event()

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if url.endswith("/deleteWebhook") or (params or {}).get("timeout") == 0:
                return _Resp(status=200, body={"ok": True, "result": []})
            # The POLL itself ends the loop after a few passes, so this test terminates whether or
            # not the fix is present. Bounding on sleeps instead would hang forever on the buggy
            # version — which is precisely the defect, and a hang is a useless failure report.
            polls.append(1)
            if len(polls) >= 4:
                stop.set()
            return _Resp(status=429, body={"parameters": {"retry_after": 7}}, text="Too Many Requests")

    async def _sleep(sec):
        slept.append(sec)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(T.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(T.asyncio, "sleep", _sleep)
    monkeypatch.setattr(T, "_get_me", lambda: _done({"username": "bot"}))

    asyncio.run(T.run_poller(lambda msg: _done(None), stop=stop))
    assert slept and all(s == 7.0 for s in slept), slept


def _done(value):
    """A coroutine that immediately returns `value` — for monkeypatching async seams."""

    async def _c():
        return value

    return _c()
