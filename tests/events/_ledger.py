"""The VERIFICATION LEDGER writer — harnesses record evidence; the page is generated from it.

Any harness that proves something calls::

    from _ledger import record
    record("box", "fire_real", "ok", "real upload → detected → judged")

Records land in `results/verification_data.json` keyed by (surface, capability) — newest wins — so
the ledger updates itself every time a live harness runs, and a cell's date is always the date it
was last PROVEN.

`results/` is gitignored: local evidence, not a committed artifact. The destination MUST be a
directory this repo actually has — the docs tree now lives outside the repository again, and writing
into a path the repo does not own made every record fail the `open()` below silently, leaving an
empty ledger with no error. The `makedirs` is what stops that recurring.
"""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "results", "verification_data.json")
os.makedirs(os.path.dirname(DATA), exist_ok=True)


def record(surface: str, capability: str, verdict: str, note: str = "", source: str = "") -> None:
    """verdict: 'ok' | 'partial' | 'blocked'. Never raises — evidence-keeping must not fail runs."""
    try:
        data = json.load(open(DATA)) if os.path.exists(DATA) else {"records": {}}
        src = source or os.path.basename(sys.argv[0] or "unknown")
        data["records"][f"{surface}::{capability}"] = {
            "surface": surface,
            "capability": capability,
            "verdict": verdict,
            "note": note,
            "source": src,
            "date": time.strftime("%Y-%m-%d %H:%M"),
        }
        json.dump(data, open(DATA, "w"), indent=1, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass
