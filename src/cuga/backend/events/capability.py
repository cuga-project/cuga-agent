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


def report(remote_agents: list[str] | None = None) -> list[str]:
    """Return the capability lines (also usable by a /api/events/status caller or a test).

    ``remote_agents`` is the roster CUGA reports when the eventing layer runs SPLIT OUT — pass it
    and the supervisor line describes what will actually execute, not this process's own (absent)
    configuration.
    """
    lines: list[str] = []
    ok = lambda s: lines.append(f"  ✓ {s}")          # noqa: E731
    no = lambda s: lines.append(f"  ✗ {s}")          # noqa: E731

    _tg_direct = os.environ.get("EVENTS_TELEGRAM_BACKEND", "direct").split(" #", 1)[0].strip() != "ap"
    ok("web chat · webhooks (/api/events/hook/…) · direct watchers (Slack/Discord/Box-direct"
       + (" · Telegram-direct" if _tg_direct else "") + ") — no extra infra"
       + (" (Telegram chat runs AP-free via long-poll)" if _tg_direct else ""))

    sup = os.environ.get("EVENTS_SUPERVISOR", "").split(" #", 1)[0].strip() in ("1", "true", "yes")
    roster = (os.environ.get("EVENTS_SUPERVISOR_ROSTER", "").strip()
              or os.path.join(os.getcwd(), "supervisor_agents.yaml"))
    # SPLIT: execution — and therefore the roster — lives on the CUGA side. Reading THIS process's
    # EVENTS_SUPERVISOR here reported "supervisor: OFF — one plain CUGA agent" while CUGA was
    # serving nine specialists. Ask the side that actually runs them.
    if remote_agents is not None:
        n = len([a for a in remote_agents if a != "cuga"])
        (ok if n else no)(
            f"supervisor: ON (on CUGA, over /run) — {n} sub-agent(s): {', '.join(remote_agents[:6])}"
            f"{'…' if len(remote_agents) > 6 else ''}"
            if n else "supervisor: OFF on CUGA — one plain agent (set CUGA_SUPERVISOR_ROSTER there)")
    elif sup:
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

    # Native scheduler: cron/poll run in-process (no AP) unless EVENTS_SCHEDULER=ap.
    _native_sched = os.environ.get("EVENTS_SCHEDULER", "native").split(" #", 1)[0].strip().lower() != "ap"
    if _native_sched:
        ok("native scheduler ON — cron/poll run in-process (no AP needed); AP is used only for "
           "integration (piece) triggers")

    ap = os.environ.get("AP_BASE_URL", "").rstrip("/")
    if ap and _reachable(f"{ap}/api/v1/flags"):
        ok(f"Activepieces reachable ({ap}) — Gmail/GitHub/Box-AP integration triggers available"
           + ("" if _native_sched else " + cron/poll via AP schedule"))
    else:
        no("Activepieces not reachable → AP-backed integration triggers (Gmail/GitHub/Box push) "
           "unavailable"
           + ("  [cron/poll still work — native scheduler]" if _native_sched
              else " and cron/poll unavailable (EVENTS_SCHEDULER=ap)")
           + "  (start it: `make up`)")

    # Telegram-direct (long-poll) is OUTBOUND, so it does NOT need a public URL — only the AP
    # webhook backend (EVENTS_TELEGRAM_BACKEND=ap) does. Keep the message honest about that.
    _tg_note = "" if _tg_direct else " / Telegram webhook"
    pub = os.environ.get("EVENTS_PUBLIC_URL", "").strip()
    # EVENTS_NO_TUNNEL (set by events_up.sh --no-tunnel) means a URL may be CONFIGURED in .env but
    # nothing is forwarding it — don't claim Slack/OAuth can reach us. This is the "up-noap" state.
    no_tunnel = os.environ.get("EVENTS_NO_TUNNEL", "").split(" #", 1)[0].strip() in ("1", "true", "yes")
    if pub and not no_tunnel:
        ok(f"public URL set ({pub}) — Slack events / OAuth callbacks{_tg_note} can reach you")
    elif pub and no_tunnel:
        no(f"public URL is CONFIGURED ({pub}) but NO tunnel is running → Slack events, "
           f"OAuth callbacks{_tg_note} won't arrive. Start one: `make up-noap-slack` (no AP + tunnel) "
           f"or `make up` (full stack)."
           + ("  [web · Telegram · Discord chat work regardless — direct/outbound]" if _tg_direct else ""))
    else:
        no(f"no EVENTS_PUBLIC_URL → Slack events, OAuth callbacks{_tg_note} unreachable "
           "(`make tunnels`, then `make channels`)"
           + ("  [Telegram chat still works — it's direct/outbound]" if _tg_direct else ""))

    return lines


def log_report(logger) -> None:
    # WARNING level on purpose: this is a startup banner the operator must SEE (uvicorn runs at
    # log_level=warning in the events launcher, filtering INFO). Nothing here is an error.
    logger.warning("events layer ENABLED — capability report:")
    for ln in report():
        logger.warning(ln)
