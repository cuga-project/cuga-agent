"""The VERIFICATION LEDGER writer — harnesses record evidence; the page is generated from it.

Any harness that proves something calls::

    from _ledger import record
    record("box", "fire_real", "ok", "real upload → detected → judged")

Records land in events/docs/verification_data.json keyed by (surface, capability) — newest wins —
and events/scripts/gen_ledger.py renders events/docs/verification.html from them. So the ledger updates
itself every time a test runs, and a cell's date is always the date it was last PROVEN.

The destination has to be a directory this repo actually has. On the 602 branch the docs tree lived
outside the repository, so every record silently failed the `open()` below and the ledger recorded
nothing at all — no error, just an empty ledger. `events/docs/` is back here, so the path is valid
again, and the `makedirs` beneath is what keeps the failure from being silent a second time.
"""

from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "events/docs", "verification_data.json")
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
