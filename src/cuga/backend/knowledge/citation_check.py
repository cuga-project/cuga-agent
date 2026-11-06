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
# A plain reference the model writes before the marker ("... stations. [handbook.txt, page 2] [s1]").
_TRAILING_REF = re.compile(r"(?:\s*\[[^\]\n]*\])+\s*$")
# A sentence ends at . ! ? followed by whitespace, at a CJK, Devanagari (।), Urdu (۔) or Arabic (؟)
# terminator, or at a line break.
_BOUNDARY = re.compile(r"[.!?](?=\s)|[。！？।۔؟]|\n")
_TERMINATORS = ".!?。！？।۔؟"
_SENTENCES = re.compile(r"(?<=[.!?])\s+|(?<=[。！？।۔؟])|\n+")
# The NLI model reads 512 tokens of source and claim together; longer sources are judged in
# windows of whole sentences so nothing is cut off.
_NLI_WINDOW_TOKENS = 400
_NLI_BATCH = 32
# An NLI "supported" needs a passage of one or two sentences that entails the claim on its own.
_NLI_EVIDENCE = 0.5
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

    A marker right after a sentence's terminator ("Contoso. [s1]") belongs to that sentence, also
    when a plain bracketed reference sits between them.
    """
    prose = _CODE_RE.sub(lambda m: " " * len(m.group(0)), text)  # keep offsets, drop code
    out = []
    for m in _MARKER_RE.finditer(prose):
        before = _TRAILING_REF.sub("", prose[: m.start()]).rstrip()
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
            judged = await asyncio.to_thread(
                _nli_judgements, [(_ATTRIBUTION.sub("", c), s) for c, _, s in pairs]
            )
            labels = [label for label, _ in judged]
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
_RANK = {NOT_STATED: 0, CONTRADICTED: 1, SUPPORTED: 2}


def _nli_model() -> tuple[Any, Any, dict[int, str]]:
    """(tokenizer, model, class index -> verdict), loaded once."""
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
        return _nli


def _windows(source: str, count_tokens: Callable[[str], int]) -> list[list[str]]:
    """The source's sentences, grouped into consecutive windows of at most _NLI_WINDOW_TOKENS."""
    windows: list[list[str]] = []
    size = 0
    for sentence in (s.strip() for s in _SENTENCES.split(source)):
        if not sentence:
            continue
        n = count_tokens(sentence)
        if windows and size + n <= _NLI_WINDOW_TOKENS:
            windows[-1].append(sentence)
            size += n
        else:
            windows.append([sentence])
            size = n
    return windows or [[source]]


def _nli_probs(pairs: list[tuple[str, str]]) -> list[list[float]]:
    """Class probabilities per (claim, premise) pair, in batches of _NLI_BATCH."""
    tokenizer, model, _ = _nli_model()
    import torch

    probs: list[list[float]] = []
    with torch.no_grad():
        for start in range(0, len(pairs), _NLI_BATCH):
            part = pairs[start : start + _NLI_BATCH]
            batch = tokenizer(
                [premise for _, premise in part],
                [claim for claim, _ in part],
                truncation="only_first",
                max_length=512,
                padding=True,
                return_tensors="pt",
            )
            probs += model(**batch).logits.softmax(dim=-1).tolist()
    return probs


def _nli_judgements(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(verdict, evidence) per (claim, source) pair.

    The verdict comes from windows of whole sentences (most sources fit in one): supported if any
    window entails the claim, otherwise contradicted if any contradicts it. Then the windows that
    reached the verdict are checked one and two sentences at a time. A supported verdict with no
    such passage entailing the claim on its own drops to not_stated: on long, low-information text
    (repeated boilerplate, lists) the model reports entailment for claims the text never makes. The evidence is the
    strongest passage for the verdict, or "" when none reaches _NLI_EVIDENCE.
    """
    tokenizer, _, to_verdict = _nli_model()
    index = {verdict: i for i, verdict in to_verdict.items()}
    windows = [
        (k, window)
        for k, (_, source) in enumerate(pairs)
        for window in _windows(source, lambda text: len(tokenizer.tokenize(text)))
    ]
    labels = [NOT_STATED] * len(pairs)
    window_labels = []
    for (k, _), p in zip(windows, _nli_probs([(pairs[k][0], " ".join(w)) for k, w in windows])):
        label = to_verdict[max(range(len(p)), key=p.__getitem__)]
        window_labels.append(label)
        if _RANK[label] > _RANK[labels[k]]:
            labels[k] = label
    # Evidence comes from every window that reached the pair's verdict, one and two sentences at a time.
    passages = [
        (k, passage)
        for (k, w), label in zip(windows, window_labels)
        if label == labels[k] != NOT_STATED
        for passage in w + [" ".join(w[i : i + 2]) for i in range(len(w) - 1)]
    ]
    evidence, strength = [""] * len(pairs), [0.0] * len(pairs)
    for (k, passage), p in zip(passages, _nli_probs([(pairs[k][0], x) for k, x in passages])):
        if p[index[labels[k]]] > strength[k]:
            strength[k], evidence[k] = p[index[labels[k]]], passage
    for k in range(len(pairs)):
        if strength[k] < _NLI_EVIDENCE:
            evidence[k] = ""
            if labels[k] == SUPPORTED:
                labels[k] = NOT_STATED
    return list(zip(labels, evidence))
