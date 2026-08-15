# Pluggable Shortlister for CUGA Lite — Spec & Design

Status: implemented (#624), with deliberate reductions — see "What shipped" below
Scope: `src/cuga/backend/cuga_graph/nodes/cuga_lite/`, `src/cuga/sdk.py`, `src/cuga/config.py`

## What shipped vs. what this document designed

The implementation deliberately dropped several designed pieces to keep the diff
against `main` minimal. Each is easy to add later if a need appears:

| Designed | Shipped | Why |
|---|---|---|
| `ShortlistResult` with a `notes` list | strategies return `List[ShortlistCandidate]` | `notes` had no producer once name-validation was dropped; a bare list is the simplest thing that works |
| Name-validation + bounded retries in `LLMShortlister` | not implemented | that is PR #549's unmerged work; duplicating it would have conflicted and changed behavior beyond this feature's scope. `main` silently drops unknown names, and so does this |
| `prewarm` config + `prepare_node` hook | not implemented | the lazy-load + fallback path already guarantees no query blocks on a download, so prewarm was a pure optimization — and it was the only reason `prepare_node.py` appeared in the diff |
| `[shortlister] model` (agent-settings key for the LLM leg) | not implemented | unrequested scope; the LLM leg keeps `settings.agent.code.model`, exactly as before |
| `prompt_preselect` | not implemented | was already marked "later, measure first"; shipping a dead config key is worse than shipping none |
| Batch/asymmetric helpers added to `storage/embedding/embedding_service.py` | implemented inside `shortlister/embedding.py` | keeps the change self-contained and leaves the knowledge engine untouched |
| Server/demo-UI override wiring | not implemented | tracked as follow-up; settings, env, `configurable` and the SDK all work |

**Related work:** no existing issue or PR proposes a pluggable/non-LLM shortlister — this is new.
Two open PRs touch the same functions and must be sequenced against; see [§8](#8-related-issues-and-prs).

---

## Context

CUGA Lite reduces a large tool catalogue down to a workable candidate set before the model
sees it. Today that reduction is **always LLM-based**, at two independent call sites, and it
is not configurable beyond an on/off threshold.

**Why change it:**

1. **Cost & latency.** Every `find_tools` call is a full LLM round-trip whose prompt contains
   the serialized schema of every tool in the app. On a 300-tool catalogue that is a large
   prompt, paid for on every discovery call. The bind-time cap adds *another* round-trip per
   `call_model` (`helpers/bind_tools.py:205-221` documents this cost explicitly).
2. **Model coupling.** The bind-time shortlister runs on *the same model being bound*, so a
   model without native structured output cannot shortlist at all — the reason
   `BindToolsUnsupportedError` exists (`bind_tools/cap.py:35-43`). A non-LLM shortlister
   removes that coupling entirely.
3. **No extension point.** `PromptUtils.shortlist_tool_names` and `PromptUtils.find_tools` are
   `@staticmethod`s called by name from two modules. There is no seam to swap the ranker.

**Intended outcome:** a `ShortlisterStrategy` seam with three built-ins (`llm` — today's
behavior and the default, `embedding` — cosine similarity, `hybrid` — cosine prefilter then
LLM rerank), selectable by config name or dotted class path, plus a `CugaAgent(shortlister=…)`
SDK argument. Default behavior is byte-for-byte unchanged.

---

## 1. Current state — exactly where shortlisting happens

CUGA Lite's graph is three nodes (`cuga_agent_core/graph/shared_graph.py:50-61`):

```
START → prepare → call_model ⇄ sandbox → END
```

Tool reduction happens in **four layers**; only layers 3 and 4 are LLM-based, and those are
the two seams this spec makes pluggable.

| # | Layer | Location | Trigger | LLM? |
|---|---|---|---|---|
| 1 | App-level filtering | `adapter/prepare_node.py:169-224` | `sub_task_app` / `api_intent_relevant_apps` / `force_lite_mode_apps` | no |
| 2 | Prompt collapse to `find_tools` | `adapter/prepare_node.py:225-263` | `total_tool_count > shortlisting_tool_threshold` (35) | no |
| **3** | **Runtime discovery — `find_tools`** | `helpers/find_tools.py:54-137` → `prompt_utils.py:297-458` | agent calls it | **yes — seam A** |
| **4** | **Bind-time provider cap** | `bind_tools/cap.py:275-383` → `prompt_utils.py:461-541` | candidates > `cuga_lite_bind_tools_max_count` (128) | **yes — seam B** |

### Seam A — `PromptUtils.find_tools(query, all_tools, all_apps, llm, run_config) -> str`

When layer 2 fires, the prompt shows **only** `find_tools`; the real callables stay in the
sandbox namespace (`adapter/prepare_node.py:249-258`). The agent then calls
`await find_tools(query, app_name)` in generated code. That runs an LLM chain against
`prompts/shortlister/system.jinja2` with schema `ShortListerOutputLite`, then enriches each
ranked name into params/response docs and returns **markdown**.

Two behaviors to preserve: no fixed result count, and failures are **swallowed into an error
string** for the agent rather than raised (`helpers/find_tools.py:112-131`).

### Seam B — `PromptUtils.shortlist_tool_names(query, all_tools, all_apps, llm, top_k, instructions, run_config) -> List[str]`

Same chain, but injects a `Return the {top_k} most relevant tools…` instruction and returns
ranked names. Guards already in place: `top_k <= 0` or empty tools → `[]`; whitespace-only
query → `[]`; hallucinated names filtered against `valid_names`; clamped to `top_k`.

Failures here are **deliberately loud** — `RuntimeError` for a genuine failure or an empty
ranking, re-raised through `adapter/graph_adapter.py:159-161` because silent truncation would
corrupt native-vs-text benchmark comparisons.

### The shared payload builder

Both seams serialize candidates through `PromptUtils._build_shortlister_payload`
(`prompt_utils.py:254-295`), whose docstring states it exists specifically to stop the two
callers drifting. The new design keeps that single-source property by routing both through one
strategy interface.

---

## 2. What already exists and must be reused

| Need | Existing utility | Path |
|---|---|---|
| Async embed function (OpenAI / fastembed local / auto) | `create_embedding_function()` → `(embed_fn, dim)` | `backend/storage/embedding/embedding_service.py:58-96` |
| Embedding config shape to copy (**not** inherit — see §3.4) | `get_embedding_config()`, `[storage.embedding]` | `embedding_service.py:22-39`, `settings.toml:136-141` |
| Default model already registered at 384 dims | `LOCAL_MODEL_DIMS["sentence-transformers/all-MiniLM-L6-v2"]` | `embedding_service.py:13-19` |
| Model cache (already warm on most installs) | `FASTEMBED_CACHE_DIR` → `~/.cache/cuga/fastembed` | `config.py:47-50` |
| Cosine precedent | `np.dot` of normalized vectors vs a threshold | `cuga_graph/policy/agent.py:282-321` |
| Param/response doc text for a tool | `PromptUtils.get_tool_docs(tool)` → `(params_doc, response_doc)` | `cuga_lite/prompt_utils.py` |
| Dotted-path class loading | `get_class(path)` (used by `page_understanding.transformer_path`) | `config.py:363-367` |
| Router → typed plan pattern | `ExecutionRouter.resolve()` → `ExecutionPlan` | `cuga_agent_core/policy/execution_policy.py:62-141` |
| Config precedence pattern | `resolve_bind_tools_fields` (configurable > model profile > settings) | `cuga_lite/model_runtime_profile.py:75-116` |
| Non-blocking model load contract | background load + `is_ready`/`ensure_loading`/`prewarm` | `backend/knowledge/reranker.py:1-20` |

**No new dependencies.** `fastembed>=0.4.0` is already a core dep (`pyproject.toml:44`).

---

## 3. Design

### 3.1 Core protocol

New package `src/cuga/backend/cuga_graph/nodes/cuga_lite/shortlister/`, `base.py`:

```python
@dataclass
class ShortlistCandidate:
    name: str                 # must match a StructuredTool.name in the request
    score: float              # higher = more relevant; scale is strategy-defined
    reasoning: str = ""       # "" for non-LLM strategies


@dataclass
class ShortlistRequest:
    query: str                       # the step query (seam A) / first user message (seam B)
    tools: List[StructuredTool]
    apps: List[AppDefinition]
    task_context: Optional[str] = None   # initial user message, kept separate — see §3A.3
    top_k: Optional[int] = None      # None = strategy decides count (seam A semantics)
    llm: Optional[BaseChatModel] = None
    run_config: Any = None
    instructions: Optional[str] = None


class ShortlisterStrategy(Protocol):
    name: ClassVar[str]

    async def shortlist(self, request: ShortlistRequest) -> List[ShortlistCandidate]: ...

# NOTE: the design below also described a ShortlistResult wrapper and a prewarm()
# hook. Neither shipped — see "What shipped" at the top of this document.


class ShortlisterUnavailableError(RuntimeError):
    """Strategy cannot run (missing model/deps). Caller degrades to fallback_strategy."""
```

`ShortlistRequest`/`ShortlistResult` are dataclasses rather than bare kwargs and a bare list so
third-party strategies do not break when a field is added later. Matches `EmbeddingSchemaConfig`
and `RerankedCandidate`. `ShortlistResult.notes` is what carries strategy-produced annotations
(hallucination filtering, degradation warnings) through to the rendered markdown without
widening the return type again later.

### 3.2 The critical refactor: split rank from render

`PromptUtils.find_tools` currently fuses ranking (L324-357) and markdown rendering (L359-458).
Extract the rendering half verbatim into `shortlister/render.py`:

```python
def render_tools_markdown(
    candidates: List[ShortlistCandidate],
    tools: List[StructuredTool],
    notes: Optional[List[str]] = None,   # appended after the tool list; see PR #549
) -> str
```

This is a pure move — same `Tool` model, same `get_tool_docs`, same `# Found N Matching Tool(s)`
header, same `"No matching tools found for your query."` empty case. It is what lets a cosine
strategy produce identical output shape with `reasoning` supplied differently.

The `notes` parameter exists so PR #549's "Filtered out N unrecognized tool names" footer
survives the extraction — it is a strategy-produced annotation, not a rendering concern. Both
the empty-result branch and the normal branch must append it, matching #549's behavior.

### 3.3 Built-in strategies

**`llm` (default — behavior-preserving).** Wraps the existing chain. Maps
`APIDetails.relevance_score → score`, `APIDetails.reasoning → reasoning`. When `top_k` is set
it generates today's `Return the {top_k} most relevant tools…` instruction; when `None` it
passes `instructions=""`. Identical prompts, identical schema, identical model resolution.

**Name-validation and retry belong here, not in `PromptUtils`.** Hallucinating a tool name is
an LLM failure mode; an embedding ranker draws names from the candidate list and structurally
cannot invent one. So PR #549's helpers — `_partition_shortlist_details`,
`_shortlist_retry_instructions`, `_format_filtered_tool_names_note`, and
`_ainvoke_shortlister_with_name_validation` — move into `llm.py` as private methods of
`LLMShortlister`, and the filtered-names note is returned as a `ShortlistResult.notes` entry.
`EmbeddingShortlister` implements neither, and pays no retry cost.

**`embedding` (cosine).**

- *Document text* per tool, built once via `doc.py::tool_document(tool, app_name)`:

  ```
  {app_name} / {tool.name}
  {tool.description}
  Parameters:
  {params_doc}       # from PromptUtils.get_tool_docs — no new schema walking
  Returns:
  {response_doc}
  ```

- *Embedding* via `create_embedding_function()`, inheriting `[storage.embedding]` unless
  `[shortlister] embedding_provider` / `embedding_model` override it.
- *Cache*: module-level `Dict[str, np.ndarray]` keyed by `sha256(model_name + tool_document)`
  — a content fingerprint, so a changed tool description re-embeds automatically. Vectors are
  **stored L2-normalized**, so scoring is one matmul: `scores = doc_matrix @ query_vec` →
  O(N·d) in numpy, not a Python loop.
- *Selection*: `top_k` when given. When `None` (seam A): every candidate with
  `score >= min_score`, capped at `[shortlister] top_k`.
- **Never-empty rule** (embedding strategy only): if nothing clears `min_score`, return
  the best `min(3, N)` anyway. `cap.py` raises `RuntimeError` on an empty ranking, and an
  empty `find_tools` result is a dead end for the agent. This is a property of the cosine
  ranker, not a guarantee the seam imposes on every strategy — the `llm` strategy can and
  does return nothing when the model finds no match.
- `reasoning` = `f"Cosine similarity {score:.3f} to the query."` so the rendered markdown
  keeps its shape.
- Raises `ShortlisterUnavailableError` only when the *backend* cannot be built (offline,
  missing model, unknown provider, absent API key). An empty query is not unavailability —
  it returns no candidates, because the fallback strategy would receive the same empty query.

**`hybrid`.** `embedding` prefilters to `[shortlister] top_k` (default 128), then
`llm` ranks that reduced pool and its ordering wins. Cuts the LLM prompt from N tools to
`top_k` while keeping reasoning quality. If the embedding leg is unavailable it degrades to pure `llm`
(logged once). The LLM leg's exceptions propagate unchanged — preserving each seam's existing
failure contract.

### 3.4 Selection: `[shortlister]` config section + router

A dedicated section, mirroring `[execution]`/`ExecutionRouter` — the repo's cleanest and most
recent extension pattern — rather than seven more flat `advanced_features` keys.

```toml
[shortlister]
strategy = "llm"          # llm | embedding | hybrid | "my.pkg.MyShortlister"
threshold = 128           # engage the cosine stage only when candidates EXCEED this
top_k = 128               # how many candidates the cosine stage keeps
max_results = 10          # seam A only: max tools actually rendered to the agent
min_score = 0.15          # cosine floor — deliberately LOW; this is a recall filter (§3A.5)
fallback_strategy = "llm" # used when `strategy` raises ShortlisterUnavailableError
query_weight = 0.7        # step query vs task context blend (§3A.3)
# Embeddings are self-contained by default: always local, never a network call, never billed.
# Deliberately does NOT inherit [storage.embedding] — that section may be set to "openai".
embedding_provider = "local"
embedding_model = "sentence-transformers/all-MiniLM-L6-v2"   # 384-dim, ~90MB, in LOCAL_MODEL_DIMS
# NOTE: `model`, `prewarm` and `prompt_preselect` were designed here but did not
# ship — see "What shipped" at the top.

# Optional per-seam overrides. Unset = inherit the values above.
[shortlister.discovery]   # seam A — PromptUtils.find_tools
# strategy = "hybrid"
[shortlister.bind_cap]    # seam B — PromptUtils.shortlist_tool_names
# strategy = "embedding"
```

`ShortlisterRouter.resolve(settings, *, seam, configurable=None, override=None)` takes a
`seam: Literal["discovery", "bind_cap"]` and layers `[shortlister.<seam>]` over `[shortlister]`
before applying `configurable` and `override`. Per-seam keys are unset by default, so a user who
only sets `strategy = "embedding"` gets it everywhere — the simple case stays simple.

### 3.4.1 Why `threshold = top_k = 128`

128 is not an arbitrary number: it is already `cuga_lite_bind_tools_max_count`, the provider cap
that seam B exists to enforce. Reusing it makes one number mean one thing across both seams —
*"more than 128 candidates is too many; cut to 128."*

The property that makes this safe:

> **At or below 128 candidates, nothing changes.** The cosine stage does not run, and both seams
> behave exactly as they do today. Cosine engages only on genuinely large catalogues — precisely
> the case where the LLM shortlister's prompt is most expensive and least accurate.

So the blast radius of enabling cosine is confined to catalogues where today's behavior is
already the weakest. Small/medium apps are bit-for-bit unaffected regardless of `strategy`.

**`threshold` is a different number from the existing `shortlisting_tool_threshold = 35`.** They
are easy to confuse and must stay distinct:

| Setting | Layer | Meaning |
|---|---|---|
| `advanced_features.shortlisting_tool_threshold` = **35** | 2 | when to *hide* tools behind `find_tools` in the prompt |
| `shortlister.threshold` = **128** | 3 & 4 | when the *cosine stage* engages inside the shortlister |

### 3.4.2 Why `top_k = 128` must not reach the agent at seam A

`top_k = 128` is correct for seam B — 128 *is* the bind cap — and correct as the hybrid prefilter
width. It is **wrong as seam A's final output**, and this is a hard constraint, not a preference:

`find_tools` renders every returned tool as markdown with description, reasoning, parameter docs,
response docs, and both input and output JSON schemas — realistically 400–1500 characters each.
128 tools is roughly 50K–190K characters. That output is a sandbox execution result, and
`code_executor.py:52` truncates it at `advanced_features.execution_output_max_length`
(70 000 chars; the `config.py:159` validator default is 35 000). So a 128-tool result would be
**silently cut mid-render**, dropping tools with no error — and whatever survived would flood the
agent's context.

Hence `max_results` (default **10**), which caps what seam A renders:

| Path at seam A | Flow | What the agent sees |
|---|---|---|
| `strategy = "hybrid"` | 300 → cosine `top_k` 128 → LLM ranks → LLM's own count | a handful, as today |
| `strategy = "embedding"` | 300 → cosine `top_k` 128 → `min_score` → `max_results` | ≤ 10 |

`max_results` is ignored at seam B, where `top_k` is the operative cap.

`plan.py`:

```python
BuiltinStrategy = Literal["llm", "embedding", "hybrid"]


class ShortlisterPlan(BaseModel):
    strategy: str              # builtin name or dotted path
    fallback_strategy: str = "llm"
    top_k: int = 10
    min_score: float = 0.30
    max_results: int = 10
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    notes: List[str] = Field(default_factory=list)   # like ExecutionPlan.fallbacks
    # `model` and `prewarm` appear in the design above but did not ship —
    # see "What shipped" at the top of this document.


class ShortlisterRouter:
    @staticmethod
    def resolve(settings, *, configurable=None, override=None) -> ShortlisterPlan
```

**Precedence** (mirrors `resolve_bind_tools_fields`):

1. `override` — a `ShortlisterStrategy` instance injected via SDK, or `configurable["shortlister"]`
2. `configurable["shortlister_strategy"]` and the other `shortlister_*` configurable keys
3. `settings.shortlister.*`
4. built-in defaults above

`factory.py`:

```python
_BUILTINS = {
    "llm": LLMShortlister,
    "embedding": EmbeddingShortlister,
    "hybrid": HybridShortlister,
}
_INSTANCES: Dict[str, ShortlisterStrategy] = {}   # keyed by resolved plan signature


def resolve_shortlister(plan: ShortlisterPlan) -> ShortlisterStrategy
```

A `strategy` value containing a `.` is treated as a dotted path and loaded with
`cuga.config.get_class` — exactly how `page_understanding.transformer_path` works today.
Unknown bare names raise `ValueError` listing the built-ins. Instances are cached by plan
signature so the embedding model loads once per process.

### 3.5 Call sites — signatures unchanged

**This is the load-bearing property of the design.** Both `PromptUtils` functions keep their
exact signature and return type; only their bodies change:

```python
# prompt_utils.py — same signature, same return type
async def find_tools(query, all_tools, all_apps, llm=None, run_config=None) -> str:
    plan = ShortlisterRouter.resolve(settings, configurable=_configurable_of(run_config))
    strategy = resolve_shortlister(plan)
    result = await strategy.shortlist(
        ShortlistRequest(
            query=query, tools=all_tools, apps=all_apps,
            top_k=None, llm=llm, run_config=run_config,
        )
    )
    return render_tools_markdown(result.candidates, all_tools, notes=result.notes)


async def shortlist_tool_names(query, all_tools, all_apps, llm=None, top_k=4,
                               instructions=None, run_config=None) -> List[str]:
    # all existing guards kept verbatim: top_k<=0 / empty tools / whitespace query -> []
    ...
    result = await strategy.shortlist(ShortlistRequest(..., top_k=top_k, ...))
    # existing hallucination filter against valid_names + dedupe + top_k clamp, unchanged
    # (redundant for embedding, but kept as defense-in-depth for custom strategies)
```

Consequence: **`cap.py`, `helpers/find_tools.py`, `adapter/graph_adapter.py`, and all six
existing test files need zero changes.** `_build_shortlister_payload` moves under the `llm`
strategy (its only consumer) but keeps its name and shared-by-both-legs role.

`run_config` is already threaded into both functions, so `configurable` reaches the router
with no new plumbing.

**One addition in `prepare_node.py`**: when `plan.prewarm and enable_find_tools`, kick a
background embed of the catalogue. This honors the reranker's stated contract that *a user
query must never block on a model download*. Default `false` — opt-in.

**SDK** (`sdk.py`): mirror the `ToolCalling` pattern that open PR #472 establishes for native
function calling — a typed config object plus an `_apply_*` merge helper — rather than poking a
raw key into `configurable`:

```python
# shortlister/config.py  (public, re-exported from cuga.__init__)
@dataclass
class Shortlister:
    strategy: Optional[str] = None          # builtin name or dotted path
    instance: Optional[ShortlisterStrategy] = None   # pre-built object wins over `strategy`
    top_k: Optional[int] = None
    min_score: Optional[float] = None
    threshold: Optional[int] = None
    max_results: Optional[int] = None
    query_weight: Optional[float] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None
    fallback_strategy: Optional[str] = None
    prewarm: Optional[bool] = None


def shortlister_to_configurable(s: Optional[Shortlister]) -> Dict[str, Any]:
    """Serialize to `shortlister_*` configurable keys. Returns {} when off (no-op)."""
```

`CugaAgent.__init__` gains `shortlister: Optional[Shortlister] = None`; `invoke()` and
`stream()` gain the same as a per-invoke override; and `_apply_shortlister(run_config,
shortlister)` merges with `configurable.setdefault(...)` so an explicit raw key set by the
caller is never clobbered — exactly as `_apply_tool_calling` does in #472. Follow its
`try/except → log and disable` guard so a malformed config degrades to the default `llm`
strategy rather than failing the run.

### 3.5.1 Every entry point that must be wired

"Configurable everywhere" is a checklist, not a principle — a key wired in only some of these is
the usual reason a setting appears not to work. All seven are in scope:

| # | Surface | What to add | Mirror this existing code |
|---|---|---|---|
| 1 | `settings.toml` | the `[shortlister]` section above | `[execution]` |
| 2 | Env vars | `DYNACONF_SHORTLISTER__STRATEGY`, `…__THRESHOLD`, `…__TOP_K`, … (free once the section exists) | any nested Dynaconf key |
| 3 | `config.py` validators | one `Validator` **per key**, so a missing section can never `AttributeError` | `config.py:217-218` |
| 4 | `configurable` (per-invoke) | `shortlister_strategy`, `shortlister_threshold`, `shortlister_top_k`, `shortlister_max_results`, `shortlister_min_score`, plus `shortlister` for a live instance | `prepare_node.py:114-118` |
| 5 | SDK | `Shortlister` dataclass, `CugaAgent(shortlister=…)`, `invoke/stream(shortlister=…)`, `_apply_shortlister` | `ToolCalling` in PR #472 |
| 6 | Public export | `Shortlister` (and `ShortlisterStrategy` for custom impls) in `cuga/__init__.py` `__all__` + `_import_map` | the existing lazy-loader entries |
| 7 | Server / demo UI | `extract_agent_feature_overrides` (`manage_routes/helpers.py:33`), the feature-flag defaults block (`helpers.py:147-154`), `draft_ops.py:20-21`, and the `agent_loop` → `graph` → `configurable` chain (`agent_loop.py:320/337/546-547`, `graph.py:74/109`) | how `shortlisting_tool_threshold` is threaded today |

Surface 7 is the one most likely to be skipped. `shortlisting_tool_threshold` is already plumbed
through all of it, so it is a working template to copy rather than new design.

**Ease-of-use check** — the three ways a user should be able to turn this on, all equivalent:

```toml
# 1. settings.toml — deployment-wide
[shortlister]
strategy = "hybrid"
```

```bash
# 2. env — one-off / container
DYNACONF_SHORTLISTER__STRATEGY=hybrid
```

```python
# 3. SDK — per agent, or per call
from cuga import CugaAgent, Shortlister

agent = CugaAgent(tools=[...], shortlister=Shortlister(strategy="hybrid"))
await agent.invoke("...", shortlister=Shortlister(strategy="embedding"))
```

Defaults are chosen so that `Shortlister(strategy="hybrid")` alone is a complete, sensible
configuration — `threshold`, `top_k`, `max_results`, `min_score` and `query_weight` all have
working defaults and none need to be set for the common case.

### 3.5.2 K is configurable at every level

There are **two** K values, and both must be settable from every surface — a K that can only be
changed in `settings.toml` is not configurable in practice:

| Knob | Meaning | Default | Applies to |
|---|---|---|---|
| `top_k` | how many candidates the cosine stage keeps | 128 | both seams (seam B's hard cap; seam A's prefilter width) |
| `max_results` | how many tools seam A actually renders to the agent | 10 | seam A only |

Both are reachable from all four control planes, with the **later row winning**:

```toml
# settings.toml — deployment-wide
[shortlister]
top_k = 64
max_results = 15
[shortlister.bind_cap]
top_k = 128          # per-seam: keep the full cap for bind, tighter K for discovery
```

```bash
DYNACONF_SHORTLISTER__TOP_K=64
DYNACONF_SHORTLISTER__MAX_RESULTS=15
```

```python
# SDK — per agent
agent = CugaAgent(tools=[...], shortlister=Shortlister(strategy="hybrid", top_k=64, max_results=15))

# SDK — per call, overrides the agent default
await agent.invoke("…", shortlister=Shortlister(top_k=32))

# raw configurable — for callers driving the graph directly
config = {"configurable": {"shortlister_top_k": 32, "shortlister_max_results": 5}}
```

Two guards on K, both tested:

- **Seam B clamps to the provider cap regardless.** If `top_k` is configured above
  `cuga_lite_bind_tools_max_count`, the effective K is the cap — `cap.py`'s existing
  `_materialize_shortlist` defense-in-depth clamp already enforces this and must keep working.
  A user-set K can lower the bound list, never raise it past what the provider accepts.
- **`top_k <= 0` disables the cosine stage** rather than returning nothing, matching
  `shortlist_tool_names`'s existing `top_k <= 0 → []` guard being a *skip*, not an error.

### 3.6 Failure semantics — preserved per seam

| Situation | Seam A (`find_tools`) | Seam B (`cap.py`) |
|---|---|---|
| Strategy unavailable (no embed model) | fall back to `fallback_strategy`, log once | same |
| Strategy raises otherwise | caught → error string to agent (existing) | `RuntimeError` (existing) |
| Model lacks structured output | n/a | `NotImplementedError` → `BindToolsUnsupportedError` → degrade to unbound |
| Empty ranking | `"No matching tools found…"` | `RuntimeError` (existing) — hence the never-empty rule |

Side benefit worth documenting: with `strategy = "embedding"`, seam B can no longer hit
`BindToolsUnsupportedError`, because the ranker no longer runs on the model being bound.

---

## 3A. The cosine strategy in detail

### 3A.1 Where exactly it plugs in

Three candidate insertion points. **Two are in scope; the third is opt-in and deliberately not
the default.**

**Point 1 — inside `PromptUtils.find_tools` (seam A).** The ranking pool is *already app-filtered*
before the strategy sees it: `find_tools_func(query, app_name)` resolves
`filtered_tools = app_to_tools_map[app_name]` and passes only those
(`helpers/find_tools.py:83-91`). So the strategy ranks within one app, typically tens of tools,
not the whole catalogue. The embedding cache is global per tool; only the *scoring* is per-app.

**Point 2 — inside `PromptUtils.shortlist_tool_names` (seam B).** The pool is `ranking_pool`
from `cap.py:_build_ranking_pool` — the bound tool list minus the `find_tools` overlay slot.
Cross-app, up to hundreds of tools. `top_k = max_count - reserve`.

**Point 3 (opt-in, `prompt_preselect`) — layer 2 in `prepare_node.py:225`.** Today when the
catalogue exceeds the threshold, the prompt collapses to *only* `find_tools`, and the agent
must spend a round-trip discovering anything. Because cosine is free, we can additionally embed
the initial user message at `prepare` time and inject the top-N tools **directly into the
prompt**, alongside `find_tools`:

```
enable_find_tools = total_tool_count > shortlisting_threshold      # unchanged
tools_for_prompt  = [find_tool] + cosine_top_n(initial_user_message)   # when prompt_preselect
```

This can remove the first `find_tools` round-trip entirely for single-step tasks. It is **not**
the default because at `prepare` time only the initial user message exists — a multi-step task
("find the account, then list its contacts, then email them") needs different tools at different
steps, and the step-2 query does not exist yet. Preselection is therefore *additive*: it never
removes `find_tools`, it only front-loads likely-needed tools. Ship it in a later step, behind
`[shortlister] prompt_preselect = false`, and measure.

### 3A.2 What it compares

**One sentence:** cosine compares **one vector built from the user's question** against **one
vector per tool, built from that tool's name + description + parameter names + return field
names** — and nothing else.

```
        QUERY SIDE                              TOOL SIDE  (one per tool)
 ┌──────────────────────────────┐      ┌────────────────────────────────────────┐
 │ step query   (weight 0.7)    │      │ app name + tool name, split to words   │
 │ task context (weight 0.3)    │      │ description (often weak or empty)      │
 │  → blended, normalized       │      │ parameter names + their descriptions   │
 └──────────────────────────────┘      │ return field NAMES (not schemas)       │
              │                        └────────────────────────────────────────┘
              │                                          │
              └────────►  cosine similarity  ◄───────────┘
                          = dot product of the two normalized vectors
```

**Explicitly NOT compared:** the JSON `args_schema`, full response schemas, `_param_constraints`,
example values, or auth metadata. Those are what make the *LLM* prompt expensive, and in a
fixed-size vector they dilute the signal rather than add to it. They still reach the agent —
`render_tools_markdown` includes them in the output — they are simply not part of the *ranking*.

Each tool's vector is computed once and cached by content hash, so a run over 300 tools embeds
300 short strings the first time and zero thereafter. Only the query is embedded per call.

#### Why this specific set

**This is where the design lives or dies, because tool text in this repo is much weaker than it
looks.** Sampling the CRM demo app:

- Endpoints carry **no docstrings and no `summary=`/`description=`**
  (`demo_tools/crm/crm_api/contacts.py`). The parser takes
  `description = op_obj.get('description', op_obj.get('summary',''))`
  (`openapi_parser_v0.py:498`), so description degrades to FastAPI's auto-summary — `"Get Contacts"` —
  or empty for MCP tools without one.
- Names are machine-generated. `determine_operation_name_strategy` uses a path segment only if
  segments are **unique**; CRM's `/contacts/` and `/contacts/{contact_id}` collide, so it falls
  back to the FastAPI operationId. After `sanitize_tool_name` the real names are
  `crm_get_contacts_contacts_get`, `crm_get_contact_contacts_contact_id_get`,
  `crm_create_contact_contacts_post`.

So: **descriptions cannot be relied on, and the tool name is the dominant signal** — but only
after it is split back into words.

Document text per tool (`doc.py::tool_document`):

```
{app_name} {split_identifier(tool.name)}
{tool.description}                      # often just "Get Contacts", sometimes ""
Parameters: {param_name}: {param_description}; ...
Returns: {top-level response field names}
```

Concretely, for `crm_get_contacts_contacts_get`:

```
crm get contacts contacts get
Get Contacts
Parameters: skip; limit; email: filter by email address
Returns: items, total, skip, limit
```

Rules that matter:

1. **Split identifiers into words.** `split_identifier` handles snake_case, camelCase and the
   duplicated verb/entity that operationIds produce. Embedding `crm_get_contacts_contacts_get`
   raw tokenizes badly; `crm get contacts contacts get` matches "list all contacts" well.
2. **Include parameter names and descriptions.** With descriptions empty, parameters are often
   the only real natural language available (`email: filter by email address`).
3. **Include only response field *names*, not schemas.** Reuse `PromptUtils.get_tool_docs`, then
   keep the names. Full JSON Schema is noise that dilutes the vector.
4. **Do not embed `args_schema` JSON.** It is what makes the *LLM* prompt expensive and it
   actively hurts a fixed-size embedding.
5. **Symmetric vs asymmetric embedding is model-dependent — do not hardcode either.**
   The default `sentence-transformers/all-MiniLM-L6-v2` is a *symmetric* model: it is trained
   for sentence-to-sentence similarity with no query-side instruction, so both query and tool
   document are embedded identically with plain `model.embed()`. `BAAI/bge-*` models are the
   opposite — trained with a query-side instruction prefix, and fastembed exposes
   `query_embed()` / `passage_embed()` for exactly that. Applying bge's prefix to MiniLM
   *degrades* results, and omitting it for bge leaves accuracy on the table.

   So the embedding helper resolves the mode from the model name: bge → asymmetric
   (`query_embed`/`passage_embed`), everything else → symmetric (`embed`). One small lookup,
   defaulting to symmetric, so an unrecognized model behaves correctly rather than oddly.
   (The knowledge engine embeds bge symmetrically today and has the same latent gap; out of
   scope here, worth its own issue.)

Query text, per seam:

| Seam | Query the strategy receives |
|---|---|
| A | `_compose_find_tools_shortlister_query(query, initial_user_message)` → `"Query: {q}\nTask context (initial user message): {init}"` — already task-aware |
| B | first user message (`_first_user_message_text`), via `graph_adapter.resolve_bind_tools` |

Seam B has nothing to merge — it only ever sees the first user message. Merging is a seam-A
concern.

### 3A.3 Query construction — merging the step query with the task context

**Merging is required, and it already happens** (`helpers/find_tools.py:33-38`). But the string
that is right for an LLM is wrong for cosine, for two reasons:

1. **Scaffolding pollutes the vector.** `"Query: "` and `"Task context (initial user message): "`
   are ~10 tokens of boilerplate that get mean-pooled into every query embedding. They carry no
   task signal and, being identical for every query, push every query vector in the same
   direction — measurably reducing discrimination. An LLM reads them as helpful structure; an
   embedding model just averages them in.
2. **Length imbalance silently destroys per-step discovery.** If the first user message is a
   150-word task description and the step query is `"list contacts for account X"`, mean pooling
   makes the task context dominate. Every step of a multi-step task then produces nearly the
   *same* query vector — so step 2 retrieves what step 1 retrieved. This is the failure mode that
   would make cosine look broken on exactly the multi-step AppWorld tasks in #150.

**Fix: merge at the vector level, not the string level.**

```python
v_query   = normalize(await embed_query(step_query))
v_context = normalize(await embed_query(task_context))     # cached per thread — see below
v = normalize(alpha * v_query + (1.0 - alpha) * v_context)
scores = doc_matrix @ v
```

- `alpha` (`[shortlister] query_weight`, default **0.7**) makes the step query dominate while the
  task context still disambiguates. Explicit and tunable, unlike string concatenation where the
  weighting is an accident of relative length.
- **The context vector is constant for the session** — the first user message never changes — so
  cache it per `thread_id`. Steady-state cost is one embedding call per `find_tools`, same as
  plain concatenation.
- Both sides are embedded as *queries* (`query_embed`), tool documents as *passages*
  (§3A.2 rule 5).
- When there is no task context, `v = v_query`, matching the existing early-return.

**Plumbing.** `helpers/find_tools.py` currently pre-composes the two into one string before
calling `PromptUtils.find_tools`, so the strategy cannot see them separately. Add an *optional*
parameter — backward compatible, existing callers and test patches unaffected:

```python
# Shipped signature — `task_context` appended last, so every existing caller and
# test patch keeps working.
async def find_tools(query, all_tools, all_apps, llm=None, run_config=None,
                     task_context: Optional[str] = None) -> str
```

`ShortlistRequest` gains `task_context: Optional[str] = None`. **`LLMShortlister` recomposes the
two with `_compose_find_tools_shortlister_query` and produces the byte-identical prompt it sends
today** — no behavior change on the default path. `EmbeddingShortlister` blends vectors instead.
When `task_context` is not supplied, the strategy falls back to treating `query` as already
composed, so nothing breaks.

### 3A.4 Cold start — a query must never wait on a model download

`all-MiniLM-L6-v2` is ~90MB. On a cold cache the first `find_tools` call would otherwise stall
for seconds to minutes. That is unacceptable, and the repo already has a stated contract for it
(`knowledge/reranker.py:1-20`): *a user query MUST NEVER block on a model download.*

`EmbeddingShortlister` follows the same shape:

```python
if not is_ready(model_name):
    ensure_loading(model_name)          # background thread, at most one per model
    raise ShortlisterUnavailableError(...)   # caller degrades to fallback_strategy = "llm"
```

- **Call 1 (cold):** model not ready → background load starts → this call is served by the `llm`
  strategy and returns at normal speed.
- **Call 2+ (warm):** cosine, as configured.
- A failed load (offline, airgapped) backs off for a cooldown before any retry, so a broken setup
  does not hammer the network or spam logs — copy `_RETRY_COOLDOWN_S` from the reranker.
- Log the substitution **once** at WARNING. Silent strategy swapping is how "my config does
  nothing" bugs happen.
- `prewarm = true` opts into loading during `prepare` instead, for deployments that prefer a
  slower startup over a first-call substitution.

Because `hybrid`'s prefilter is the embedding leg, the same rule makes `hybrid` degrade to plain
`llm` on call 1 — which is exactly today's behavior, so the degraded path is already well tested.

### 3A.5 The limitation that shapes the whole design

Cosine cannot reliably separate near-duplicate CRUD siblings. `get_contacts` (list) and
`get_contact` (by id) differ by one character and one parameter; `create_contact` and
`update_contact` are semantically adjacent. Their embeddings are nearly identical.

That is **exactly the failure class issue #150 documented** — "cart CRUD tools instead of just
showing cart", "order-creation instead of order-lookup". So:

> **Cosine is a recall device, not a precision device.** Used alone for final selection it would
> likely reproduce or worsen #150's errors.

The design consequence, and the recommended defaults:

| Seam | Recommended strategy | Why |
|---|---|---|
| **A** (discovery — precision matters, the agent acts on the result) | `hybrid` | cosine cuts 300 → 50 with high recall; the LLM makes the precision call with full schemas |
| **B** (provider cap — only needs "don't drop the needed tool") | `embedding` | recall is the whole job; exact ordering within the cap is irrelevant, and it removes the LLM round-trip *per `call_model`* plus the `BindToolsUnsupportedError` coupling |

This is why per-seam configuration (§3.4) is not over-engineering — the two seams genuinely want
different strategies, and `min_score` must be **low** (recall-biased), not tuned for precision.

---

## 4. Files

**New** — `src/cuga/backend/cuga_graph/nodes/cuga_lite/shortlister/`
(each file well under the <1000-line rule in `cuga_graph/nodes/AGENTS.md`):

| File | Contents |
|---|---|
| `base.py` | `ShortlistCandidate`, `ShortlistRequest`, `ShortlisterStrategy`, `ShortlisterUnavailableError` |
| `plan.py` | `ShortlisterPlan`, `ShortlisterRouter.resolve` |
| `factory.py` | `_BUILTINS`, dotted-path via `get_class`, instance cache |
| `llm.py` | `LLMShortlister` + the migrated `_build_shortlister_payload` |
| `embedding.py` | `EmbeddingShortlister`, vector cache, numpy cosine |
| `hybrid.py` | `HybridShortlister` |
| `render.py` | `render_tools_markdown` (moved from `find_tools` L359-458) |
| `doc.py` | `tool_document`, `split_identifier`, `tool_fingerprint` (§3A.2) |

**Modified**

| File | Change |
|---|---|
| `cuga_lite/prompt_utils.py` | Both functions delegate to a strategy; signatures preserved, `find_tools` gains an optional `task_context` kwarg (§3A.3) |
| `cuga_lite/helpers/find_tools.py` | Stop pre-composing query + task context into one string; pass both through so the strategy can weight them (§3A.3). Error-swallowing behavior unchanged |
| `storage/embedding/embedding_service.py` | Add `create_embedding_batch_function()` exposing fastembed's native `model.embed(list)` and OpenAI's `aembed_documents` (falls back to `asyncio.gather` over the single-text fn), **plus model-conditional symmetric/asymmetric embedding** — plain `embed` for MiniLM-class models, `query_embed`/`passage_embed` for `bge-*` (§3A.2 rule 5) |
| `cuga_lite/adapter/prepare_node.py` | Optional prewarm hook; log the resolved plan |
| `src/cuga/sdk.py` | `shortlister=` ctor arg + configurable injection |
| `src/cuga/settings.toml` | New `[shortlister]` section |
| `src/cuga/config.py` | Validators for every `shortlister.*` key — **and add the missing `advanced_features.shortlisting_tool_threshold` validator** (see §7) |
| `src/cuga/__init__.py` | Export `Shortlister`, `ShortlisterStrategy` via `__all__` + `_import_map` (§3.5.1 surface 6) |
| `server/manage_routes/helpers.py`, `draft_ops.py`, `utils/agent_loop.py`, `cuga_graph/graph.py` | Thread the `shortlister_*` overrides the same way `shortlisting_tool_threshold` is threaded today (§3.5.1 surface 7) |

**Unchanged (deliberately):** `bind_tools/cap.py`, `helpers/bind_tools.py`,
`adapter/graph_adapter.py`, `prompts/shortlister/system.jinja2`.

Note `helpers/find_tools.py` *was* on this list before §3A.3 introduced weighted query blending.
Passing the step query and task context separately requires touching it — a small change (stop
calling `_compose_find_tools_shortlister_query`, forward both values), and `LLMShortlister`
recomposes them so the prompt it sends stays byte-identical.

---

## 5. Rollout — four independently shippable PRs

1. **Seam + LLM strategy.** Protocol, plan/router, factory, `render.py` extraction,
   `LLMShortlister`, `[shortlister]` section, validators. Pure refactor — *acceptance
   criterion: all six existing shortlister test files pass untouched.*
   **Blocked on PR #549 landing first** — see [§8](#8-related-issues-and-prs).
2. **Embedding strategy.** `doc.py`, `embedding.py`, batch + asymmetric embedding helpers, cache.
   Ships with the CRUD-sibling recall/precision harness (§6) — the numbers decide step 3's
   defaults, so this step must not also change any default.
3. **Hybrid strategy.** `hybrid.py` + per-seam overrides. Flip the recommended
   defaults (`discovery = "hybrid"`, `bind_cap = "embedding"`) **only if step 2's numbers support
   it**; otherwise leave `llm` as the global default and document the measurement.
4. **SDK arg + docs.** `CugaAgent(shortlister=…)`, README section, this document.
5. *(later, optional)* **Layer-2 preselection.** `prompt_preselect` (§3A.1 point 3), measured
   against the round-trips it removes versus the tools it front-loads wrongly.

---

## 6. Verification

**Unit** — new files in `src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/`, following the
mock-adapter shape of `test_prepare_node_weak_schema_tools.py`:

- `test_shortlister_factory.py` — builtin resolution; dotted path via `get_class`; precedence
  (instance > configurable > settings > default); per-seam override layering; unknown name
  raises; instance cache reuse.
- `test_shortlister_defaults.py` — the `threshold = 128` contract (§3.4.1): with 128 candidates
  the cosine stage does **not** run and output is identical to `strategy="llm"`; with 129 it
  does. Plus `max_results` caps seam A at 10 while seam B is capped by `top_k`, and
  `shortlister.threshold` is not confused with `advanced_features.shortlisting_tool_threshold`.
- `test_shortlister_config_surfaces.py` — one assertion per surface in §3.5.1: env var, validator
  default with the section absent, `configurable` override beating settings, SDK ctor arg,
  per-invoke arg beating ctor, `from cuga import Shortlister`, and the server
  `extract_agent_feature_overrides` round-trip. This is the test that catches a half-wired key.
- `test_shortlister_embedding.py` — deterministic fake embed fn (hash-derived unit vectors)
  asserting rank order, `min_score` floor, the never-empty rule, and cache-hit counts.
- `test_shortlister_hybrid.py` — assert the LLM leg receives exactly `top_k`
  candidates and that its ordering wins.
- `test_shortlister_render.py` — `render_tools_markdown` output matches the pre-refactor
  `find_tools` markdown for a fixed candidate list (golden test guarding the extraction).
- `test_shortlister_doc.py` — `split_identifier` on the real name shapes:
  `crm_get_contacts_contacts_get` → `crm get contacts contacts get`, camelCase, and the
  `__`-collapsed operationId forms. Guards §3A.2 rule 1, the single highest-leverage detail.

**The evaluation that decides `embedding` vs `hybrid`** (§3A.5) — a CRUD-sibling recall/precision
harness over the CRM app, using real embeddings rather than a fake fn:

- **Recall@`top_k`** for `embedding` — does the needed tool survive the cosine cut? This is the
  only number that justifies the default `top_k`, and the only one that matters for seam B.
  Report it at K = 32 / 64 / 128 so the default is a measured choice, not an assumed one.
- **Precision@1** for `embedding` on sibling pairs — `get_contacts` (list) vs `get_contact`
  (by id), `create_contact` vs `update_contact`. Expected to be **poor**; the test asserts the
  documented limitation rather than a passing score, so a future contributor cannot quietly
  promote `embedding` to seam A's default without re-measuring.
- Mark `@pytest.mark.stability` (needs a model download) and reuse #150's task phrasings as
  fixtures so the numbers are comparable to the failure cases that motivated this work.

**Regression (the real gate)** — these must pass with no edits:

```bash
uv run pytest tests/unit/test_cuga_lite_bind_tools.py \
              tests/unit/test_bind_tools_safe_fallback.py \
              tests/unit/test_find_tools_exception.py \
              src/cuga/backend/cuga_graph/nodes/cuga_lite/tests/ -m unit
```

**Added to `tests/unit/test_cuga_lite_bind_tools.py`** — over-cap path with
`strategy="embedding"` binds `top_k` tools with **zero** LLM invocations (assert the patched
shortlister chain is never called).

**E2E** — a `strategy = "embedding"` variant of
`src/system_tests/e2e/crm_contacts_email_test_find_tools.py`, `@pytest.mark.stability`.

**Manual smoke** — a registry with >35 tools so layer 2 fires:

```bash
DYNACONF_SHORTLISTER__STRATEGY=embedding uv run cuga start demo
```

Expect a log line naming the resolved strategy, no shortlister LLM span in Langfuse, and
`find_tools` returning the same markdown shape as before.

**CI** — new `tests/unit/` files must be registered in the matching group in
`.github/workflows/tests.yml:68-119` or they will not run.

---

## 7. Adjacent findings (surfaced, not silently fixed)

Found while tracing; each is a deliberate call, not an oversight:

1. **`advanced_features.shortlisting_tool_threshold` has no dynaconf Validator**, unlike every
   other cap in this area. A missing settings.toml key `AttributeError`s at
   `adapter/prepare_node.py:117`. → **Fix in PR 1** (one line, same file we're already editing).
2. **Both Lite shortlister paths default to `settings.agent.code.model`, not
   `settings.agent.shortlister.model`** (`prompt_utils.py:347` and `:518`) — only the classic
   graph node uses the dedicated model. → Exposed as `[shortlister] model`, defaulting to `""`
   = today's behavior. **Not** changed by default.
3. **Dead assets**: `cuga_lite/prompts/shortlister/user.jinja2` is referenced by no Python
   (both callers inline their human messages);
   `configurations/instructions/default/shortlister.md` is 0 bytes, so `{{ instructions }}` in
   the classic prompt is always empty. → Noted; **out of scope**.
4. **Shortlisting is undocumented** outside `settings.toml` inline comments — nothing in
   README/AGENTS/docs describes `find_tools`, `shortlisting_tool_threshold`, or
   `cuga_lite_bind_tools_*`. → PR 4 closes this gap for the whole subsystem.
5. **Version drift**: `src/cuga/__init__.py:28` says `0.2.20`, `pyproject.toml:3` says `0.3.1`.
   → Pre-existing, unrelated, **out of scope**.

---

## 8. Related issues and PRs

Surveyed `cuga-project/cuga-agent` as of 2026-08-11. **No issue or PR proposes a pluggable or
non-LLM shortlister** — this design is new and needs its own tracking issue.

### Issues

| # | State | Relationship |
|---|---|---|
| [#546](https://github.com/cuga-project/cuga-agent/issues/546) — Validate shortlister/`find_tools` output against real tool names with retry | OPEN | **Complementary.** Hallucination validation, not ranker choice. Composes cleanly: retry lives in `LLMShortlister`; `embedding` cannot hallucinate, so it satisfies #546 by construction. |
| [#150](https://github.com/cuga-project/cuga-agent/issues/150) — Shortlister enhancement | CLOSED (2026-04) | **Motivation.** AppWorld trace analysis rated shortlister mis-selection a *high*-severity top cause of first divergence — generic search over wishlist retrieval, omitted `place order` / add-to-cart endpoints. Closed with "review the prompt", i.e. the accuracy problem was addressed prompt-only. Strengthens the case for `hybrid`. |
| [#312](https://github.com/cuga-project/cuga-agent/issues/312) — CodeAgent invokes undefined tools | OPEN | Downstream symptom of the same hallucination class as #546. |
| [#353](https://github.com/cuga-project/cuga-agent/issues/353) — Epic: Tool Gateway & Secure Execution Runtime | OPEN | Parent epic of #546; the natural home for a shortlister issue. |

### Pull requests

| # | State | Relationship |
|---|---|---|
| [#549](https://github.com/cuga-project/cuga-agent/pull/549) — validate shortlister tool names with retry (#546) | **OPEN, CONFLICTING, Unit Tests failing**, last updated 2026-08-05 | **Direct collision.** +147/−25 in `prompt_utils.py`, rewriting the bodies of *both* `find_tools` and `shortlist_tool_names` — the exact regions PR 1 extracts. See sequencing below. |
| [#472](https://github.com/cuga-project/cuga-agent/pull/472) — native function calling, opt-in (#471) | OPEN | **Overlapping files, no logic conflict**: `prompt_utils.py` (+14/−1), `helpers/bind_tools.py`, `adapter/prepare_node.py`, `sdk.py`, `config.py`, `settings.toml`. Establishes the `ToolCalling` + `tool_calling_to_configurable` + `_apply_tool_calling` pattern this spec now copies for `Shortlister` (§3.5). |
| [#203](https://github.com/cuga-project/cuga-agent/pull/203) — cap `bind_tools` count and shortlist over the limit | MERGED | Created seam B and its loud-failure contract. Its review notes are the source of the "single payload builder prevents drift" and "no silent truncation" constraints. |
| [#67](https://github.com/cuga-project/cuga-agent/pull/67) — handle exception in `find_tools` when shortlister LLM fails | MERGED | Source of seam A's swallow-into-error-string contract. |
| [#524](https://github.com/cuga-project/cuga-agent/pull/524) — graceful `bind_tools` fallback | MERGED | Source of `BindToolsUnsupportedError` degradation. |

### Sequencing

**Land #549 first, then rebase PR 1 on top.** Rationale:

- #549 is a bug fix with a tracked issue; this spec is an enhancement. The bug fix should not
  wait on a refactor.
- Rebasing #549 onto a merged PR 1 would be *harder*, not easier — its helpers would have to be
  re-homed into `llm.py` by an author who did not write that file.
- Done in this order, PR 1's extraction is mechanical: move #549's four private helpers into
  `LLMShortlister` unchanged and route their filtered-names note through `ShortlistResult.notes`.
- #549 is currently red (conflicting + failing unit tests), so this is not a short wait. If it
  stalls, the fallback is to land PR 1 first and open a follow-up that ports #549's logic into
  `llm.py` — coordinate with its author (`sami-marreed`) before choosing that path.

**#472 needs no sequencing** — its `prompt_utils.py` change is small and elsewhere in the file,
and its SDK/settings work is a pattern to follow rather than a conflict. Whichever lands second
takes a trivial rebase.

**Tracking issue:** #624, filed under epic #612 (Vakra evaluation), cross-linked to #546.

---

## 9. Contribution process — read before opening any PR

Read `CONTRIBUTING.md` in full before the first PR. The rules below are the ones this work will
actually trip over; the file is authoritative if it has changed.

### 9.1 Authorship — everything under Segev's name

- **Commit identity:** `Segev Shlomov <105229001+segevshlomovIBM@users.noreply.github.com>`.
  This is the GitHub identity that matches prior upstream commits. The local git config holds
  the IBM address (`segev.shlomov1@ibm.com`), which is **not** the GitHub identity — set the
  author/committer per commit via `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env vars rather than
  changing repo config.
- **DCO is required on every commit** — `git commit -s`. The `Signed-off-by` line must match the
  author exactly, or CI fails. To fix after the fact:
  `git commit --amend --no-edit --signoff` (one commit) or
  `git rebase --signoff HEAD~<n>` (several), then force-push.
- **No co-authors and no tool attribution** in commits, PR bodies, or the issue. Sole author.
- Assign the issue and every PR to `segevshlomovIBM`.

### 9.2 PR mechanics

- **Branches:** `feature/<lowercase-hyphenated>` off `main` — e.g.
  `feature/624-pluggable-shortlister`. No underscores, no uppercase, no trailing hyphen.
- **Conventional Commits** for both commit messages and the **PR title** — the repo squash-merges,
  so the PR title becomes the permanent commit message. Suggested titles per step:
  `refactor(cuga-lite): extract shortlister strategy seam (#624)`,
  `feat(cuga-lite): cosine shortlister strategy (#624)`,
  `feat(cuga-lite): hybrid shortlister strategy (#624)`,
  `feat(sdk): configure shortlister from CugaAgent (#624)`.
- **PR template:** use `.github/PULL_REQUEST_TEMPLATE/feature.md` (append `?template=feature.md`
  to the PR URL) — `## Feature`, `Closes #624`, `### Summary`, `### Testing` checklist.
- **Size limit — this constrains the plan.** CONTRIBUTING asks for **< ~300 changed lines** and
  one topic per PR. Step 1 (seam + `llm` strategy + config + validators + tests) will exceed that
  if done as one PR. Split it: **1a** protocol + factory + router + `render.py`/`doc.py`
  extraction (mechanical, no behavior change), **1b** config surfaces + validators + wiring.
  Steps 2–4 are each comfortably under the limit on their own.

### 9.3 Pre-PR checklist

```bash
uv sync --dev
uv run ruff format
uv run ruff check --fix
uv run pytest -m "not stability and not slow and not pgvector and not manual and not e2e and not load"
```

Also required by CONTRIBUTING:

- Secret scan before committing:
  `detect-secrets scan --update .secrets.baseline` then `detect-secrets audit .secrets.baseline`.
  Note the baseline is known-stale locally — diff against `main`'s baseline rather than
  wholesale-regenerating it, or the PR carries unrelated churn.
- `uv.lock` is **not** touched — this feature adds no dependencies, so a lockfile diff in the PR
  means something went wrong.
- New `tests/unit/` files must be registered in the matching group in
  `.github/workflows/tests.yml` or CI silently skips them.
- No generated files, no `.env`, no local config.
