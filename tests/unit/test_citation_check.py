"""Citation check: claims are matched to markers, verdicts are aggregated per source, and the
final answer carries each source's ``support``. The checkers themselves run against real
models in ``tests/integration/test_citation_check_live.py``."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.answer import final_answer
from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.knowledge import sources
from cuga.backend.knowledge.citation_check import check_citations, claims_by_marker
from cuga.backend.knowledge.sources import _reset_all_ledgers_for_tests, get_ledger

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Issued to Contoso [s1]. Total $4,200 [s2].",
            [("Issued to Contoso", ["s1"]), ("Total $4,200", ["s2"])],
        ),
        (
            "Issued to Contoso. [s1] Total $4,200. [s2]",
            [("Issued to Contoso.", ["s1"]), ("Total $4,200.", ["s2"])],
        ),
        ("Both facts hold [s1, s3].", [("Both facts hold", ["s1", "s3"])]),
        ("备份每天执行一次。[s1]加密存储【s2】", [("备份每天执行一次。", ["s1"]), ("加密存储", ["s2"])]),
        ("See `code [s9]` here.", []),
        ("- first point [s1]\n- second point [s2]", [("- first point", ["s1"]), ("- second point", ["s2"])]),
        (
            "छुट्टी मिलती है [s1]। वेतन हर महीने मिलता है [s2]।",
            [("छुट्टी मिलती है", ["s1"]), ("वेतन हर महीने मिलता है", ["s2"])],
        ),
        (
            "Vans charge only at the depot. [fleet_handbook.txt, page 2] [s1]",
            [("Vans charge only at the depot.", ["s1"])],
        ),
    ],
    ids=[
        "marker-before-period",
        "marker-after-period",
        "two-ids",
        "cjk",
        "code-span",
        "bullets",
        "hindi-danda",
        "reference-before-marker",
    ],
)
def test_claims_are_the_sentences_markers_belong_to(text, expected):
    assert claims_by_marker(text) == expected


def test_long_sources_are_judged_in_whole_sentence_windows(monkeypatch):
    from cuga.backend.knowledge import citation_check

    monkeypatch.setattr(citation_check, "_NLI_WINDOW_TOKENS", 4)
    words = lambda text: len(text.split())  # noqa: E731
    assert citation_check._windows("a b. c d e. f.", words) == [["a b."], ["c d e.", "f."]]
    assert citation_check._windows("一二三。四五。", len) == [["一二三。"], ["四五。"]]


def test_nli_support_needs_a_passage_that_entails_on_its_own(monkeypatch):
    """The real model reports entailment from long boilerplate; a supported verdict must be backed
    by one or two sentences that entail the claim by themselves, the shorter when both do."""
    from cuga.backend.knowledge import citation_check as cc

    def probs(passage: str) -> list[float]:
        if "36 months" in passage:  # longer passages score a little higher, as the real model does
            strength = 0.6 + 0.01 * len(passage.split())
            return [strength, 1 - strength, 0.0]
        return [0.9, 0.05, 0.05] if len(passage.split()) > 8 else [0.05, 0.9, 0.05]

    to_verdict = {0: cc.SUPPORTED, 1: cc.NOT_STATED, 2: cc.CONTRADICTED}
    monkeypatch.setattr(cc, "_nli_model", lambda: (SimpleNamespace(tokenize=str.split), None, to_verdict))
    monkeypatch.setattr(cc, "_nli_probs", lambda pairs: [probs(p) for _, p in pairs])
    boiler = "Section one covers inspections. Section two covers gloves. Section three covers lights."
    claim = "The warranty lasts 36 months."
    assert cc._nli_judgements([(claim, boiler), (claim, f"{boiler} The warranty lasts 36 months.")]) == [
        (cc.NOT_STATED, ""),
        (cc.SUPPORTED, "The warranty lasts 36 months."),
    ]


def _fake_llm(labels: dict[int, str], seen: list | None = None):
    async def generate(prompt: str) -> str:
        if seen is not None:
            seen.append(prompt)
        return "Here you go:\n" + json.dumps({str(k): v for k, v in labels.items()})

    return generate


CHUNKS = {"s1": "Invoice INV-9 was issued to Contoso.", "s2": "The total is $4,200."}


def test_a_source_gets_the_worst_verdict_over_the_claims_citing_it():
    text = "Issued to Contoso [s1]. Billed $4,200 [s2]. Contoso paid in full [s1]."
    verdicts = asyncio.run(
        check_citations(
            text, CHUNKS.get, "llm", generate=_fake_llm({1: "supported", 2: "supported", 3: "not_stated"})
        )
    )
    assert verdicts == {"s1": "not_stated", "s2": "supported"}


def test_ids_without_a_source_are_not_sent_to_the_checker():
    seen: list[str] = []
    verdicts = asyncio.run(
        check_citations("A [s1]. B [s7].", CHUNKS.get, "llm", generate=_fake_llm({1: "contradicted"}, seen))
    )
    assert verdicts == {"s1": "contradicted"}
    assert "2. SOURCE" not in seen[0]


@pytest.mark.parametrize("reply", ["no json at all", '{"1": "maybe"}'], ids=["no-json", "unknown-label"])
def test_unusable_checker_replies_never_claim_support(reply):
    async def generate(prompt: str) -> str:
        return reply

    verdicts = asyncio.run(check_citations("A [s1].", CHUNKS.get, "llm", generate=generate))
    assert verdicts in ({}, {"s1": "not_stated"})


def test_off_or_nothing_cited_returns_no_verdicts():
    assert asyncio.run(check_citations("A [s1].", CHUNKS.get, "off")) == {}
    assert asyncio.run(check_citations("No citations here.", CHUNKS.get, "llm", generate=_fake_llm({}))) == {}


# --- final answer -----------------------------------------------------------------------------


@pytest.fixture
def thread(monkeypatch):
    _reset_all_ledgers_for_tests()
    ledger = get_ledger("t-check")
    ledger.begin_turn()
    for text in ("Invoice INV-9 was issued to Contoso.", "Backups run every night."):
        ledger.register(
            SimpleNamespace(
                text=text, filename=f"{text[:6]}.txt", page=1, scope="agent", score=0.9, section_path=""
            ),
            query="q",
        )
    monkeypatch.setattr(sources, "_agent_flag_lookup", lambda: True)
    yield ledger
    sources.set_agent_citation_check_lookup(None)


def _finalize(answer: str):
    state = SimpleNamespace(
        final_answer=answer, thread_id="t-check", sources=[], final_answer_finalized=False
    )
    asyncio.run(FinalAnswerNode.finalize_answer(state))
    return state


def test_checked_answer_carries_support_on_each_source(thread, monkeypatch):
    sources.set_agent_citation_check_lookup(lambda: "llm")
    monkeypatch.setattr(
        final_answer,
        "_citation_check_llm",
        lambda: SimpleNamespace(generate=_fake_llm({1: "supported", 2: "contradicted"})),
    )
    state = _finalize("Issued to Contoso [s1]. Backups run weekly [s2].")

    assert state.final_answer == "Issued to Contoso [1]. Backups run weekly [2]."
    assert [(s["filename"], s["support"]) for s in state.sources] == [
        ("Invoic.txt", "supported"),
        ("Backup.txt", "contradicted"),
    ]


def test_an_earlier_turn_citation_is_kept_only_when_verified(thread, monkeypatch):
    thread.begin_turn()  # a follow-up answered from the previous turn's search results
    sources.set_agent_citation_check_lookup(lambda: "llm")
    monkeypatch.setattr(
        final_answer,
        "_citation_check_llm",
        lambda: SimpleNamespace(generate=_fake_llm({1: "supported", 2: "contradicted"})),
    )
    state = _finalize("Issued to Contoso [s1]. Backups run weekly [s2].")

    assert state.final_answer == "Issued to Contoso [1]. Backups run weekly ."
    assert [(s["filename"], s["support"]) for s in state.sources] == [("Invoic.txt", "supported")]


def test_earlier_turn_citations_strip_when_checking_is_off(thread):
    thread.begin_turn()
    sources.set_agent_citation_check_lookup(lambda: "off")
    state = _finalize("Issued to Contoso [s1].")
    assert (state.final_answer, state.sources) == ("Issued to Contoso .", [])


def test_check_off_leaves_sources_unchanged(thread):
    sources.set_agent_citation_check_lookup(lambda: "off")
    state = _finalize("Issued to Contoso [s1].")
    assert state.sources and "support" not in state.sources[0]


def test_a_failing_checker_never_blocks_the_answer(thread, monkeypatch):
    sources.set_agent_citation_check_lookup(lambda: "llm")

    async def broken(prompt: str) -> str:
        raise TimeoutError("model unavailable")

    monkeypatch.setattr(final_answer, "_citation_check_llm", lambda: SimpleNamespace(generate=broken))
    state = _finalize("Issued to Contoso [s1].")

    assert state.final_answer == "Issued to Contoso [1]."
    assert state.sources and "support" not in state.sources[0]


@pytest.mark.parametrize(
    ("claim", "stripped"),
    [
        ("The Chinese document states that backups run daily", "backups run daily"),
        ("According to the policy, passwords expire after 90 days", "passwords expire after 90 days"),
        ("This invoice shows a total of $4,200", "a total of $4,200"),
        ("Backups run daily", "Backups run daily"),
    ],
)
def test_attribution_framing_is_removed_before_the_nli_check(claim, stripped):
    from cuga.backend.knowledge.citation_check import _ATTRIBUTION

    assert _ATTRIBUTION.sub("", claim) == stripped
