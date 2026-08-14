#!/usr/bin/env python
"""One-shot conversion: the seeded worker fleet → the canonical supervisor roster (CUGA-main's
supervisor schema, consumed by load_supervisor_config).

    python events/scripts/gen_supervisor_roster.py   # writes events/examples/rosters/default.yaml

After conversion the YAML is the SOURCE OF TRUTH for sub-agents (edit it by hand; `make reload`
rebuilds). This script stays only to re-derive the file during the transition — it is NOT a
runtime dependency.

NO trigger hints are emitted into the prompts. An earlier version appended "HANDLES TRIGGERS: …"
lines to every sub-agent's instructions, on the theory that the supervisor read them when routing.
It does not: the routing prompt renders each sub-agent as `{{ agent['description'] }}`, and
CugaAgent has no `description`, so every entry falls back to "Internal agent: <name>". The hints
only ever reached the sub-agent's OWN prompt — read after routing had already chosen it — while
costing ~50% of the roster's prompt text. Trigger ownership lives in the structured
`integrations[].triggers` on each AgentSpec (events/seed.py), which is machine-readable and is what
the tests assert against.
(Design notes live in the events_channels_docs repo.)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "events" / "examples" / "rosters" / "default.yaml"

SUPERVISOR_INSTRUCTIONS = """\
You are CUGA. You coordinate specialist sub-agents. For EVERY task — even one that looks trivial,
like summarizing a short email — delegate to exactly ONE best-suited sub-agent and return its
answer. NEVER answer the task yourself: attribution and auditability depend on a specialist
handling it. Event payloads are prefixed with their trigger, e.g. [github/new_pr] or
[gmail/new_email]: pick the sub-agent whose role covers that source. For plain
questions, pick by domain.
"""


def main() -> int:
    from cuga.backend.events import seed

    agents = seed.default_agents()
    blocks = [
        "# GENERATED once by events/scripts/gen_supervisor_roster.py — now the SOURCE OF TRUTH.",
        "# Edit by hand; `make reload` rebuilds the supervisor.",
        "",
        "supervisor:",
        "  name: cuga",
        "  special_instructions: |",
        *(f"    {ln}" for ln in SUPERVISOR_INSTRUCTIONS.splitlines()),
        "",
        "agents:",
    ]
    for a in agents:
        if a.name == "concierge":
            continue
        body = (a.prompt or "").strip()
        blocks.append(f"  - name: {a.name}")
        blocks.append("    special_instructions: |")
        blocks.extend(f"      {ln}" for ln in body.splitlines())
        if a.mcp_servers:
            blocks.append("    mcp_servers:")
            blocks.extend(f"      - name: {m}" for m in a.mcp_servers)
        blocks.append("")
    OUT.write_text("\n".join(blocks))
    n = sum(1 for b in blocks if b.startswith("  - name:"))
    print(f"✓ wrote {OUT.name} — supervisor 'cuga' + {n} sub-agents (canonical schema)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
