"""DIRECT-event dispatch — standing watchers on transports CUGA already receives itself.

Slack's Events API, Discord's Gateway and the telegram message stream all reach CUGA without
Activepieces. Historically those events had exactly ONE consumer — the converse path (a human
message → the concierge) — and every other event type (a reaction, a member join, a channel
created) was dropped at the door. This module gives them a second consumer: **direct watcher
subscriptions** — rows armed by the concierge with ``ap_flow_id=None`` + an ``event`` +
``config`` (the trigger registry's direct-backend rows).

    event arrives (slack reaction / discord join / channel message)
        ▸ match(store, app, event, …)           — which active watchers want this?
        ▸ dispatch_all(...)                     — each fires its agent through POST /invoke
                                                   (the same seam every AP flow uses), delivering
                                                   back to where the watcher was armed from.

Filters come from the subscription's ``config`` slots:
    channel — the platform-native channel id the watcher is scoped to ("" = any)
    emoji   — reaction name, colons stripped ("" = any)
    pattern — a substring/regex the message text must match ("" = any)

No credentials here: outbound delivery rides /invoke's existing direct-channel adapters.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("cuga.events.direct")

try:
    from . import triggers as _registry
except ImportError:  # flat load (tests put the events dir on sys.path)
    import triggers as _registry  # type: ignore


def kind_for(app: str, direct_kind: str) -> str:
    """A transport event type ("reaction_added", "GUILD_MEMBER_ADD") → our canonical event kind.
    "" when no registry row consumes it (the caller then drops the event, as before)."""
    for t in _registry.rows():
        if t.backend == "direct" and t.app == app and t.direct_kind == direct_kind:
            return t.event
    return ""


# ── ACTION EXECUTION for a DIRECT trigger (Option A) ─────────────────────────────────────────────
# A direct watcher can carry an action_plan (built by the concierge, stashed on sub.config). After the
# agent answers, we run it: an AP-only action fires its reusable EXECUTOR flow (engine.trigger_flow);
# a same-app action would send via the app's bot adapter (dormant until slack/discord actions exist).
_TEXT_KEYS = ("text", "content", "message", "body", "subject", "name")


def _payload_text(payload: dict) -> str:
    """A best-effort text haystack from a direct event payload (slack {text}, discord {content}, …)."""
    if not isinstance(payload, dict):
        return str(payload or "")
    parts = [str(payload[k]) for k in _TEXT_KEYS if isinstance(payload.get(k), (str, int, float))]
    return " ".join(parts) if parts else " ".join(str(v) for v in payload.values()
                                                   if isinstance(v, (str, int, float)))


def _fill_answer(value, answer: str):
    """Substitute the ``{{answer}}`` sentinel (executor_body places it for answer-source params) with
    the agent's real answer, recursively through lists/dicts."""
    if isinstance(value, str):
        return value.replace("{{answer}}", answer or "")
    if isinstance(value, list):
        return [_fill_answer(v, answer) for v in value]
    if isinstance(value, dict):
        return {k: _fill_answer(v, answer) for k, v in value.items()}
    return value


def _eval_condition(when: dict, *, answer: str, payload: dict) -> bool:
    """Evaluate a branch condition for a DIRECT trigger. Content conditions ('mentions urgent') check
    the incoming MESSAGE (+ the answer, since direct triggers rarely branch on the answer alone);
    a from-address condition checks the payload sender. Ops: CONTAINS / EQUALS / STARTS_WITH."""
    field = str((when or {}).get("field", "answer")).lower()
    op = str((when or {}).get("op", "CONTAINS")).upper()
    val = str((when or {}).get("value", "")).strip().lower()
    if not val:
        return False
    if "from" in field:
        hay = str((payload or {}).get("from") or (payload or {}).get("user") or _payload_text(payload))
    else:
        hay = f"{_payload_text(payload)} {answer or ''}"
    hay = hay.lower()
    if op == "CONTAINS":
        return val in hay
    if op == "STARTS_WITH":
        return hay.strip().startswith(val)
    if op == "EQUALS":
        return hay.strip() == val or val in hay
    return False


def _pick_branch_step(branches: list, *, answer: str, payload: dict):
    """EXECUTE_FIRST_MATCH: first condition branch that matches wins; else the fallback (when=None)."""
    for b in branches:
        if b.get("when") is not None and _eval_condition(b["when"], answer=answer, payload=payload):
            return b.get("step")
    for b in branches:                                    # no condition matched → fallback
        if b.get("when") is None:
            return b.get("step")
    return None


async def _run_step(engine, sub, step: dict, *, answer: str, payload: dict) -> tuple[bool, str]:
    """Run one action step. executor → POST the resolved body to its AP webhook flow (SYNC, so we
    learn the run's real outcome instead of a fire-and-forget 200); direct_send → send via the app's
    bot adapter. Returns ``(ok, detail)`` — ok False if the step was unrunnable OR the AP run failed;
    detail is a short human reason on failure (surfaced back to the origin). Never raises."""
    kind = step.get("kind")
    try:
        if kind == "executor":
            if engine is None or not step.get("flow_id"):
                return False, "executor unavailable (no engine/flow)"
            body = _fill_answer(step.get("body") or {}, answer)
            ok, detail = await engine.trigger_flow(step["flow_id"], body, sync=True)
            log.info("direct action executor app=%s action=%s flow=%s → ok=%s %s",
                     step.get("app"), step.get("ap_action"), step.get("flow_id"), ok, detail)
            return ok, ("" if ok else detail)
        if kind == "direct_send":                         # same-app bot send (dormant today)
            from . import delivery
            native = (sub.thread_id or "").split(":", 2)[-1] or (sub.deliver_to or [""])[0]
            ok, detail = await delivery.send_direct(step["app"], native, answer or "")
            return bool(ok), ("" if ok else str(detail))
    except Exception as e:  # noqa: BLE001 — an action failure must not crash the dispatch
        log.warning("direct action step failed kind=%s app=%s: %s", kind, step.get("app"), e)
        return False, str(e)
    return False, f"unknown step kind {kind!r}"


async def run_action_plan(engine, sub, *, answer: str, payload: dict) -> tuple[int, list[str]]:
    """Execute the subscription's action_plan (if any) after the agent answered. Linear steps run in
    order; a branched plan runs the FIRST matching branch (else the fallback). Returns
    ``(fired, failures)`` — fired = steps that succeeded, failures = short reasons for steps that did
    NOT (so the caller can correct the origin instead of leaving a false 'done' claim standing)."""
    plan = (getattr(sub, "config", None) or {}).get("action_plan")
    if not plan:
        return 0, []
    if plan.get("branches"):
        step = _pick_branch_step(plan["branches"], answer=answer, payload=payload)
        steps = [step] if step else []
    else:
        steps = plan.get("steps") or []
    fired = 0
    failures: list[str] = []
    for st in steps:
        if not st:
            continue
        ok, detail = await _run_step(engine, sub, st, answer=answer, payload=payload)
        if ok:
            fired += 1
        else:
            label = st.get("app") or st.get("ap_action") or st.get("kind") or "action"
            failures.append(f"{label}: {detail}" if detail else str(label))
    return fired, failures


def _cfg_match(cfg: dict, *, channel: str = "", text: str = "", emoji: str = "") -> bool:
    want_ch = str(cfg.get("channel") or "").lstrip("#")
    if want_ch and want_ch != str(channel or "").lstrip("#"):
        return False
    want_emoji = str(cfg.get("emoji") or "").strip(":")
    if want_emoji and want_emoji != str(emoji or "").strip(":"):
        return False
    pattern = str(cfg.get("pattern") or "")
    if pattern:
        try:
            if not re.search(pattern, text or "", re.I):
                return False
        except re.error:                      # a bad user regex degrades to substring match
            if pattern.lower() not in (text or "").lower():
                return False
    return True


def match(store, app: str, event: str, *, channel: str = "", text: str = "",
          emoji: str = "") -> list:
    """Active DIRECT watcher subscriptions for (app, event) whose config filters accept this
    occurrence. Store may be None (events layer without a subscription store) → []."""
    if store is None or not event:
        return []
    out = []
    for sub in store.list(status="active"):
        if sub.ap_flow_id:                    # AP-armed flows are AP's to fire, not ours
            continue
        if sub.source_connector != app or (sub.event or "") != event:
            continue
        if not _cfg_match(sub.config or {}, channel=channel, text=text, emoji=emoji):
            continue
        out.append(sub)
    return out


def describe(app: str, event: str, payload: dict) -> str:
    """A compact, human-readable rendering of the event for the agent's prompt. The FULL payload
    also rides in event.payload, so nothing is lost by the summary."""
    bits = []
    for key in ("text", "reaction", "user", "channel", "name", "content"):
        v = payload.get(key)
        if v:
            bits.append(f"{key}={v}" if not isinstance(v, dict) else f"{key}={v.get('id', v)}")
    return f"[{app}/{event}] " + (", ".join(str(b) for b in bits) or "(see payload)")


async def dispatch_all(subs: list, *, app: str, event: str, payload: dict, engine=None) -> int:
    """Fire every matched watcher through POST /invoke (agent pinned to the subscription's,
    deliver=True → the answer goes back to the origin the watcher was armed from, via the
    existing direct-channel delivery). If a watcher carries an ``action_plan`` (a DIRECT trigger →
    action, Option A), run it after the agent answers — passing ``engine`` so an executor step can
    fire its AP flow. Returns how many dispatched; never raises."""
    import httpx
    try:
        from .secret_seam import secret as _secret
    except ImportError:
        from secret_seam import secret as _secret
    port = os.environ.get("EVENTS_CUGA_PORT", "7860")
    gw = _secret("GATEWAY_TOKEN")
    n = 0
    for sub in subs:
        text = (f"{sub.prompt}\n\nThe watched event just happened: "
                f"{describe(app, event, payload)}")
        inv = {"agent": sub.target_agent, "text": text, "deliver": True, "scope": sub.tenant,
               "source": {"type": "integration", "name": app, "thread_id": sub.thread_id},
               "event": {"kind": event, "payload": dict(payload or {})}}
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(f"http://127.0.0.1:{port}/invoke",
                                 headers={"X-Gateway-Token": gw}, json=inv)
            log.info("direct dispatch %s/%s → %s (HTTP %s)", app, event, sub.target_agent,
                     r.status_code)
            n += 1
            # DIRECT trigger → action (Option A): run the stashed plan with the agent's answer.
            if (getattr(sub, "config", None) or {}).get("action_plan"):
                answer = ""
                try:
                    answer = (r.json() or {}).get("answer") or ""
                except Exception:  # noqa: BLE001
                    answer = ""
                fired, failures = await run_action_plan(engine, sub, answer=answer, payload=payload)
                log.info("direct action_plan fired %d step(s), %d failed for %s",
                         fired, len(failures), sub.id)
                # The agent's answer (claiming e.g. 'email sent') was already delivered to the origin
                # with deliver=True — BEFORE this plan ran. If the action then failed, that claim is
                # now false and silent. Post a correction to the same origin so the failure is seen.
                if failures:
                    try:
                        from . import delivery
                        native = (sub.thread_id or "").split(":", 2)[-1] or (sub.deliver_to or [""])[0]
                        note = ("⚠️ Heads up — I generated the summary, but the follow-up action "
                                "did **not** complete:\n• " + "\n• ".join(failures[:4]) +
                                "\n(If this is a Gmail/OAuth error, the connector likely needs to be "
                                "reconnected.)")
                        await delivery.send_direct(app, native, note)
                    except Exception as e:  # noqa: BLE001
                        log.warning("failed to deliver action-failure notice for %s: %s", sub.id, e)
        except Exception as e:  # noqa: BLE001 — one broken watcher must not drop the others
            log.warning("direct dispatch %s/%s → %s failed: %s", app, event, sub.target_agent, e)
    return n
