"""One turn, start to finish. Read it top to bottom — that is the whole flow.

    voice note ─► [speech service] ─► text ─┐
    typed text ─────────────────────────────┤─► [knowledge service] ─► CUGA ─► text reply ─┐
                                                                                            └─► [speech service] ─► voice reply

Three service calls at most, each optional: an unset URL means that stage does not happen. There are
no steps to order, no providers to choose and no per-channel configuration, because none is needed —
a voice note gets speech because it IS a voice note, and a channel that never sends audio never
triggers it.

What is deliberately NOT here: which model hears the audio, which voice answers, how the text is
cleaned before it is spoken, which index is searched. All of that lives behind the service URLs,
where it can change without this file changing.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from ..trace import Trace, new_trace_id
from . import services
from .message import AUDIO, InboundMessage, Part, Turn

AskFn = Callable[[Turn], Awaitable[str]]

COULD_NOT_HEAR = "Sorry, I couldn't make out that voice note. Please try again, or type your question."
PROMPT = "Context:\n{context}\n\nQuestion: {query}"


async def handle(inbound: InboundMessage, *, channel, ask: AskFn) -> Turn:
    turn = Turn(inbound=inbound, query=inbound.text())
    tr = Trace(new_trace_id())
    voice_in = [p for p in inbound.parts if p.kind == AUDIO]

    # 1 · audio → text
    if voice_in:
        if not services.speech_url():
            tr("turn.unhandled", channel=inbound.channel, reason="no EVENTS_SPEECH_URL")
            return turn
        try:
            heard = []
            for part in voice_in:
                data = part.data or await channel.fetch(part)
                heard.append(await services.speech_to_text(data, part.mime))
            transcript = " ".join(t for t in heard if t).strip()
        except Exception as e:  # noqa: BLE001 — tell the human; never ask the agent an empty question
            tr.error("turn.speech_to_text", err=type(e).__name__, detail=str(e)[:200])
            await channel.send(inbound.sender, [Part.of_text(COULD_NOT_HEAR)])
            return turn
        if not transcript:
            await channel.send(inbound.sender, [Part.of_text(COULD_NOT_HEAR)])
            return turn
        turn.query = f"{turn.query}\n{transcript}".strip() if turn.query else transcript
        turn.note("speech_to_text", chars=len(transcript))

    if not turn.query.strip():
        return turn

    # 2 · the knowledge base, if there is one
    if services.knowledge_url():
        try:
            context = await services.search(turn.query)
        except Exception as e:  # noqa: BLE001 — a search that fails must not lose the question
            context = ""
            tr.error("turn.search", err=type(e).__name__, detail=str(e)[:200])
        if context:
            turn.query = PROMPT.format(context=context, query=turn.query)
            turn.stateless = True  # the passages ride in the query; replaying history re-sends stale ones
            turn.note("search", chars=len(context))

    # 3 · the agent
    turn.answer = (await ask(turn)) or ""
    if not turn.answer:
        tr.error("turn.no_answer", channel=inbound.channel)
        return turn
    text_result = await channel.send(inbound.sender, [Part.of_text(turn.answer)])

    # 4 · text → audio, for whoever spoke to us. The text is already delivered, so a failure here
    #     costs the voice note, not the answer.
    spoke = False
    if voice_in and services.speech_url():
        try:
            data, mime = await services.text_to_speech(
                turn.answer, tuple(getattr(channel, "accepted_audio", ()))
            )
            res = await channel.send(inbound.sender, [Part.of_audio(data=data, mime=mime)])
            spoke = all(r.get("ok") for r in res)
            turn.note("text_to_speech", bytes=len(data), mime=mime, sent=spoke)
        except Exception as e:  # noqa: BLE001
            tr.error("turn.text_to_speech", err=type(e).__name__, detail=str(e)[:200])

    tr(
        "turn.done",
        channel=inbound.channel,
        modality=inbound.modality,
        steps=turn.log,
        text_ok=all(r.get("ok") for r in text_result),
        voice=spoke,
    )
    return turn
