"""LIVE matrix — every TRIGGER MODE against every CHANNEL SINK and every INTEGRATION SOURCE.

`live_e2e.py` proves each surface works once. This proves the whole grid is *plumbed*: for each
(mode × sink) and (integration × sink) it drives the real arming path and records what came back.

    rows:  NOW · CRON · POLL · PUSH(box) · PUSH(github) · PUSH(gmail) · WEBHOOK
    cols:  web · slack · discord · telegram          ← the sink the flow delivers to

HOW THE SINK IS CHOSEN (this is the whole trick). `concierge.find_or_create_flow` derives the sink
from `_origin`, which is the caller's `thread_id`: a thread of the form `gw:<channel>:<native>` means
"the user is talking to me from <channel>", so the flow delivers back there. We therefore arm each
cell by POSTing to `/invoke` with exactly the envelope that channel's transport posts — the real path,
not a shortcut. `dedup_key` includes the sink (`agent|source|cadence|sink|owner`), so each cell is a
genuinely distinct flow rather than one flow matched four times.

OUTCOMES — a cell is not simply pass/fail. The grid records which of these happened:

    ✓  ARMED       a new AP flow was created and its ap_flow_id verified
    ≡  REUSED      an equivalent flow already existed (dedup_key hit) — the plumbing works
    ?  NEEDS-INPUT the concierge asked for a missing trigger slot (a repo, a folder, a JD)
    ⚠  CONNECT     the gate reported not-connected (only trustworthy while AP is reachable)
    !  CLAIMS      the model said "already set up" but NO subscription exists — stale thread memory,
                   i.e. it answered from conversation history without calling the arm tool
    ✗  ERROR       HTTP error, no subscription, or an armed flow with no ap_flow_id
    –  SKIP        that channel/integration isn't configured in .env

Only ✗ fails the run. NEEDS-INPUT and CONNECT are *correct* behaviours, and this harness exists to
show the plumbing exists — not to insist every cell arms on a bare utterance. `!` is reported but not
fatal: it is a property of a warm thread, not of the plumbing.

Two cells arm only if you hand them the slot they need:
    GITHUB_TEST_REPO=owner/repo   → the github PR watcher can arm (NB: arming creates a real repo
                                    webhook; leave unset to keep the cell at NEEDS-INPUT)
    BOX_FOLDER_ID=<id>            → the box watcher stops asking which folder

NOT COVERED (and deliberately so): firing real data through an armed watcher. Sending a real email to
prove the gmail PUSH flow *runs* is `live_gmail_e2e.py`; a real Box upload is `live_box_e2e.py`; a real
open PR is `live_github_e2e.py`. This harness proves the flow is created and lives in AP, nothing more.

Run:  make test-matrix
      make test-matrix ARGS="--no-cleanup"
      GITHUB_TEST_REPO=me/sandbox .venv/bin/python tests/events/live_matrix.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_e2e import (  # noqa: E402  — shared HTTP/env/report plumbing, one source of truth
    BASE,
    RUN,
    connect_needed_app,
    env,
    flow_alive,
    gw_headers,
    has_digit,
    hook_path,
    http,
    srv,
)

ARMED, REUSED, NEEDS, CONNECT, ERROR, SKIPPED, STALE = "✓", "≡", "?", "⚠", "✗", "–", "!"
LABEL = {
    ARMED: "armed",
    REUSED: "reused",
    NEEDS: "needs-input",
    CONNECT: "connect-needed",
    ERROR: "error",
    SKIPPED: "skip",
    STALE: "claims-existing-but-none",
}

# The model may answer "that's already set up" from conversation memory without calling the arm tool.
# We never take its word for it: a reuse claim is only believed when a matching subscription exists.
_REUSE_PHRASES = (
    "reusing existing flow",
    "already set up",
    "already active",
    "already exists",
    "is already",
    "no new flow was created",
    "already configured",
    "is active",
    "trigger for",
    "watcher is active",
)

CHANNELS = ["web", "slack", "discord", "telegram"]
INTEGRATIONS = ["box", "github", "gmail"]

DEADLINE = time.monotonic() + float(os.environ.get("MATRIX_BUDGET_SECS", "900"))


def left() -> float:
    return DEADLINE - time.monotonic()


# ── channel targets: the native id each sink delivers to ──────────────────────
def discover_targets() -> dict:
    """native id per channel, or None when that channel isn't usable. web needs no native id."""
    t = {"web": ""}

    tok = env("SLACK_BOT_TOKEN")
    if tok:
        chan = env("SLACK_TEST_CHANNEL")
        if not chan:
            _, lst = http(
                "GET",
                "https://slack.com/api/conversations.list?types=public_channel&limit=200",
                headers={"Authorization": f"Bearer {tok}"},
                timeout=20,
            )
            member = [c for c in lst.get("channels", []) if c.get("is_member")]
            chan = member[0]["id"] if member else ""
        t["slack"] = chan or None
    else:
        t["slack"] = None

    dtok = env("DISCORD_BOT_TOKEN")
    if dtok:
        chan = env("DISCORD_TEST_CHANNEL_ID")
        if not chan:
            dh = {"Authorization": f"Bot {dtok}"}
            _, guilds = http("GET", "https://discord.com/api/v10/users/@me/guilds", headers=dh, timeout=20)
            if isinstance(guilds, list) and guilds:
                _, chans = http(
                    "GET",
                    f"https://discord.com/api/v10/guilds/{guilds[0]['id']}/channels",
                    headers=dh,
                    timeout=20,
                )
                text = [c for c in (chans if isinstance(chans, list) else []) if c.get("type") == 0]
                chan = text[0]["id"] if text else ""
        t["discord"] = chan or None
    else:
        t["discord"] = None

    t["telegram"] = env("TELEGRAM_CHAT_ID") or None if env("TELEGRAM_BOT_TOKEN") else None
    return t


def thread_for(channel: str, native: str, tag: str) -> str:
    """A `gw:<channel>:<native>` thread makes the concierge deliver back to that channel. `web` has no
    gateway origin, so the sink falls back to the agent's first configured channel — which is exactly
    what a web user gets, and is why the web column can legitimately read `reused`.

    The `#<locus>` suffix gives each run FRESH conversation memory. Without it the model remembers the
    previous run on the same thread and answers "that's already set up" without ever calling the arm
    tool — a phantom result. It is safe for slack/discord only: `principal.channel_origin` strips `#`
    at fire time (principal.py:121), so a DIRECT channel's delivery target is recomputed correctly.
    `concierge.find_or_create_flow` does its own parse WITHOUT stripping (concierge.py:262-263), so an
    AP-backed sink (telegram) would bake `chat_id#locus` into the AP send step. Telegram therefore
    keeps a plain thread and carries the memory-bleed caveat — see `classify`.
    """
    if channel == "web":
        return f"web:{RUN}:{tag}"
    if channel == "telegram":
        return f"gw:telegram:{native}"  # no suffix: AP send step bakes the target verbatim
    return f"gw:{channel}:{native}#{RUN}{tag}"


def ask(channel: str, native: str, tag: str, text: str, timeout=300):
    """Drive the concierge over the SAME entrypoint the channel's transport uses."""
    if channel == "web":
        code, rep = srv(
            "POST",
            "/api/concierge",
            {"text": text, "thread_id": thread_for(channel, native, tag)},
            timeout=timeout,
        )
        return code, str(rep.get("reply", ""))
    payload = {
        "text": text,
        "agent": "concierge",
        "deliver": False,
        "source": {
            "type": "channel",
            "name": channel,
            "thread_id": thread_for(channel, native, tag),
            "user": f"e2e-{RUN}",
        },
        "event": {"kind": "message", "payload": {}},
    }
    code, rep = srv("POST", "/invoke", payload, gw_headers(), timeout=timeout)
    return code, str(rep.get("answer", rep.get("error", "")))


# ── subscription bookkeeping ─────────────────────────────────────────────────
def subs():
    code, s = srv("GET", "/api/events/subscriptions", timeout=30)
    return s.get("subscriptions", []) if code == 200 else []


def match(before: set, *, mode=None, source=None, sink=None):
    """(sub, is_new). Prefers a subscription this call created; falls back to a dedup_key reuse."""
    fresh = reused = None
    for s in subs():
        if mode and s.get("mode") != mode:
            continue
        if source and not (s.get("source_connector") == source and s.get("source_type") == "integration"):
            continue
        if sink and sink not in (s.get("deliver_to") or []):
            continue
        if s.get("id") in before:
            reused = reused or s
        else:
            fresh = fresh or s
    return (fresh, True) if fresh else (reused, False)


def adopt_new(before: set) -> list:
    """Ids of every subscription created since `before`. Called before any sink filtering so that a
    flow armed to the WRONG sink is still deleted at cleanup instead of leaking a live AP flow."""
    return [s["id"] for s in subs() if s["id"] not in before]


def wrong_sink(before: set, expected: str, **kw):
    """A flow WAS created by this call, but not with the sink we asked from. `find_or_create_flow`
    lets the model's explicit `deliver_to` argument override the origin channel (concierge.py:266),
    so a model slip silently arms a Discord-origin request to deliver into Telegram. That is a real
    mis-route, not a harness artefact — surface it as an error with the sink it actually chose."""
    if expected == "web":
        return None
    for s in subs():
        if s["id"] in before:
            continue
        if kw.get("mode") and s.get("mode") != kw["mode"]:
            continue
        if kw.get("source") and s.get("source_connector") != kw["source"]:
            continue
        got = ", ".join(s.get("deliver_to") or []) or "nothing"
        return ERROR, f"armed but delivers to '{got}', asked from '{expected}'"
    return None


def classify(reply: str, sub, is_new, app: str | None, ap_live: bool) -> tuple[str, str]:
    """Map (reply, subscription) → one grid symbol + a short note.

    `sub` is matched with the CELL'S OWN sink filter (and, for the web column, by "created since the
    snapshot"). That is the only evidence that counts. `dedup_key` embeds the sink, so a legitimate
    reuse for this cell REQUIRES a subscription with this sink — a flow belonging to some other cell
    proves nothing. Accepting one would repaint stale-memory phantoms as passes, which is the exact
    failure this symbol set exists to expose."""
    low = reply.lower()
    if sub:
        # A NATIVE cron/poll has no AP flow BY DESIGN — the in-process scheduler owns the schedule
        # and fires it via /invoke. Demanding an ap_flow_id here predates that scheduler and marks
        # every working native flow as an error.
        if (sub.get("backend") or "") == "native":
            return (ARMED if is_new else REUSED), "native"
        if not sub.get("ap_flow_id"):
            return ERROR, "armed but no ap_flow_id"
        # An ap_flow_id proves nothing on its own — the flow may have been deleted out from under the
        # subscription (dedup never re-checks). Confirm it still exists in AP.
        alive, detail = flow_alive(sub)
        if not alive:
            return ERROR, detail
        return (ARMED if is_new else REUSED), sub["ap_flow_id"][:10]

    # The gate fires on the first UNCONNECTED integration the agent needs — not necessarily `app`
    # (resume_judge declares box AND gmail). Accept a connect prompt naming any of them.
    want = connect_needed_app(reply) if app else None
    if want:
        if not ap_live:
            return ERROR, "connect-needed while AP is DOWN — false negative, not a real prompt"
        return CONNECT, f"gate says '{want}' not connected"

    if any(p in low for p in _REUSE_PHRASES):
        return STALE, "model says a flow exists; no subscription with this sink does"

    # THE ARMING-VERB GATE — a correct refusal, not a failure. concierge.py refuses to arm a
    # standing flow from bare chat ("standing flows are created with a verb so nothing schedules
    # itself by accident") and tells the user to type `/automate …`. This harness drives bare
    # utterances, so the gate is the EXPECTED answer for every CRON/POLL cell.
    #
    # It was landing in the catch-all ERROR below, but only sometimes: the LLM relays the refusal in
    # its own words, and the keyword list underneath happens to catch some phrasings and not others.
    # So the same correct behaviour scored ? on one sink and ✗ on another, and the counts moved
    # between identical runs (5 errors, then 3, same deployment, same commit). A matrix that goes
    # red for a documented safety feature is the "red cell nobody believes" this file's own comments
    # warn about — it hides the next real regression.
    if "/automate" in low or "arm that from plain chat" in low:
        return NEEDS, "arming-verb gate (correct: bare chat must not arm)"

    if any(
        w in low
        for w in (
            "repo",
            "repository",
            "folder",
            "which ",
            "specify",
            "job description",
            " jd",
            "share the",
            "provide the",
            "attach",
            "what ",
        )
    ):
        return NEEDS, reply[:44].replace("\n", " ")
    return ERROR, reply[:60].replace("\n", " ") or "no subscription, no explanation"


# ── the grid ─────────────────────────────────────────────────────────────────
class Grid:
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.cell: dict[tuple[str, str], tuple[str, str]] = {}

    def put(self, row, col, sym, note=""):
        self.cell[(row, col)] = (sym, note)
        print(f"     {sym} {row:<16} → {col:<9} {note}", flush=True)

    def render(self):
        w = max(len(r) for r in self.rows) + 3  # +3 so the longest label keeps a gutter
        print("\n" + "─" * 72)
        print("  TRIGGER × SINK MATRIX\n")
        print("  " + " " * w + "".join(f"{c:<11}" for c in self.cols))
        for r in self.rows:
            line = f"  {r:<{w}}"
            for c in self.cols:
                line += f"{self.cell.get((r, c), (' ', ''))[0]:<11}"
            print(line)
        print(
            "\n  ✓ armed   ≡ reused (dedup)   ? needs-input   ⚠ connect-needed"
            "   ! claims-existing (none found)   ✗ error   – skip"
        )
        counts: dict[str, int] = {}
        for sym, _ in self.cell.values():
            counts[sym] = counts.get(sym, 0) + 1
        print("  " + " · ".join(f"{LABEL[s]}: {n}" for s, n in sorted(counts.items())))
        stale = [(r, c, n) for (r, c), (s, n) in self.cell.items() if s == STALE]
        if stale:
            print("\n  Claims-existing but no subscription found (stale thread memory, not a hard fail):")
            for r, c, n in stale:
                print(f"    ! {r} → {c}: {n}")
        errs = [(r, c, n) for (r, c), (s, n) in self.cell.items() if s == ERROR]
        if errs:
            print("\n  Errors:")
            for r, c, n in errs:
                print(f"    ✗ {r} → {c}: {n}")
        print(
            "\n  RESULT:",
            "\033[31mFAIL\033[0m" if errs else "\033[32mPASS\033[0m",
            "(only ✗ fails; ? and ⚠ are correct behaviours)",
        )
        return 1 if errs else 0


# ── the cells ────────────────────────────────────────────────────────────────
def run_now(g: Grid, targets: dict):
    print("\n\033[1m[NOW]\033[0m  ask each channel a question, validate the answer")
    for ch in CHANNELS:
        if targets.get(ch) is None:
            g.put("NOW", ch, SKIPPED, "channel not configured")
            continue
        code, reply = ask(
            ch, targets[ch], "now", "what is the current price of bitcoin in usd? just the number"
        )
        if code == 200 and has_digit(reply):
            g.put("NOW", ch, ARMED, reply[:36].replace("\n", " "))
        else:
            g.put("NOW", ch, ERROR, f"HTTP {code}: {reply[:50]}")


def run_standing(g: Grid, targets: dict, ap_live: bool, created: list):
    """CRON and POLL, once per sink."""
    cases = [
        ("CRON", "every day at 9am send me new arxiv papers on mixture of experts", "CRON"),
        ("POLL", "watch bitcoin every 2 minutes and ping me on any move", "POLL"),
    ]
    for row, utter, mode in cases:
        print(f"\n\033[1m[{row}]\033[0m  arm one flow per sink")
        for ch in CHANNELS:
            if targets.get(ch) is None:
                g.put(row, ch, SKIPPED, "channel not configured")
                continue
            if left() < 40:
                g.put(row, ch, SKIPPED, "time budget exhausted")
                continue
            before = {s["id"] for s in subs()}
            code, reply = ask(ch, targets[ch], row.lower(), utter)
            if code != 200:
                g.put(row, ch, ERROR, f"HTTP {code}")
                continue
            # Register EVERY subscription this call created, before any sink filtering. A flow armed
            # to the WRONG sink is still a real AP flow we must delete, or the harness leaks.
            created.extend(adopt_new(before))
            sub, is_new = match(before, mode=mode, sink=(ch if ch != "web" else None))
            sym, note = classify(reply, sub, is_new, None, ap_live)
            if sym == STALE:
                sym, note = wrong_sink(before, ch, mode=mode) or (sym, note)
            # A NATIVE cron/poll needs no Activepieces at all — it arms into the subscription store
            # and the in-process scheduler fires it (live_fire.py proves both against this same
            # deployment). Only an AP-BACKED flow is broken when AP is down. Without this test the
            # whole cron/poll half of the matrix reported ✗ on a stack where it demonstrably works,
            # which is worse than a gap: a red cell nobody believes hides the next real regression.
            if sub and not ap_live and (sub.get("backend") or "") != "native":
                sym, note = ERROR, "armed but AP is down"
            g.put(row, ch, sym, note)


def run_push(g: Grid, targets: dict, conn: dict, ap_live: bool, created: list):
    repo = env("GITHUB_TEST_REPO")
    folder = env("BOX_FOLDER_ID")
    utters = {
        "box": (
            "when a resume lands in my Box"
            + (f" folder {folder}" if folder else "")
            + ", judge it against this JD — 'senior python engineer, 5y, distributed systems' — and tell me"
        ),
        "github": (
            f"when a pull request opens on {repo}, summarize it and message me"
            if repo
            else "when a pull request opens on my repo, summarize it and message me"
        ),
        "gmail": "when an email from my boss arrives, summarize it and message me",
    }
    for app in INTEGRATIONS:
        row = f"PUSH({app})"
        print(f"\n\033[1m[{row}]\033[0m  status={conn.get(app)}  — arm one watcher per sink")
        for ch in CHANNELS:
            if targets.get(ch) is None:
                g.put(row, ch, SKIPPED, "channel not configured")
                continue
            if left() < 40:
                g.put(row, ch, SKIPPED, "time budget exhausted")
                continue
            before = {s["id"] for s in subs()}
            code, reply = ask(ch, targets[ch], f"push{app}", utters[app])
            if code != 200:
                g.put(row, ch, ERROR, f"HTTP {code}")
                continue
            created.extend(adopt_new(before))
            # mode is intentionally unconstrained: with EVENTS_BOX_BACKEND=direct a box "push"
            # correctly arms as mode=POLL (box-poll-*), because the direct backend polls Box's API.
            sub, is_new = match(before, source=app, sink=(ch if ch != "web" else None))
            sym, note = classify(reply, sub, is_new, app, ap_live)
            if sym in (STALE, ERROR):
                sym, note = wrong_sink(before, ch, source=app) or (sym, note)
            g.put(row, ch, sym, f"{note}{'' if ch == 'web' else ' → ' + ch}")


def run_webhook(g: Grid):
    print("\n\033[1m[WEBHOOK]\033[0m  generic inbound trigger (direct, no AP)")
    code, rep = srv(
        "POST",
        hook_path("monitoring"),
        {"alert": "HighCPU", "service": "checkout-api", "value": "97%", "threshold": "85%"},
        timeout=240,
    )
    ans = str(rep.get("answer", ""))
    ok = code == 200 and rep.get("ok") and any(s in ans for s in ("P1", "P2", "P3", "sever"))
    g.put("WEBHOOK", "web", ARMED if ok else ERROR, ans[:44].replace("\n", " ") or f"HTTP {code}")
    for ch in ("slack", "discord", "telegram"):
        g.put("WEBHOOK", ch, SKIPPED, "hook has no per-channel sink; delivery is agent-side")


def gh_hook_ids(repo: str) -> set:
    """Webhook ids currently on the repo. Empty set if we can't read them (no perms / no repo)."""
    tok = env("GITHUB_TOKEN")
    if not (repo and tok):
        return set()
    code, hooks = http(
        "GET",
        f"https://api.github.com/repos/{repo}/hooks",
        timeout=20,
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
    )
    return {h["id"] for h in hooks} if code == 200 and isinstance(hooks, list) else set()


def cleanup(created: list, repo: str = "", hooks_before: set | None = None):
    """Delete this run's subscriptions, then this run's GitHub webhooks.

    Deleting an AP flow does NOT remove the repo webhook the github piece created, so arming the
    github row leaves live hooks POSTing at your public URL until they are removed here. We only ever
    delete hook ids that did not exist before the run."""
    if created:
        print(f"\n\033[1m[cleanup]\033[0m  deleting {len(created)} subscription(s) this run created")
        bad = 0
        for sid in created:
            code, _ = srv("DELETE", f"/api/events/subscriptions/{sid}", timeout=60)
            bad += code != 200
        print(f"     {len(created) - bad} deleted, {bad} failed")

    if repo and hooks_before is not None:
        new_hooks = gh_hook_ids(repo) - hooks_before
        if not new_hooks:
            return
        tok = env("GITHUB_TOKEN")
        print(f"     removing {len(new_hooks)} github webhook(s) this run created on {repo}")
        for hid in new_hooks:
            code, _ = http(
                "DELETE",
                f"https://api.github.com/repos/{repo}/hooks/{hid}",
                timeout=20,
                headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
            )
            print(f"       hook {hid}: {'removed' if code in (204, 200) else f'FAILED HTTP {code}'}")


def main() -> int:
    a = argparse.ArgumentParser(description="Live matrix: every trigger mode × every channel sink.")
    a.add_argument("--no-cleanup", action="store_true")
    args = a.parse_args()

    print(f"\033[1mCUGA live matrix\033[0m — {BASE}  (budget {left():.0f}s)")
    code, st = srv("GET", "/api/events/status", timeout=20)
    if code != 200:
        print(f"server unreachable at {BASE} — run `make up`")
        return 2
    ap_url = env("AP_BASE_URL", "http://localhost:8081").rstrip("/")
    ap_live = http("GET", f"{ap_url}/api/v1/flags", timeout=8)[0] == 200
    _, integ = srv("GET", "/api/events/integrations", timeout=20)
    conn = {i["name"]: i.get("status") for i in integ.get("integrations", [])}
    print(f"  AP reachable: {ap_live}   integrations: { {k: conn.get(k) for k in INTEGRATIONS} }")
    if not ap_live:
        print(
            "  \033[33mAP is DOWN\033[0m — nothing can arm, and a 'connect your credentials' reply "
            "would be a false negative. Cells will read ✗ rather than ⚠."
        )

    targets = discover_targets()
    print("  sinks:", {k: (v if v else "—") for k, v in targets.items()})

    # Arming the github row creates REAL repo webhooks. Snapshot them so cleanup removes only ours.
    repo = env("GITHUB_TEST_REPO")
    hooks_before = gh_hook_ids(repo) if repo else set()
    if repo:
        print(f"  github repo: {repo} ({len(hooks_before)} existing webhook(s) — will be preserved)")

    rows = ["NOW", "CRON", "POLL"] + [f"PUSH({a})" for a in INTEGRATIONS] + ["WEBHOOK"]
    g = Grid(rows, CHANNELS)
    created: list[str] = []
    try:
        run_now(g, targets)
        run_standing(g, targets, ap_live, created)
        run_push(g, targets, conn, ap_live, created)
        run_webhook(g)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        if not args.no_cleanup:
            cleanup(created, repo, hooks_before)
        elif created:
            print(f"\n[cleanup] skipped — {len(created)} left: {', '.join(created)}")
            if repo:
                print(f"[cleanup] github webhooks on {repo} were NOT removed — they will keep firing")
    return g.render()


if __name__ == "__main__":
    sys.exit(main())
