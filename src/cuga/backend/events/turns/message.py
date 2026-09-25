"""The channel-neutral message model for one conversational TURN.

A channel adapter (``whatsapp_direct``, later ``telegram_direct`` …) turns its wire payload into an
``InboundMessage`` made of ``Part``s. The turn pipeline only ever sees Parts. The adapter turns the
outbound Parts back into its own API calls. Nothing between those two edges knows which channel it
serves — that is what lets a voice pipeline written for WhatsApp run unchanged on Telegram.

A Part is deliberately ONE flat dataclass rather than a class per kind. Steps are wired from YAML,
and a step written for audio should be able to ignore a document Part without an isinstance ladder;
new kinds (image, location, document) need no new types, only a new ``kind`` string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TEXT = "text"
AUDIO = "audio"
IMAGE = "image"
DOCUMENT = "document"


@dataclass
class Part:
    """One piece of a message. Media may arrive as bytes (``data``) or as a channel handle (``ref``,
    e.g. a WhatsApp media id) that the channel fetches only when a step actually needs the bytes."""

    kind: str
    text: str = ""
    data: bytes = b""
    mime: str = ""
    ref: str = ""
    language: str = ""  # BCP-47 (``te-IN``) when known
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of_text(cls, text: str, language: str = "") -> Part:
        return cls(kind=TEXT, text=text, language=language)

    @classmethod
    def of_audio(
        cls, *, data: bytes = b"", mime: str = "", ref: str = "", language: str = "", **meta
    ) -> Part:
        return cls(kind=AUDIO, data=data, mime=mime, ref=ref, language=language, meta=dict(meta))

    def __repr__(self) -> str:  # never dump audio bytes into a log line
        body = f"text={self.text[:40]!r}" if self.kind == TEXT else f"bytes={len(self.data)} ref={self.ref!r}"
        return f"Part({self.kind}, {body}, mime={self.mime!r}, language={self.language!r})"


@dataclass
class InboundMessage:
    """What a human sent, normalised. ``sender`` is the channel's native id for that human — the
    delivery address AND the per-user identity on 1:1 channels like WhatsApp."""

    channel: str
    sender: str
    parts: list[Part]
    message_id: str = ""
    sender_name: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def modality(self) -> str:
        """The input kind routes match on. Audio wins over text: a voice note with a caption is a
        voice note."""
        kinds = [p.kind for p in self.parts]
        if AUDIO in kinds:
            return AUDIO
        if TEXT in kinds:
            return TEXT
        return kinds[0] if kinds else ""

    def text(self) -> str:
        return "\n".join(p.text for p in self.parts if p.kind == TEXT and p.text).strip()


@dataclass
class Turn:
    """One exchange. ``query`` is what the agent is asked (the transcript, plus any passages);
    ``answer`` is what it said; ``stateless`` asks CUGA to ignore thread history, which a turn that
    carries retrieved passages must, or yesterday's context rides along with today's question."""

    inbound: InboundMessage
    query: str = ""
    answer: str = ""
    stateless: bool = False
    log: list[dict[str, Any]] = field(default_factory=list)

    def note(self, what: str, **fields) -> None:
        self.log.append({"step": what, **fields})
