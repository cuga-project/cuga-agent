#!/usr/bin/env python
"""Regenerate the `const EX = [...]` block in events_docs/api/examples.html from the ONE source of
truth, src/cuga/backend/events/catalog.py. The surrounding HTML/CSS/JS is hand-maintained; only the
data array is spliced in, so the doc board can never drift from the Studio Examples tab again.

    python scripts/gen_examples.py            # rewrite in place
    python scripts/gen_examples.py --check    # exit 1 if out of date (used by the consistency test)

The catalog carries extra keys (`outcome`, `star`) the board also uses; we emit the full record so the
board and the Studio stay byte-identical on the shared fields.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "events_docs" / "api" / "examples.html"
# keys the board renders, in a stable order → deterministic diff
KEYS = ["id", "title", "trigger", "outcome", "utterance", "agent", "channel",
        "integration", "phase", "live", "note", "star", "ap_trigger", "feasibility", "needs"]
MARKER = re.compile(r"const EX = (\[.*?\]);", re.S)


def _catalog():
    sys.path.insert(0, str(ROOT))
    from src.cuga.backend.events import catalog  # noqa: E402
    return [{k: e[k] for k in KEYS} for e in catalog.EXAMPLES]


def _block(rows) -> str:
    return "const EX = " + json.dumps(rows, ensure_ascii=False) + ";"


def main() -> int:
    check = "--check" in sys.argv
    html = HTML.read_text()
    m = MARKER.search(html)
    if not m:
        print("!! could not find `const EX = [...]` in examples.html", file=sys.stderr)
        return 2
    fresh = _block(_catalog())
    if m.group(0).strip() == fresh.strip():
        print(f"✓ examples.html up to date ({len(_catalog())} examples)")
        return 0
    if check:
        print("✗ examples.html is stale — run: python scripts/gen_examples.py", file=sys.stderr)
        return 1
    HTML.write_text(html[:m.start()] + fresh + html[m.end():])
    print(f"✓ wrote examples.html — {len(_catalog())} examples from catalog.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
