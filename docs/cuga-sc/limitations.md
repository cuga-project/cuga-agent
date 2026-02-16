# CUGA Manage / Single-Config (cuga-sc) — Limitations

This document describes current limitations and design choices for the manage flow (draft vs published config and policies).

## Draft vs published selector: `X-Use-Draft`

**Current behavior:** The server uses the HTTP header **`X-Use-Draft`** to decide whether a request uses draft or published context.

- When `X-Use-Draft` is set to a truthy value (`1`, `true`, `yes`, `on`), the request uses:
  - Draft agent config (from `agent_config_draft` table)
  - Draft policy collection (`{collection_name}_draft`)
  - Draft app state / agent graph
- When the header is absent or falsy, published config and main policy collection are used.

**Limitation:** This is a boolean, single-purpose flag. It does not scale to:

- Multiple named environments (e.g. staging, preview)
- Per-user or per-tenant configs
- Multiple agents with different configs in one deployment

**Preferred evolution:** Support a generic **agent/context selector** (e.g. `X-Agent-Id` or `agent_id` query param) with values such as `default` and `draft`. Draft would be one possible value rather than a special header. New contexts would be new agent ids without new headers.

---

## Policies: two collections (draft vs main)

**Current behavior:** Policies are stored in Milvus (or Milvus Lite). There are **two collections** in the **same** `.db` file:

- Main: `cuga_policies` (or `settings.policy.collection_name`)
- Draft: `{base_name}_draft` (e.g. `cuga_policies_draft`)

There is **no** separate `*_draft.db` file; both collections live in the same Milvus Lite database (e.g. `milvus_policies.db`). The draft collection is created on first use when `PolicyStorage` is initialized with `collection_name=draft_collection`.

**Tradeoffs:**

- **Two collections (current):** Strong isolation between draft and published; simple “draft vs prod” model; easy to reset draft by dropping the draft collection. Schema is duplicated across collections.
- **One collection with scope/agent_id:** Single schema and one place to backup/migrate; “publish” could be an update of scope or a copy by scope. Requires a schema field (e.g. `scope` or `agent_id`) and every query/insert must filter by it; missing filters could mix draft and published.

Either approach is valid; the codebase currently uses two collections for clarity and isolation.

---

## Config: SQLite with two tables

**Current behavior:** Agent config (tools, LLM, etc.) is stored in SQLite (`manage_config.db`):

- **`agent_config`** — published versions (versioned rows).
- **`agent_config_draft`** — single row (id=1) for the current draft.

So config already uses “one DB, two logical buckets.” Policies mirror that idea with two collections in one Milvus DB.

---

## Storage summary

| Data        | Store        | Draft vs published                                      |
|------------|--------------|---------------------------------------------------------|
| Agent config | SQLite       | Two tables: `agent_config`, `agent_config_draft`        |
| Policies   | Milvus Lite  | Two collections in same DB: `cuga_policies`, `cuga_policies_draft` |

---

## Where draft is created

- **Config draft:** Rows in `agent_config_draft` are created/updated by the manage API (e.g. `POST /api/manage/config/draft`). Table is created on first DB access in `config_store`.
- **Policy draft collection:** Created when `PolicyStorage(collection_name=f"{base_name}_draft", ...)` is first initialized and `initialize_async()` runs (e.g. at startup for the draft agent, or on first GET/POST to policy endpoints with `X-Use-Draft`). Implemented in `src/cuga/backend/cuga_graph/policy/storage.py` (`_create_collection()`).
