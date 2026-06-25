# Client-side cancellation contract for autosave PATCHes

This doc covers **Slice A** of the single-reindex guarantee work. Slice B
(engine generation counter) lands separately and closes the
server-side gap this doc explicitly leaves open.

## Endpoints in scope

All draft autosave PATCHes that ManagePage fires through debounced
hooks / blur handlers:

- `PATCH /api/manage/config/draft/knowledge`
- `PATCH /api/manage/config/draft/llm`
- `PATCH /api/manage/config/draft/tools`
- `PATCH /api/manage/config/draft/agent`
- `PATCH /api/manage/config/draft/special_instructions`

The publish flow (`POST /api/manage/config`) is **out of scope** — it's
user-initiated, single-shot, and races aren't possible by construction.

## Client contract (frontend — `ManagePage.tsx`)

1. Each autosave family owns ONE `AbortController` ref. Refs:
   `knowledgeAbortRef`, `toolsAbortRef`, `llmAbortRef`, `agentAbortRef`,
   `specialInstructionsAbortRef`.
2. On every new config change, the prior controller is `.abort()`-ed
   BEFORE the new request is queued. A fresh `AbortController` is
   created and stored in the ref.
3. `signal` is threaded through `api.patchManageConfigDraft*` to
   `apiFetch`, which spreads `init` into the native `fetch`. The
   browser's `fetch` honors the signal: an `.abort()` cancels the
   request mid-flight (or after the response headers arrive but before
   body read).
4. After every `await`, side-effect code (`setAutoReindexTrigger`,
   `setAdaptationServerError`, `setCurrentVersion`, toasts) is gated
   on `ac.signal.aborted`. A late-arriving response from a superseded
   PATCH MUST NOT poison state belonging to a newer config.
5. `AbortError` rejections are caught and swallowed silently via the
   `isAbortError(err)` helper. No console.error, no toast, no log.
   Any other error type passes through to existing handling.
6. On component unmount, all five controllers are `.abort()`-ed so the
   browser can release connection slots.

## Server contract (current — Slice A only)

Slice A is purely client-side. The server's existing semantics stand:

- The handler processes every request to completion, regardless of
  client disconnect mid-stream.
- Server-side state mutations (`engine.apply_knowledge_config`,
  auto-reindex tasks) DO still happen for aborted requests.
- `request.is_disconnected()` is checked at the top of
  `patch_draft_knowledge` and logged at DEBUG level only. The check
  is informational; it does NOT short-circuit work in Slice A.

## What Slice A buys

- **One reindex tile per user action.** The UI's `setAutoReindexTrigger`
  is gated on `ac.signal.aborted`, so even if two server-side applies
  ran, only the latest's `task_ids` reach the panel polling.
- **One round-trip per intent.** A user who picks 3 profiles within
  800ms now produces ONE PATCH, not three. (Two of the three never
  fire their debounce; the third fires with the latest config.)
- **Faster perceived response.** The user sees stale work cancelled
  immediately on their next click, not after the prior PATCH replies.
- **No leaked AbortControllers** thanks to the unmount cleanup
  effect.

## What Slice A explicitly does NOT buy

- **No prevention of server-side double-apply.** A PATCH whose body is
  already received by the server still runs `engine.apply` even if
  the client has cancelled. This is what Slice B addresses with an
  `_apply_generation` counter on the engine.
- **No cross-tab race protection.** Tab 1 and Tab 2 editing the same
  agent's config can still produce non-deterministic last-writer-wins
  on the server. Slice B's generation counter combined with a
  per-agent lease on the backend would be the proper fix.
- **No retry-on-network-failure.** Existing silent-drop semantics
  preserved; next config change naturally fires a fresh PATCH.

## Verification checklist (manual)

Open DevTools → Network tab → filter `draft/knowledge`:

1. Click **Max Quality** tile.
2. Within 800ms, click **Standard** tile.
3. **Expected:** ONE PATCH fires after the second debounce expires. The
   first never reaches the network because the debounce was reset by
   the second click. Network tab shows one PATCH only.

Repeat with a longer gap:

1. Click **Max Quality**. Wait 1 second (PATCH starts).
2. Within the PATCH's flight (still showing as Pending), click **Speed**.
3. **Expected:** First PATCH transitions to `(canceled)` in red. Second
   PATCH issues after its own 800ms debounce. `setAutoReindexTrigger`
   only fires for the second response — verified by setting a
   breakpoint or `console.log` in that branch (gated on
   `ac.signal.aborted === false`).

Open DevTools Console — no AbortError should appear. The handler at
the `isAbortError(err)` branch is the only place that surfaces them.

## Why the cleanup doesn't `.abort()`

```ts
return () => {
  clearTimeout(t);
  // intentionally NO .abort() here
};
```

React's effect cleanup runs BEFORE every re-run AND on unmount. If we
aborted in cleanup, the new effect run that immediately follows would
find its controller already aborted (we create the new controller in
the body, but the cleanup runs first). Instead, we abort at the TOP
of every new effect run (`ref.current?.abort()`), which is the correct
cancellation point. The unmount-cleanup useEffect with empty deps
handles the actual "page is going away" case.

## Why we use one controller per family, not one global

Each autosave family (knowledge / tools / LLM / agent / special
instructions) corresponds to an INDEPENDENT user surface. A user
adjusting the LLM model while a knowledge profile change is
auto-saving must NOT have the knowledge save cancelled. Separate
controllers preserve independence.

## Pointers to related code

- `src/frontend_workspaces/frontend/src/api.ts` — `signal?: AbortSignal`
  param threaded through 5 PATCH helpers
- `src/frontend_workspaces/frontend/src/ManagePage.tsx` — 5 refs,
  `isAbortError` helper, unmount cleanup useEffect, per-autosave
  abort-at-fire + signal-gate-on-side-effects
- `src/cuga/backend/server/manage_routes.py` — `is_disconnected()`
  DEBUG log in `patch_draft_knowledge` only (the other 4 autosaves
  have negligible server-side work, so the log isn't worth the noise)

## What Slice B will add

- `engine._apply_generation: int` counter incremented atomically by
  `commit_knowledge_update`.
- In-flight `_ingest_inner` / reindex workers capture the generation
  at start and check it before each batch; raise
  `ReindexSupersededError` if it moved.
- `apply_knowledge_config` drops pinned `collection_config` rows for
  agent collections on embedder change (no more stale pin).
- New task status `superseded` (distinct from `cancelled` /
  `failed`).
- Frontend recognizes `superseded` in the poll loop as a silent
  retirement (the next auto-reindex trigger arms a fresh tile).

Slice A's `signal.aborted` guards on the UI side are exactly what
Slice B needs to depend on — the two slices compose cleanly.
