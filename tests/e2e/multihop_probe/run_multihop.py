"""Multi-hop QA: retrieval (BM25, the knowledge feature) vs searching a saved corpus file.

Not run in CI — it makes real model calls.

Datasets (MULTIHOP_DATASET):
- ``musique`` — MuSiQue-Ans (Trivedi et al., 2022): 2-4 hop questions built so single-hop
  shortcuts fail; 20 paragraphs per question. Sampled per hop count (2/3/4).
- ``2wiki`` — 2WikiMultihopQA (Ho et al., 2020): Wikipedia + Wikidata questions with gold
  evidence triples per hop; 10 paragraphs per question. Sampled per type: compositional
  (2 hops), inference (2+ hops through a relation rule), bridge_comparison (two 2-hop
  chains, then a comparison). The easy ``comparison`` type is left out by default.

The paragraphs of the sampled questions are pooled with those of other dev questions
into one corpus of ~2M chars, so finding the right paragraphs is a real retrieval
problem, not reading comprehension over a handful of paragraphs.

Conditions (MULTIHOP_CONDITION):
- ``knowledge``: the product's knowledge feature — the corpus is ingested once into the
  agent knowledge base (chunking, fastembed embeddings, hybrid search as configured in
  ``[knowledge]``), and each agent runs with ``enable_knowledge=True``, so it searches
  through ``knowledge_search_knowledge``. No other tools.
- ``rag``: one tool, ``search_knowledge_base(query, top_k)`` — plain BM25 over the
  paragraphs. A minimal retrieval baseline.
- ``file``: one tool, ``get_knowledge_base()`` — the whole corpus as one result, saved to
  a file and searched: by a search subagent (``DYNACONF_LARGE_OUTPUT__MODE=subagent``) or
  by the agent itself with grep/read (``=main_agent``). Needs
  ``DYNACONF_LARGE_OUTPUT__ENABLED=true``.

Grading: pass when the gold answer or an alias appears in the answer (normalized as in
the SQuAD scorer, all Unicode punctuation stripped), else the LLM judge from
``large_output_probe`` decides.

Run (from the repo root; policies isolated as in large_output_probe's README):
    MULTIHOP_DATASET=2wiki MULTIHOP_CONDITION=knowledge uv run python tests/e2e/multihop_probe/run_multihop.py

Env: MULTIHOP_PER_KIND (questions per hop count / type, default 8), MULTIHOP_KINDS
(comma list, overrides the default kinds), MULTIHOP_CORPUS_CHARS (default 2,000,000),
MULTIHOP_SEED (default 7), MULTIHOP_CONCURRENCY (default 4), MULTIHOP_EXECUTION_MODE
(hybrid | function_calling, default hybrid).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import string
import sys
import time
import unicodedata
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.tools import tool

from cuga.config import settings
from cuga.sdk import CugaAgent

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"

DATASET = os.environ.get("MULTIHOP_DATASET", "musique").strip().lower()
CONDITION = os.environ.get("MULTIHOP_CONDITION", "file").strip().lower()
PER_KIND = int(os.environ.get("MULTIHOP_PER_KIND", os.environ.get("MULTIHOP_PER_HOP", "8")))
CORPUS_CHARS = int(os.environ.get("MULTIHOP_CORPUS_CHARS", "2000000"))
SEED = int(os.environ.get("MULTIHOP_SEED", "7"))
CONCURRENCY = int(os.environ.get("MULTIHOP_CONCURRENCY", "4"))
EXECUTION_MODE = os.environ.get("MULTIHOP_EXECUTION_MODE", "hybrid").strip().lower()

DATASETS = {
    "musique": {
        "path": DATA_DIR / "musique_ans_v1.0_dev.jsonl",
        "url": "https://huggingface.co/datasets/dgslibisey/MuSiQue/resolve/main/musique_ans_v1.0_dev.jsonl",
        "kinds": ["2-hop", "3-hop", "4-hop"],
    },
    "2wiki": {
        "path": DATA_DIR / "2wiki_dev.json",
        "url": "https://huggingface.co/datasets/voidful/2WikiMultihopQA/resolve/main/dev.json",
        "kinds": ["compositional", "inference", "bridge_comparison"],
    },
}

# The vendored LLM judge lives next to the large-output probe.
sys.path.insert(0, str(HERE.parent / "large_output_probe"))
from judge import CorrectnessJudge  # noqa: E402
from utils import JudgeInput  # noqa: E402


# ── data ─────────────────────────────────────────────────────────────────────


def _download(spec: dict) -> Path:
    path = spec["path"]
    if not path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {spec['url']} to {path} ...")
        urllib.request.urlretrieve(spec["url"], path)
    return path


def load_rows() -> List[dict]:
    """Rows in one shape: id, kind, question, answer, aliases, chain, paragraphs[(title, text)]."""
    if DATASET not in DATASETS:
        raise SystemExit(f"MULTIHOP_DATASET must be one of {sorted(DATASETS)}, got {DATASET!r}")
    path = _download(DATASETS[DATASET])
    rows = []
    if DATASET == "musique":
        for line in path.open(encoding="utf-8"):
            r = json.loads(line)
            if not r.get("answerable", True):
                continue
            rows.append(
                {
                    "id": r["id"],
                    "kind": f"{r['id'][0]}-hop",
                    "question": r["question"],
                    "answer": r["answer"],
                    "aliases": r.get("answer_aliases") or [],
                    "chain": [f"{d['question']} => {d['answer']}" for d in r["question_decomposition"]],
                    "paragraphs": [(p["title"], p["paragraph_text"]) for p in r["paragraphs"]],
                }
            )
    else:
        for r in json.load(path.open(encoding="utf-8")):
            rows.append(
                {
                    "id": r["_id"],
                    "kind": r["type"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "aliases": [],
                    "chain": [" -> ".join(e) for e in r.get("evidences") or []],
                    "paragraphs": [(title, " ".join(sentences).strip()) for title, sentences in r["context"]],
                }
            )
    return rows


def build(rows: List[dict]) -> tuple[List[dict], List[dict]]:
    """``(questions, paragraphs)``: PER_KIND questions per kind, and the pooled corpus."""
    kinds = [k.strip() for k in os.environ.get("MULTIHOP_KINDS", "").split(",") if k.strip()]
    kinds = kinds or DATASETS[DATASET]["kinds"]
    rng = random.Random(SEED)
    by_kind: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    questions = [q for k in kinds for q in rng.sample(by_kind[k], min(PER_KIND, len(by_kind[k])))]

    chosen = {q["id"] for q in questions}
    others = [r for r in rows if r["id"] not in chosen]
    rng.shuffle(others)
    seen = set()
    paragraphs: List[dict] = []
    size = 0
    for row in questions + others:
        if size >= CORPUS_CHARS and row["id"] not in chosen:
            break
        for title, text in row["paragraphs"]:
            if (title, text) in seen or not text:
                continue
            seen.add((title, text))
            paragraphs.append({"title": title, "text": text})
            size += len(title) + len(text) + 10
    rng.shuffle(paragraphs)
    for i, p in enumerate(paragraphs, start=1):
        p["id"] = i
    return questions, paragraphs


def _line(p: dict) -> str:
    return f"[{p['id']}] {p['title']}: {p['text']}"


# ── retrieval baseline: BM25 ─────────────────────────────────────────────────

_TOKEN = re.compile(r"\w+")


class BM25:
    def __init__(self, docs: List[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [Counter(_TOKEN.findall(d.lower())) for d in docs]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def top(self, query: str, k: int) -> List[int]:
        terms = [t for t in _TOKEN.findall(query.lower()) if t in self.idf]
        scores = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in terms:
                f = d.get(t)
                if f:
                    s += (
                        self.idf[t]
                        * f
                        * (self.k1 + 1)
                        / (f + self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg))
                    )
            if s:
                scores.append((s, i))
        return [i for _, i in sorted(scores, reverse=True)[:k]]


def make_tools(paragraphs: List[dict]):
    if CONDITION == "knowledge":
        return []
    if CONDITION == "rag":
        lines = [_line(p) for p in paragraphs]
        index = BM25(lines)

        @tool
        def search_knowledge_base(query: str, top_k: int = 5) -> str:
            """Search the knowledge base (an encyclopedia of short articles) and return the top_k most
            relevant paragraphs (max 10). Search again with different words for each fact you need."""
            hits = index.top(query, max(1, min(int(top_k), 10)))
            return "\n\n".join(lines[i] for i in hits) or "No results."

        return [search_knowledge_base]

    corpus = "\n".join(_line(p) for p in paragraphs)

    @tool
    def get_knowledge_base() -> str:
        """Return the whole knowledge base: an encyclopedia of short articles, one paragraph per line,
        formatted "[id] Title: text"."""
        return corpus

    return [get_knowledge_base]


# ── the knowledge feature ────────────────────────────────────────────────────


def _knowledge_dir() -> Path:
    return DATA_DIR / f"knowledge_{DATASET}_s{SEED}_k{PER_KIND}_c{CORPUS_CHARS}"


# One KnowledgeEngine per persist dir: the engine takes an exclusive lock on it, so a second
# CugaAgent(enable_knowledge=True) in the same process fails to start its own engine and (the SDK
# only logs a warning) runs without knowledge tools. Every per-question agent shares this client.
_SHARED_KNOWLEDGE: Optional[object] = None


async def prepare_knowledge(paragraphs: List[dict]) -> None:
    """Ingest the corpus once into the agent knowledge base (reused across runs)."""
    global _SHARED_KNOWLEDGE
    persist = _knowledge_dir()
    settings.knowledge.enabled = True
    settings.knowledge.persist_dir = str(persist)
    marker = persist / "ingested.json"
    if marker.exists():
        print(f"knowledge: reusing {persist}")
        _SHARED_KNOWLEDGE = CugaAgent(tools=[], enable_knowledge=True).knowledge
        return
    persist.mkdir(parents=True, exist_ok=True)
    corpus_md = persist / f"corpus_{DATASET}.md"
    # One section per paragraph, so chunks keep the article title.
    corpus_md.write_text("\n\n".join(f"## {p['title']}\n\n{p['text']}" for p in paragraphs), encoding="utf-8")
    agent = CugaAgent(tools=[], enable_knowledge=True)
    started = time.monotonic()
    task = await agent.knowledge.ingest(str(corpus_md), scope="agent")
    print(f"knowledge: ingested {corpus_md.name} in {time.monotonic() - started:.0f}s -> {task}")
    status = str((task or {}).get("status", "")).lower()
    if status not in ("completed", "complete", "success", "done"):
        raise SystemExit(f"knowledge ingest did not complete: {task}")
    marker.write_text(json.dumps(task, default=str))
    _SHARED_KNOWLEDGE = agent.knowledge


# ── grading ──────────────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    # Any Unicode punctuation, not only ASCII: models write “curly quotes” and en dashes.
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(
        " " if unicodedata.category(ch).startswith("P") or ch in string.punctuation else ch for ch in text
    )
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


_judge: Optional[CorrectnessJudge] = None


def grade(row: dict, answer: str) -> tuple[bool, str]:
    golds = [row["answer"], *row.get("aliases", [])]
    norm = f" {_normalize(answer)} "
    if any(f" {_normalize(g)} " in norm for g in golds if _normalize(g)):
        return True, "contains gold"
    global _judge
    try:
        _judge = _judge or CorrectnessJudge(config={})
        verdict = _judge.judge(
            JudgeInput(
                capability="multihop_probe",
                domain=DATASET,
                query=row["question"],
                additional_instructions=f"Accepted aliases: {golds}",
                gt_answer=row["answer"],
                pred_answer=answer,
                gt_tool_calls=[],
                pred_tool_calls=[],
                gt_tool_responses=[],
                pred_tool_responses=[],
            )
        )
        return verdict.score == 1, f"judge: {verdict.explanation or ''}"[:200]
    except Exception as exc:
        return False, f"judge unavailable: {exc}"


# ── run ──────────────────────────────────────────────────────────────────────


async def run_one(row: dict, tools, sem: asyncio.Semaphore) -> dict:
    async with sem:
        started = time.monotonic()
        agent = CugaAgent(tools=tools, enable_knowledge=CONDITION == "knowledge")
        if CONDITION == "knowledge":
            agent._knowledge_client = _SHARED_KNOWLEDGE  # see _SHARED_KNOWLEDGE
        try:
            result = await agent.invoke(row["question"], track_tool_calls=True)
            answer, error = result.answer or "", result.error
            receipt = result.receipt
            calls = [c.get("name") for c in (result.tool_calls or [])]
            if CONDITION == "knowledge" and not any(str(c).startswith("knowledge_") for c in calls):
                print(f"warning: {row['id']} made no knowledge search", flush=True)
        except Exception as exc:
            answer, error, receipt, calls = "", str(exc), None, []
        ok = answer and "Maximum step limit" not in answer
        passed, why = grade(row, answer) if ok else (False, "no answer")
        return {
            "id": row["id"],
            "kind": row["kind"],
            "question": row["question"],
            "gold": row["answer"],
            "chain": row["chain"],
            "answer": answer,
            "passed": passed,
            "why": why,
            "error": error,
            "tool_calls": calls,
            "tokens": receipt.total_tokens if receipt else None,
            "seconds": round(time.monotonic() - started, 1),
        }


async def main() -> None:
    if CONDITION not in ("knowledge", "rag", "file"):
        raise SystemExit(f"MULTIHOP_CONDITION must be knowledge, rag or file, got {CONDITION!r}")
    if CONDITION == "file" and not settings.large_output.enabled:
        raise SystemExit("MULTIHOP_CONDITION=file needs DYNACONF_LARGE_OUTPUT__ENABLED=true")
    questions, paragraphs = build(load_rows())
    corpus_chars = sum(len(_line(p)) + 1 for p in paragraphs)
    label = CONDITION if CONDITION != "file" else f"file_{settings.large_output.mode}"
    print(
        f"dataset: {DATASET}, condition: {label}, execution: {EXECUTION_MODE}, {len(questions)} questions "
        f"({dict(Counter(q['kind'] for q in questions))}), corpus: {len(paragraphs):,} paragraphs / "
        f"{corpus_chars:,} chars\n",
        flush=True,
    )
    if CONDITION == "knowledge":
        await prepare_knowledge(paragraphs)
    tools = make_tools(paragraphs)
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(run_one(q, tools, sem) for q in questions))

    out = DATA_DIR / f"results_{DATASET}_{label}_{EXECUTION_MODE}.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    lines = []
    for kind in dict.fromkeys(q["kind"] for q in questions):
        rs = [r for r in results if r["kind"] == kind]
        lines.append(f"{kind}: {sum(r['passed'] for r in rs)}/{len(rs)}")
    tokens = [r["tokens"] for r in results if r["tokens"] is not None]
    lines.append(
        f"total: {sum(r['passed'] for r in results)}/{len(results)} passed; tokens {sum(tokens):,}; "
        f"step-limit {sum('Maximum step limit' in r['answer'] for r in results)}; "
        f"errors {sum(1 for r in results if r['error'])} ({DATASET}, {label})"
    )
    lines.append(f"results: {out}")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    os.environ.setdefault("DYNACONF_ADVANCED_FEATURES__FORCE_AUTONOMOUS_MODE", "true")
    settings.advanced_features.run_receipt = True
    settings.advanced_features.cuga_lite_execution_mode = EXECUTION_MODE
    asyncio.run(main())
