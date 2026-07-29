"""The events CAPABILITY REPORT — printed at startup so ``cuga start … --events`` tells you exactly
what is live and what still needs infrastructure, each with its one-line fix.

The event layer is TIERED: some capabilities need nothing beyond this process (web chat, webhooks,
direct Slack/Discord watchers), others need Activepieces (cron/poll + AP-backed integration
triggers) or a public URL (Slack events, OAuth callbacks, Telegram). Rather than silently pretend,
we probe and report — the same honesty the harnesses use (ARMED ≠ works).

Stdlib only; every probe is best-effort and fast (never blocks startup)."""

from __future__ import annotations

import os
import urllib.error
import urllib.request


def _reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True                          # a 4xx still means "something answered"
    except Exception:  # noqa: BLE001
        return False


def report() -> list[str]:
    """Return the capability lines (also usable by a /api/events/status caller or a test)."""
    lines: list[str] = []
    ok = lambda s: lines.append(f"  ✓ {s}")          # noqa: E731
    no = lambda s: lines.append(f"  ✗ {s}")          # noqa: E731

    ok("web chat · webhooks (/api/events/hook/…) · direct watchers (Slack/Discord/Box-direct) "
       "— no extra infra")

    sup = os.environ.get("EVENTS_SUPERVISOR", "").split(" #", 1)[0].strip() in ("1", "true", "yes")
    roster = (os.environ.get("EVENTS_SUPERVISOR_ROSTER", "").strip()
              or os.path.join(os.getcwd(), "supervisor_agents.yaml"))
    if sup:
        n = 0
        try:
            import yaml
            n = len((yaml.safe_load(open(roster)) or {}).get("agents") or [])
        except Exception:  # noqa: BLE001
            pass
        (ok if n else no)(
            f"supervisor: ON — {n} sub-agent(s) from {os.path.basename(roster)}"
            if n else f"supervisor: ON but roster {roster} missing/empty "
                      f"(set EVENTS_SUPERVISOR_ROSTER or add supervisor_agents.yaml)")
    else:
        ok("supervisor: OFF — one plain CUGA agent (set EVENTS_SUPERVISOR=1 + a roster for specialists)")

    ap = os.environ.get("AP_BASE_URL", "").rstrip("/")
    if ap and _reachable(f"{ap}/api/v1/flags"):
        ok(f"Activepieces reachable ({ap}) — cron/poll + Gmail/GitHub/Box-AP triggers available")
    else:
        no("Activepieces not reachable → cron/poll + AP-backed integration triggers unavailable "
           "(start it: `make ap`, or the full stack: `make up`)")

    pub = os.environ.get("EVENTS_PUBLIC_URL", "").strip()
    if pub:
        ok(f"public URL set ({pub}) — Slack events / OAuth callbacks / Telegram can reach you")
    else:
        no("no EVENTS_PUBLIC_URL → Slack events, OAuth callbacks, Telegram webhooks unreachable "
           "(`make tunnels`, then `make channels`)")

    return lines


def log_report(logger) -> None:
    # WARNING level on purpose: this is a startup banner the operator must SEE (uvicorn runs at
    # log_level=warning in the events launcher, filtering INFO). Nothing here is an error.
    logger.warning("events layer ENABLED — capability report:")
    for ln in report():
        logger.warning(ln)
