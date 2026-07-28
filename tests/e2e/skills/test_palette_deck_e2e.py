"""End-to-end: markdown in, a real .pptx out, driven by the agent.

Nothing here is mocked. A real model runs the real CugaLite graph, loads the
real installed `palette` skill, and drives a real Palette server through its
CLI in a real sandbox. What comes back is a PowerPoint file on disk, which
these tests open and inspect.

Two routes in, matching how people actually arrive at a deck:

  * **A plan already exists** — markdown in Palette's plan format goes straight
    to Stage 2. This is the fast path (~4 min).
  * **Only source material exists** — unstructured notes go through Stage 1
    first, where the crafter imposes a narrative, then build (~6 min).

Both assert on the artifact, not on the transcript: the file must be a valid
OOXML package with the expected number of slide parts, and every slide must
have rendered to a PNG. A deck that "succeeded" with two slides or a 12KB
.pptx is a failure.

Requirements: credentials for the project's LLM, a reachable Palette server
with RITS_API_KEY set, and the palette skill installed. Each is checked and
skipped on rather than failing obscurely.

    palette-skill serve ensure          # start Palette
    uv run pytest tests/e2e/skills/test_palette_deck_e2e.py -m e2e -v -s

Set PALETTE_DECK_OUT to keep the decks somewhere you can open them:

    PALETTE_DECK_OUT=~/Desktop/palette-decks uv run pytest ... -m e2e -s
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from tests.e2e.skills.conftest import MinimalToolProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLED_SKILL = REPO_ROOT / ".cuga" / "skills" / "palette"
FIXTURES = Path(__file__).parent / "fixtures"
PALETTE_URL = os.environ.get("PALETTE_URL", "http://127.0.0.1:18814")

#: Where to keep the produced decks. Defaults to a temp dir; point it at
#: something durable to actually look at the output.
DECK_OUT = Path(os.environ.get("PALETTE_DECK_OUT", "/tmp/palette-decks")).expanduser()

#: Collected for the end-of-session summary table.
_RESULTS: list[dict] = []


def _palette_health() -> dict | None:
    try:
        with urllib.request.urlopen(f"{PALETTE_URL}/health", timeout=3) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


_HEALTH = _palette_health()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not (INSTALLED_SKILL / "SKILL.md").is_file(),
        reason="palette skill not installed — run `make skill-install CUGA=<repo>` in project-palette",
    ),
    pytest.mark.skipif(_HEALTH is None, reason=f"no Palette server at {PALETTE_URL}"),
    pytest.mark.skipif(
        bool(_HEALTH) and not _HEALTH.get("rits_key_set"),
        reason="Palette has no RITS_API_KEY — every build would fail at the first model call",
    ),
]


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@dataclass
class DeckResult:
    """What a run produced, for assertions and for the summary table."""

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
                1
                for n in archive.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )


def _configure(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, *, step_seconds: int | None = None
) -> None:
    """Settings a skills-enabled agent runs under, mirroring `demo_palette`.

    The preset's two departures from CUGA's defaults both exist because a deck
    is minutes of polling rather than a handful of calls, and both were paid
    for in failed runs:

      * a **120s step**, so each bounded poll covers real ground. At the 30s
        default a ten-minute build is twenty-odd polls, and agents abandon that
        around step 40 of 100 — with the build still running, finishing
        uncollected minutes later.
      * **auto-continue on**, so a progress note the model writes as prose does
        not read as a finished answer and end the run.

    ``step_seconds`` overrides the first for a test that wants something else.
    """
    from cuga.config import settings

    monkeypatch.chdir(workspace)
    # 120s mirrors `_apply_palette_supervisor_env`, and the skill's CUGA
    # profile polls at 100s to fit inside it. At the 30s default a ten-minute
    # build is twenty-odd polls and the agent abandons it around step 40.
    monkeypatch.setattr(
        settings.advanced_features, "sandbox_execution_timeout", step_seconds or 120
    )
    monkeypatch.setenv("CUGA_FOLDER", str(workspace / ".cuga"))
    monkeypatch.setenv("PALETTE_URL", PALETTE_URL)
    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    # Mirrors `_apply_palette_supervisor_env`. Left off, the first "still
    # rendering, I'll keep checking" the model emits ends the run mid-build.
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)
    monkeypatch.setattr(settings.policy, "enabled", False)


def _prepare_skill(tmp_path: Path) -> Path:
    """A project root holding the real installed skill, ready to be discovered."""
    shutil.copytree(INSTALLED_SKILL, tmp_path / ".cuga" / "skills" / "palette")
    return tmp_path


def _sandbox_workspace(thread_id: str) -> Path:
    """The directory the agent's `./` actually refers to.

    Not the process cwd: CUGA gives each thread its own workspace under
    ``<cwd>/cuga_workspace/``, and that is where run_command runs and where
    uploads have to be staged for the agent to see them. Resolved through
    CUGA's own helper so this cannot drift from the executor.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
        thread_workspace_root,
    )

    return thread_workspace_root(thread_id)


def _stage_uploads(thread_id: str, *sources: Path) -> Path:
    """Put the markdown where the agent will look for it."""
    uploads = _sandbox_workspace(thread_id) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.copy2(source, uploads / source.name)
    return uploads


async def _run_agent(prompt: str, thread_id: str, *, max_steps: int) -> str:
    """Drive the real graph with the project's real model; return the transcript.

    ``max_steps`` has to be generous. Each bounded poll is one step and advances
    only ~25s, so a four-minute build alone costs ten to twelve steps, a draft
    another four or five, and setup and delivery the rest. Budgets that look
    ample for a normal task starve this one just before the download.
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

    result = await graph.ainvoke(
        CugaLiteState(chat_messages=[HumanMessage(content=prompt)], thread_id=thread_id),
        config={
            "configurable": {
                "thread_id": thread_id,
                "apps_list": [],
                "cuga_lite_max_steps": max_steps,
            }
        },
    )
    messages = result.get("chat_messages", []) if isinstance(result, dict) else []
    return "\n".join(str(getattr(m, "content", "")) for m in messages)


def _collect(name: str, workspace: Path, transcript: str) -> DeckResult:
    """Find whatever the agent produced and copy it somewhere durable."""
    pptx_files = sorted(workspace.rglob("*.pptx"))
    slides = sorted(workspace.rglob("slide-*.png"))
    pptx = pptx_files[0] if pptx_files else None

    keep = DECK_OUT / name
    if pptx or slides:
        keep.mkdir(parents=True, exist_ok=True)
        if pptx:
            shutil.copy2(pptx, keep / "deck.pptx")
            pptx = keep / "deck.pptx"
        kept_slides = []
        for slide in slides:
            shutil.copy2(slide, keep / slide.name)
            kept_slides.append(keep / slide.name)
        slides = kept_slides

    outcome = DeckResult(name, workspace, pptx, slides, transcript)
    _RESULTS.append(
        {
            "name": name,
            "pptx": str(outcome.pptx) if outcome.pptx else "(none)",
            "slides": outcome.slide_parts,
            "previews": len(outcome.slides),
            "bytes": outcome.pptx.stat().st_size if outcome.pptx else 0,
        }
    )
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
    assert len(outcome.slides) >= min_slides, (
        f"{outcome.name}: {len(outcome.slides)} preview PNG(s) for "
        f"{outcome.slide_parts} slides — previews were not rendered"
    )
    for slide in outcome.slides:
        assert slide.stat().st_size > 5_000, f"{outcome.name}: {slide.name} is suspiciously small"
        assert slide.read_bytes().startswith(b"\x89PNG"), f"{outcome.name}: {slide.name} is not a PNG"


#: Palette's renderer forces IBM Plex (render.py's font safelist) and substitutes
#: Calibri for anything else the model asks for. A deck hand-written with
#: pptxgenjs defaults would carry Arial or Calibri and no Plex at all, so a
#: Plex-heavy file could only have come through Palette's renderer.
PALETTE_FONT_MARKER = "IBM Plex"


def _assert_came_from_palette(outcome: DeckResult) -> None:
    """Provenance from the artifact, which cannot be summarised away.

    The transcript is the obvious place to look for `load_skill("palette")`,
    but CUGA compacts long conversations — and a draft-then-build run is long
    enough to trigger it, which erases the very lines this would check. So the
    file itself carries the proof, and the transcript is only a bonus.
    """
    with zipfile.ZipFile(outcome.pptx) as archive:
        xml = b"".join(
            archive.read(n) for n in archive.namelist() if n.startswith("ppt/slides/slide")
        ).decode("utf-8", "replace")
    assert PALETTE_FONT_MARKER in xml, (
        f"{outcome.name}: the .pptx uses no IBM Plex typeface, so it did not come out of "
        "Palette's renderer — the agent built a deck some other way."
    )

    summarised = "summary of the conversation to date" in outcome.transcript
    if not summarised:
        assert (
            'load_skill("palette")' in outcome.transcript
            or "load_skill('palette')" in outcome.transcript
        ), "the agent built something without loading the palette skill"
        assert "palette-skill" in outcome.transcript, (
            "the skill was loaded but its CLI was never driven"
        )


def _assert_orchestrator_was_used(workspace: Path, name: str) -> None:
    """`palette-skill deck` leaves a state file; hand-managed sequences do not.

    This is the distinction that matters. An agent stitching draft/build calls
    together itself owns the thread id, the polling and the notion of "done" —
    and that is precisely the arrangement that produced a deck announcement
    with no deck behind it. ``run_deck`` owns all three instead, and records
    that it did in ``.palette-deck.json``. No file, no orchestrator.
    """
    states = sorted(workspace.rglob(".palette-deck.json"))
    assert states, (
        f"{name}: no .palette-deck.json anywhere — the agent hand-managed the "
        "draft/build sequence instead of running `palette-skill deck`, which is "
        "the exact arrangement that hallucinated a completed deck."
    )
    stage = json.loads(states[0].read_text()).get("stage")
    assert stage == "done", f"{name}: orchestrator stopped in stage {stage!r}, not 'done'"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deck_from_a_bare_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt that hallucinated, verbatim, with nothing added.

    Every other test here scaffolds: it names the stage, says "poll until the
    build finishes", sometimes even passes a ``--max-seconds`` value. Under
    that much instruction the agent cannot go wrong in the way it actually
    went wrong. This one says what a person says — draft, show me, build —
    and leaves the agent to work out the rest.

    It failed once already, announcing files that were never written while the
    server logs showed four drafts and zero builds. It runs at the default 30s
    step, like the demo, because the shortened step was part of the pressure.
    """
    _configure(monkeypatch, _prepare_skill(tmp_path))
    thread_id = f"deck_bare_{uuid.uuid4().hex[:8]}"

    transcript = await _run_agent(
        "Draft a plan for a Q3 sales review, show it to me, then build it.",
        thread_id,
        max_steps=100,
    )

    workspace = _sandbox_workspace(thread_id)
    outcome = _collect("bare_request", workspace, transcript)
    _assert_real_deck(outcome, min_slides=5)
    _assert_came_from_palette(outcome)
    _assert_orchestrator_was_used(workspace, outcome.name)


@pytest.mark.asyncio
async def test_deck_from_a_plan_markdown_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Markdown already in plan format goes straight to Stage 2."""
    source = FIXTURES / "plan_agent_skills.md"
    _configure(monkeypatch, _prepare_skill(tmp_path))
    thread_id = f"deck_plan_{uuid.uuid4().hex[:8]}"
    _stage_uploads(thread_id, source)

    transcript = await _run_agent(
        f"Build a slide deck from the plan in ./uploads/{source.name}. "
        "It is already in Palette's plan format, so do not draft a new plan — "
        "start the build directly from that file. Poll until the build finishes, "
        "then save the .pptx and all slide previews into ./deck/.",
        thread_id,
        max_steps=70,
    )

    outcome = _collect("from_plan_markdown", _sandbox_workspace(thread_id), transcript)
    _assert_real_deck(outcome, min_slides=5)
    _assert_came_from_palette(outcome)


@pytest.mark.asyncio
async def test_deck_from_unstructured_source_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw notes must go through Stage 1 before they can be built."""
    source = FIXTURES / "source_sandbox_notes.md"
    _configure(monkeypatch, _prepare_skill(tmp_path), step_seconds=120)
    thread_id = f"deck_notes_{uuid.uuid4().hex[:8]}"
    _stage_uploads(thread_id, source)

    transcript = await _run_agent(
        f"Turn the engineering notes in ./uploads/{source.name} into a slide deck for "
        "platform engineers. The file is raw notes, not a plan — draft a plan from it "
        "first, then build the deck. Save the .pptx and all slide previews into ./deck/. "
        "The step limit is raised for this task, so poll with --max-seconds 100 "
        "rather than 25 — you will need far fewer polls that way.",
        thread_id,
        max_steps=95,
    )

    outcome = _collect("from_source_notes", _sandbox_workspace(thread_id), transcript)
    _assert_real_deck(outcome, min_slides=5)
    _assert_came_from_palette(outcome)


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
        print(
            f"  {row['name']:<24} {row['slides']:>6} {row['previews']:>5} {size:>10}  {row['pptx']}"
        )
    print("=" * 78)
