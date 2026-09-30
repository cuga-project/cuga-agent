# Deferred Tool Loading, `tool_search`, and Compact Validated Schemas for Function-Calling Mode

Status: **implemented (first cut)** on `feature/fc-deferred-tool-search`, off unless
`cuga_lite_execution_mode = "function_calling"` and the catalogue exceeds
`shortlisting_tool_threshold` (`cuga_lite_tool_search = "auto"`). See §9 for what shipped,
what differs from this design, and what is still open.
Base: `origin/feature/native-function-calling-mode` @ `5d563236` (native function-calling
execution mode). Everything here is additive to that branch. It is only active when
`cuga_lite_execution_mode = "function_calling"` (and later the proposed `hybrid` mode),
except §3.9 (validation), which benefits CodeAct too. CodeAct's prompt path is untouched.

Scope: `src/cuga/backend/cuga_graph/nodes/cuga_lite/` (`adapter/`, `helpers/`, `bind_tools/`,
`providers/`), `src/cuga/backend/tools_env/registry/utils/schema_utils.py`,
`src/cuga/backend/cuga_graph/state/agent_state.py`, `settings.toml`, `config.py`.

Reference implementation: Claude Code's deferred tools, `ToolSearch`, and Zod tool
validation, as found in free-code (`free-code-main/src/tools/ToolSearchTool/`,
`src/utils/toolSearch.ts`, `src/services/api/claude.ts`,
`src/services/tools/toolExecution.ts`, `src/utils/toolErrors.ts`, `src/tools/MCPTool/`).

**Goals, in priority order:**

1. **Small context.** On a typical turn the model sees about 5–15 tool schemas, not 128.
2. **Discovery like Claude Code.** Tools are deferred by default. `tool_search` returns the
   top 5 and costs no LLM call on the common path. What the model loads stays loaded.
3. **Validation like Zod.** Every tool has one faithful schema that is checked before
   execution, rejects bad input with a readable error that says what to fix, and is
   rendered compactly wherever the model reads it as text.

---

## 1. The problem

### 1.1 Large catalogues overload function-calling mode

In CodeAct mode a large catalogue is already handled well. Above
`shortlisting_tool_threshold` (35), the prompt collapses to the `find_tools` meta-tool
(`adapter/prepare_node.py:237-274`). Generated code can call *any* tool in
`_tools_context`, so "discovered" and "callable" coincide.

Function-calling mode breaks that equivalence. A native call is reliable only against a
schema the provider received through `bind_tools`, so the question becomes *which schemas
to bind on each model call*. Segev's branch answers it like this:

| Step | Where | What happens |
|---|---|---|
| 1 | `prepare_node.py:740-749` | `_lc_bind_tools_executable_names` = **the entire executable set** |
| 2 | `graph_adapter.py:365-381` (`resolve_bind_tools`) | FC upgrades bind mode `none` → `tools` with that full list |
| 3 | `bind_tools/cap.py:275` (`apply_bind_tools_cap_and_merge`) | Over `cuga_lite_bind_tools_max_count` (128): run the shortlister against the **first user message**, keep the top 128 |
| 4 | `shared_nodes.py` → `call_model` | Steps 2–3 run on **every** `call_model`, with no caching |

Consequences at CUGA's catalogue sizes (AppWorld ≈ 473 tools, M3 apps with hundreds of
endpoints):

1. **Up to 128 full JSON schemas are sent on every model call.** Even as a ceiling, 128
   is an order of magnitude more than a model needs on one step, and selection accuracy
   drops as the list grows.
2. **One extra LLM round-trip per turn.** The shortlister reruns on each `call_model`,
   with the same input every time.
3. **Static relevance.** The ranking is keyed on the first user message only.
4. **Discovery and callability are decoupled.** `find_tools` returns markdown, and the
   tool it finds isn't bound unless it was already in the top 128. The model either calls
   it without a schema (`tool_exec` runs it because the callable is in `_tools_context`,
   `tool_exec_node.py:320`), or gives up.
5. **Misses are unrecoverable.** Nothing can bring a missed tool into the bound set (see
   the hockey case in `docs/design/tool-discovery-subagent.md`).
6. **"Unknown tool" lists every name** (`tool_exec_node.py:322-325`), which is thousands of
   tokens at 473 tools.

### 1.2 Tool schemas are both lossy and bloated

`create_tool_from_api_dict` (`providers/registry.py:150-285`) turns OpenAPI parameters
into a Pydantic `InputModel` via `create_model`. Field types come from
`python_type_for_schema` (`tools_env/registry/utils/schema_utils.py:59`), which maps
each property to a **bare** Python type:

- `enum` is dropped, so `{"type":"string","enum":["asc","desc"]}` becomes `str`.
- `array.items` is dropped, so it becomes `list` of anything.
- Nested `object.properties` are dropped, so it becomes `dict` of anything.
- `minimum`/`maximum`/`pattern`/`format` are dropped. They're kept only as prompt text in
  `param_constraints`.
- Ambiguous `oneOf`/`anyOf` becomes `Any`.

This loss causes three problems:

- **Wrong wire schema.** `bind_tools` derives the provider schema from that `InputModel`,
  so the model is told less than the API knows. It can't see the enum, so it guesses.
- **Weak validation.** `model_validate` (`registry.py:~228`) accepts what the real API
  will reject. The error only surfaces as a remote 4xx after a counted tool call.
- **Raw error text.** A validation failure returns Pydantic's raw `str(ValidationError)`
  (`registry.py:208`). It also happens *inside* the tool function, so it already
  consumed a slot under `counted_tool_call`.

Meanwhile the schema is **bloated where it doesn't help**. Each bound schema carries
Pydantic `title` fields on every property and the full, often multi-paragraph OpenAPI
description. The response schema appears in the `find_tools` markdown as raw JSON.

## 2. How Claude Code does it

### 2.1 Deferred loading and `ToolSearch`

| Mechanism | free-code location | Behaviour |
|---|---|---|
| Tool tiers | `ToolSearchTool/prompt.ts` `isDeferredTool` | **Every MCP (external) tool is deferred**, plus any tool with `shouldDefer`, unless it sets `alwaysLoad`. `ToolSearch` is never deferred |
| Announcement by name only | `claude.ts:1330`, `toolSearch.ts` `getDeferredToolsDelta` | Deferred tools are listed by **name only**, with no schemas |
| Search | `ToolSearchTool.ts` `searchToolsWithKeywords` | `{query, max_results = 5}`. **Deterministic, no LLM.** See §2.2 |
| Loading | `ToolSearchTool.ts` `mapToolResultToToolResultBlockParam` | Returns `tool_reference` blocks, which the Anthropic API expands into full definitions server-side |
| Discovered set | `toolSearch.ts` `extractDiscoveredToolNames`, `claude.ts:1155-1166` | Each request binds `always-loaded ∪ discovered`. There is no count cap. The set stays small because each search adds at most `max_results` (5) |
| Survives compaction | `compact.ts:606` `preCompactDiscoveredTools` | The discovered set is carried across summarization |
| Undiscovered call | `toolExecution.ts:578` `buildSchemaNotSentHint` | *"schema was not sent… call ToolSearch with `select:<name>`, then retry"* |
| Auto-enable | `toolSearch.ts` `checkAutoThreshold` | Defer only when deferred schemas exceed 10% of the context window |

### 2.2 The ranker: how Claude Code filters external tools

`searchToolsWithKeywords` (`ToolSearchTool.ts`) runs in this order:

1. **Exact name.** If the whole query equals a tool name, return that tool alone.
2. **Server prefix.** A query of `mcp__<server>` returns that server's tools, up to
   `max_results`.
3. **Term parsing.** Terms prefixed with `+` are **required**: candidates must match all
   of them in name parts, description, or `searchHint`. Other terms only score.
4. **Name parsing.** `mcp__server__action_name` is split into parts `server`, `action`,
   `name`. Built-in CamelCase names are split on case boundaries.
5. **Scoring per term:**

   | Signal | MCP tool | Built-in |
   |---|---|---|
   | Exact name-part match | **12** | 10 |
   | Substring of a name part | 6 | 5 |
   | Full-name fallback (only if nothing else scored) | 3 | 3 |
   | `searchHint` (curated phrase), word boundary | 4 | 4 |
   | Description, word boundary | 2 | 2 |

6. Drop zero scores, sort descending, and return the **top `max_results` (default 5)**.

Note what the 12-vs-10 split means: an MCP tool's name parts (server *and* action)
outrank a built-in's; the server segment is **not** weighted above the action segment.
There's no model call and no embedding, and it's cheap enough to run on every search.
CUGA's port (§3.4) deliberately goes further and makes the **app** segment the heaviest
signal, because CUGA queries usually name the app and its operation names are noisy.

### 2.3 Schemas and validation

- **Built-in tools** declare `inputSchema` as a **Zod** object. It serves as:
  - the wire schema: `zodToJsonSchema`, sent as JSON Schema;
  - the validator: `tool.inputSchema.safeParse(input)`, which runs **before** permission
    checks and before `call` (`toolExecution.ts:614-620`);
  - the error text: `formatZodValidationError` (`utils/toolErrors.ts:66`) groups issues
    into *missing required*, *unexpected*, and *type mismatch (expected X, received Y)*.
  - Many tools also add a semantic `validateInput`.
- **External MCP tools are *not* Zod-validated client-side.** `MCPTool`'s `inputSchema` is
  `z.object({}).passthrough()` (`MCPTool.ts:14`). The server's JSON Schema is forwarded
  untouched as `inputJSONSchema` (`services/mcp/client.ts:1815`), and the MCP server
  validates.
- **The model always receives JSON Schema.** Zod is the author-side definition, never
  what goes over the wire.

### 2.4 What transfers to CUGA

| Claude Code | Transfers? | CUGA adaptation |
|---|---|---|
| MCP tools always deferred | ✅ | Every registry/OpenAPI/MCP tool is deferred. Only in-graph tools are core |
| Top-5 `ToolSearch` results, no count cap | ✅ | `max_results = 5`, with a **token budget** on the loaded set instead of a 128 count cap (§3.5) |
| Keyword ranker, server weight 12 | ✅ | **App name = MCP server.** Port the scorer, then fall back to embedding and, last, the LLM (§3.3) |
| `tool_reference` server-side loading | ❌ Anthropic API only | Load client-side: bind the discovered schemas on the next `call_model` |
| Zod as single source of truth | ✅ (as Pydantic) | Pydantic *is* Python's Zod. Make the `InputModel` **faithful**, validate before execution, format errors like `formatZodValidationError` (§3.9) |
| MCP passthrough (no client validation) | ⚠️ Do better | CUGA already builds a model per tool, so validating locally avoids a counted remote round-trip for a type error |
| JSON Schema on the wire | Required | Every function-calling provider needs JSON Schema in `tools`. **Minify** it (§3.10). The compact TypeScript-style form is for text the model *reads* (§3.10) |

## 3. Design

### 3.1 Tool tiers

| Tier | Members | Bound when |
|---|---|---|
| **core** | `tool_search`, `create_update_todos` (if enabled), `spawn_agent`/`get_agent_result` (if enabled), `execute_python` (future `hybrid`) | Always |
| **pinned** | `cuga_lite_bind_tools_apps` / `cuga_lite_bind_tools_tool_names` (reinterpreted as "always load"), plus a registry-level `always_load` flag (Claude Code's `alwaysLoad`) | Always |
| **deferred** | **Every** registry / OpenAPI / MCP tool not pinned, like Claude Code's MCP rule | Once discovered |

Computed once in `prepare_node` and stored in `lc_bind_tools_meta`
(`_lc_bind_tools_core_names`, `_pinned_names`, `_deferred_names`).
`_lc_bind_tools_executable_names` remains their union.

### 3.2 Enablement

`cuga_lite_tool_search = "off" | "auto" | "on"` (default `auto`). It's resolved in layers,
like `cuga_lite_execution_mode`: `configurable`, then the model profile, then settings.

- `off`: Segev's current behaviour (full bind, then cap, then per-call shortlist).
- `on`: always defer.
- `auto`: defer when the executable set exceeds `shortlisting_tool_threshold` (35, the
  same size at which CodeAct switches to `find_tools`), **or** its minified schemas would
  exceed `cuga_lite_tool_search_context_pct` (10) of the context window.

When active, `resolve_bind_tools` never calls `apply_bind_tools_cap_and_merge` on the
executable set, so **there is no per-turn shortlister call.**

### 3.3 `tool_search` with a Claude-Code-style ranker

```
tool_search(query: str, app_name: str | None = None, max_results: int = 5)
```

The ranker is a cascade, cheapest first. **The first stage that yields a confident result
wins.**

| # | Stage | Cost | Mirrors |
|---|---|---|---|
| 1 | `select:a,b,c`: exact names, multi-select. Already-loaded names are a no-op; unknown names get ≤ 3 close matches (difflib) | ~0 | `select:` |
| 2 | Bare exact tool name | ~0 | exact fast path |
| 3 | **App prefix.** `app_name` given, or the query is exactly an app name: restrict to that app. If the app has ≤ `max_results` tools, return them all | ~0 | `mcp__server` prefix |
| 4 | **Keyword scorer** (§3.4): `+required` terms, name-part / app / hint / description weights | ~ms | `searchToolsWithKeywords` |
| 5 | **Embedding** (local, `shortlister/embedding.py`): runs only if stage 4 is *not confident* | ~10s of ms, no LLM | — (CUGA-specific) |
| 6 | **LLM shortlister** (`[shortlister.discovery]`, app-scoped, pool capped at 60): runs only when stage 4 is not confident **and** stage 5 is unavailable (`low_confidence`), or always (`always`) | 1 LLM call | — (CUGA-specific) |
| 7 | **Escalation subagent** (`tool_discovery_dispatch.py`): runs only if `tool_discovery.enabled` and stage 6 returned nothing | subagent | — |

**Confidence** (stages 4–5) is `top_score ≥ min_score` **and**
`top_score − score[max_results] ≥ margin`: the top-5 must stand out from the rest.
Thresholds are settings with measured defaults (§7). Claude Code needs no fallback
because its names are self-describing. CUGA's OpenAPI names often aren't (the hockey
miss), which is why stages 5–6 exist. On the common path, though, a search costs
**no LLM call**.

Without `app_name`, and when no app name appears in the query, stages 4–6 first pick the
top apps by aggregate score (the two-stage pool from `_app_scoped_ranking_pool`,
`react_hybrid/tool_shortlist_middleware.py` on `feature/tool-discovery-subagent`), and then
rank within them. Ranking ~473 tools in one LLM prompt was measured to overflow a 131K
context.

**The result** is always **≤ `max_results` (default 5)** tools, rendered in the compact
signature form (§3.10):

```
Loaded 3 tools (callable now):
hockey_get_coaches_who_were_players(season?: int, limit?: int = 50) -> Coach[]
  List coaches who previously played in the league.
hockey_get_player_coach_ids(player_id: int) -> {coach_ids: int[]}
  …
```

`ToolMessage.artifact = {"discovered": [...], "stage": "keyword|embedding|llm|…", "scores": {...}}`.
The artifact feeds §3.4's set and the eval telemetry.

### 3.4 The keyword scorer: a CUGA port of `searchToolsWithKeywords`

In a new module, `shortlister/keyword.py`, registered as a `ShortlisterRouter` strategy so
`find_tools` can use it too:

- **Name parts.** Registry names are `<app>_<operation>`. Split on `_`, `-`, `.` and
  camelCase. The **app segment is scored like the MCP server** (12 for an exact match,
  6 for a substring), and operation parts like built-in parts (10 / 5).
- **Required terms** (`+term`) pre-filter over name parts, description and `search_hint`.
- **`search_hint`** (4): a new optional registry field, a curated capability phrase. It
  defaults to the OpenAPI `summary`, which is the exact text that fixed the hockey miss
  when the query mirrored it.
- **Description** (2): word-boundary match. OpenAPI `operationId` and path segments are
  added as extra "name parts" at substring weight, since they carry the resource nouns.
- **Light normalization**: lower-casing, plural/singular folding (`coaches` ↔ `coach`), and
  a small stop-word list. No stemming library.
- Descriptions are lower-cased once per catalogue and cached (Claude Code memoizes by
  deferred-set key).

### 3.5 Working set: a token budget, not a 128 count cap

```
bound = core ∪ pinned ∪ loaded
```

`loaded` is the discovered set **after** the budget below. It's ordered by discovery, only
grows at the tail, and costs no LLM call to compute.

The loaded set is limited by **minified schema tokens**, not by count:

- `cuga_lite_tool_search_loaded_token_budget`: default **6,000** tokens, roughly 10–20
  minified tools (see §3.10).
- `cuga_lite_tool_search_loaded_max`: default **20**, a hard count backstop for providers
  that degrade with many tools.
- Over either limit: evict **least-recently-used** loaded tools (by last appearance in
  `tool_calls`). Core and pinned are never evicted. Evicted tools stay callable from code
  and re-loadable with `select:`. The next `tool_search` result lists what was unloaded
  (one line).

`cuga_lite_bind_tools_max_count` (128) stays only as the provider's absolute limit on
`core + pinned + loaded`. In practice it's unreachable.

Claude Code has no cap here because each search adds only 5 tools. CUGA adds a budget
because an agent loop running for 35 steps can accumulate many searches.

### 3.6 The discovered set: persistence

- **From history.** `discovered_tool_names(messages)` unions `artifact["discovered"]` from
  `tool_search` `ToolMessage`s (the `extractDiscoveredToolNames` equivalent). The native
  transcript persists across turns on Segev's branch (`a6fa89d3`).
- **On state.** `discovered_tools: List[str]`, with an ordered-union reducer, on both
  `CugaLiteState` and `AgentState`. It crosses the subgraph boundary like
  `tool_calls_used_thread` (`agent_state.py:999`).
- **Across summarization.** `apply_context_summarization`
  (`utils/context_management_utils.py:94`) must keep `AIMessage.tool_calls` and their
  `ToolMessage`s together and never drop `discovered_tools`. This is the
  `preCompactDiscoveredTools` equivalent. It's a prerequisite: today the summarizer has no
  notion of tool messages.

### 3.7 Announcing deferred tools

Additions to `prompts/fc_prompt.jinja2`:

```
# FINDING TOOLS
Most tools are not loaded. Call `tool_search` to load the ones you need (top 5 per search);
loaded tools are callable on your next step and stay loaded.
- select:<name>[,<name>…]   load exact tools
- <keywords>  +<required>   keyword search; name the app for best results
Apps (loaded/total): hockey (0/212), movies (0/48), …
```

At or below `cuga_lite_tool_search_list_names_max` (150) deferred tools, the prompt also
lists names per app, which lets strong models go straight to `select:`. Above that, it
lists apps and counts only. Runtime-added apps (`prepare_node.py:491-494`) get a one-line
delta on the next outbound user turn. That line is outbound-only and never persisted
(like the variables addendum), mirroring `deferred_tools_delta`.

### 3.8 Calling a tool that isn't loaded

In `tool_exec` (`tool_exec_node.py:320`):

1. **Known but not loaded.** Validate the arguments with the tool's schema (§3.9) first.
   - **Valid:** execute, then auto-promote the tool into `discovered`.
   - **Invalid:** don't execute. Return the §3.9 error, followed by *"`<name>`'s schema
     wasn't loaded. Call `tool_search` with `select:<name>` and retry."* This is
     `buildSchemaNotSentHint`.
2. **Unknown name.** Return up to 5 close names (difflib), plus *"or use `tool_search`."*
   Never list the full catalogue.

### 3.9 Zod-style validation: one faithful schema, checked before execution

**The principle** (`inputSchema` + `safeParse`): each tool has **one** schema object that
is the wire schema, the validator and the error vocabulary. In Python that object is the
Pydantic `InputModel`. It exists today but is lossy (§1.2).

**A. A faithful `InputModel`.** Replace `python_type_for_schema`'s flat mapping with a
recursive JSON-Schema-to-Pydantic builder (`schema_utils.build_field_type`):

| JSON Schema | Today | Proposed |
|---|---|---|
| `enum` | `str` | `Literal[...]` |
| `array` + `items` | `list` | `List[<item type>]` (recursive) |
| `object` + `properties` | `dict` | nested `create_model(...)` (recursive, depth-capped) |
| `minimum`/`maximum`/`minLength`/`pattern` | prompt text only | `Field(ge=, le=, min_length=, pattern=)` |
| `format: date`/`date-time`/`email` | `str` | `str` + `json_schema_extra={"format": …}`. Checked, but lax so ISO strings still pass |
| `oneOf`/`anyOf` | `Any` | `Union[...]` when every branch is typed; else `Any` (unchanged, fail-open) |
| unknown keys | allowed, then dropped | `model_config = ConfigDict(extra="forbid")`, so they're reported as *unexpected* |

Fallback: if model construction fails for a tool, keep today's flat model for that tool
and log it. This never blocks tool registration. `mcp_manager/adapter.py` and
`providers/combined.py` (a duplicate of the registry builder) get the same builder.

**B. Validate in `tool_exec`, before the budget.** `tool_exec` calls
`InputModel.model_validate(kwargs)` **before** `counted_tool_call`, the same order as
Claude Code: `safeParse`, then permissions, then `call`. So a malformed call:

- doesn't consume `max_tool_calls_per_run` / `_per_thread`;
- doesn't hit the remote API;
- returns in milliseconds.

The in-function validation in `registry.py` stays as defence in depth for CodeAct, where
code calls the function directly. CodeAct gets the better error text from D below for
free.

**C. Strict vs. lax.** Pydantic's default *lax* mode coerces `"5"` to `5`. Keep lax mode:
weaker models string-encode numbers, and Zod's strictness there would cost retries for no
safety gain. `extra="forbid"` and `Literal` enums provide the strictness that matters.
`cuga_lite_tool_validation = "lax" | "strict" | "off"` (default `lax`).

**D. Error text like `formatZodValidationError`** (`utils/toolErrors.ts:66`). This is a new
`helpers/tool_validation_errors.py` that maps Pydantic error types into Claude Code's three
groups:

```
Invalid arguments for hockey_get_games:
- missing required: season
- unexpected: team_name  (valid parameters: season, team_id, limit)
- type mismatch: limit expected integer, received string "fifty"
- not allowed: order must be one of "asc" | "desc", received "ascending"
Fix the arguments and call hockey_get_games again.
```

It incorporates the "valid parameter names" hint that already exists on
`feature/hybrid-cuga-agent` (commits `199eabe4`/`dd022afe`/`1d778242`).

**E. Semantic validation (optional).** An optional per-tool `validate_input(args) -> str | None`
hook, the equivalent of Claude Code's `validateInput`. It lets policy and ToolGuard plug
in checks that a schema can't express. It runs after schema validation and before the
budget.

### 3.10 Context reduction: minified wire schemas and compact text signatures

There are two surfaces, with different constraints.

**A. The wire (`bind_tools`) must be JSON Schema.** Minify it in a new
`bind_tools/minify.py`, applied to the model LangChain derives, before `_safe_bind`:

- drop every `title` (Pydantic adds one per property and per model);
- inline `$ref`/`$defs` up to a small depth, and drop unused `$defs`;
- truncate the tool `description` to its first sentence or
  `cuga_lite_tool_description_max_chars` (default 300). Truncate parameter descriptions
  to 120 characters;
- drop `default: null` and `anyOf: [{…}, {"type": "null"}]`. Mark optionality through
  `required` alone;
- never include response schemas. Those go only in the text form (B).

**B. What the model reads as text uses compact TypeScript-style signatures.** A new
`helpers/signature_render.py` renders **the same `InputModel`** (so the text can never
disagree with the validator):

```
hockey_get_games(season: int, team_id?: int, order?: "asc" | "desc" = "desc", limit?: int (1..100) = 50) -> Game[]
  Games for a season, newest first.
  Game = {id: int, date: date, home: {team_id: int, score: int}, away: {…}}
```

Rules:

- `?` marks optional parameters, followed by the default;
- enums render as unions of literals, ranges as `(lo..hi)`, arrays as `T[]`;
- nested objects are inline to depth 2, with `{…}` beyond that;
- the return type is rendered from `_response_schemas` the same way and truncated to a
  per-tool budget, `cuga_lite_signature_max_chars` (default 600);
- the description is one line.

Where the compact form is used:

- `tool_search` results (§3.3). This replaces the markdown and JSON dump from
  `render_tools_markdown`;
- the deferred-name announcement, when names are listed (§3.7). Names only; signatures
  come via search;
- the "known but not loaded" hint (§3.8);
- optionally the CodeAct `find_tools` output behind the same renderer
  (`cuga_lite_find_tools_render = "markdown" | "signature"`, default `markdown` until
  measured).

**Measured** (cl100k tokens) on a realistic 6-parameter endpoint with enums, bounds and a
nested array response:

| Form | Tokens |
|---|---|
| Default wire schema (faithful model, LangChain conversion) | 348 |
| Minified wire schema (description ≤ 300 chars kept whole) | 284 |
| Compact signature incl. return type + one-line description | 111 |

Minification alone saves less than hoped (~18%) because short descriptions are kept whole;
the big reduction comes from **how many** schemas are bound (§3.5), not from per-schema
trimming. A typical turn binds `core (≈1–3) + loaded (≈5–15)` schemas — roughly 1.5–5K
tokens — against up to 128 full schemas today.

**C. Rejected alternative: signature-only binding.** Bind a single generic
`call_tool(name: str, args: object)` and show signatures only as text. This would give
the minimum context, but it gives up provider-side argument structuring. Every call
would then depend on our validator catching errors after the fact, and it's the pattern
`tool_exec`'s "schema not loaded" hint exists to steer away from. Keep it only as a
possible fallback for providers with hard tool-count limits; it's not part of this design.

### 3.11 Interaction with other modes and features

- **CodeAct:** the prompt path is unchanged. It benefits from §3.9: faithful models,
  better error text, and pre-budget validation for native-style calls recovered from
  `tool_calls`.
- **Future `hybrid` mode:** `execute_python` is core. Code can call **any** tool
  regardless of the loaded set, so deferral governs only the native surface. `tool_search`
  results note that loaded tools are also callable from code.
- **Policy / HITL:** `tool_search` is read-only. Loading never bypasses tool approval.
- **Budgets / tracking:** `tool_search` and validation failures aren't counted by
  `counted_tool_call`. The tracker adds `Tool_search` (query, stage, discovered) and
  `Tool_validation_error` steps.
- **Step discipline** (`one_tool_per_step`): `tool_search` is exempt from "first call
  only".

## 4. Settings

| Key (`advanced_features`) | Default | Meaning |
|---|---|---|
| `cuga_lite_tool_search` | `"auto"` | `off` \| `auto` \| `on` (FC/hybrid) |
| `cuga_lite_tool_search_context_pct` | `10` | `auto`: defer when minified schemas exceed this % of context |
| `cuga_lite_tool_search_max_results` | `5` | Results per search (Claude Code's default) |
| `cuga_lite_tool_search_loaded_token_budget` | `6000` | Minified-schema token budget for loaded tools (LRU eviction) |
| `cuga_lite_tool_search_loaded_max` | `20` | Hard count backstop for loaded tools |
| `cuga_lite_tool_search_llm_fallback` | `"low_confidence"` | `never` \| `low_confidence` \| `always` (stage 6) |
| `cuga_lite_tool_search_min_score` / `_margin` | measured | Confidence gate for stages 4–5 |
| `cuga_lite_tool_search_list_names_max` | `150` | List deferred names in the prompt up to this many |
| `cuga_lite_tool_search_seed_k` | `0` | Pre-load the top K (≤ 5) once in prepare (for weak-FC model profiles) |
| `cuga_lite_tool_validation` | `"lax"` | `lax` \| `strict` \| `off`: pre-execution validation in `tool_exec` |
| `cuga_lite_tool_description_max_chars` | `300` | Wire-schema description truncation |
| `cuga_lite_signature_max_chars` | `600` | Per-tool budget for the compact text signature |
| `cuga_lite_find_tools_render` | `"markdown"` | CodeAct `find_tools` output: `markdown` \| `signature` |

Reused: `shortlisting_tool_threshold` (auto trigger), `cuga_lite_bind_tools_max_count`
(provider absolute limit), `cuga_lite_bind_tools_apps`/`_tool_names` (pinned),
`[shortlister.discovery]` (stage 6). All keys are readable from `config["configurable"]`
and env (`DYNACONF_ADVANCED_FEATURES__…`).

## 5. Change list

| File | Change |
|---|---|
| `tools_env/registry/utils/schema_utils.py` | Recursive `build_field_type` (enum/items/properties/constraints), fail-open fallback |
| `cuga_lite/providers/registry.py`, `providers/combined.py`, `registry/mcp_manager/adapter.py` | Use the faithful builder, `extra="forbid"`, `search_hint` from the OpenAPI `summary` |
| `cuga_lite/helpers/tool_validation_errors.py` (new) | Pydantic errors grouped Zod-style: missing / unexpected / type / enum |
| `cuga_lite/helpers/signature_render.py` (new) | Compact TS-style signature from `InputModel` + `_response_schemas` |
| `cuga_lite/bind_tools/minify.py` (new) | Wire-schema minifier |
| `cuga_lite/shortlister/keyword.py` (new) | `searchToolsWithKeywords` port as a `ShortlisterRouter` strategy |
| `cuga_lite/helpers/tool_search.py` (new) | `create_tool_search_tool`: stages 1–7, compact result, artifact |
| `cuga_lite/bind_tools/deferred.py` (new) | Tiers, `discovered_tool_names`, token-budget LRU, `resolve_deferred_bind_set` |
| `cuga_lite/adapter/prepare_node.py` | Tier split, `tool_search` construction, optional seed, prompt inputs |
| `cuga_lite/adapter/graph_adapter.py` | `resolve_bind_tools`: deferred branch (no cap/shortlist), minify before bind |
| `cuga_lite/adapter/tool_exec_node.py` | Pre-budget validation, not-loaded hint + auto-promote, close-match unknown tool, `tool_search` exemptions |
| `cuga_lite_graph.py`, `state/agent_state.py` | `discovered_tools` + ordered-union reducer |
| `utils/context_management_utils.py` | Keep tool-call pairs; carry `discovered_tools` |
| `prompts/fc_prompt.jinja2` | `FINDING TOOLS` block |
| `model_runtime_profile.py` | `resolve_tool_search_mode`; per-model `seed_k` / `llm_fallback` |
| `settings.toml`, `config.py` | Keys from §4 |

## 6. Tests

All `@pytest.mark.unit`, in `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/` (collected
by `unit-a`) or `tests/unit/`:

- **Schema builder:** enum → `Literal`, nested arrays and objects, constraints, `oneOf`
  fallback to `Any`, depth cap. A construction failure falls back to the flat model.
  Round-trip: build a model from each fixture OpenAPI operation, then `model_json_schema()`
  keeps enums and items.
- **Validation errors:** each error class renders into the right group. Valid parameters
  are listed on unexpected keys. Lax mode coerces `"5"` → `5`. Strict mode rejects it.
- **Pre-budget ordering:** a malformed call returns an error `ToolMessage`, **does not**
  increment `tool_calls_used_run`, and never awaits the tool coroutine.
- **Minifier:** no `title`, no null-union noise, description truncated, token count below
  a fixture ceiling. The minified schema still validates the same payloads.
- **Signature renderer:** golden strings for a fixture catalogue. Output is ≤
  `signature_max_chars`.
- **Keyword scorer:** app-name weight dominates, `+required` filters, `search_hint`
  outranks description, plural folding, exact-name fast path. Parity cases ported from
  `ToolSearchTool.ts` behaviour.
- **Cascade:** a confident keyword hit never calls the embedding or LLM mocks; low
  confidence escalates in order; `llm_fallback="never"` never calls the LLM.
- **Working set:** grows only via discovery, has stable order, respects the token budget
  and count backstop with LRU eviction, never evicts core or pinned. **No shortlister call
  on `call_model`** when tool search is active.
- **Not loaded / unknown:** valid arguments execute and promote; invalid arguments give
  the hint and no execution. Unknown names give ≤ 5 suggestions.
- **Multi-turn and summarization:** the discovered set survives an SDK two-turn run
  (extend `tests/unit/test_fc_sdk_two_turn.py`) and a forced summarization.

## 7. Evaluation

Configurations:

1. FC + cap (today on Segev's branch)
2. FC + tool search, keyword-only (`llm_fallback=never`)
3. FC + tool search, full cascade
4. Configuration 3 + `seed_k=5`
5. CodeAct baseline

Each runs with validation `off` and `lax`.

- **Datasets:** M3 (hockey, movie_3, formula_1: the known discovery misses) and AppWorld
  (~473 tools).
- **Headline metric:** **input tokens per model call** (tool-schema share reported
  separately).
- **Other metrics:** task success; LLM calls spent on discovery per task; model
  round-trips per task; ranker stage hit-rates; `tool_search` hit/miss; loaded-set size
  over time; evictions; validation rejections that were caught locally vs. remote 4xx
  errors.
- **Models:** one strong FC model and gpt-oss-20b (weak FC, has a runtime profile).
- **Calibration:** `min_score`/`margin` are fit on a held-out slice of M3 queries so that
  stage 4 alone reaches ≥ 90% of the full cascade's recall@5.

Expectations:

- tool-schema tokens per call drop by roughly an order of magnitude;
- discovery LLM calls drop to near zero on apps with descriptive names;
- success is equal or better, because misses become recoverable and remote 4xx loops
  become local corrections;
- a possible small regression on single-call tasks from the extra discovery round-trip,
  which is what `seed_k` measures.

## 8. Risks and open questions

1. **Keyword recall on OpenAPI names.** This is why the cascade exists. Better
   `search_hint`s (from the OpenAPI `summary`, or LLM-generated offline once per
   catalogue) raise stage-4 recall without per-query cost.
2. **Weak models not calling `tool_search`.** Mitigations: `seed_k`, the prompt block,
   and the existing blocked-claim retry in `classify_auto_continue`.
3. **Faithful schemas rejecting what the API accepts.** Mitigations: fail-open `Any` for
   ambiguous branches, lax mode by default, per-tool fallback to the flat model, and
   `cuga_lite_tool_validation = "off"` as a kill switch.
4. **Pydantic `Literal` and `pattern` on large catalogues.** Model construction cost at
   startup needs measuring. Build lazily per tool on first load if it's significant.
5. **Prompt caching.** Append-only loading limits cache invalidation to the tail, and
   eviction breaks it. The budget is sized so eviction is rare.
6. **Summarizer pairing is a prerequisite** (§3.6), independent of this feature.
7. **Naming.** A new `tool_search` keeps CodeAct's `find_tools` contract and few-shots
   byte-identical. Revisit when `hybrid` unifies the surfaces.
8. **Merge order.** This depends on `feature/native-function-calling-mode`, and on
   `_app_scoped_ranking_pool` / `tool_discovery_dispatch` from
   `feature/tool-discovery-subagent`. Land those first, or copy the pool function in. The
   §3.9 validation and §3.10 renderer have no FC dependency, so they can land first,
   independently, on `main`.


## 9. Implementation status

Branch `feature/fc-deferred-tool-search`, on top of `origin/feature/native-function-calling-mode`.

**Shipped**

| Piece | Where |
|---|---|
| Faithful input models (enum/items/nested/bounds, `extra="forbid"`), recovered from the registry's constraint strings and `schema` summaries; `cuga_lite_tool_validation = lax \| strict \| off` | `tools_env/registry/utils/schema_model.py`, used by `providers/registry.py` and `providers/combined.py` |
| Zod-style grouped errors (missing / unexpected + valid params / type mismatch / not allowed) | `helpers/tool_validation_errors.py` |
| Pre-budget validation in `tool_exec` (rejects only; the tool still receives the original kwargs) | `adapter/tool_exec_node.py::_validate_args` |
| Compact TS-style signatures from the same Pydantic model; opt-in for CodeAct `find_tools` via `cuga_lite_find_tools_render = "signature"` | `helpers/signature_render.py`, `shortlister/render.py::render_tools_signatures` |
| Wire-schema minifier (tool-search bind path only; the legacy cap path is byte-identical) | `bind_tools/minify.py` |
| Keyword ranker (port of `searchToolsWithKeywords` + name-coverage length normalization), built-in shortlister strategy `keyword` | `shortlister/keyword.py` |
| OpenAPI `summary` carried through the registry and scored as the search hint | `mcp_manager/openapi_parser_v0.py`, `providers/registry.py` |
| `tool_search` tool: select / exact / app narrowing / keyword / embedding (RRF-fused, agreement-gated) / LLM | `helpers/tool_search.py` |
| Tiers, discovered set (history + `discovered_tools` state carry with `union_ordered` reducer), token-budget LRU, minified bind | `bind_tools/deferred.py`, `state/agent_state.py`, `cuga_lite_graph.py` |
| `auto` trigger by count **or** schema share of the context window (`cuga_lite_tool_search_context_pct`, 10%) | `bind_tools/deferred.py::tool_search_active` |
| Wiring: prepare builds the surface and the `FINDING TOOLS` prompt block; `resolve_bind_tools` binds `core ∪ pinned ∪ loaded` with no shortlister call; `tool_exec` runs `tool_search` uncounted, exempt from step discipline, adds the `select:` hint and close-match unknown-tool replies | `adapter/prepare_node.py`, `adapter/graph_adapter.py`, `adapter/tool_exec_node.py`, `prompts/fc_prompt.jinja2` |
| Trajectory steps `Tool_search` and `Tool_validation_error` | `adapter/tool_exec_node.py::_collect_step` |
| Pair-safe truncation: summary-failure hard truncation and the sliding window never start on an orphaned `ToolMessage` (LangChain's middleware already was safe) | `utils/message_utils.py::pair_safe_tail` |
| Optional `seed_k` (keyword stage only, no model call) | `helpers/tool_search.py::prepare_tool_search_surface` |
| Calibration harness over the M3 REST catalogue | `scripts/tool_search_calibration.py` |

**Calibration (M3 REST catalogue, 80 domains, 9,891 endpoints, mean pool 124 per domain).**
Each endpoint's source comment (written independently of its `summary`) is the query; the
endpoint is the gold tool; ranking is within the endpoint's domain. The comments and
summaries share an author, so absolute numbers are optimistic compared with real user
phrasing — use them to compare variants, not as benchmark scores.

| Keyword stage | recall@1 | recall@5 |
|---|---|---|
| Claude Code scoring, ported | 70.6% | 93.3% |
| + name-coverage normalization (w = 0.5) | **79.5%** | **96.8%** |

Confidence gate: an absolute margin does not separate hits from misses (long queries give
large raw scores; AUC 0.83), a relative margin `(top − first_cut) / top` does better
(AUC 0.85). With `min_score = 10`, `margin_ratio = 0.2`, the keyword stage answers
**91%** of searches with no model call, with the gold tool in the top 5 for **98.1%** of
them; the remaining 9% go to embedding / LLM. Normalization also fixes the documented
hockey miss (`hockey_get_coaches_who_were_players` now ranks first; pinned by a test).

**Live M3 results (hockey, 10 labeled tasks, gpt-oss-120b; bundles in
`cuga-eval/benchmarks/m3/evaluation_bundles/20260930_*`).** CodeAct 4/10 at 1.2M tokens;
function calling + tool_search 5–6/10 at 126–166K tokens (≈7–9× fewer). Tasks where the gold
tool's response was reached: CodeAct 5, FC v1 6, v2 6. (A "novelty" variant — repeat searches
skipping already-loaded tools — reached 7 but was rolled back.) Fixes that came out of those runs:

- `select:` with unknown (guessed) names falls back to a keyword search on the words of the
  names instead of returning nothing;
- the LLM stage's picks lead but are padded from the keyword/embedding ranking, never replacing it;
- a per-search time budget (`cuga_lite_tool_search_time_budget_s`, 20 s) skips the embedding/LLM
  stages when spent instead of hitting tool_exec's 30 s timeout;
- one corrective turn when the model answers after `tool_search` without calling any tool.

**Differs from the design above**

- The keyword ranker adds name-coverage length normalization (not in Claude Code).
- The confidence gate uses a relative margin (`cuga_lite_tool_search_margin_ratio`), not
  an absolute one.
- The embedding stage is gated by agreement with the keyword top pick rather than a
  cosine threshold (scores are not comparable across embedding models); disagreement
  escalates to the LLM in `low_confidence` mode.
- The minifier applies only on the tool-search bind path, not to every native bind.

**Not done yet**

- Escalation-subagent stage (§3.3 stage 7) — depends on `feature/tool-discovery-subagent`.
- Per-tool semantic `validate_input` hook (§3.9 E) — no producer exists yet.
- End-to-end evaluation (§7) on live M3 / AppWorld runs with real models; the calibration
  above covers the keyword stage only, on a proxy query set.
- Per-model defaults for `seed_k` (e.g. gpt-oss-20b) — wait for the live evaluation to show
  whether weak function-calling models skip `tool_search`.
