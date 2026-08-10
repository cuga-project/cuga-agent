"""End-to-end: a request in, a real .pptx out, driven by the agent.

Nothing here is mocked. A real model runs the real CugaLite graph, loads the
real installed `palette` skill, and drives `palette.py` in a real sandbox.
What comes back is a PowerPoint file on disk, which these tests open and
inspect.

Requirements, each checked and skipped on rather than failing obscurely:

  * a Palette checkout at ``$PALETTE_HOME`` (the skill shells out to it)
  * ``RITS_API_KEY`` in this process's environment — the sandbox inherits it
  * credentials for the project's LLM

    export PALETTE_HOME=~/Documents/GitHub/project-palette-july25
    export RITS_API_KEY=...
    uv run pytest tests/e2e/skills/test_palette_deck_e2e.py -m e2e -v -s

Set ``PALETTE_DECK_OUT`` to keep the decks somewhere you can open them.

These assert on the **artifact**, not the transcript: a long agent run gets
context-summarised, which erases the very lines a transcript check would look
for. A deck that "succeeded" with two slides or a 12 KB .pptx is a failure.
"""

from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from tests.e2e.skills.conftest import MinimalToolProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLED_SKILL = REPO_ROOT / ".cuga" / "skills" / "palette"
PALETTE_HOME = Path(os.environ.get("PALETTE_HOME", REPO_ROOT.parent / "project-palette-july25"))

#: Where to keep the produced decks. Point it somewhere durable to look at them.
DECK_OUT = Path(os.environ.get("PALETTE_DECK_OUT", "/tmp/palette-decks")).expanduser()

#: Collected for the end-of-session summary table.
_RESULTS: list[dict] = []

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (INSTALLED_SKILL / "SKILL.md").is_file(),
        reason="palette skill not installed — run `make skill-install CUGA=<repo>` in project-palette",
    ),
    pytest.mark.skipif(
        not (PALETTE_HOME / "palette.py").is_file(),
        reason=f"no Palette checkout at {PALETTE_HOME}; set PALETTE_HOME",
    ),
    pytest.mark.skipif(
        not os.environ.get("RITS_API_KEY", "").strip(),
        reason="RITS_API_KEY is not exported; the sandbox inherits this process's env",
    ),
]


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@dataclass
class DeckResult:
    name: str
    workspace: Path
    pptx: Path | None
    slides: list[Path]
    transcript: str

    @property
    def slide_parts(self) -> int:
        """Slide XML parts inside the .pptx — the real slide count."""
        if not self.pptx or not self.pptx.is_file():
            return 0
        with zipfile.ZipFile(self.pptx) as archive:
            return sum(
                1 for n in archive.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )


def _configure(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    """Settings a skills-enabled agent runs under, mirroring `demo_palette`.

    The two departures from CUGA's defaults both exist because a deck is
    minutes of work rather than a handful of calls:

      * a **120s step**, because drafting a plan is one blocking model call of
        about a minute and the 30s default cannot hold it. (The *build* never
        blocks a step — the skill starts it detached.)
      * **auto-continue on**, so a progress note the model writes as prose does
        not read as a finished answer and end the run.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
    from cuga.config import settings

    # The Seatbelt policy pins <cwd>/cuga_workspace as the only writable tree
    # and the executor caches it. Each test has its own tmp_path, so a cached
    # executor denies every write and the sandbox exits 1.
    CodeExecutor._native_executor = None

    monkeypatch.chdir(workspace)
    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 120)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)
    monkeypatch.setenv("CUGA_FOLDER", str(workspace / ".cuga"))
    monkeypatch.setenv("PALETTE_HOME", str(PALETTE_HOME))
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.policy, "enabled", False)


def _prepare_skill(tmp_path: Path) -> Path:
    """A project root holding the real installed skill, ready to be discovered."""
    shutil.copytree(INSTALLED_SKILL, tmp_path / ".cuga" / "skills" / "palette")
    return tmp_path


def _sandbox_workspace(thread_id: str) -> Path:
    """The directory the agent's `./` actually refers to.

    Not the process cwd: CUGA gives each thread its own workspace under
    ``<cwd>/cuga_workspace/``. Resolved through CUGA's own helper so this
    cannot drift from the executor.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
        thread_workspace_root,
    )

    return thread_workspace_root(thread_id)


async def _run_agent(prompt: str, thread_id: str, *, max_steps: int) -> str:
    """One turn. Returns the transcript."""
    return (await _converse([prompt], thread_id, max_steps=max_steps))[1]


async def _converse(turns: list[str], thread_id: str, *, max_steps: int) -> tuple[list, str]:
    """Drive the real graph across several user turns on one thread.

    A deck needs two turns by design: the skill makes the confirmation gate
    mandatory, so the agent presents the plan and stops. A single-turn harness
    can never produce a deck, and a test built that way measures nothing.

    `chat_messages` has no `add_messages` reducer, so a second `ainvoke` would
    *replace* the history rather than append. The prior turn's messages are
    therefore passed back explicitly.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
        CugaLiteState,
        create_cuga_lite_graph,
    )
    from cuga.backend.llm.models import LLMManager
    from cuga.config import settings

    model = LLMManager().get_model(settings.agent.code.model)
    graph = create_cuga_lite_graph(
        model=model, tool_provider=MinimalToolProvider(), apps_list=[]
    ).compile()

    history: list = []
    transcript_parts: list[str] = []
    for turn in turns:
        result = await graph.ainvoke(
            CugaLiteState(
                chat_messages=[*history, HumanMessage(content=turn)], thread_id=thread_id
            ),
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "apps_list": [],
                    "cuga_lite_max_steps": max_steps,
                }
            },
        )
        history = result.get("chat_messages", []) if isinstance(result, dict) else []
        transcript_parts.append(
            "\n".join(str(getattr(m, "content", "")) for m in history)
        )
    return history, "\n".join(transcript_parts)


def _collect(name: str, workspace: Path, transcript: str) -> DeckResult:
    """Find whatever the agent produced and copy it somewhere durable."""
    pptx_files = sorted(workspace.rglob("*.pptx"))
    slides = sorted(workspace.rglob("*.png"))
    pptx = pptx_files[0] if pptx_files else None

    keep = DECK_OUT / name
    if pptx or slides:
        keep.mkdir(parents=True, exist_ok=True)
        if pptx:
            shutil.copy2(pptx, keep / "deck.pptx")
            pptx = keep / "deck.pptx"
        slides = [shutil.copy2(s, keep / s.name) and keep / s.name for s in slides]

    outcome = DeckResult(name, workspace, pptx, slides, transcript)
    _RESULTS.append({
        "name": name,
        "pptx": str(outcome.pptx) if outcome.pptx else "(none)",
        "slides": outcome.slide_parts,
        "previews": len(outcome.slides),
        "bytes": outcome.pptx.stat().st_size if outcome.pptx else 0,
    })
    return outcome


def _assert_real_deck(outcome: DeckResult, *, min_slides: int) -> None:
    """A deck is only real if the file opens and has the slides it claims."""
    assert outcome.pptx is not None, (
        f"{outcome.name}: no .pptx anywhere under the workspace.\n"
        f"Transcript tail:\n{outcome.transcript[-2000:]}"
    )
    assert zipfile.is_zipfile(outcome.pptx), f"{outcome.name}: .pptx is not a valid OOXML package"

    with zipfile.ZipFile(outcome.pptx) as archive:
        names = archive.namelist()
    assert "ppt/presentation.xml" in names, f"{outcome.name}: missing presentation part"

    assert outcome.slide_parts >= min_slides, (
        f"{outcome.name}: only {outcome.slide_parts} slide part(s), expected >= {min_slides}. "
        "A near-empty deck means the build failed part-way and was reported as success."
    )
    assert outcome.pptx.stat().st_size > 20_000, (
        f"{outcome.name}: .pptx is {outcome.pptx.stat().st_size} bytes — too small to hold real slides"
    )


#: Palette's renderer forces IBM Plex and substitutes Calibri for anything else,
#: so a Plex-heavy file could only have come through it. A deck hand-written
#: with pptxgenjs defaults would carry Arial or Calibri and no Plex at all.
PALETTE_FONT_MARKER = "IBM Plex"


def _assert_came_from_palette(outcome: DeckResult) -> None:
    """Provenance from the artifact, which cannot be summarised away."""
    with zipfile.ZipFile(outcome.pptx) as archive:
        xml = b"".join(
            archive.read(n) for n in archive.namelist() if n.startswith("ppt/slides/slide")
        ).decode("utf-8", "replace")
    assert PALETTE_FONT_MARKER in xml, (
        f"{outcome.name}: the .pptx uses no IBM Plex typeface, so it did not come out of "
        "Palette's renderer — the agent built a deck some other way."
    )


def _assert_the_plan_was_shown_first(outcome: DeckResult) -> None:
    """The confirmation gate is the workflow, not a nicety.

    A plan is cheap to change and a render costs minutes, so the skill requires
    the user to approve before building. The plan file on disk is the evidence
    that step happened at all.
    """
    plans = list(outcome.workspace.rglob("plan.md"))
    assert plans, (
        f"{outcome.name}: no plan.md anywhere — the agent went straight to build-deck, "
        "skipping the confirmation gate the skill makes mandatory."
    )
    assert plans[0].stat().st_size > 200, f"{outcome.name}: plan.md is too short to be a real plan"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deck_from_a_bare_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What a person actually types, over the two turns a deck actually takes.

    Deliberately unscaffolded: neither turn names a command, mentions the plan
    step, or says how to poll. Every skill bug found so far came from a prompt
    shaped like this, and none was caught by a unit test.

    Two turns because the skill makes the confirmation gate mandatory — the
    agent presents the plan and stops, which is correct. A single-turn version
    of this test failed for exactly that reason and was measuring the harness,
    not the skill.
    """
    _configure(monkeypatch, _prepare_skill(tmp_path))
    thread_id = f"deck_bare_{uuid.uuid4().hex[:8]}"

    _, transcript = await _converse(
        [
            "Build me a deck about vector databases for backend engineers.",
            "The plan looks good. Go ahead and build the deck.",
        ],
        thread_id,
        max_steps=100,
    )

    workspace = _sandbox_workspace(thread_id)
    outcome = _collect("bare_request", workspace, transcript)
    _assert_the_plan_was_shown_first(outcome)
    _assert_real_deck(outcome, min_slides=3)
    _assert_came_from_palette(outcome)


@pytest.mark.asyncio
async def test_deck_from_pasted_material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Material pasted into the chat must reach `--context`, not the request.

    This is the path for hosts with no file upload. Passed as part of the
    request the crafter treats it as instructions; passed via `--context` it is
    grounded source material and shaped into slides.
    """
    _configure(monkeypatch, _prepare_skill(tmp_path))
    thread_id = f"deck_ctx_{uuid.uuid4().hex[:8]}"

    material = (
        "Q3 platform notes.\n"
        "- Ingest latency p99 fell from 840ms to 310ms after the batching change.\n"
        "- Two regions still run the legacy path: eu-central and ap-south.\n"
        "- Storage cost per TB dropped 22% following the tiering rollout.\n"
        "- Open risk: the replay tool has no back-pressure and has twice caused "
        "  a queue backlog during incident recovery.\n"
    )

    _, transcript = await _converse(
        [
            "Turn these notes into a 4-slide deck for platform engineers.\n\n" + material,
            "Looks right. Build it.",
        ],
        thread_id,
        max_steps=100,
    )

    workspace = _sandbox_workspace(thread_id)
    outcome = _collect("pasted_material", workspace, transcript)
    _assert_the_plan_was_shown_first(outcome)
    _assert_real_deck(outcome, min_slides=3)
    _assert_came_from_palette(outcome)

    plan = next(iter(workspace.rglob("plan.md"))).read_text(errors="replace").lower()
    assert any(token in plan for token in ("latency", "310", "tiering", "replay")), (
        "the plan mentions nothing from the pasted notes — the material was not "
        f"grounded into it.\nPlan:\n{plan[:800]}"
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    """Print where the decks landed so they can actually be looked at."""
    if not _RESULTS:
        return
    print("\n" + "=" * 78)
    print("  PALETTE DECKS PRODUCED")
    print("=" * 78)
    print(f"  {'Run':<24} {'Slides':>6} {'PNGs':>5} {'Size':>10}  Path")
    for row in _RESULTS:
        size = f"{row['bytes']:,}" if row["bytes"] else "-"
        print(f"  {row['name']:<24} {row['slides']:>6} {row['previews']:>5} {size:>10}  {row['pptx']}")
    print("=" * 78)
