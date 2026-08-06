# 2026-08-06 — the web never heard back (and five bugs found underneath it)

Branch `event_support`. Started from one user report:

> In the main CUGA UX when I use `/automate` and ask for something, the flow gets armed — it is also
> executed but I don't see it show up in the UX. It is seen in the dashboard. Same is true from
> concierge.

That was real, and fixing it exposed three more defects that had been live on Code Engine, one in
our own test harness, and a sixth that only appeared once the fix was deployed. This is the write-up; a resume checklist is at the end.

---

## 1. The reported bug: a web-armed flow fired and told nobody

**Root cause.** `delivery.send_direct` had branches for `slack`, `discord` and `telegram` and **none
for `web`**, so `is_direct("web")` was false and `/invoke` skipped the delivery block entirely. The
answer went to the runs log and stopped there. Worse, a browser thread id (`web:studio`, or the main
chat's UUID) carries no `gw:` prefix, so `channel_origin()` returned `None` and there was no
delivery address to use even if a sender had existed.

**Why it was hard to see as a bug rather than a limitation.** It had actually been *written down* as
one — `events_docs/plans/SPLIT_AND_HITL_ARMING_SPEC.md` §5 said "Web same-thread is a known
limitation (no async connection)". That framing is half right: a browser genuinely cannot be pushed
to. But it *can be drained*.

**The fix.** `web` becomes a direct channel whose transport is a **durable per-thread mailbox**:

| Piece | What it does |
|---|---|
| `events/web_inbox.py` (new) | `web_inbox` table + `put()` / `list(thread_id, since)`, capped at 2 000 rows |
| `delivery.send_direct("web", …)` | writes the fire to the mailbox instead of dropping it |
| `app.py` `/invoke` | when a thread has no `gw:` origin and this is **not** a NOW answer, treat it as `("web", thread_id)` |
| `principal.unscoped_thread()` (new) | strips the `<scope>::` prefix — see below |
| `GET /api/events/inbox` (new) | cursor feed: oldest-first, `since` **exclusive**, scope-isolated |
| `ConciergeChat.tsx` · `CarbonChat.tsx` | poll every 15 s and append `⚡ flow fired` into the transcript |

**The near-miss worth remembering.** `Principal.thread()` namespaces an armed thread as
`<scope>::<thread_id>`, so the subscription holds `default/default/local::web:studio` while the tab
polls for `web:studio`. Delivering to the stored string would have written every fire into a mailbox
nobody reads — a bug *indistinguishable from the one being fixed*. `channel_origin` already tolerated
the prefix by searching for `gw:`; `web` has no such marker, so it needed an explicit strip.
Isolation is preserved by the mailbox's own `scope` column.

**Verified live**, not just in tests: your existing `web:studio` cron (which had fired 35 times
unseen) started landing in the mailbox within minutes of deploy, and the server log shows
`deliver via=direct channel=web ok=True` for both the Studio thread and a main-chat UUID thread.

---

## 2. `REAL` is not the same type in SQLite and Postgres

Found while investigating what looked like **duplicate fires** — two runs rows per tick, same
second, different durations.

They were not duplicates. **SQLite's `REAL` is an 8-byte double; Postgres's `REAL` is `float4`** —
about 7 significant digits. Every timestamp in this schema is a Unix epoch (~1.79e9, ten digits), so
on Postgres the low bits were discarded and every instant snapped to a **~100-second grid**, off by
up to ±50 s. Measured directly against a real PostgreSQL:

```
             wrote       REAL (float4)     DOUBLE (float8)   error
    1785992246.473      1785992200.000      1785992246.473    -46.473s
    1785992247.473      1785992200.000      1785992247.473    -47.473s
    1785992253.473      1785992200.000      1785992253.473    -53.473s
    1785992276.473      1785992300.000      1785992276.473    +23.527s
    1785992312.473      1785992300.000      1785992312.473    -12.473s

  5 distinct instants spanning 66s → REAL preserved 2 distinct values
```

Two genuinely-separate fires ~66 s apart therefore *collided on one stored timestamp*, and the Runs
tab rendered them as a double fire. The alternating 1, 2, 1, 2 pattern across ticks is exactly what
bucketing a ~66 s signal into a 100 s grid produces.

**What it actually broke, silently, only in the cloud:**

- `subscription.next_fire` / `last_fire` — a "1 minute" cron drifted onto the ~100 s grid
- `now_run.ts` — the Runs log's ordering and times were wrong by up to a minute
- `watch_state.*` — the poll delta tiers' state timestamps
- **`web_inbox.ts`** — the browser's cursor is a `ts` and `since` is *exclusive*, so two fires in one
  bucket meant **the second was skipped forever**: a lost message, no error anywhere. The feature in
  §1 would have shipped with a message-dropping bug.

**Fix.** `db._to_pg_types()` rewrites `REAL` → `DOUBLE PRECISION` in DDL (word-boundary and
quote-aware, DDL only), plus `widen_real_columns()` — a one-time, idempotent repair for a database an
older build already created. It ran on the live database at boot:

```
events db: widened 13 float4 column(s) to double precision — subscription.created_at,
subscription.next_fire, subscription.last_fire, subscription.expires_at, watch_state.baseline,
watch_state.threshold, watch_state.updated_at, pending_arm.expires_at, web_inbox.ts,
app_user.created_at, identity.created_at, link_token.created_at, now_run.ts
```

It does **not** recover precision already lost — rows written as float4 keep their rounded value.
Only new writes are exact.

> **The lesson worth keeping: the offline suite structurally cannot catch this class of bug**, because
> SQLite is unaffected. Schema changes need `make test-pg` against a real PostgreSQL.

---

## 3. The database password was in the Code Engine logs

Boot logged its store location verbatim. For SQLite that is a harmless path; for the managed
PostgreSQL it is `postgres://user:PASSWORD@host/db`. So **every boot wrote live database credentials
in plaintext** into the platform log — readable by anyone with log access to the project, and
retained long after a rotation.

`db._redact()` already existed and was used by `db.py`; `service.py` simply did not call it. Now it
does, and the live log reads `postgres://ibm_cloud_…:***@…`.

**Still open (your call):** the password itself has been exposed in logs and should be **rotated**.

---

## 4. A truncated LLM rewrite could arm a corrupted prompt

The confirm card for "every minute send me the price of bitcoin" read:

```
• The agent will be asked: “The price of bitcoi.”
```

`_strip_cadence` (the regex fallback) is clean — this came from the LLM rewrite in
`_single_shot_task`, which stopped mid-word. Both existing guards passed: no cadence word leaked and
it was not oversized. So the corrupted string would have become the flow's **permanent** prompt and
was displayed on the confirm card, whose entire purpose is to show the human the exact instruction
the agent will receive forever.

Added `_looks_truncated()`, deliberately narrow: reject only when the final word is a **strict prefix
of a longer word in the input** ("bitcoi" against "bitcoin"). A genuine rephrase, or one ending in a
word that appears in full, passes untouched.

---

## 5. Our own matrix test was lying about cron and poll

`live_matrix.py` marked **every** armed flow as `✗ ERROR "armed but AP is down"` whenever
Activepieces was unreachable, and `classify()` errored earlier still on "armed but no ap_flow_id". Both
predate the native scheduler. A native cron/poll has no AP flow *by design* — and `live_fire.py`
proves those same flows arm **and fire** against the same deployment.

So the whole cron/poll half of the matrix reported red on a stack where it demonstrably works. That
is worse than a gap: a red cell nobody believes is where the next real regression hides.

---

## 6. The backlog flood (found by deploying, not by testing)

With §1 live, the deployed mailbox reported **50 messages waiting** for the Studio tab — the
accumulated fires that had been invisible. Which exposed a rough edge in the fix itself: a first
load asks with `since=0` to recover what was missed while the tab was closed, and for a
minute-by-minute cron that means *replaying hundreds of messages into a chat window*, 50 at a time,
every 15 s. That is a flood, not a recovery.

`GET /api/events/inbox` now takes **`max_age`** (seconds), and the clients ask for a day.

It is a **server-side** parameter deliberately. The obvious implementation — have the browser send
`since = now/1000 - 86400` — is wrong: the cursor is a *server* timestamp, so any disagreement
between the two clocks silently skips messages (browser ahead) or re-renders them (browser behind).
The server owns its own clock; `max_age` is ignored the moment a real cursor exists, which is every
poll after the first.

Verified live: `?max_age=86400` → 50 messages, `?max_age=300` → 5.

## 7. Where the four channels actually stand

Proven on the deployed stack, by reading the message back out of the channel or the server's own
delivery log — not by trusting a 200:

| Surface | NOW (chat) | Scheduled fire delivered |
|---|---|---|
| Slack | ✅ | ✅ arm→confirm→fire in-thread |
| Discord | ✅ | ✅ `deliver via=direct channel=discord ok=True` |
| Telegram | ✅ | ✅ `deliver via=direct channel=telegram ok=True` |
| Web | ✅ | ✅ **new** — mailbox, both Studio and main chat |

New harness `tests/events/live_fire_delivery.py` posts the native scheduler's own tick body and then
reads the message back. Telegram is reported as `~ SENT` rather than `✓ VERIFIED` because the Bot API
exposes no "messages I sent" read — the distinction is printed rather than glossed.

---

## Test results (2026-08-06, against the deployed stack)

| Suite | Result |
|---|---|
| `make test` (offline, hermetic) | **392 passed · 0 failed** · 27 skipped |
| `make test-pg` (real PostgreSQL) | **27 passed · 0 failed** |
| `live_e2e.py` → CE | **29 passed · 0 failed** · 5 skipped (all AP-absent by design) |
| `live_fire_delivery.py` → CE | **PASS** · 2 verified · 2 sent · 0 failed |
| `live_fire.py` → CE | **8 of 10 fired** end-to-end · 1 fail · 1 skip |
| `live_matrix.py` → CE | **PASS** · 13 armed · 12 needs-input · **0 errors** |

`live_fire`'s two non-passes are **configuration gaps, not code**: Box has no token on the
deployment (`BOX_DEV_TOKEN` unset) and GitHub has no `GITHUB_TEST_REPO` — the harness refuses to
guess a repository to arm against, which is the right call.

The trigger × sink matrix, previously red across the whole cron/poll half (see §5):

```
                 web        slack      discord    telegram
  NOW            ✓          ✓          ✓          ✓
  CRON           ✓          ✓          ✓          ✓
  POLL           ✓          ✓          ✓          ✓
  PUSH(box)      ?          ?          ?          ?          ← ? = needs-input, correct with no AP
  PUSH(github)   ?          ?          ?          ?
  PUSH(gmail)    ?          ?          ?          ?
  WEBHOOK        ✓          –          –          –
```

`~sent` for telegram and discord means **delivered, readback impossible**: Telegram's Bot API has no
"messages I sent" route, and this Discord bot token is refused (403, code 1010) on message history.
Both are confirmed by the server's own delivery log rather than glossed:

```
deliver via=direct channel=telegram ok=True reason=ok
deliver via=direct channel=discord  ok=True reason=ok
deliver via=direct channel=web      ok=True reason=ok      ← both the Studio and a main-chat thread
```

## Still open

1. **Rotate the PostgreSQL password** — it was in the logs (§3).
2. **The events service is anonymously readable and writable on its public route.** Every read
   endpoint (armed flows, run history *including answers*, agent config) answers without a token, and
   two writes do too: `POST /api/concierge` (anyone can arm a flow) and `POST /api/events/hook/{name}`
   (`EVENTS_WEBHOOK_KEY` is unset — the service warns about this at every boot). Only `/invoke` (401)
   and `/api/events/admin/*` (403) are guarded. This is a design decision, not a patch: the Studio is
   a browser SPA with nowhere safe to keep a token.
3. **The native scheduler has no lease.** With `min-scale = max-scale = 1` this is confined to the
   overlap during a rolling deploy, but any moment two instances exist, every flow fires twice.
4. **Poll tier picks `fuzzy` on CE where it picks `threshold` locally** — diagnostic logging is in
   place (`poll … delta kind=… src=… text=…`), not yet root-caused.
5. **`cuga-core` receives the whole `cuga-events-secrets` secret** — database credentials and every
   bot token — though it needs none of them. Splitting the secret is still outstanding.
6. **Nothing is committed.** The tree has been uncommitted since `2db545ad`.

## Resume checklist

- [ ] `make test` (offline) and `make pg && make test-pg` (Postgres) — both were green at write time
- [ ] `git status` — review, then commit; the deployed image is already built from this tree
- [ ] Decide on §2 above (auth), then set `EVENTS_WEBHOOK_KEY` at minimum
- [ ] Rotate the database password (§3)
- [ ] `make ce-status` / `make ce-logs GREP=deliver` to confirm the stack after any redeploy


---

## Postscript: a flaky run, and why it is recorded here

The first regression pass against the final image reported `live_e2e`: **17 passed · 6 failed**. Every
failure was `HTTP 0 <urlopen error [Errno 8] nodename nor servname provided>` — `getaddrinfo` failing
on the *client* machine, so no request ever left it. A clean re-run against the same deployed image
returned **29 passed · 0 failed** in 78 s, and `live_fire_delivery.py` had passed against that image
in between, exercising the same `/api/concierge` and `/invoke` paths.

Worth writing down because the failure mode is indistinguishable at a glance from a real regression,
and the honest move is the same both times: **re-run before concluding either way**. `HTTP 0` in this
harness means "the client never connected" — check DNS and app health before reading it as a server
fault. (The same session also saw `make test` hit a 10-minute wall twice; that was self-inflicted
contention from concurrently-backgrounded jobs, and the suite completes in ~60 s run alone.)
