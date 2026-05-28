# Event-Driven CUGA — Docs Explorer

A small FastAPI app that gives you a **node-based** view of the design
package: every doc and diagram is a node, edges show the reading order, and
clicking a node renders it in a panel beside the graph.

## Run it

```bash
# One-time deps (if you don't already have them)
pip install fastapi uvicorn

# Launch
cd docs/explorer
./run.sh
# or: python3 -m uvicorn app:app --port 8765 --reload

# Open
open http://localhost:8765
```

Defaults to port `8765`. Override with `PORT=8000 ./run.sh`.

## What you'll see

- **Left:** an interactive graph of every doc + diagram in `docs/`.
  - Solid orange edges = "the obvious next read".
  - Dashed grey edges = cross-references (diagrams referenced by a doc).
  - Dotted edges = alternative entry points.
  - **Rectangles** are markdown docs; **diamonds** are images/GIFs.
  - Colors match the categories shown in the header legend.

- **Right:** the rendered content of whichever node you click.
  - Markdown is rendered with images inlined.
  - Images and GIFs are shown full-width; click to enlarge.

## Suggested starting points

| Node | Why |
|---|---|
| README | One-paragraph summary + glossary + reading paths |
| Building blocks | Vocabulary diagram, no flow wiring |
| Deck | Full 14-slide narrative — most self-contained doc |
| Setup + runtime (single-agent) | "What actually happens" diagram |
| Setup + runtime (multi-agent) | Same shape, for the scout+critic case |

## Layout

```
docs/explorer/
├── app.py              FastAPI server
├── run.sh              launcher
├── README.md           this file
└── static/
    ├── index.html
    ├── style.css
    └── app.js          cytoscape graph + markdown renderer
```

Edit the `NODES` and `EDGES` arrays in [app.py](app.py) to add or rewire docs.

## Talking-point flow

If you're presenting this live, walk the graph in this order:

1. **README** — set context, name the package.
2. **Building blocks** — names the 10 primitives (no wiring, fast to absorb).
3. **Full architecture** — same primitives, now wired up. Point out the
   Routing Agent (intelligent, setup-time) vs. Dispatcher (mechanical, runtime).
4. **Setup + runtime (single-agent)** — show one rule's lifecycle.
5. **Push flow GIF** — walk through CUGA's 6 stages on a real example.
6. **Setup + runtime (multi-agent)** — same shape with two agents collaborating.
7. **Roadmap** — five phases, ~3 months to demo every events.md scenario.
8. **From Loops** — only if engineering depth is wanted.
