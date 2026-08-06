#!/usr/bin/env python
"""One-shot conversion: the seeded worker fleet → supervisor_agents.yaml (CUGA-main's CANONICAL
supervisor schema, consumed by load_supervisor_config).

    python scripts/gen_supervisor_roster.py            # writes ./supervisor_agents.yaml

After conversion the YAML is the SOURCE OF TRUTH for sub-agents (edit it by hand; `make reload`
rebuilds). This script stays only to re-derive the file during the transition — it is NOT a
runtime dependency. Trigger HANDLES hints are generated from the registry so the supervisor's
routing descriptions can never drift from the triggers that actually exist.
(Plan: events_docs/plans/SUPERVISOR_REFACTOR.md, Phase 1.)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
OUT = ROOT / "supervisor_agents.yaml"

SUPERVISOR_INSTRUCTIONS = """\
You are CUGA. You coordinate specialist sub-agents. For EVERY task — even one that looks trivial,
like summarizing a short email — delegate to exactly ONE best-suited sub-agent and return its
answer. NEVER answer the task yourself: attribution and auditability depend on a specialist
handling it. Event payloads are prefixed with their trigger, e.g. [github/new_pr] or
[gmail/new_email]: pick the sub-agent whose HANDLES line declares that trigger. For plain
questions, pick by domain.
"""


def _handles(spec, tr) -> str:
    """The HANDLES routing hint, derived from the registry — never hand-written."""
    lines = []
    for integ in spec.integrations or []:
        app = integ.get("app", "")
        declared = integ.get("triggers")
        rows = [t for t in tr.events_for(app) if (not declared or t.event in declared)]
        if rows:
            lines.append("HANDLES TRIGGERS: " + ", ".join(f"{t.app}/{t.event} ({t.title})" for t in rows))
    return "\n".join(lines)


def main() -> int:
    from cuga.backend.events import seed, triggers as tr

    agents = seed.default_agents()
    blocks = [
        "# GENERATED once by scripts/gen_supervisor_roster.py — now the SOURCE OF TRUTH.",
        "# Edit by hand; `make reload` rebuilds the supervisor. HANDLES lines mirror the",
        "# trigger registry (events/triggers.py); keep them in sync when editing triggers.",
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
        instr = (a.prompt or "").strip()
        hints = _handles(a, tr)
        body = instr + ("\n" + hints if hints else "")
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
