"""Both citation checkers against real models on labeled claims, including Chinese and Hindi
sources. Live: the ``llm`` checker calls the configured agent model, and ``nli`` downloads the
NLI model (~1 GB) on first use."""

import asyncio
import re
from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.knowledge import sources
from cuga.backend.knowledge.citation_check import CONTRADICTED, SUPPORTED, check_citations
from cuga.backend.knowledge.sources import _reset_all_ledgers_for_tests, get_ledger

pytestmark = pytest.mark.e2e

SOURCES = {
    "s1": "Invoice INV-2026-0009 was issued to Contoso for the backup service. The total is $4,200.",
    "s2": "Passwords must be at least 12 characters long and must be rotated every 90 days.",
    "s3": "我们的数据备份策略每天执行一次，所有备份文件都会加密并保存在两个不同的数据中心。",
    "s4": "कर्मचारियों को हर महीने डेढ़ दिन की छुट्टी मिलती है।",
}
CASES = [
    ("INV-2026-0009 was issued to Contoso [s1].", SUPPORTED),
    ("INV-2026-0009 was issued to Globex [s1].", CONTRADICTED),
    ("Passwords need at least 12 characters [s2].", SUPPORTED),
    ("Passwords must be rotated every 30 days [s2].", CONTRADICTED),
    ("Data is backed up every day and the backups are encrypted [s3].", SUPPORTED),
    ("Data is backed up once a week [s3].", CONTRADICTED),
    ("Employees get one and a half days of leave every month [s4].", SUPPORTED),
    ("Employees get three days of leave every month [s4].", CONTRADICTED),
    # framed the way agent answers phrase it
    ("The Chinese document states that backups run once every day and are encrypted [s3].", SUPPORTED),
    ("According to the invoice, INV-2026-0009 was issued to Contoso [s1].", SUPPORTED),
    ("The invoice document states that INV-2026-0009 was issued to Globex [s1].", CONTRADICTED),
]


@pytest.mark.parametrize("checker", ["llm", "nli"])
def test_long_sources_support_only_what_they_state(checker):
    """A fact past the NLI model's 512 tokens is still seen, and the same long text without the
    fact supports nothing (whole-text NLI called the boilerplate alone "supported")."""
    if checker == "nli":
        pytest.importorskip("transformers")
    from cuga.backend.knowledge_llm_bridge import CugaChatGenerator

    filler = " ".join(
        f"Section {i} of the maintenance handbook describes routine inspection steps for the plant floor."
        for i in range(60)
    )
    sources = {"s1": filler + " The QX-7741 pump warranty lasts 36 months from installation.", "s2": filler}
    claim = "The QX-7741 pump warranty lasts 36 months"
    generate = CugaChatGenerator().generate if checker == "llm" else None
    verdicts = asyncio.run(
        check_citations(f"{claim} [s1]. {claim} [s2].", sources.get, checker, generate=generate)
    )
    assert verdicts["s1"] == SUPPORTED
    assert verdicts["s2"] != SUPPORTED


def _verdicts(checker: str, generate=None) -> list[str]:
    async def run():
        return [
            (await check_citations(claim, SOURCES.get, checker, generate=generate)).get(
                re.search(r"\[(s\d+)\]", claim).group(1)
            )
            for claim, _ in CASES
        ]

    return asyncio.run(run())


def _assert_separates(found: list[str]) -> None:
    misses = [(claim, want, got) for (claim, want), got in zip(CASES, found) if got != want]
    assert len(misses) <= 1, misses
    assert not any(want == CONTRADICTED and got == SUPPORTED for _, want, got in misses), misses


def test_llm_checker_separates_supported_from_contradicted():
    from cuga.backend.knowledge_llm_bridge import CugaChatGenerator

    _assert_separates(_verdicts("llm", CugaChatGenerator().generate))


def test_nli_checker_separates_supported_from_contradicted():
    pytest.importorskip("transformers")
    _assert_separates(_verdicts("nli"))


@pytest.mark.parametrize("checker", ["llm", "nli"])
def test_a_follow_up_keeps_a_supported_earlier_citation_and_strips_a_misattributed_one(checker, monkeypatch):
    if checker == "nli":
        pytest.importorskip("transformers")
    _reset_all_ledgers_for_tests()
    ledger = get_ledger("t-live")
    ledger.begin_turn()
    for key in ("s1", "s3"):  # the invoice and the Chinese backup policy become s1 and s2
        ledger.register(
            SimpleNamespace(
                text=SOURCES[key], filename=f"{key}.txt", page=1, scope="agent", score=0.9, section_path=""
            ),
            query="q",
        )
    ledger.begin_turn()  # the follow-up answers without searching again
    monkeypatch.setattr(sources, "_agent_flag_lookup", lambda: True)
    sources.set_agent_citation_check_lookup(lambda: checker)
    state = SimpleNamespace(
        final_answer="INV-2026-0009 was issued to Contoso [s1]. Passwords are rotated every 90 days [s2].",
        thread_id="t-live",
        sources=[],
        final_answer_finalized=False,
    )
    try:
        asyncio.run(FinalAnswerNode.finalize_answer(state))
    finally:
        sources.set_agent_citation_check_lookup(None)

    assert [(s["filename"], s["support"]) for s in state.sources] == [("s1.txt", SUPPORTED)]
    assert state.final_answer.startswith("INV-2026-0009 was issued to Contoso [1].")
    assert "[2]" not in state.final_answer
