"""Turn handling: a voice note in, a spoken answer out — via two service calls and nothing else.

What these pin down:
  * the flow is fixed and readable: audio → text → (search) → agent → text reply → voice reply;
  * an unset service URL means that stage is skipped, never that the turn breaks;
  * the layer stays ignorant: it sends audio and gets text back, and never asks which model, which
    language, or what to strip before speaking;
  * failures degrade in the right direction — the human is told when we cannot hear them, and the
    text answer still goes out when the voice reply fails.
"""

import httpx
import pytest

from cuga.backend.events.turns import AUDIO, TEXT, InboundMessage, Part, engine, handle, services


class FakeChannel:
    name = "fake"
    accepted_audio = ("audio/ogg; codecs=opus", "audio/mpeg")

    def __init__(self):
        self.sent: list[list[Part]] = []
        self.fetched: list[str] = []

    async def fetch(self, part):
        self.fetched.append(part.ref)
        part.data = b"voice-bytes"
        return part.data

    async def send(self, recipient, parts):
        self.sent.append(list(parts))
        return [{"ok": True} for _ in parts]


def _voice(ref="MEDIA1"):
    return InboundMessage(
        channel="whatsapp",
        sender="15551234567",
        parts=[Part.of_audio(ref=ref, mime="audio/ogg; codecs=opus")],
    )


def _text(t="what is good seed?"):
    return InboundMessage(channel="whatsapp", sender="15551234567", parts=[Part.of_text(t)])


def _asker(answer="**Good seed** is genetically pure (p. 29)."):
    asked = []

    async def ask(turn):
        asked.append(turn.query)
        return answer

    return ask, asked


@pytest.fixture
def speech(monkeypatch):
    """A speech service that is configured and records what it was given."""
    calls = {"stt": [], "tts": []}
    monkeypatch.setenv("EVENTS_SPEECH_URL", "http://speech:8300")

    async def stt(audio, mime=""):
        calls["stt"].append({"bytes": audio, "mime": mime})
        return "मंची विथनम"

    async def tts(text, accept=()):
        calls["tts"].append({"text": text, "accept": tuple(accept)})
        return b"OggS-reply", "audio/ogg; codecs=opus"

    monkeypatch.setattr(services, "speech_to_text", stt)
    monkeypatch.setattr(services, "text_to_speech", tts)
    return calls


@pytest.fixture(autouse=True)
def _no_services(monkeypatch):
    monkeypatch.delenv("EVENTS_SPEECH_URL", raising=False)
    monkeypatch.delenv("EVENTS_KNOWLEDGE_URL", raising=False)


# ── the flow ────────────────────────────────────────────────────────────────────────────────────
async def test_typed_text_is_asked_and_answered_with_no_services_at_all():
    ch, (ask, asked) = FakeChannel(), _asker("an answer")
    turn = await handle(_text("hi"), channel=ch, ask=ask)
    assert asked == ["hi"]
    assert [[p.kind for p in b] for b in ch.sent] == [[TEXT]]
    assert turn.answer == "an answer" and turn.stateless is False


async def test_a_voice_note_with_no_speech_service_is_left_alone():
    """Nothing to hear it with. Silence beats a wrong reply, and the trace says why."""
    ch, (ask, asked) = FakeChannel(), _asker()
    await handle(_voice(), channel=ch, ask=ask)
    assert asked == [] and ch.sent == [] and ch.fetched == []


async def test_voice_in_voice_out_is_two_service_calls(speech):
    ch, (ask, asked) = FakeChannel(), _asker()
    await handle(_voice(), channel=ch, ask=ask)
    assert ch.fetched == ["MEDIA1"], "media is fetched only because something needs the bytes"
    assert speech["stt"] == [{"bytes": b"voice-bytes", "mime": "audio/ogg; codecs=opus"}]
    assert asked == ["मंची विथनम"], "the agent gets the transcript, nothing added"
    assert [[p.kind for p in b] for b in ch.sent] == [[TEXT], [AUDIO]], "text first, then the voice reply"
    assert speech["tts"][0]["text"] == "**Good seed** is genetically pure (p. 29)."
    assert speech["tts"][0]["accept"] == ch.accepted_audio, "the service is told what the channel can play"


async def test_the_answer_goes_to_the_service_as_written(speech):
    """No stripping, no language tagging, no truncation here — that is the service's job."""
    ch, (ask, _) = FakeChannel(), _asker("## Heading\n- **bold** (p. 29)\nhttps://x.y")
    await handle(_voice(), channel=ch, ask=ask)
    assert speech["tts"][0]["text"] == "## Heading\n- **bold** (p. 29)\nhttps://x.y"


async def test_typed_text_never_triggers_speech_even_when_the_service_is_there(speech):
    ch, (ask, _) = FakeChannel(), _asker()
    await handle(_text(), channel=ch, ask=ask)
    assert speech["stt"] == [] and speech["tts"] == []
    assert [[p.kind for p in b] for b in ch.sent] == [[TEXT]]


async def test_when_we_cannot_hear_it_we_say_so_and_do_not_ask_the_agent(monkeypatch):
    monkeypatch.setenv("EVENTS_SPEECH_URL", "http://speech:8300")

    async def boom(audio, mime=""):
        raise services.ServiceError("speech service down")

    monkeypatch.setattr(services, "speech_to_text", boom)
    ch, (ask, asked) = FakeChannel(), _asker()
    await handle(_voice(), channel=ch, ask=ask)
    assert asked == []
    assert ch.sent[0][0].text == engine.COULD_NOT_HEAR


async def test_an_empty_transcript_is_treated_the_same_way(monkeypatch):
    monkeypatch.setenv("EVENTS_SPEECH_URL", "http://speech:8300")
    monkeypatch.setattr(services, "speech_to_text", lambda *a, **k: _empty())
    ch, (ask, asked) = FakeChannel(), _asker()
    await handle(_voice(), channel=ch, ask=ask)
    assert asked == [] and ch.sent[0][0].text == engine.COULD_NOT_HEAR


async def _empty():
    return "   "


async def test_a_failed_voice_reply_still_leaves_the_text_answer(monkeypatch, speech):
    async def boom(text, accept=()):
        raise services.ServiceError("tts down")

    monkeypatch.setattr(services, "text_to_speech", boom)
    ch, (ask, _) = FakeChannel(), _asker()
    await handle(_voice(), channel=ch, ask=ask)
    assert [[p.kind for p in b] for b in ch.sent] == [[TEXT]]


# ── the knowledge base ──────────────────────────────────────────────────────────────────────────
async def test_the_passages_are_put_in_front_of_the_agent_and_the_turn_goes_stateless(monkeypatch):
    monkeypatch.setenv("EVENTS_KNOWLEDGE_URL", "http://kb:8770")

    async def search(q):
        return "[Page 29]: Characteristics of good seed"

    monkeypatch.setattr(services, "search", search)
    ch, (ask, asked) = FakeChannel(), _asker()
    turn = await handle(_text("what is good seed?"), channel=ch, ask=ask)
    assert asked == ["Context:\n[Page 29]: Characteristics of good seed\n\nQuestion: what is good seed?"]
    assert turn.stateless is True, "passages ride in the query; replaying history would re-send stale ones"


async def test_a_search_that_fails_still_asks_the_question(monkeypatch):
    monkeypatch.setenv("EVENTS_KNOWLEDGE_URL", "http://kb:8770")

    async def boom(q):
        raise services.ServiceError("kb down")

    monkeypatch.setattr(services, "search", boom)
    ch, (ask, asked) = FakeChannel(), _asker()
    turn = await handle(_text("q"), channel=ch, ask=ask)
    assert asked == ["q"] and turn.stateless is False


# ── the service client ──────────────────────────────────────────────────────────────────────────
def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    class C(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", C)


async def test_the_three_calls_the_services_module_makes(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.url.path, req.headers.get("authorization")))
        if req.url.path == "/v1/speech-to-text":
            return httpx.Response(200, json={"text": "heard"})
        if req.url.path == "/v1/text-to-speech":
            return httpx.Response(200, content=b"OggS", headers={"content-type": "audio/ogg; codecs=opus"})
        return httpx.Response(200, json={"context": "[Page 1]: x"})

    _mock_httpx(monkeypatch, handler)
    monkeypatch.setenv("EVENTS_SPEECH_URL", "http://speech:8300/")  # trailing slash tolerated
    monkeypatch.setenv("EVENTS_KNOWLEDGE_URL", "http://kb:8770")
    monkeypatch.setenv("EVENTS_SPEECH_TOKEN", "tok")
    assert await services.speech_to_text(b"aud", "audio/ogg") == "heard"
    assert (await services.text_to_speech("hi", ("audio/ogg",)))[0] == b"OggS"
    assert await services.search("q") == "[Page 1]: x"
    assert [s[0] for s in seen] == ["/v1/speech-to-text", "/v1/text-to-speech", "/v1/search"]
    assert seen[0][1] == "Bearer tok" and seen[2][1] is None, "each service has its own token"


async def test_an_unhappy_service_raises_something_readable(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(503, text="warming up"))
    monkeypatch.setenv("EVENTS_SPEECH_URL", "http://speech:8300")
    with pytest.raises(services.ServiceError, match="HTTP 503"):
        await services.speech_to_text(b"a")


def test_an_unset_url_is_simply_off(monkeypatch):
    monkeypatch.delenv("EVENTS_SPEECH_URL", raising=False)
    monkeypatch.setenv("EVENTS_KNOWLEDGE_URL", "http://kb:8770 # with a comment")
    assert services.speech_url() == ""
    assert services.knowledge_url() == "http://kb:8770"
