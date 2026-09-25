"""Turn handling — what the eventing layer does with one inbound message.

    message.py   the channel-neutral model (Part, InboundMessage, Turn)
    channel.py   ChannelIO — what a channel must offer (fetch media, send parts)
    services.py  the service calls: speech-to-text, text-to-speech, search
    engine.py    handle() — the whole flow, top to bottom

Speech and retrieval are SERVICE CALLS behind URLs, not a configurable pipeline: the model, the
language handling and the text cleanup belong to the service, so this layer stays a thin orchestrator.
"""

from .engine import handle
from .message import AUDIO, DOCUMENT, IMAGE, TEXT, InboundMessage, Part, Turn
from .services import ServiceError, knowledge_url, speech_url

__all__ = [
    "AUDIO",
    "DOCUMENT",
    "IMAGE",
    "ServiceError",
    "TEXT",
    "InboundMessage",
    "Part",
    "Turn",
    "handle",
    "knowledge_url",
    "speech_url",
]
