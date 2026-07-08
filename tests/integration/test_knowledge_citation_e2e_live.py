"""Opt-in LIVE-LLM smoke test for knowledge citations.

Gated behind @pytest.mark.e2e (the repo's real-service marker). Run explicitly,
with a real LLM + embedder configured:

    pytest tests/integration/test_knowledge_citation_e2e_live.py -m e2e -v -s

It runs ONE real chat turn that must cite an ingested doc, then asserts the
INVARIANT (robust to model nondeterminism): whatever citation-shaped markers the
model emitted were RESOLVED into [n] chips — no raw [sN] / 【sN】 / (sN) / … survive
— and every rendered chip number is backed by a source snapshot. If the model
declines to cite this run, the test SKIPS (not a regression). This is the net the
deterministic test cannot provide: it catches a *real* model emitting a marker
format the resolver doesn't handle (new drift) before it ships.

Caveats (why it's opt-in): it drives the SDK's OWN knowledge engine built from
settings, so it ingests a small doc into the configured knowledge store as a side
effect and needs a working LLM + embedder. It hard-skips if the agent/model/ingest
can't be set up in the current environment.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import uuid
from pathlib import Path

import pytest

# Any bracket-family cite marker (sN) that SHOULD have been resolved away. If one
# survives in the final answer, the model cited but it didn't render as a chip —
# the regression class this whole harness exists to catch. Resolved chips are
# bare [n] (no s-prefix) and never match this.
_UNRESOLVED_CITE_RE = re.compile(r"[\[［【〔(<]\s*[sS]\d+\s*[\]］】〕)>]")


def _acme_doc(tmpdir: str) -> Path:
    p = Path(tmpdir) / "acme.txt"
    p.write_text(
        "ACME Corp financial summary. In fiscal year 2025, ACME Corp reported "
        "total revenue of 4,521 million dollars, up 12 percent year over year. "
        "The strongest performing segment was cloud services."
    )
    return p


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_live_chat_citations_resolve_to_chips_and_sources():
    from cuga.sdk import CugaAgent

    try:
        agent = CugaAgent(enable_knowledge=True, enable_citations=True)
    except Exception as exc:  # noqa: BLE001 — live-env setup is out of our control
        pytest.skip(f"cannot construct a live CugaAgent in this env: {exc}")

    try:
        # Ingest into the agent's OWN knowledge engine — the same one invoke()
        # searches (sdk.py wires _knowledge_client._engine into the run config).
        tmpdir = tempfile.mkdtemp()
        doc = _acme_doc(tmpdir)
        try:
            client = agent.knowledge  # lazily builds the client + engine
            task = await client.ingest(str(doc), scope="agent")
            task_id = task.get("task_id")
            status = None
            for _ in range(120):  # up to ~60s
                status = await client._engine.get_task(task_id)
                if status and status.get("status") in ("completed", "failed"):
                    break
                await asyncio.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"knowledge ingest unavailable in this env: {exc}")
        if not status or status.get("status") != "completed":
            pytest.skip(f"ingest did not complete: {status}")

        result = await agent.invoke(
            "Using the knowledge base, what was ACME Corp's total revenue in "
            "fiscal year 2025? Cite the source.",
            thread_id=str(uuid.uuid4()),
        )
        answer = (getattr(result, "answer", None) or "")

        # INVARIANT 1 — no citation-shaped marker left unresolved (any bracket style).
        leftover = _UNRESOLVED_CITE_RE.search(answer)
        assert not leftover, (
            f"model emitted a citation marker that never resolved to a chip: "
            f"{leftover.group(0)!r} — the resolver doesn't handle this format. "
            f"Add the bracket to _MARKER_RE (sources.py)."
        )

        # INVARIANT 2 — if it rendered [n] chips, sources must back every one.
        chip_nums = {int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", answer)}
        if not chip_nums:
            pytest.skip("model did not cite the knowledge base this run (nondeterministic)")
        assert result.sources, "answer shows [n] chips but result.sources is empty"
        source_nums = {s["n"] for s in result.sources}
        assert chip_nums <= source_nums, (
            f"chips {sorted(chip_nums)} not all backed by sources {sorted(source_nums)}"
        )
        for s in result.sources:
            assert s.get("filename") and s.get("cite_id"), f"malformed source snapshot: {s}"
    finally:
        await agent.aclose()
