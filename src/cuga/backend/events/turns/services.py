"""The two service calls a voice turn needs — and nothing else.

A voice note becomes text, and an answer becomes audio, by POSTing to a service. WHICH model, which
language, how the text is cleaned before it is spoken: all of that is the service's business, not
this layer's. The events layer holds no model runtime, no provider registry and no per-step options;
it holds two URLs.

    EVENTS_SPEECH_URL      speech in and out   POST /v1/speech-to-text · POST /v1/text-to-speech
    EVENTS_KNOWLEDGE_URL   retrieval           POST /v1/search
    EVENTS_SPEECH_TOKEN / EVENTS_KNOWLEDGE_TOKEN   optional bearer tokens

An unset URL means that stage does not happen: no speech service → voice notes are answered in text,
typed chat is unaffected. Swapping Whisper for Sarvam, or adding a language, is a change inside the
service (or a different URL) — never a change here.

``events/examples/speech_service`` is a reference implementation of the contract; any service that
answers the same three endpoints will do.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("events.turns")

TIMEOUT = float(os.environ.get("EVENTS_SERVICE_TIMEOUT", "300"))


class ServiceError(RuntimeError):
    """A service was unreachable or unhappy. The message is safe to log."""


def _url(name: str) -> str:
    return (os.environ.get(name, "").split(" #", 1)[0].strip().rstrip("/")) or ""


def speech_url() -> str:
    return _url("EVENTS_SPEECH_URL")


def knowledge_url() -> str:
    return _url("EVENTS_KNOWLEDGE_URL")


def _headers(token_env: str) -> dict:
    tok = (os.environ.get(token_env, "") or "").strip()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


async def _post(url: str, path: str, token_env: str, **kw) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{url}{path}", headers=_headers(token_env), **kw)
    except httpx.HTTPError as e:
        raise ServiceError(f"{url}{path}: {type(e).__name__}") from e
    if r.status_code != 200:
        raise ServiceError(f"{url}{path}: HTTP {r.status_code} {r.text[:160]}")
    return r


async def speech_to_text(audio: bytes, mime: str = "") -> str:
    """Audio in, text out. What model hears it, and in which language, is the service's decision."""
    url = speech_url()
    if not url:
        raise ServiceError("no EVENTS_SPEECH_URL configured")
    r = await _post(
        url,
        "/v1/speech-to-text",
        "EVENTS_SPEECH_TOKEN",
        files={"file": ("audio", audio, mime or "application/octet-stream")},
    )
    return str((r.json() or {}).get("text") or "").strip()


async def text_to_speech(text: str, accept: tuple[str, ...] = ()) -> tuple[bytes, str]:
    """Text in, audio out. ``accept`` tells the service what the destination can play; picking a
    voice, a language and what to strip before speaking is the service's job."""
    url = speech_url()
    if not url:
        raise ServiceError("no EVENTS_SPEECH_URL configured")
    r = await _post(
        url, "/v1/text-to-speech", "EVENTS_SPEECH_TOKEN", json={"text": text, "accept": list(accept)}
    )
    if not r.content:
        raise ServiceError("text-to-speech returned no audio")
    return r.content, r.headers.get("content-type", "application/octet-stream")


async def search(query: str) -> str:
    """The passages to put in front of the agent, already formatted by the service ("[Page 29]: …")."""
    url = knowledge_url()
    if not url:
        raise ServiceError("no EVENTS_KNOWLEDGE_URL configured")
    r = await _post(url, "/v1/search", "EVENTS_KNOWLEDGE_TOKEN", json={"query": query})
    return str((r.json() or {}).get("context") or "")
