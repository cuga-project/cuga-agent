"""The channel interface a turn pipeline talks to.

Channel-SPECIFIC message handling (Meta's media ids, the 24-hour window, which audio codecs a phone
will play) lives in the channel module — ``whatsapp_direct.WhatsAppIO`` — behind this Protocol. The
engine and the steps only ever call ``fetch`` and ``send``, which is what keeps them channel-free.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .message import Part


@runtime_checkable
class ChannelIO(Protocol):
    name: str
    # MIME types the channel can deliver as a playable voice message, best first. A TTS step passes
    # this to the provider; anything else gets transcoded (or dropped) at send time.
    accepted_audio: tuple[str, ...]

    async def fetch(self, part: Part) -> bytes:
        """The bytes behind a media Part that arrived as a handle (``part.ref``)."""
        ...

    async def send(self, recipient: str, parts: Sequence[Part]) -> list[dict]:
        """Deliver ``parts`` in order; one result dict per part (``{"ok": bool, …}``)."""
        ...
