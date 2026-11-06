"""Check that each cited source supports the claim it is attached to.

A claim is the sentence a ``[sN]`` marker belongs to, without its markers. Two checkers:

- ``llm``: the agent's own model judges all (claim, source) pairs in one call. Multilingual.
- ``nli``: a local multilingual NLI model (mDeBERTa-v3 XNLI, MIT license), for deployments
  that avoid extra API calls. About 1 GB, downloaded on first use.

On 36 labeled claims, including Chinese and Hindi sources, both separated supported from
contradicted claims with AUC 0.96. The shipped reranker scored 0.81 and failed on Hindi.

Each pair gets ``supported``, ``contradicted`` or ``not_stated``. A source's verdict is the
worst over the claims that cite it. Checking never blocks an answer: on any failure, no verdicts.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from cuga.backend.knowledge.sources import _CODE_RE, _MARKER_RE

SUPPORTED, CONTRADICTED, NOT_STATED = "supported", "contradicted", "not_stated"
_WORST_FIRST = (CONTRADICTED, NOT_STATED, SUPPORTED)
NLI_MODEL = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
_MAX_CLAIM_CHARS = 400
# A sentence ends at . ! ? followed by whitespace, at a CJK terminator, or at a line break.
_BOUNDARY = re.compile(r"[.!?](?=\s)|[。！？]|\n")
_TERMINATORS = ".!?。！？"
# A leading attribution ("The Chinese document states that ...", "According to the policy, ...").
# NLI models judge the framed sentence as a claim about the document and answer "not stated";
# the LLM checker reads past it. English only: for other languages prefer the llm checker.
_ATTRIBUTION = re.compile(
    r"^(?:according to [^,]{1,80},\s*|(?:the|this|that)\s+(?:[\w-]+\s+){0,3}"
    r"(?:document|file|source|text|policy|report|record|invoice|page|section|note|article)s?\s+"
    r"(?:states?|says?|mentions?|notes?|indicates?|explains?|shows?|reports?|confirms?|specifies?)"
    r"\s+(?:that\s+)?)",
    re.IGNORECASE,
)


def claims_by_marker(text: str) -> list[tuple[str, list[str]]]:
    """``(claim, [cite_id, ...])`` for each citation marker outside code spans.

    A marker right after a sentence's terminator ("Contoso. [s1]") belongs to that sentence.
    """
    prose = _CODE_RE.sub(lambda m: " " * len(m.group(0)), text)  # keep offsets, drop code
    out = []
    for m in _MARKER_RE.finditer(prose):
        before = prose[: m.start()].rstrip()
        body = before[:-1] if before[-1:] in _TERMINATORS else before
        start = max((b.end() for b in _BOUNDARY.finditer(body)), default=0)
        claim = " ".join(_MARKER_RE.sub(" ", before[start:]).split())[-_MAX_CLAIM_CHARS:]
        ids = [i.lower() for i in re.split(r"[\s,]+", m.group(1)) if i]
        if claim:
            out.append((claim, ids))
    return out


async def check_citations(
    text: str,
    source_text: Callable[[str], str | None],
    checker: str,
    *,
    generate: Callable[[str], Awaitable[str]] | None = None,
) -> dict[str, str]:
    """Verdict per cite_id for the citations in ``text``.

    ``source_text(cite_id)`` returns the cited chunk, or None for an id that will not resolve.
    ``generate`` is the agent's LLM (``async (prompt) -> str``), needed for ``checker="llm"``.
    """
    pairs = [
        (claim, cid, chunk)
        for claim, ids in claims_by_marker(text)
        for cid in ids
        if (chunk := source_text(cid))
    ]
    if not pairs:
        return {}
    try:
        if checker == "llm":
            labels = await _llm_labels([(c, s) for c, _, s in pairs], generate)
        elif checker == "nli":
            labels = await asyncio.to_thread(_nli_labels, [(_ATTRIBUTION.sub("", c), s) for c, _, s in pairs])
        else:
            return {}
    except Exception:
        logger.exception("citation check ({}) failed; answer delivered without verdicts", checker)
        return {}
    verdicts: dict[str, list[str]] = {}
    for (_, cid, _), label in zip(pairs, labels):
        verdicts.setdefault(cid, []).append(label)
    return {cid: next(v for v in _WORST_FIRST if v in found) for cid, found in verdicts.items()}


async def _llm_labels(
    pairs: list[tuple[str, str]], generate: Callable[[str], Awaitable[str]] | None
) -> list[str]:
    if generate is None:
        raise ValueError("citation check 'llm' needs the agent's model")
    prompt = (
        "For each numbered item, decide whether the SOURCE supports the CLAIM. Judge only from the "
        "source text, which may be in a different language than the claim. Reply with one JSON "
        'object mapping each item number to "supported", "contradicted" or "not_stated", and nothing '
        "else.\n\n"
        + "\n\n".join(
            f"{i}. SOURCE: {source}\n   CLAIM: {claim}" for i, (claim, source) in enumerate(pairs, 1)
        )
    )
    reply = await generate(prompt)
    found = re.search(r"\{.*\}", reply, re.S)
    if not found:
        raise ValueError(f"citation check reply has no JSON object: {reply[:200]!r}")
    answers = json.loads(found.group(0))
    allowed = {SUPPORTED, CONTRADICTED, NOT_STATED}
    return [
        label if (label := str(answers.get(str(i), "")).strip().lower()) in allowed else NOT_STATED
        for i in range(1, len(pairs) + 1)
    ]


_nli: tuple[Any, Any, dict[int, str]] | None = None
_nli_lock = threading.Lock()


def _nli_labels(pairs: list[tuple[str, str]]) -> list[str]:
    global _nli
    with _nli_lock:
        if _nli is None:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(NLI_MODEL)
            model = AutoModelForSequenceClassification.from_pretrained(NLI_MODEL).eval()
            names = {i: n.lower() for i, n in model.config.id2label.items()}
            to_verdict = {
                i: SUPPORTED if "entail" in n else CONTRADICTED if "contradict" in n else NOT_STATED
                for i, n in names.items()
            }
            _nli = (tokenizer, model, to_verdict)
        tokenizer, model, to_verdict = _nli
    import torch

    with torch.no_grad():
        batch = tokenizer(
            [source for _, source in pairs],
            [claim for claim, _ in pairs],
            truncation="only_first",
            max_length=512,
            padding=True,
            return_tensors="pt",
        )
        best = model(**batch).logits.argmax(dim=-1).tolist()
    return [to_verdict[i] for i in best]
