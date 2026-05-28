"""Event-Driven CUGA docs explorer.

A small FastAPI server that serves a node-based UI for navigating the
event-driven design package. Run:

    python -m uvicorn app:app --reload --port 8765

Then open http://localhost:8765
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).parent.resolve()
DOCS = HERE.parent  # docs/

app = FastAPI(title="Event-Driven CUGA — Docs Explorer")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

# Serve images, GIFs, and SVGs from docs/ directly
@app.get("/asset/{path:path}")
def asset(path: str):
    target = (DOCS / path).resolve()
    # safety: must be under DOCS
    try:
        target.relative_to(DOCS)
    except ValueError:
        raise HTTPException(403, "outside docs/")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, str(target))
    return FileResponse(target)


@app.get("/api/doc/{name}")
def doc(name: str) -> PlainTextResponse:
    """Return raw markdown for a doc node."""
    target = (DOCS / name).resolve()
    try:
        target.relative_to(DOCS)
    except ValueError:
        raise HTTPException(403)
    if not target.exists():
        raise HTTPException(404, str(target))
    return PlainTextResponse(target.read_text(), media_type="text/markdown")


# ─────────────────────────── GRAPH DEFINITION ───────────────────────────
# Categories with colors (Cytoscape style tags)
CATEGORIES: dict[str, dict[str, str]] = {
    "entry":    {"color": "#C44A00", "label": "Entry point"},
    "deck":     {"color": "#7A5A00", "label": "Deck / overview"},
    "reference":{"color": "#0F3D6E", "label": "Reference"},
    "roadmap":  {"color": "#1F5E1F", "label": "Roadmap / plan"},
    "evolution":{"color": "#4B2A8A", "label": "Evolution (from-loops)"},
    "proposal": {"color": "#666666", "label": "Original proposal (historical)"},
    "diagram":  {"color": "#8A1F1F", "label": "Architecture diagram"},
    "flow":     {"color": "#8A4F00", "label": "Flow animation (GIF)"},
    "chart":    {"color": "#283593", "label": "Chart / Gantt"},
    "kafka":    {"color": "#5D4037", "label": "Kafka migration (M9)"},
    "rationale":{"color": "#00695C", "label": "Design decisions (why)"},
}

# Nodes — each entry: id, label, category, kind (md|image|gif), file
NODES: list[dict[str, Any]] = [
    # MD docs
    {"id": "readme",         "label": "README (start here)",          "cat": "entry",     "kind": "md",
     "file": "event_driven_README.md",
     "blurb": "Single-paragraph summary, reading paths, glossary."},
    {"id": "deck",           "label": "Deck (14 slides)",             "cat": "deck",      "kind": "md",
     "file": "event_driven_deck.md",
     "blurb": "The full narrative with diagrams inline — most self-contained."},
    {"id": "reference",      "label": "Reference — building blocks",  "cat": "reference", "kind": "md",
     "file": "event_driven_reference.md",
     "blurb": "First-class citizens, trigger taxonomy, setup-vs-runtime split."},
    {"id": "roadmap",        "label": "Roadmap — milestones",         "cat": "roadmap",   "kind": "md",
     "file": "event_driven_roadmap.md",
     "blurb": "M0–M9 with risk-retired-by-each-step framing."},
    {"id": "from_loops",     "label": "From Loops — code evolution",  "cat": "evolution", "kind": "md",
     "file": "event_driven_from_loops.md",
     "blurb": "What survives, what generalizes, what's deprecated. First-PR-ready."},
    {"id": "proposal",       "label": "Original proposal (historical)","cat": "proposal", "kind": "md",
     "file": "event_driven_agent_proposal.md",
     "blurb": "Older terminology (Supervisor/Router). Kept for context."},

    # Architecture & block diagrams
    {"id": "blocks",         "label": "Building blocks (no wiring)",  "cat": "diagram",   "kind": "image",
     "file": "event_driven_building_blocks.png",
     "blurb": "10 named primitives, grouped by role. Start-here for vocabulary."},
    {"id": "arch_full",      "label": "Full architecture",            "cat": "diagram",   "kind": "image",
     "file": "event_driven_full_architecture.png",
     "blurb": "Canonical architecture — producers, bus, consumers, registry, supervisor."},
    {"id": "setup_flow",     "label": "Setup + runtime (single-agent)","cat": "diagram",  "kind": "image",
     "file": "event_driven_setup_flow.png",
     "blurb": "Two-phase: setup turn writes registry; trigger later wakes the agent."},
    {"id": "multi_flow",     "label": "Setup + runtime (multi-agent)","cat": "diagram",   "kind": "image",
     "file": "event_driven_multi_agent_setup_flow.png",
     "blurb": "Scout + Critic collaboration through the bus, 19 numbered steps."},

    # Chart
    {"id": "roadmap_png",    "label": "Roadmap chart (use cases)",    "cat": "chart",     "kind": "image",
     "file": "event_driven_roadmap.png",
     "blurb": "Each utterance from events.md → milestone when it first works."},

    # Flow GIFs
    {"id": "flow_push",      "label": "Flow: PUSH (support email)",   "cat": "flow",      "kind": "gif",
     "file": "event_flow/flow_push_support_email.gif",
     "blurb": "9 frames. Support email → triage → Linear + reply."},
    {"id": "flow_pull",      "label": "Flow: PULL (changelog watch)", "cat": "flow",      "kind": "gif",
     "file": "event_flow/flow_pull_changelog_watch.gif",
     "blurb": "9 frames. Poller + state-diff; most ticks emit nothing."},
    {"id": "flow_timed",     "label": "Flow: TIMED (Monday HN digest)","cat": "flow",     "kind": "gif",
     "file": "event_flow/flow_timed_hn_monday_digest.gif",
     "blurb": "9 frames. Cron → CUGA fetches HN → composes → emits."},
    {"id": "flow_swarm",     "label": "Flow: SWARM (scout + critic)", "cat": "flow",      "kind": "gif",
     "file": "event_flow/flow_swarm_scout_critic.gif",
     "blurb": "10 frames. Two agents collaborating through the bus."},

    # Kafka migration (Phase 5 / M9)
    {"id": "kafka_doc",      "label": "Kafka migration guide",        "cat": "kafka",     "kind": "md",
     "file": "event_driven_kafka_migration.md",
     "blurb": "When to switch, the KafkaInbox adapter, schema discipline, op checklist, step-by-step cutover."},
    {"id": "kafka_arch",     "label": "Kafka deployment architecture","cat": "kafka",     "kind": "image",
     "file": "event_driven_kafka_architecture.png",
     "blurb": "M9 deployment: producers + Kafka topics + consumer-group worker pools + DLQ + observability."},

    # Design decisions / rationale
    {"id": "decisions",      "label": "Design decisions (why)",        "cat": "rationale", "kind": "md",
     "file": "event_driven_design_decisions.md",
     "blurb": "Why per-agent inboxes (not a shared queue), why direct addressing, why MCP isn't events, etc."},
]

# Edges show reading order & cross-reference. Three relation types:
# - reads_next: A is the obvious next read after B
# - references: A references B (illustrates, deep-dives, etc.)
# - alternative: alternative entry into similar territory
EDGES: list[dict[str, str]] = [
    # Primary reading order
    {"src": "readme", "dst": "deck",       "rel": "reads_next", "label": "deck path"},
    {"src": "readme", "dst": "reference",  "rel": "reads_next", "label": "eng path"},
    {"src": "readme", "dst": "from_loops", "rel": "reads_next", "label": "PR-ready"},
    {"src": "readme", "dst": "blocks",     "rel": "reads_next", "label": "vocab"},

    # Deck references diagrams + flows
    {"src": "deck", "dst": "arch_full",   "rel": "references", "label": "Slide 3"},
    {"src": "deck", "dst": "setup_flow",  "rel": "references", "label": "Slide 5"},
    {"src": "deck", "dst": "flow_push",   "rel": "references", "label": "Slide 7"},
    {"src": "deck", "dst": "flow_timed",  "rel": "references", "label": "Slide 8"},
    {"src": "deck", "dst": "roadmap_png", "rel": "references", "label": "Slide 12"},
    {"src": "deck", "dst": "multi_flow",  "rel": "references", "label": "swarm slide"},

    # Reference deepens building blocks
    {"src": "reference", "dst": "blocks",    "rel": "references"},
    {"src": "reference", "dst": "arch_full", "rel": "references"},

    # Roadmap references chart + from_loops for code-level detail
    {"src": "roadmap", "dst": "roadmap_png", "rel": "references"},
    {"src": "roadmap", "dst": "from_loops",  "rel": "references"},

    # From-loops references the diagrams that motivate it
    {"src": "from_loops", "dst": "arch_full",   "rel": "references"},
    {"src": "from_loops", "dst": "setup_flow",  "rel": "references"},

    # Proposal is referenced by the newer narrative docs
    {"src": "deck",      "dst": "proposal", "rel": "alternative"},
    {"src": "from_loops","dst": "proposal", "rel": "alternative"},

    # Flow gifs cross-link (alternative scenarios)
    {"src": "flow_push", "dst": "flow_timed", "rel": "alternative"},
    {"src": "flow_push", "dst": "flow_pull",  "rel": "alternative"},
    {"src": "flow_push", "dst": "flow_swarm", "rel": "alternative"},

    # Kafka — reached from roadmap (Phase 5) and from-loops
    {"src": "roadmap",   "dst": "kafka_doc",  "rel": "reads_next", "label": "Phase 5"},
    {"src": "kafka_doc", "dst": "kafka_arch", "rel": "references"},
    {"src": "kafka_doc", "dst": "arch_full",  "rel": "references", "label": "before-Kafka"},
    {"src": "from_loops","dst": "kafka_doc",  "rel": "alternative"},

    # Design decisions reachable from reference + readme
    {"src": "reference", "dst": "decisions",  "rel": "reads_next", "label": "the why"},
    {"src": "readme",    "dst": "decisions",  "rel": "references", "label": "rationale"},
    {"src": "decisions", "dst": "kafka_doc",  "rel": "references"},
]


# Map flow gifs → their per-frame folder so the UI can drive a custom player
FLOW_FRAMES = {
    "flow_push":  "event_flow/push",
    "flow_pull":  "event_flow/pull",
    "flow_timed": "event_flow/timed",
    "flow_swarm": "event_flow/swarm",
}


@app.get("/api/graph")
def graph() -> dict:
    enriched = []
    for n in NODES:
        path = (DOCS / n["file"]).resolve()
        extras = {"abs_path": str(path)}
        # If this is a flow gif AND we have per-frame PNGs, list them so the
        # UI can use a scrubbable PNG-sequence player instead of a raw GIF.
        if n["id"] in FLOW_FRAMES:
            frame_dir = DOCS / FLOW_FRAMES[n["id"]]
            if frame_dir.is_dir():
                frames = sorted(frame_dir.glob("frame_*.png"))
                if frames:
                    extras["frames"] = [
                        f"{FLOW_FRAMES[n['id']]}/{f.name}" for f in frames
                    ]
        enriched.append({**n, **extras})
    return {"nodes": enriched, "edges": EDGES, "categories": CATEGORIES}


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")
