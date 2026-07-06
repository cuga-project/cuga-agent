# Knowledge Citations & Sources — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every claim in an agent answer that comes from the knowledge base carries a numbered citation `[1]` that survives multi-hop retrieval and multi-turn conversations, renders as an interactive Carbon-styled chip in both chat UIs, opens a sources panel with highlighted snippets, and can be toggled at the agent level (UI + SDK + TOML) and per session.

**Architecture:** A thread-scoped **Source Ledger** on the server records every retrieved chunk at the two retrieval choke points (in-process `KnowledgeClient.search_envelope` and HTTP `POST /api/knowledge/search`, which together cover chat, sandbox, MCP, E2B, and SDK paths) and stamps a stable `cite_id` (`s1`, `s2`, …) onto each wire chunk. The LLM is contract-bound to emit `[sN]` markers. A **Citation Resolver** at the single terminal node (`FinalAnswerNode`) validates markers against the ledger, drops hallucinated ids, renumbers to per-message display numbers `[1]..[k]`, and attaches a self-contained `sources` snapshot to the answer. Sources ride the existing `Answer` SSE event (so history replay works for free) and a new `InvokeResult.sources` field (SDK). The UI renders markers as a self-styled `<cuga-cite>` web component (works inside `@carbon/ai-chat`'s shadow DOM **and** CardManager's marked-HTML, no MutationObserver) plus a shared Carbon `SourcesPanel`.

**Tech stack:** Existing only — Python/FastAPI/LangGraph backend, `@carbon/react` + `@carbon/ai-chat` 1.6.0 + `marked`/`react-markdown` frontend, dynaconf settings, sqlite/pg storage. **Zero new dependencies.**

---

# Part 1 — System Design

## 1.1 Citation lifecycle (end to end)

```
 turn N                                                        turn N+1 (no retrieval)
┌───────────────────────────────────────────────────────┐    ┌──────────────────────────┐
│ user query                                             │    │ follow-up question       │
│   ↓                                                    │    │   ↓                      │
│ LLM → knowledge_search_knowledge("q1")   ← hop 1       │    │ LLM sees prior turns'    │
│   engine.search → results                              │    │ tool results + answers   │
│   ★ LEDGER.register(chunks) → s1,s2,s3                 │    │ (with [sN] markers) in   │
│   envelope chunks carry cite_id: s1..s3                │    │ chat history             │
│   ↓                                                    │    │   ↓                      │
│ LLM → knowledge_search_knowledge("q2")   ← hop 2       │    │ answers citing [s2]      │
│   ★ LEDGER.register → s4,s5  (s2 re-retrieved → s2!)   │    │ (still valid: ledger is  │
│   ↓                                                    │    │  thread-scoped)          │
│ LLM final text: "…is 4521 [s2]. Deadline is …[s4][s1]."│    │   ↓                      │
│   ↓                                                    │    │ RESOLVER → "[1]" +       │
│ ★ FinalAnswerNode → RESOLVER:                          │    │ sources snapshot again   │
│   validate ids ∈ ledger, drop fakes,                   │    └──────────────────────────┘
│   renumber by first appearance: s2→1, s4→2, s1→3       │
│   state.final_answer = "…is 4521 [1]. …[2][3]."        │
│   state.sources = [{n:1,cite_id:"s2",filename,page,    │
│                     snippet,scope,score,query}, …]     │
│   ↓                                                    │
│ Answer SSE event {data, variables, sources} ──────────►│ UI: [1] chips + sources footer
│ persisted in stream_events ───────────────────────────►│ history replay re-renders chips
└───────────────────────────────────────────────────────┘
```

**Key invariants:**
1. `cite_id` is **stable per thread**: the same chunk (by content identity) re-retrieved in any later hop or turn gets the same `sN`. This is what makes "cite something retrieved earlier" work.
2. The **LLM-visible transcript keeps raw `[sN]` markers** (chat messages are appended *before* resolution); only `state.final_answer` — the display copy — is rewritten to `[1]`. So in later turns the model can still reference stable ids, while users see clean per-message numbers.
3. Display numbers are **per message**, assigned in order of first appearance (SOTA convention — Perplexity/ChatGPT/Gemini all do per-message numbering).
4. Every persisted message's `sources` snapshot is **self-contained** (filename, page, snippet, scope, score, query). Rendering old messages never needs the ledger, the collection, or even the document to still exist.

## 1.2 Decision log

| Decision | Choice | Why (and what was rejected) |
|---|---|---|
| Where the ledger lives | Server-side, thread-scoped, module-level registry in `knowledge/sources.py` | The LLM calls retrieval from sandboxed generated code; results only transit LLM context otherwise (subject to trimming/summarization). All retrieval paths converge on two seams that both have `thread_id`. Rejected: state-field accumulator (`tool_calls` precedent) — requires plumbing through `AgentState`+`CugaLiteState`+sandbox `Command` updates AND misses the ChatAgent auto-exec path and the MCP/HTTP path. |
| How the LLM cites | Emits `[sN]` markers using `cite_id`s stamped on tool results | Model cannot know final per-message numbers at generation time (multi-hop, dedup, ordering). Copying short stable ids is the most reliable LLM behavior. Rejected: model emits `[1]` directly (breaks on multi-hop renumber, hallucination-prone); structured JSON citations in output (breaks streaming/formatting, high failure rate on small models). |
| Marker syntax | `[s1]`, case-insensitive, comma lists `[s1, s4]` allowed | Low collision (requires `s`+digits inside brackets), trivially regex-able, cheap for the model. Resolver skips code fences/spans. |
| Resolution point | `FinalAnswerNode` (all 5 sender branches) | It is the single node every path traverses before `END` (chat, task-analyzer, cuga_lite, supervisor, browser-regen). Final answer is emitted once at end-of-turn (no token streaming of the final text today), so end-resolution loses nothing. |
| Numbering scope | Per-message display numbers, thread-stable internal ids | Matches user expectation ("this answer has 3 sources") and SOTA products. Global-conversation numbering rejected: message 7 showing `[12][15]` reads broken standalone. |
| Chunk identity | `sha1(scope|filename|page|sha1(text))` content hash | Vector-store `chunk_id` is uuid4-per-ingest, discarded at read time, and unstable across reindex. Content hash gives cross-turn AND cross-reindex stability with zero storage changes. |
| Transport to UI | Ride the existing `Answer` SSE event payload + persisted `stream_events` | `customLoadHistory` replays stream events → citations survive reload **for free**. Rejected: new SSE event type (second thing to persist/replay/order). |
| Inline chip rendering | Self-styled `<cuga-cite>` web component injected into answer HTML/markdown | Verified: `@carbon/ai-chat` 1.6.0 markdown renderer passes raw HTML and upgrades custom elements inside its shadow DOM (global registry; DOMPurify whitelists custom elements even when sanitization is on). Same element works in CardManager's `marked` HTML. One component, both surfaces, no MutationObserver hack. Rejected: `conversational_search` native item (no inline `[n]` chips — highlight+carousel UX only, and it replaces our markdown rendering); `renderUserDefinedResponse` alone (block-level only, cannot interleave mid-sentence). |
| Sources footer (per message) | `user_defined` message item + `renderUserDefinedResponse` (carbon-chat); plain React under the Answer card (CardManager) | This IS the supported ai-chat extension point for block content inside an assistant message. |
| Enable/disable | `KnowledgeConfig.citations_enabled: bool = True` (agent level, travels through existing draft-PATCH/publish/snapshot machinery) + per-session override via the **existing dormant** `SessionKnowledgeState.overrides` + `patch_session_overrides()` (already built, persisted, and unit-tested — just dark) | "Agent knowledge part" = Manage → Knowledge → Settings tab toggle; "session knowledge part" = chat Knowledge side panel toggle. Not in `vector_config_hash()` → **never triggers reindex**. Not profile-owned. |
| Snippet highlighting | Client-side `<mark>` of retrieving-query terms (ledger stores the query per chunk) | Char offsets/bboxes from Docling are discarded at ingest (`engine.py:2486`); storing them requires schema change + full reindex of every customer corpus. Query-term highlighting answers "why was this chosen" with zero ingest changes. Explicitly deferred: offset-precise highlighting. |
| Turn correlation | None added | Verified: `thread_id` is the only id reaching the knowledge layer. Per-message numbering is computed at resolution (no turn id needed); the ledger is deliberately thread-scoped. |

## 1.3 Data contracts (single source of truth)

### SourceRecord (backend, `knowledge/sources.py`)
```python
@dataclass
class SourceRecord:
    cite_id: str          # "s1" — stable per thread, first-seen order
    key: str              # content identity hash (16 hex chars)
    scope: str            # "agent" | "session"
    filename: str
    page: int | None
    section_path: str
    text: str             # full chunk text (the snippet)
    score: float
    query: str            # the query that first retrieved it
    cited: bool = False   # set true once any resolved answer cited it
```

### Wire chunk (extends `envelope._result_to_chunk` output; only when citations enabled)
```json
{"source": "agent", "text": "…", "filename": "report.pdf", "page": 4,
 "section_path": "2 › Findings", "cite_id": "s3"}
```

### Per-message sources snapshot (in `Answer` payload, stream_events, `InvokeResult.sources`)
```json
{"n": 1, "cite_id": "s3", "filename": "report.pdf", "page": 4,
 "section_path": "2 › Findings", "scope": "agent",
 "snippet": "<full chunk text>", "score": 0.83, "query": "report number"}
```

### Answer SSE payload (extended; `src/cuga/backend/server/main.py` ~1559-1571)
```json
{"data": "…is 4521 [1].", "variables": {...}, "active_policies": [...],
 "sources": [ <snapshot>, ... ]}
```
`sources` key present only when non-empty (wire stays terse when feature off/unused).

### Frontend shared type (`agentic_chat/src/citations/types.ts`)
```ts
export interface MessageSource {
  n: number; cite_id: string; filename: string;
  page?: number | null; section_path?: string; scope: string;
  snippet: string; score?: number; query?: string;
}
```

### Settings key
```toml
[knowledge]
citations_enabled = true   # default ON when knowledge is on; search-only → never reindexes
```

## 1.4 Edge-case matrix (exhaustive; each row names its handling site)

| # | Edge case | Handling | Where |
|---|---|---|---|
| 1 | LLM invents a cite_id not in ledger | Marker stripped from text, excluded from sources, warn-log | resolver (`sources.py`) |
| 2 | LLM cites nothing despite retrieving | `sources: []`, no footer/chips; retrieval steps still visible in reasoning UI | resolver + UI (renders nothing when empty) |
| 3 | Same chunk cited multiple times in one answer | All markers map to one display number | resolver (first-appearance map) |
| 4 | Comma list `[s1, s4]` / adjacent `[s1][s4]` | Split into individual numbered chips `[1][2]` | resolver regex + frontend CSS spacing |
| 5 | Marker inside code fence/inline code | Left untouched (never rewritten/stripped) — protects code correctness | resolver + frontend injector both split on fences/spans |
| 6 | Same chunk re-retrieved in a later hop/turn | Content-hash dedupe → same `cite_id` returned | `SourceLedger.register` |
| 7 | Follow-up turn, no new retrieval, cites old source | Ledger is thread-scoped; contract says earlier ids stay valid; prior transcript keeps `[sN]` raw | ledger + invariant 2 |
| 8 | Context summarization/window trimming erases old tool results | Model can no longer *see* those ids → cannot cite; no correctness issue (unknown ids stripped anyway) | accepted; resolver guards |
| 9 | Server restart mid-conversation | In-memory ledger lost, but (a) MemorySaver graph state is also lost today (same blast radius), (b) old messages render from self-contained snapshots, (c) ledger **rehydrates** cited entries from persisted stream events on first miss | Task 7 rehydrator |
| 10 | Reindex / document deleted / session collection GC'd (7-day) | Snapshots self-contained → old messages still render; "Open document" returns 404 → panel disables the button with a tooltip | UI panel handles fetch failure |
| 11 | Ledger unbounded growth | Cap 500 records/thread (evict oldest *uncited* first); ledger registry LRU-capped at 300 threads | `sources.py` |
| 12 | Thread deleted (`DELETE /api/conversations/{id}`, `/reset`) | `drop_ledger(thread_id)` alongside existing `_delete_session_knowledge_for_thread` | Task 7 |
| 13 | `thread_id` missing at search time (SDK tools injected without thread, agent-scope-only runs) | Wrapper fix injects `thread_id` whenever available regardless of scope; when genuinely absent → chunks get no `cite_id` (feature silently off for that call, debug-log once) | Task 4 |
| 14 | Citations toggled OFF mid-conversation | New envelopes get no cite_ids + contract omitted; old messages keep rendering their snapshots; resolver still strips stale markers if model imitates history | enablement checked per-search + per-prompt-assembly |
| 15 | Toggled ON mid-conversation | Ledger starts accumulating from next search; prior retrievals uncitable (were never stamped) — correct | — |
| 16 | Session override vs agent default | Effective = session override if set, else agent config; override persisted in `session_knowledge.json` | `citations_enabled_for()` |
| 17 | Browser-mode regen path (`_generate_final_answer` LLM rewrite) | System prompt gains "preserve `[sN]` markers verbatim" rule | Task 5 |
| 18 | OutputFormatter policies (LLM reformat of final answer) | Formatter prompt gains same preserve rule | Task 5 |
| 19 | Model writes bare `[1]` from pretraining habit | Not matched by resolver (requires `s`+digits); contract explicitly forbids; harmless literal text | contract + resolver |
| 20 | WXO output format (raw text, no structured side-channel) | Plain-text `Sources:` footer appended to the answer string in WXO branch only | Task 7 |
| 21 | `page` is a chunk ordinal for plain-text formats (`engine.py:4973` misnomer) | UI omits "p.N" for extensions `.txt .md .log .json .csv .xml` (helper `pageLabel()`) | frontend `types.ts` helper |
| 22 | Hebrew/RTL filenames (real demo data) | Chips/panel use `dir="auto"` on filename spans | components |
| 23 | XSS via filename/snippet | Chip attributes are escaped by the injector; snippets render as React text / `textContent` (never `innerHTML`); `<mark>` built via split-parts, not HTML strings | Task 10 |
| 24 | Concurrent turns on one thread | Ledger ops under `threading.Lock` (searches run in threads); worst case interleaved cite_ids — never corrupt | `sources.py` |
| 25 | HITL interrupt / user Stop mid-run | No final answer → no resolution; ledger keeps entries for the resumed/next turn | inherent |
| 26 | Draft "Try It Out" agents (`X-Use-Draft`) | Draft PATCH already live-applies config to the engine (`live_applied`), so the flag is honored; per-draft `search_config` in `prepare_node` controls the prompt contract | verify in Task 5 |
| 27 | E2B remote executor | Knowledge stubs call back over HTTP → HTTP seam records ledger; in-process seam not needed there | covered by dual-seam design |
| 28 | agentic_chat history reload (plain-text messages, all card structure already lost today) | Chips lost on reload in the *extension* surface only — pre-existing limitation of that surface, unchanged; carbon-chat (shipped web UI) replays full stream events → chips persist | accepted, documented |
| 29 | `by_source` grouping duplicates chunk dicts | They are the *same dict objects* as `results` (`envelope.py:303-304`) → stamping `results` also stamps `by_source` | zip-stamp in Task 4 |
| 30 | >8 sources in one message | Footer shows 8 + "+N more" chip that opens the panel | `MessageSources.tsx` |

## 1.5 UX specification (Carbon for AI)

**Design intent:** citations should feel like part of the AI's voice — precise, quiet, trustworthy. Everything interactive uses Carbon tokens so both light chat surfaces inherit correctly, and the panel carries the one `AILabel` (matching the existing "one AILabel per surface" convention, `ClientAdaptationPanel.tsx:368`).

### Inline chip (`<cuga-cite n="2" filename="report.pdf" page="4" scope="agent">`)
```
  …the report number is 4521 ⟦2⟧ .
                            ▲ superscript pill: 16px min-height, radius 8px,
                              background var(--cds-layer-02,#f4f4f4),
                              color var(--cds-link-primary,#0f62fe),
                              border 1px solid var(--cds-border-subtle-01,#e0e0e0),
                              font: 11px IBM Plex Sans, padding 0 5px, cursor pointer
  hover  → background var(--cds-layer-hover-02) + hover card (below)
  focus  → 2px var(--cds-focus,#0f62fe) outline (keyboard reachable, role="button")
  active/selected (panel open on this source) → filled var(--cds-link-primary), white text
```
**Hover card** (rendered inside the element's own shadow root — no portal/z-index wars):
```
  ┌──────────────────────────────────┐
  │ 📄 report.pdf        agent · p.4 │   filename dir="auto", ellipsis at 240px
  │ 2 › Findings                     │   section_path, --cds-text-secondary
  │ "…the report number 4521 was…"   │   snippet first 140 chars
  │           Click to view source → │   --cds-text-helper, 11px
  └──────────────────────────────────┘
  180ms delay in, 8px offset above chip, subtle shadow, radius 4px, max-width 280px
```
Click → dispatches `cuga-cite-click` (bubbles, composed — crosses ai-chat shadow DOM) with `{n}`.

### Per-message sources footer (below the answer, inside the same message)
```
  ─────────────────────────────────
  Sources
  ┌────────────────────────┐ ┌───────────────────────┐ ┌─────────┐
  │ ① report.pdf      p.4  │ │ ② handbook.docx  p.12 │ │ +3 more │
  └────────────────────────┘ └───────────────────────┘ └─────────┘
```
- "Sources" label: 12px, `--cds-text-secondary`, letter-spacing .32px (Carbon label style).
- Cards: number badge (matches chip style) + filename (ellipsis, `dir="auto"`) + page tag + scope dot (● blue=agent, ● teal=session, with tooltip "Agent knowledge"/"This conversation").
- Click card → panel opens scrolled to that source. Max 8 cards, then "+N more".

### Sources panel (shared `SourcesPanel.tsx`, slides in from the right like `KnowledgeSidePanel`)
```
  ┌───────────────────────────────────────────┐
  │ ✕   Sources                     [AI]      │  header: AILabel size="mini",
  │     3 sources · cited in this answer      │  textLabel="Grounded answer"
  ├───────────────────────────────────────────┤
  │ ① report.pdf                    agent p.4 │  ← active: 3px inset border-left
  │    2 › Findings                           │     var(--cds-interactive)
  │    ┌─────────────────────────────────┐    │
  │    │ …the **report number 4521** was │    │  full snippet, retrieving-query
  │    │ issued on 12.3, see appendix…   │    │  terms wrapped in <mark>
  │    └─────────────────────────────────┘    │  (--cds-highlight background)
  │    Found for: "report number"   ▓▓▓▓░ 83% │  query + relevance meter (subtle)
  │    [Open document ↗]                      │  GET /api/knowledge/documents/file
  ├───────────────────────────────────────────┤
  │ ② handbook.docx …                         │
  └───────────────────────────────────────────┘
```
- Snippet block: `--cds-layer-01` background, 13px Plex, `white-space: pre-wrap`, max-height 260px with fade + "Show more".
- Relevance meter: 4px bar, `--cds-support-info`; label "relevance" on hover only (avoid over-claiming precision).
- "Open document": secondary ghost Button; on 404 → disabled + tooltip "Document no longer in the knowledge base".
- Empty state (panel opened with 0 sources — shouldn't happen from chips, defensive): "This answer didn't cite knowledge-base sources."
- Keyboard: Esc closes, chips are tab-reachable, panel gets `role="complementary"` `aria-label="Answer sources"`.
- Motion: 160ms ease-out slide (matches `KnowledgeSidePanel.css` timing).

### Settings surfaces
- **Manage → Knowledge → Settings tab** (agent level): a `Tile` after the session-level tile — Carbon `Toggle id="knowledge-citations-enabled"`, label "Citations", helper "Number knowledge sources in answers ([1]) with clickable snippets", disabled when master knowledge toggle is off.
- **Chat → Knowledge side panel** ("This Conversation" section, session level): compact toggle "Citations for this chat", visible only when the agent-level flag is on; writes the per-session override.

---

# Part 2 — File structure

**Create (backend):**
- `src/cuga/backend/knowledge/sources.py` — SourceLedger, registry, resolver, enablement helpers, envelope stamping. One module, no engine import (duck-typed on result objects) so unit tests stay light.
- `tests/unit/test_source_ledger.py`, `tests/unit/test_citation_resolver.py`, `tests/unit/test_envelope_citations.py`, `tests/unit/test_citation_settings.py`, `tests/unit/test_final_answer_citations.py`, `tests/unit/test_session_citation_override.py`

**Create (frontend, shared):**
- `src/frontend_workspaces/agentic_chat/src/citations/types.ts`
- `src/frontend_workspaces/agentic_chat/src/citations/citeElement.ts` — `<cuga-cite>` web component
- `src/frontend_workspaces/agentic_chat/src/citations/injectCitations.ts` (+ `injectCitations.test.ts`)
- `src/frontend_workspaces/agentic_chat/src/citations/SourcesPanel.tsx` + `SourcesPanel.css`
- `src/frontend_workspaces/agentic_chat/src/citations/MessageSources.tsx`
- `src/frontend_workspaces/agentic_chat/src/citations/index.ts`

**Modify (backend):** `knowledge/config.py` (flag), `configurations/knowledge/knowledge_settings.toml`, `knowledge/envelope.py` (no-op — stamping happens at callers; listed to anchor the contract comment), `knowledge/client.py` (stamp seam), `knowledge/routes.py` (stamp seam + session settings routes), `knowledge/awareness.py` (contract), `knowledge/engine.py` (`get_settings` exposure), `cuga_graph/nodes/cuga_lite/adapter/prepare_node.py` (thread_id wrapper fix), `cuga_graph/nodes/answer/final_answer.py` (resolution), `cuga_graph/nodes/answer/final_answer_agent/prompts/system.jinja2` (preserve rule), `cuga_graph/policy/output_formatter_utils.py` (preserve rule), `cuga_graph/state/agent_state.py` (`sources` field), `cuga_graph/utils/agent_loop.py` (`AgentLoopAnswer.sources`), `backend/server/main.py` (Answer payload, override lookup wiring, ledger cleanup, agent-context flag), `src/cuga/sdk.py` (`InvokeResult.sources`, `enable_citations`).

**Modify (frontend):** `agentic_chat/package.json` (exports), `agentic_chat/src/CardManager.tsx` (+ `CardManager.css`), `agentic_chat/src/App.tsx` (panel host), `agentic_chat/src/KnowledgeSidePanel.tsx` (session toggle), `agentic_chat/src/KnowledgeConfig.tsx` (agent toggle), `frontend/src/ManagePage.tsx` (type+default), `frontend/src/api.ts` (session-settings calls), `frontend/src/carbon-chat/customSendMessage.ts`, `frontend/src/carbon-chat/customLoadHistory.ts`, `frontend/src/carbon-chat/carbonChatHelpers.ts`, `frontend/src/carbon-chat/CarbonChat.tsx`, `frontend/src/ChatLanding.tsx` (flag plumb).

Ordering rationale: ledger+resolver first (pure, testable), config second, seams third, prompts fourth, graph/egress fifth, SDK sixth, then frontend shared→carbon→extension→settings. Each task leaves the tree green and committable.

---

# Part 3 — Tasks

> Run all backend tests with `uv run pytest <file> -v`. Run `uv run ruff check --fix src tests` before every commit. Frontend tests: `npm run test -w agentic_chat -- --run`. **Commit messages: plain conventional commits, no AI attribution.**

### Task 1: Source ledger (`knowledge/sources.py`)

**Files:**
- Create: `src/cuga/backend/knowledge/sources.py`
- Test: `tests/unit/test_source_ledger.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_source_ledger.py
from types import SimpleNamespace

from cuga.backend.knowledge.sources import (
    SourceLedger,
    get_ledger,
    drop_ledger,
    _reset_all_ledgers_for_tests,
)


def _chunk(text="alpha beta", filename="a.pdf", page=1, scope="agent", score=0.9, section_path=""):
    return SimpleNamespace(
        text=text, filename=filename, page=page, scope=scope,
        score=score, section_path=section_path,
    )


def setup_function():
    _reset_all_ledgers_for_tests()


def test_register_assigns_sequential_cite_ids():
    ledger = SourceLedger()
    assert ledger.register(_chunk(text="one"), query="q") == "s1"
    assert ledger.register(_chunk(text="two"), query="q") == "s2"


def test_register_is_idempotent_for_same_content():
    ledger = SourceLedger()
    a = ledger.register(_chunk(text="same", page=3), query="q1")
    b = ledger.register(_chunk(text="same", page=3), query="q2")  # later hop/turn
    assert a == b == "s1"
    assert len(ledger) == 1
    # first retrieving query is kept
    assert ledger.get("s1").query == "q1"


def test_different_page_or_file_is_a_different_source():
    ledger = SourceLedger()
    assert ledger.register(_chunk(text="same", page=3), query="q") == "s1"
    assert ledger.register(_chunk(text="same", page=4), query="q") == "s2"
    assert ledger.register(_chunk(text="same", page=3, filename="b.pdf"), query="q") == "s3"


def test_get_unknown_returns_none():
    assert SourceLedger().get("s99") is None


def test_cap_evicts_oldest_uncited_first():
    ledger = SourceLedger(max_records=3)
    ledger.register(_chunk(text="t1"), query="q")   # s1
    ledger.register(_chunk(text="t2"), query="q")   # s2
    ledger.get("s1").cited = True
    ledger.register(_chunk(text="t3"), query="q")   # s3
    ledger.register(_chunk(text="t4"), query="q")   # s4 -> evicts s2 (oldest uncited)
    assert ledger.get("s1") is not None   # cited survives
    assert ledger.get("s2") is None
    assert ledger.get("s4").cite_id == "s4"


def test_thread_registry_isolated_and_droppable():
    l1 = get_ledger("t-1")
    l2 = get_ledger("t-2")
    assert l1 is not l2
    assert get_ledger("t-1") is l1
    l1.register(_chunk(), query="q")
    drop_ledger("t-1")
    assert len(get_ledger("t-1")) == 0


def test_get_ledger_without_create_returns_none_on_miss():
    assert get_ledger("nope", create=False) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_source_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cuga.backend.knowledge.sources'`

- [ ] **Step 3: Implement `sources.py` (ledger half)**

```python
# src/cuga/backend/knowledge/sources.py
"""Thread-scoped source ledger + citation resolution for the knowledge engine.

Deliberately imports nothing from ``engine.py`` — retrieval results are
duck-typed (``text/filename/page/scope/score/section_path`` attributes) so
this module stays cheap to import in unit tests and in the graph layer.

The ledger is the memory that makes citations survive multi-hop retrieval
and multi-turn conversations: every retrieved chunk is registered under a
content-identity hash and receives a thread-stable ``cite_id`` (``s1``,
``s2``, …). Re-retrieving the same chunk in any later hop or turn returns
the SAME id, which is what lets the LLM cite a source found three turns
ago. Display numbers ([1], [2]) are a per-message concern — assigned by
``resolve_citations`` at final-answer time, never stored here.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger


@dataclass
class SourceRecord:
    cite_id: str
    key: str
    scope: str
    filename: str
    page: int | None
    section_path: str
    text: str
    score: float
    query: str
    cited: bool = False

    def to_snapshot(self, n: int) -> dict[str, Any]:
        """Self-contained per-message source entry. Must render without the
        ledger, the collection, or the document still existing."""
        snap: dict[str, Any] = {
            "n": n,
            "cite_id": self.cite_id,
            "filename": self.filename,
            "page": self.page,
            "scope": self.scope,
            "snippet": self.text,
            "query": self.query,
        }
        if self.section_path:
            snap["section_path"] = self.section_path
        if self.score:
            snap["score"] = round(float(self.score), 4)
        return snap


def _content_key(scope: str, filename: str, page: int | None, text: str) -> str:
    text_h = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    raw = f"{scope}|{filename}|{page if page is not None else ''}|{text_h}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


class SourceLedger:
    """Per-thread registry of retrieved chunks. Thread-safe: retrieval runs
    on worker threads while resolution runs on the event loop."""

    def __init__(self, max_records: int = 500):
        self._by_key: OrderedDict[str, SourceRecord] = OrderedDict()
        self._by_cite_id: dict[str, SourceRecord] = {}
        self._counter = 0
        self._max = max_records
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._by_key)

    def register(self, result: Any, *, query: str) -> str:
        """Record one retrieved chunk; return its stable cite_id."""
        text = getattr(result, "text", "") or ""
        filename = getattr(result, "filename", "") or ""
        page = getattr(result, "page", None)
        scope = getattr(result, "scope", "") or ""
        key = _content_key(scope, filename, page, text)
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                return existing.cite_id
            self._counter += 1
            record = SourceRecord(
                cite_id=f"s{self._counter}",
                key=key,
                scope=scope,
                filename=filename,
                page=page,
                section_path=getattr(result, "section_path", "") or "",
                text=text,
                score=float(getattr(result, "score", 0.0) or 0.0),
                query=query,
            )
            self._by_key[key] = record
            self._by_cite_id[record.cite_id] = record
            self._evict_if_needed()
            return record.cite_id

    def get(self, cite_id: str) -> SourceRecord | None:
        return self._by_cite_id.get(cite_id.lower())

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Re-insert a persisted snapshot (restart rehydration). Keeps the
        original cite_id and bumps the counter past it so future ids never
        collide with ids already present in the on-disk conversation."""
        cite_id = str(snapshot.get("cite_id", "")).lower()
        m = re.fullmatch(r"s(\d+)", cite_id)
        if not m:
            return
        key = _content_key(
            snapshot.get("scope", "") or "",
            snapshot.get("filename", "") or "",
            snapshot.get("page"),
            snapshot.get("snippet", "") or "",
        )
        with self._lock:
            if key in self._by_key or cite_id in self._by_cite_id:
                return
            record = SourceRecord(
                cite_id=cite_id,
                key=key,
                scope=snapshot.get("scope", "") or "",
                filename=snapshot.get("filename", "") or "",
                page=snapshot.get("page"),
                section_path=snapshot.get("section_path", "") or "",
                text=snapshot.get("snippet", "") or "",
                score=float(snapshot.get("score", 0.0) or 0.0),
                query=snapshot.get("query", "") or "",
                cited=True,
            )
            self._by_key[key] = record
            self._by_cite_id[cite_id] = record
            self._counter = max(self._counter, int(m.group(1)))

    def _evict_if_needed(self) -> None:
        # Called under self._lock. Evict oldest uncited first; cited records
        # are referenced by persisted messages' history semantics — keep them
        # as long as possible so re-citing stays possible.
        while len(self._by_key) > self._max:
            victim_key = None
            for k, rec in self._by_key.items():
                if not rec.cited:
                    victim_key = k
                    break
            if victim_key is None:  # everything cited — evict absolute oldest
                victim_key = next(iter(self._by_key))
            victim = self._by_key.pop(victim_key)
            self._by_cite_id.pop(victim.cite_id, None)


# --- module-level thread registry -------------------------------------------

_ledgers: OrderedDict[str, SourceLedger] = OrderedDict()
_registry_lock = threading.Lock()
_MAX_THREADS = 300

# Optional hook set by the server so a fresh process can rebuild cited
# entries from persisted conversation events (see main.py wiring).
_rehydrator: Callable[[str, SourceLedger], None] | None = None


def set_rehydrator(fn: Callable[[str, SourceLedger], None] | None) -> None:
    global _rehydrator
    _rehydrator = fn


def get_ledger(thread_id: str, create: bool = True) -> SourceLedger | None:
    if not thread_id:
        return None
    with _registry_lock:
        ledger = _ledgers.get(thread_id)
        if ledger is not None:
            _ledgers.move_to_end(thread_id)
            return ledger
        if not create:
            return None
        ledger = SourceLedger()
        _ledgers[thread_id] = ledger
        while len(_ledgers) > _MAX_THREADS:
            _ledgers.popitem(last=False)
    if _rehydrator is not None:
        try:
            _rehydrator(thread_id, ledger)
        except Exception:
            logger.exception("source-ledger rehydration failed for thread {}", thread_id)
    return ledger


def drop_ledger(thread_id: str) -> None:
    with _registry_lock:
        _ledgers.pop(thread_id, None)


def _reset_all_ledgers_for_tests() -> None:
    with _registry_lock:
        _ledgers.clear()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_source_ledger.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/knowledge/sources.py tests/unit/test_source_ledger.py
git commit -m "feat(knowledge): thread-scoped source ledger for citations"
```

### Task 2: Citation resolver (same module)

**Files:**
- Modify: `src/cuga/backend/knowledge/sources.py`
- Test: `tests/unit/test_citation_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_citation_resolver.py
from types import SimpleNamespace

from cuga.backend.knowledge.sources import (
    SourceLedger,
    has_citation_markers,
    resolve_citations,
)


def _ledger_with(n=3):
    ledger = SourceLedger()
    for i in range(n):
        ledger.register(
            SimpleNamespace(
                text=f"chunk text {i}", filename=f"f{i}.pdf", page=i + 1,
                scope="agent", score=0.8, section_path="",
            ),
            query=f"query {i}",
        )
    return ledger


def test_has_markers_detection():
    assert has_citation_markers("answer [s1] end")
    assert has_citation_markers("multi [s1, s12]")
    assert has_citation_markers("upper [S2]")
    assert not has_citation_markers("plain [1] and [note] text")


def test_renumbers_by_first_appearance():
    text, sources = resolve_citations("b is [s2]. a is [s1]. b again [s2].", _ledger_with())
    assert text == "b is [1]. a is [2]. b again [1]."
    assert [s["n"] for s in sources] == [1, 2]
    assert sources[0]["cite_id"] == "s2"
    assert sources[0]["filename"] == "f1.pdf"
    assert sources[0]["snippet"] == "chunk text 1"


def test_comma_list_expands_to_adjacent_numbers():
    text, sources = resolve_citations("fact [s1, s3].", _ledger_with())
    assert text == "fact [1][2]."
    assert len(sources) == 2


def test_unknown_ids_are_stripped():
    text, sources = resolve_citations("real [s1] fake [s9].", _ledger_with())
    assert text == "real [1] fake ."
    assert len(sources) == 1


def test_mixed_known_unknown_in_one_bracket():
    text, sources = resolve_citations("x [s1, s9].", _ledger_with())
    assert text == "x [1]."
    assert len(sources) == 1


def test_code_fences_and_inline_code_untouched():
    raw = "use [s1].\n```py\nprint(arr[s1])\n```\nand `x[s2]` inline [s2]."
    text, sources = resolve_citations(raw, _ledger_with())
    assert "print(arr[s1])" in text
    assert "`x[s2]`" in text
    assert text.startswith("use [1].")
    assert text.endswith("inline [2].")
    assert len(sources) == 2


def test_marks_records_cited():
    ledger = _ledger_with()
    resolve_citations("cite [s1]", ledger)
    assert ledger.get("s1").cited is True
    assert ledger.get("s2").cited is False


def test_no_markers_returns_text_unchanged_and_empty_sources():
    text, sources = resolve_citations("no citations here", _ledger_with())
    assert text == "no citations here"
    assert sources == []


def test_none_ledger_strips_all_markers():
    text, sources = resolve_citations("orphan [s1]", None)
    assert text == "orphan "
    assert sources == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_citation_resolver.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_citations'`

- [ ] **Step 3: Append resolver to `sources.py`**

```python
# append to src/cuga/backend/knowledge/sources.py

# --- citation resolution ------------------------------------------------------

# [s1] or [s1, s4] — case-insensitive. Requires the s-prefix so plain
# bracketed numbers/text ("[1]", "[note]") never match.
_MARKER_RE = re.compile(r"\[\s*([sS]\d+(?:\s*,\s*[sS]\d+)*)\s*\]")
# Segments the answer so markers inside code are never rewritten.
_CODE_RE = re.compile(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)")


def has_citation_markers(text: str) -> bool:
    return bool(text) and _MARKER_RE.search(text) is not None


def resolve_citations(
    text: str, ledger: SourceLedger | None
) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite ``[sN]`` markers into per-message display numbers ``[k]``.

    - Display numbers are assigned in order of first appearance.
    - Ids missing from the ledger (hallucinated, or evicted) are stripped.
    - Code fences / inline code are left byte-identical.
    Returns ``(display_text, sources_snapshots)``.
    """
    if not text or not has_citation_markers(text):
        return text, []

    numbers: dict[str, int] = {}          # cite_id -> display n
    ordered: list[SourceRecord] = []

    def _sub(match: re.Match) -> str:
        out = []
        for raw_id in match.group(1).split(","):
            cite_id = raw_id.strip().lower()
            record = ledger.get(cite_id) if ledger is not None else None
            if record is None:
                logger.warning("citation marker [{}] not in ledger — stripped", cite_id)
                continue
            if cite_id not in numbers:
                numbers[cite_id] = len(numbers) + 1
                record.cited = True
                ordered.append(record)
            out.append(f"[{numbers[cite_id]}]")
        return "".join(out)

    parts = _CODE_RE.split(text)
    resolved = "".join(
        part if i % 2 else _MARKER_RE.sub(_sub, part) for i, part in enumerate(parts)
    )
    sources = [rec.to_snapshot(n=i + 1) for i, rec in enumerate(ordered)]
    return resolved, sources
```

- [ ] **Step 4: Run both test files**

Run: `uv run pytest tests/unit/test_citation_resolver.py tests/unit/test_source_ledger.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/knowledge/sources.py tests/unit/test_citation_resolver.py
git commit -m "feat(knowledge): citation marker resolver with per-message renumbering"
```

### Task 3: `citations_enabled` config flag + session override helper

**Files:**
- Modify: `src/cuga/backend/knowledge/config.py` (dataclass field ~line 449, `from_settings` ~line 1040, `validate` near the `rerank_enabled` bool check ~line 802)
- Modify: `src/cuga/configurations/knowledge/knowledge_settings.toml` (top `[knowledge]` block)
- Modify: `src/cuga/backend/knowledge/engine.py` (`get_settings` dict, ~line 3894-3988)
- Modify: `src/cuga/backend/knowledge/sources.py` (enablement helper)
- Test: `tests/unit/test_citation_settings.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_citation_settings.py
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge import sources as sources_mod
from cuga.backend.knowledge.sources import citations_enabled_for, set_session_override_lookup


def teardown_function():
    set_session_override_lookup(None)


def test_default_is_enabled():
    assert KnowledgeConfig().citations_enabled is True


def test_round_trips_through_to_dict_and_coerce():
    cfg = KnowledgeConfig.coerce_and_validate({"citations_enabled": False})
    assert cfg.citations_enabled is False
    assert cfg.to_dict()["citations_enabled"] is False
    # string coercion parity with other bool fields
    cfg2 = KnowledgeConfig.coerce_and_validate({"citations_enabled": "true"})
    assert cfg2.citations_enabled is True


def test_not_in_vector_config_hash():
    a = KnowledgeConfig(citations_enabled=True)
    b = KnowledgeConfig(citations_enabled=False)
    assert a.vector_config_hash() == b.vector_config_hash()


def test_effective_enablement_prefers_session_override():
    cfg = KnowledgeConfig(citations_enabled=True)
    assert citations_enabled_for(cfg, "t-1") is True
    set_session_override_lookup(lambda tid: {"citations_enabled": False} if tid == "t-1" else {})
    assert citations_enabled_for(cfg, "t-1") is False
    assert citations_enabled_for(cfg, "t-2") is True
    # override can also force ON over an agent-level OFF
    cfg_off = KnowledgeConfig(citations_enabled=False)
    set_session_override_lookup(lambda tid: {"citations_enabled": True})
    assert citations_enabled_for(cfg_off, "t-1") is True
    assert citations_enabled_for(cfg_off, None) is False  # no thread -> agent default


def test_lookup_errors_fall_back_to_config():
    def boom(tid):
        raise RuntimeError("provider down")
    set_session_override_lookup(boom)
    assert citations_enabled_for(KnowledgeConfig(citations_enabled=True), "t") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_citation_settings.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'citations_enabled'` / ImportError on helpers

- [ ] **Step 3: Implement**

`config.py` — add the field directly after `session_level_enabled: bool = True` (~line 449):

```python
    # Numbered source citations ([1] + snippets) in agent answers. Search-only
    # behavior: deliberately EXCLUDED from vector_config_hash() so flipping it
    # never triggers a reindex. Per-session override lives in
    # SessionKnowledgeState.overrides (see knowledge/sources.py).
    citations_enabled: bool = True
```

`config.py` `from_settings` — next to the sibling reads (~line 1040-1042):

```python
            citations_enabled=kb.get("citations_enabled", True),
```

`config.py` `validate()` — alongside the `rerank_enabled` isinstance check (~line 802):

```python
        if not isinstance(self.citations_enabled, bool):
            raise ValueError("knowledge.citations_enabled must be a boolean")
```

> Note for implementor: `coerce_and_validate` coerces bools generically from dataclass field types (`config.py:950-958`) and filters unknown keys against `dataclasses.fields(KnowledgeConfig)` — adding the field is sufficient for the PATCH/publish/snapshot round-trip. Verify with the test; do NOT add it to any profile TOML or to `vector_config_hash()`.

`knowledge_settings.toml` — after `session_level_enabled = true`:

```toml
citations_enabled = true  # numbered [1] source citations in answers (UI toggle: Knowledge -> Settings)
```

`engine.py` `get_settings()` — add to the `"knowledge": {...}` dict next to `session_level_enabled`:

```python
            "citations_enabled": self._config.citations_enabled,
```

`sources.py` — append:

```python
# --- enablement ---------------------------------------------------------------

# Server wires this to the session provider (main.py); SDK leaves it None so
# enablement falls back to the agent-level KnowledgeConfig flag alone.
_override_lookup: Callable[[str], dict[str, Any] | None] | None = None


def set_session_override_lookup(
    fn: Callable[[str], dict[str, Any] | None] | None,
) -> None:
    global _override_lookup
    _override_lookup = fn


def citations_enabled_for(config: Any, thread_id: str | None) -> bool:
    """Effective citations flag: per-session override wins over agent config."""
    base = bool(getattr(config, "citations_enabled", True))
    if thread_id and _override_lookup is not None:
        try:
            overrides = _override_lookup(thread_id) or {}
            if "citations_enabled" in overrides:
                return bool(overrides["citations_enabled"])
        except Exception:
            logger.exception("session override lookup failed; using agent-level flag")
    return base
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_citation_settings.py tests/unit/test_source_ledger.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/cuga/backend/knowledge/config.py src/cuga/backend/knowledge/engine.py \
        src/cuga/backend/knowledge/sources.py src/cuga/configurations/knowledge/knowledge_settings.toml \
        tests/unit/test_citation_settings.py
git commit -m "feat(knowledge): citations_enabled config flag with per-session override hook"
```

### Task 4: Stamp `cite_id` at both retrieval seams

**Files:**
- Modify: `src/cuga/backend/knowledge/sources.py` (stamping helper)
- Modify: `src/cuga/backend/knowledge/client.py` (`search_envelope`, ~line 201-300 — stamp before return)
- Modify: `src/cuga/backend/knowledge/routes.py` (`search`, ~line 281-425 — stamp each envelope before returning; both the single-scope and `_run_multi` branches)
- Modify: `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py` (~line 448-449 — always forward `thread_id`)
- Test: `tests/unit/test_envelope_citations.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_envelope_citations.py
from types import SimpleNamespace

from cuga.backend.knowledge.sources import (
    SourceLedger,
    annotate_envelope_with_citations,
    CITATION_DIRECTIVE,
    _reset_all_ledgers_for_tests,
    get_ledger,
)


def _result(text, filename="a.pdf", page=1, scope="agent"):
    return SimpleNamespace(text=text, filename=filename, page=page, scope=scope,
                           score=0.8, section_path="")


def _envelope(results):
    chunks = [{"source": r.scope, "text": r.text, "filename": r.filename, "page": r.page}
              for r in results]
    env = {"scope": "agent", "results": chunks,
           "retrieval": {"reading_directive": "base directive."}}
    # emulate envelope.py by_source sharing the SAME dict objects
    env["by_source"] = {"agent": [c for c in chunks]}
    return env


def setup_function():
    _reset_all_ledgers_for_tests()


def test_stamps_cite_ids_and_extends_directive():
    results = [_result("one"), _result("two")]
    env = _envelope(results)
    annotate_envelope_with_citations(env, results, thread_id="t-1", query="q")
    assert env["results"][0]["cite_id"] == "s1"
    assert env["results"][1]["cite_id"] == "s2"
    # by_source shares dicts -> stamped too
    assert env["by_source"]["agent"][0]["cite_id"] == "s1"
    assert CITATION_DIRECTIVE.strip() in env["retrieval"]["reading_directive"]


def test_same_chunk_next_call_keeps_cite_id():
    results = [_result("stable")]
    env1 = _envelope(results)
    annotate_envelope_with_citations(env1, results, thread_id="t-1", query="q1")
    env2 = _envelope(results)
    annotate_envelope_with_citations(env2, results, thread_id="t-1", query="q2")
    assert env1["results"][0]["cite_id"] == env2["results"][0]["cite_id"] == "s1"
    assert len(get_ledger("t-1")) == 1


def test_no_thread_id_is_a_noop():
    results = [_result("x")]
    env = _envelope(results)
    annotate_envelope_with_citations(env, results, thread_id="", query="q")
    assert "cite_id" not in env["results"][0]
    assert env["retrieval"]["reading_directive"] == "base directive."


def test_result_count_mismatch_is_safe():
    results = [_result("x")]
    env = _envelope(results)
    env["results"].append({"source": "agent", "text": "phantom"})
    annotate_envelope_with_citations(env, results, thread_id="t-1", query="q")
    assert env["results"][0]["cite_id"] == "s1"
    assert "cite_id" not in env["results"][1]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_envelope_citations.py -v`
Expected: FAIL — ImportError on `annotate_envelope_with_citations`

- [ ] **Step 3: Implement the stamping helper (`sources.py`)**

```python
# append to src/cuga/backend/knowledge/sources.py

# Rides on retrieval.reading_directive so the LLM sees it at composition time
# (the system-prompt contract is thousands of tokens upstream by then).
CITATION_DIRECTIVE = (
    " CITATIONS: each result carries a cite_id. In your FINAL text answer, "
    "append [<cite_id>] immediately after every claim taken from that chunk "
    "(example: 'The total is 4,521 [s2].'). Use ONLY cite_ids you received in "
    "this conversation; ids from earlier searches this conversation remain "
    "valid. Never invent ids; never write bare numeric citations like [1]."
)


def annotate_envelope_with_citations(
    envelope: dict[str, Any],
    results: list[Any],
    *,
    thread_id: str | None,
    query: str,
) -> None:
    """Register results in the thread ledger and stamp ``cite_id`` onto the
    wire chunks (in place). No-op when thread_id is missing — a search that
    cannot be correlated to a conversation cannot be cited.

    ``envelope["results"][i]`` must correspond to ``results[i]`` (the
    invariant of build_retrieval_envelope). ``by_source`` holds the SAME
    chunk dict objects, so stamping results also stamps the grouped view.
    """
    if not thread_id or not results:
        if not thread_id:
            logger.debug("knowledge search without thread_id — citations skipped")
        return
    ledger = get_ledger(thread_id)
    if ledger is None:
        return
    chunks = envelope.get("results") or []
    for chunk, result in zip(chunks, results):
        chunk["cite_id"] = ledger.register(result, query=query)
    retrieval = envelope.get("retrieval")
    if isinstance(retrieval, dict) and retrieval.get("reading_directive"):
        retrieval["reading_directive"] += CITATION_DIRECTIVE
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_envelope_citations.py -v`
Expected: all PASS

- [ ] **Step 5: Wire seam 1 — `KnowledgeClient.search_envelope` (`client.py`)**

In `search_envelope` (client.py:201-300), immediately after each `build_retrieval_envelope(...)` result is assigned (there is one assignment per branch — single-scope and the session→all fallback), add before `return`:

```python
        from cuga.backend.knowledge.sources import (
            annotate_envelope_with_citations,
            citations_enabled_for,
        )
        if citations_enabled_for(self._engine._config, thread_id):
            annotate_envelope_with_citations(
                envelope, results, thread_id=thread_id, query=query
            )
```

(Use the local variable names of that function — the results list passed into `build_retrieval_envelope` and the returned dict. Import at module top instead of inline if ruff prefers; `sources.py` has no heavy imports.)

- [ ] **Step 6: Wire seam 2 — HTTP route (`routes.py` `search`)**

Same call in both places the route finalizes a response dict from `build_retrieval_envelope` (single-scope branch and `_run_multi`), using `identity.thread_id`:

```python
    if citations_enabled_for(engine._config, identity.thread_id):
        annotate_envelope_with_citations(
            response, results, thread_id=identity.thread_id, query=query
        )
```

Add imports at top of `routes.py`:
```python
from cuga.backend.knowledge.sources import (
    annotate_envelope_with_citations,
    citations_enabled_for,
)
```

> Implementor note: the two seams double-cover nothing in practice (chat/SDK go through `client.py` in-process; sandbox→registry→MCP and E2B go through the HTTP route). If a deployment ever routed one call through both, the ledger's content-hash idempotency makes the second stamp identical — harmless by construction.

- [ ] **Step 7: Fix thread_id forwarding for agent-scope searches (`prepare_node.py` ~448-449)**

Replace:
```python
        if tid and "session" in allowed_scopes:
            kwargs.setdefault("thread_id", tid)
```
with:
```python
        # Forward the conversation id whenever we have one — the knowledge
        # layer needs it for the citations ledger even on agent-scope
        # searches. Session-collection access is still gated by scope checks
        # server-side; this only adds correlation, not access.
        if tid:
            kwargs.setdefault("thread_id", tid)
```

- [ ] **Step 8: Run the knowledge unit-test suite for regressions**

Run: `uv run pytest tests/unit -k "knowledge or citation or envelope or session" -v`
Expected: PASS (pre-existing envelope/scope tests unaffected — stamping only adds keys)

- [ ] **Step 9: Commit**

```bash
git add src/cuga/backend/knowledge/sources.py src/cuga/backend/knowledge/client.py \
        src/cuga/backend/knowledge/routes.py \
        src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py \
        tests/unit/test_envelope_citations.py
git commit -m "feat(knowledge): stamp stable cite_ids on retrieval results at both seams"
```

### Task 5: Prompt contract (system-prompt + regen/formatter preservation)

**Files:**
- Modify: `src/cuga/backend/knowledge/awareness.py` (contract constant + conditional append in `assemble_system_prompt_section`, ~line 410-466)
- Modify: `src/cuga/backend/cuga_graph/nodes/answer/final_answer_agent/prompts/system.jinja2` (preserve rule)
- Modify: `src/cuga/backend/cuga_graph/policy/output_formatter_utils.py` (preserve rule in the reformat prompt)
- Test: extend `tests/unit/test_citation_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_citation_settings.py
from cuga.backend.knowledge.awareness import CITATIONS_CONTRACT


def test_citations_contract_content():
    assert "[s3]" in CITATIONS_CONTRACT
    assert "cite_id" in CITATIONS_CONTRACT
    assert "earlier turns" in CITATIONS_CONTRACT.lower()
    assert "never write bare numeric" in CITATIONS_CONTRACT.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_citation_settings.py::test_citations_contract_content -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Add the contract to `awareness.py`**

Module-level constant:

```python
CITATIONS_CONTRACT = """
## Citations (mandatory when answering from knowledge results)

Every knowledge search result carries a `cite_id` (like "s3"). In your FINAL
answer, append the marker right after each claim that comes from a retrieved
chunk — before the sentence-ending punctuation is fine:
"The report number is 4521 [s3]."

Rules:
- Use ONLY cite_ids that appeared in this conversation's search results.
  Results from earlier turns of this conversation remain citable by their
  original ids. Never invent an id; never write bare numeric citations
  like [1] — the UI assigns display numbers automatically.
- Multiple supporting chunks: [s1][s4] (or [s1, s4]).
- Do NOT add a "Sources" section or list — the UI renders sources from
  your markers.
- If no retrieved chunk supports a claim, omit the claim or say the
  knowledge base doesn't cover it (no marker).
"""
```

In `assemble_system_prompt_section` (awareness.py:410+), after the contract/instructions text is composed and gated on the same `cfg` used for `max_search_attempts` (the `search_config or engine._config` resolution at awareness.py:441):

```python
    if getattr(cfg, "citations_enabled", True):
        contract_text = contract_text + "\n" + CITATIONS_CONTRACT
```

(Attach to whatever local holds the knowledge-instructions contract before assembly — follow the existing composition; keep the `contract_chars` accounting consistent by appending before the length is measured.)

> Session-override nuance: the prompt is assembled once per run with only agent-level `cfg` in hand. That is correct — a session override to OFF still hard-disables stamping at the search seam (Task 4), so the model simply has no cite_ids to copy; the resolver then finds no valid markers. An ON-override with agent-level OFF enables stamping, and the per-envelope `CITATION_DIRECTIVE` (Task 4) teaches marker syntax at retrieval time even without this system-prompt section. Both directions degrade correctly.

- [ ] **Step 4: Preserve markers through the regen path**

`final_answer_agent/prompts/system.jinja2` — add to `# Constraints & Guidelines:` list:

```
* **Citation markers**: If the initial_draft_answer contains citation markers like [s3] or [s1, s4], preserve them VERBATIM, attached to the same claims. Never renumber, merge, translate, or drop them; never add new ones.
```

`output_formatter_utils.py` — locate the LLM reformat prompt string and append the same single-line rule:

```
Preserve citation markers like [s3] verbatim and attached to the same claims; never renumber, drop, or invent them.
```

- [ ] **Step 5: Run tests + commit**

Run: `uv run pytest tests/unit/test_citation_settings.py -v` — Expected: PASS

```bash
git add src/cuga/backend/knowledge/awareness.py \
        src/cuga/backend/cuga_graph/nodes/answer/final_answer_agent/prompts/system.jinja2 \
        src/cuga/backend/cuga_graph/policy/output_formatter_utils.py \
        tests/unit/test_citation_settings.py
git commit -m "feat(knowledge): LLM citation contract in prompt + marker preservation rules"
```

### Task 6: Resolution at `FinalAnswerNode` + `AgentState.sources`

**Files:**
- Modify: `src/cuga/backend/cuga_graph/state/agent_state.py` (~line 1004, next to `tool_calls`)
- Modify: `src/cuga/backend/cuga_graph/nodes/answer/final_answer.py`
- Test: `tests/unit/test_final_answer_citations.py`

- [ ] **Step 1: Add the state field (`agent_state.py`, after `tool_calls`)**

```python
    # Resolved citation sources for the current final_answer (per-message
    # snapshots, see knowledge/sources.py). Display copy only — the raw [sN]
    # markers stay in chat history so later turns can re-cite stable ids.
    sources: List[Dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_final_answer_citations.py
from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.answer.final_answer import FinalAnswerNode
from cuga.backend.knowledge.sources import get_ledger, _reset_all_ledgers_for_tests


class _State(SimpleNamespace):
    pass


def _state(answer, thread_id="t-x"):
    return _State(final_answer=answer, thread_id=thread_id, sources=[])


def setup_function():
    _reset_all_ledgers_for_tests()


def _seed_ledger(thread_id="t-x"):
    ledger = get_ledger(thread_id)
    ledger.register(
        SimpleNamespace(text="chunk", filename="f.pdf", page=2, scope="agent",
                        score=0.9, section_path=""),
        query="q",
    )


def test_resolves_markers_and_sets_sources():
    _seed_ledger()
    state = _state("answer [s1] done")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "answer [1] done"
    assert state.sources[0]["filename"] == "f.pdf"


def test_no_markers_sets_empty_sources_without_ledger_creation():
    state = _state("plain answer", thread_id="never-seen-thread")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "plain answer"
    assert state.sources == []
    assert get_ledger("never-seen-thread", create=False) is None


def test_hallucinated_marker_stripped_even_without_ledger():
    state = _state("fake [s7] claim", thread_id="fresh-thread")
    FinalAnswerNode.apply_citation_resolution(state)
    assert state.final_answer == "fake  claim"
    assert state.sources == []


def test_resolution_errors_never_break_the_answer(monkeypatch):
    _seed_ledger()
    state = _state("answer [s1]")
    monkeypatch.setattr(
        "cuga.backend.knowledge.sources.resolve_citations",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    FinalAnswerNode.apply_citation_resolution(state)   # must not raise
    assert state.final_answer == "answer [s1]"         # untouched on failure
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/unit/test_final_answer_citations.py -v`
Expected: FAIL — no attribute `apply_citation_resolution`

- [ ] **Step 4: Implement in `final_answer.py`**

Add to `FinalAnswerNode`:

```python
    @staticmethod
    def apply_citation_resolution(state) -> None:
        """Rewrite [sN] ledger markers in final_answer into per-message [n]
        display numbers and attach self-contained source snapshots.

        Must run AFTER variable placeholder replacement and AFTER output
        formatters, and must never break answer delivery — any failure
        leaves the text as-is.
        """
        try:
            from cuga.backend.knowledge.sources import (
                get_ledger,
                has_citation_markers,
                resolve_citations,
            )

            text = state.final_answer or ""
            if not has_citation_markers(text):
                state.sources = []
                return
            ledger = get_ledger(state.thread_id, create=True) if state.thread_id else None
            resolved, sources = resolve_citations(text, ledger)
            state.final_answer = resolved
            state.sources = sources
        except Exception:
            logger.exception("citation resolution failed; delivering unresolved answer")
```

Call it in **every** terminal branch of `node_handler` / `_generate_final_answer`, always as the last mutation of `state.final_answer` before the `FinalAnswerOutput` is constructed:

1. `sender == CHAT_AGENT` branch (final_answer.py:86-95): after `state.final_answer = final_answer_content`, add `FinalAnswerNode.apply_citation_resolution(state)` and build `FinalAnswerOutput(..., final_answer=state.final_answer)`.
2. `sender == TASK_ANALYZER_AGENT` branch (:98-108): same, right after the sender check (before constructing output). This branch's answers never cite, but the guard is O(1) — uniformity beats special-casing.
3. `sender == CUGA_LITE` branch (:109-119): same, immediately before `FinalAnswerOutput(...)`.
4. `sender == CUGA_SUPERVISOR` branch (:122-148): after `state.final_answer = answer_to_forward`.
5. `_generate_final_answer` (:162-184): after `state.final_answer = final_answer_output.final_answer` (i.e., **after** `replace_variables_placeholders`), then re-sync `final_answer_output.final_answer = state.final_answer` before it is tracked.

> Note: the `state.append_to_last_chat_message(...)` call at final_answer.py:173-175 runs BEFORE resolution by existing order — leave it. That is invariant 2: the LLM transcript keeps raw `[sN]` so later turns can re-cite; only the display copy is renumbered.

- [ ] **Step 5: Run tests + commit**

Run: `uv run pytest tests/unit/test_final_answer_citations.py -v` — Expected: PASS

```bash
git add src/cuga/backend/cuga_graph/state/agent_state.py \
        src/cuga/backend/cuga_graph/nodes/answer/final_answer.py \
        tests/unit/test_final_answer_citations.py
git commit -m "feat(agent): resolve citation markers at FinalAnswerNode into per-message sources"
```

### Task 7: Server egress — Answer payload, WXO footer, cleanup, rehydration

**Files:**
- Modify: `src/cuga/backend/cuga_graph/utils/agent_loop.py` (`AgentLoopAnswer` ~line 118-128; `get_output` ~line 536-678)
- Modify: `src/cuga/backend/server/main.py` (Answer payload ~1559-1571; WXO branch; lifespan wiring; thread cleanup ~162-172 & `/reset` ~2171)

- [ ] **Step 1: Extend `AgentLoopAnswer` (agent_loop.py:118-128)**

```python
class AgentLoopAnswer(BaseModel):
    end: bool
    interrupt: bool = False
    answer: Optional[Any] = None
    has_tools: bool = False
    tools: List[ToolCall]
    flow_generalized: Optional[bool] = False
    sources: List[Dict[str, Any]] = Field(default_factory=list)
```
(add `Dict` to the typing imports if missing.)

- [ ] **Step 2: Populate it in `get_output`**

Wherever the terminal answer is built from state values (the `"FinalAnswerAgent" in event_keys or "CodeAgent" in event_keys` handling, agent_loop.py:580-673), read the state's `sources` alongside `final_answer` and pass `sources=state_data.get("sources") or []` into every `AgentLoopAnswer(...)` construction in that path (leave interrupt/error constructions at the default).

- [ ] **Step 3: Extend the `Answer` SSE payload (main.py ~1559-1571)**

In the default-format branch where `final_answer_text = json.dumps({"data": event.answer, "variables": variables_metadata, "active_policies": ...})` is built, extend:

```python
                answer_payload = {
                    "data": event.answer,
                    "variables": variables_metadata,
                    "active_policies": active_policies,
                }
                if event.sources:
                    answer_payload["sources"] = event.sources
                final_answer_text = json.dumps(answer_payload)
```

In the WXO branch (raw `event.answer` string), append a plain-text footer when sources exist:

```python
                if event.sources:
                    lines = []
                    for s in event.sources:
                        page = f" p.{s['page']}" if s.get("page") is not None else ""
                        lines.append(f"[{s['n']}] {s['filename']}{page}")
                    event.answer = f"{event.answer}\n\nSources:\n" + "\n".join(lines)
```

No persistence change needed: the `Answer` event (with sources embedded) is already buffered into `stream_events_buffer` and saved (main.py:1604-1607, 1680-1690) → history replay carries citations automatically.

- [ ] **Step 4: Wire override lookup + rehydrator + cleanup (main.py lifespan / helpers)**

Where `app_state.knowledge_provider = PersistentSessionProvider(...)` is created (main.py:552-554), add:

```python
    from cuga.backend.knowledge import sources as knowledge_sources

    def _session_overrides_lookup(thread_id: str):
        provider = getattr(app_state, "knowledge_provider", None)
        if provider is None:
            return None
        session = provider.get_session(thread_id)
        return session.overrides if session else None

    knowledge_sources.set_session_override_lookup(_session_overrides_lookup)

    def _rehydrate_ledger(thread_id: str, ledger) -> None:
        """After a restart, rebuild cited entries from persisted Answer events
        so cite_ids stay collision-free and re-citable."""
        import anyio

        async def _load():
            history = await conversation_db.get_stream_events(
                app_state.agent_id or "cuga-default", thread_id
            )
            return history.events if history else []

        try:
            events = anyio.from_thread.run(_load)  # ledger misses occur on worker threads
        except Exception:
            events = []
        for ev in events:
            if ev.event_name != "Answer":
                continue
            try:
                data_line = next(
                    ln for ln in ev.event_data.splitlines() if ln.startswith("data: ")
                )
                payload = json.loads(data_line[len("data: "):])
                for snap in payload.get("sources", []) or []:
                    ledger.restore(snap)
            except Exception:
                continue

    knowledge_sources.set_rehydrator(_rehydrate_ledger)
```

> Implementor: match the real `conversation_db` accessor + stored event framing (`event_data` holds the full framed SSE string for pass-through events but plain data for `Answer` — check `main.py:1604-1607` vs `:1682-1689`; if `Answer` is buffered with plain JSON data, drop the `data: `-line scan and `json.loads(ev.event_data)` directly). If the sync/async bridge proves awkward, an acceptable simplification is to rehydrate in `event_stream` (async context) at turn start when `get_ledger(thread_id, create=False) is None` — same effect, simpler call path; then `set_rehydrator` isn't needed. **Choose the simpler one; do not build both.**

Thread cleanup — in `_delete_session_knowledge_for_thread` (main.py:162-172) and the `/reset` handler (main.py:2171-2217):

```python
    from cuga.backend.knowledge.sources import drop_ledger
    drop_ledger(thread_id)
```

- [ ] **Step 5: Expose the agent-level flag to the chat UI**

In `GET /api/agent/context` (main.py:3426+, where `knowledge_enabled` / `agent_level_knowledge_enabled` / `session_level_knowledge_enabled` are returned at :3442-3443), add:

```python
        "citations_enabled": bool(
            engine and getattr(engine._config, "citations_enabled", True)
        ),
```
(follow the existing pattern used for the other knowledge flags in that handler).

- [ ] **Step 6: Verify + commit**

Run: `uv run pytest tests/unit -k "citation or ledger" -v` and boot smoke: `uv run python -c "from cuga.backend.server import main"`
Expected: PASS / clean import

```bash
git add src/cuga/backend/cuga_graph/utils/agent_loop.py src/cuga/backend/server/main.py
git commit -m "feat(server): stream per-message citation sources on the Answer event"
```

### Task 8: Session-level settings routes (the "session knowledge part" toggle)

**Files:**
- Modify: `src/cuga/backend/knowledge/routes.py`
- Test: `tests/unit/test_session_citation_override.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_session_citation_override.py
from cuga.backend.knowledge.session_provider import SessionProvider
from cuga.backend.knowledge.routes import _apply_session_settings_patch


def test_patch_applies_only_allowed_keys():
    provider = SessionProvider()
    state = _apply_session_settings_patch(
        provider, "t-1", {"citations_enabled": False, "evil": 1}, user_id="u", tenant_id=""
    )
    assert state.overrides == {"citations_enabled": False}


def test_patch_coerces_truthy_strings():
    provider = SessionProvider()
    state = _apply_session_settings_patch(
        provider, "t-1", {"citations_enabled": "true"}, user_id="u", tenant_id=""
    )
    assert state.overrides["citations_enabled"] is True


def test_empty_patch_rejected():
    import pytest
    provider = SessionProvider()
    with pytest.raises(ValueError):
        _apply_session_settings_patch(provider, "t-1", {"unknown": 1}, user_id="u", tenant_id="")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_session_citation_override.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement routes + helper (`routes.py`)**

```python
_SESSION_SETTINGS_ALLOWED = {"citations_enabled"}


def _coerce_flag(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _apply_session_settings_patch(provider, thread_id: str, body: dict, *, user_id: str, tenant_id: str):
    patch = {k: _coerce_flag(v) for k, v in (body or {}).items() if k in _SESSION_SETTINGS_ALLOWED}
    if not patch:
        raise ValueError(
            f"no valid session settings in patch; allowed: {sorted(_SESSION_SETTINGS_ALLOWED)}"
        )
    return provider.patch_session_overrides(thread_id, patch, user_id=user_id, tenant_id=tenant_id)


@knowledge_router.get("/session/settings")
async def get_session_settings(
    request: Request, identity: KnowledgeIdentity = Depends(require_internal_or_auth)
):
    """Per-conversation knowledge settings overrides (citations toggle)."""
    if not identity.thread_id:
        raise HTTPException(status_code=400, detail="X-Thread-ID header required")
    provider = getattr(request.app.state.app_state, "knowledge_provider", None)
    if provider is None:
        raise HTTPException(status_code=503, detail="knowledge not initialized")
    session = provider.get_session(identity.thread_id)
    return {"thread_id": identity.thread_id, "overrides": (session.overrides if session else {})}


@knowledge_router.patch("/session/settings")
async def patch_session_settings(
    request: Request, identity: KnowledgeIdentity = Depends(require_internal_or_auth)
):
    if not identity.thread_id:
        raise HTTPException(status_code=400, detail="X-Thread-ID header required")
    provider = getattr(request.app.state.app_state, "knowledge_provider", None)
    if provider is None:
        raise HTTPException(status_code=503, detail="knowledge not initialized")
    body = await request.json()
    try:
        state = _apply_session_settings_patch(
            provider, identity.thread_id, body,
            user_id=identity.user_id or "", tenant_id=identity.tenant_id or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"thread_id": identity.thread_id, "overrides": state.overrides}
```

(Match the file's existing imports/`Depends` style; `require_internal_or_auth` and `KnowledgeIdentity` are already imported there for `/search`. Session ownership is enforced by the provider's existing `check_session_access` semantics via `get_or_create_session`/`patch_session_overrides` args — mirror what `resolve_collection` passes.)

- [ ] **Step 4: Run + commit**

Run: `uv run pytest tests/unit/test_session_citation_override.py tests/unit/test_session_knowledge.py -v`
Expected: PASS (including the pre-existing session provider tests)

```bash
git add src/cuga/backend/knowledge/routes.py tests/unit/test_session_citation_override.py
git commit -m "feat(knowledge): per-session settings routes lighting up the overrides extension point"
```

### Task 9: SDK — `InvokeResult.sources` + `enable_citations`

**Files:**
- Modify: `src/cuga/sdk.py` (`InvokeResult` :129-146; constructor :1593-1670; `knowledge` property :2044-2069; extraction sites :2300, :2483, :3098)

- [ ] **Step 1: Extend `InvokeResult`**

```python
    sources: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Citation sources for the answer: [{n, cite_id, filename, page, "
        "section_path, scope, snippet, score, query}] — populated when knowledge "
        "citations are enabled and the answer cites retrieved chunks",
    )
```

- [ ] **Step 2: Populate at all three construction sites**

Sites 1 & 2 (CugaAgent.invoke, sdk.py:2300-2306 and :2483-2489): alongside the existing `tool_calls` extraction add
```python
        sources = result.get("sources", []) or []
```
and pass `sources=sources` into `InvokeResult(...)`. Site 3 (CugaSupervisor.invoke, :3098-3103): mirror the dict/attr dual-read pattern used for `tool_calls`:
```python
    sources = (
        result.get("sources", []) if isinstance(result, dict) else getattr(result, "sources", [])
    ) or []
```

- [ ] **Step 3: Constructor toggle**

Add `enable_citations: Optional[bool] = None` to `CugaAgent.__init__` (docstring: "None = follow knowledge settings; True/False override `knowledge.citations_enabled` for this agent instance"). Store `self._enable_citations = enable_citations`. In the `knowledge` property (sdk.py:2044-2069), after `config = KnowledgeConfig.from_settings(settings)`:

```python
        if self._enable_citations is not None:
            config.citations_enabled = self._enable_citations
```

- [ ] **Step 4: Verify + commit**

Run: `uv run pytest src/cuga/sdk_core/tests -x -q -k "not integration"` (or the repo's fast SDK suite) and `uv run python -c "from cuga.sdk import InvokeResult; print(InvokeResult(sources=[{'n':1}]).sources)"`
Expected: PASS / `[{'n': 1}]`

```bash
git add src/cuga/sdk.py
git commit -m "feat(sdk): expose citation sources on InvokeResult and enable_citations toggle"
```

Stream consumers need no new API: `CugaAgent.stream()` yields raw LangGraph updates, and the `FinalAnswerAgent` node update now contains both `final_answer` and `sources` — document this in the field docstring above.

### Task 10: Frontend shared citation core (`agentic_chat/src/citations/`)

**Files:**
- Create: `types.ts`, `citeElement.ts`, `injectCitations.ts`, `injectCitations.test.ts`, `MessageSources.tsx`, `SourcesPanel.tsx`, `SourcesPanel.css`, `index.ts` under `src/frontend_workspaces/agentic_chat/src/citations/`
- Modify: `src/frontend_workspaces/agentic_chat/package.json` (exports)

- [ ] **Step 1: `types.ts`**

```ts
export interface MessageSource {
  n: number;
  cite_id: string;
  filename: string;
  page?: number | null;
  section_path?: string;
  scope: string; // "agent" | "session"
  snippet: string;
  score?: number;
  query?: string;
}

const PAGELESS_EXTENSIONS = [".txt", ".md", ".log", ".json", ".csv", ".xml"];

/** Page label, honest about formats where "page" is really a chunk ordinal. */
export function pageLabel(source: Pick<MessageSource, "filename" | "page">): string {
  if (source.page === null || source.page === undefined) return "";
  const lower = source.filename.toLowerCase();
  if (PAGELESS_EXTENSIONS.some((ext) => lower.endsWith(ext))) return "";
  return `p.${source.page}`;
}

export function scopeLabel(scope: string): string {
  return scope === "session" ? "This conversation" : "Agent knowledge";
}

export function escapeAttr(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
```

- [ ] **Step 2: `injectCitations.ts` + failing tests first**

```ts
// injectCitations.test.ts
import { describe, expect, it } from "vitest";
import { injectCitations } from "./injectCitations";
import type { MessageSource } from "./types";

const SRC = (n: number): MessageSource => ({
  n, cite_id: `s${n}`, filename: `f${n}.pdf`, page: n, scope: "agent", snippet: "text",
});

describe("injectCitations", () => {
  it("replaces known [n] with cuga-cite elements", () => {
    const out = injectCitations("fact [1].", [SRC(1)]);
    expect(out).toContain('<cuga-cite n="1"');
    expect(out).toContain('filename="f1.pdf"');
    expect(out).not.toContain("[1]");
  });

  it("leaves unknown bracket numbers alone", () => {
    expect(injectCitations("array [3] index", [SRC(1)])).toBe("array [3] index");
  });

  it("never rewrites inside code fences or inline code", () => {
    const text = "cite [1]\n```\nx[1]\n```\nand `y[1]` done [1]";
    const out = injectCitations(text, [SRC(1)]);
    expect(out).toContain("x[1]");
    expect(out).toContain("`y[1]`");
    expect(out.match(/<cuga-cite/g)?.length).toBe(2);
  });

  it("escapes hostile filenames", () => {
    const evil: MessageSource = { ...SRC(1), filename: '"><img src=x onerror=1>' };
    const out = injectCitations("x [1]", [evil]);
    expect(out).not.toContain("<img");
    expect(out).toContain("&quot;&gt;&lt;img");
  });

  it("returns text unchanged when no sources", () => {
    expect(injectCitations("plain [1]", [])).toBe("plain [1]");
  });
});
```

Run: `npm run test -w agentic_chat -- --run src/citations/injectCitations.test.ts` — Expected: FAIL (module missing). Then implement:

```ts
// injectCitations.ts
import { escapeAttr, pageLabel, type MessageSource } from "./types";

const CODE_SPLIT = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g;
const MARKER = /\[(\d{1,3})\]/g;

/**
 * Replace resolved display markers [n] in answer markdown with <cuga-cite>
 * custom elements. Only numbers present in `sources` are replaced; code
 * fences and inline code are untouched. Output is markdown-with-inline-HTML,
 * safe for both `marked` (CardManager) and @carbon/ai-chat's markdown-it
 * (html:true, custom elements whitelisted).
 *
 * `messageKey` (optional) is stamped as a `msg` attribute on every chip so a
 * document-level click listener can resolve WHICH message's source set the
 * chip belongs to (chips only carry `n`, which repeats across messages).
 */
export function injectCitations(
  text: string,
  sources: MessageSource[],
  messageKey?: string,
): string {
  if (!text || !sources?.length) return text;
  const byN = new Map(sources.map((s) => [s.n, s]));
  const parts = text.split(CODE_SPLIT);
  return parts
    .map((part, i) => {
      if (i % 2 === 1) return part; // code segment
      return part.replace(MARKER, (whole, num) => {
        const source = byN.get(Number(num));
        if (!source) return whole;
        const page = pageLabel(source);
        return (
          `<cuga-cite n="${source.n}"` +
          (messageKey ? ` msg="${escapeAttr(messageKey)}"` : "") +
          ` filename="${escapeAttr(source.filename)}"` +
          (page ? ` page="${escapeAttr(page)}"` : "") +
          ` scope="${escapeAttr(source.scope)}"` +
          (source.section_path ? ` section="${escapeAttr(source.section_path)}"` : "") +
          ` preview="${escapeAttr((source.snippet || "").slice(0, 140))}"` +
          `></cuga-cite>`
        );
      });
    })
    .join("");
}
```

Re-run the test — Expected: PASS.

- [ ] **Step 3: `citeElement.ts` — the `<cuga-cite>` web component**

```ts
// citeElement.ts
// Self-contained citation chip. Registered on the GLOBAL custom-element
// registry, so it upgrades both in light DOM (CardManager marked HTML) and
// inside @carbon/ai-chat's nested shadow roots (verified: ai-chat 1.6.0 dist/es
// uses the global registry and its markdown renderer allows custom elements).
// All styling lives in the component's own shadow root — Carbon *tokens*
// inherit as CSS custom properties with hardcoded fallbacks for the extension
// surface, which loads no Carbon CSS in the message area.

export const CITE_CLICK_EVENT = "cuga-cite-click";

const TEMPLATE = `
<style>
  :host { display: inline-block; vertical-align: super; line-height: 0; position: relative; }
  button {
    all: unset; cursor: pointer; box-sizing: border-box;
    min-width: 16px; height: 16px; padding: 0 5px; border-radius: 8px;
    font: 600 11px/16px "IBM Plex Sans", system-ui, sans-serif; text-align: center;
    color: var(--cds-link-primary, #0f62fe);
    background: var(--cds-layer-02, #f4f4f4);
    border: 1px solid var(--cds-border-subtle-01, #e0e0e0);
    transition: background 70ms ease, color 70ms ease;
  }
  button:hover { background: var(--cds-layer-hover-02, #e8e8e8); }
  button:focus-visible { outline: 2px solid var(--cds-focus, #0f62fe); outline-offset: 1px; }
  :host([active]) button { background: var(--cds-link-primary, #0f62fe); color: #ffffff; }
  .card {
    display: none; position: absolute; bottom: calc(100% + 8px); left: 50%;
    transform: translateX(-50%); z-index: 9000;
    width: max-content; max-width: 280px; padding: 10px 12px;
    background: var(--cds-layer-01, #ffffff);
    border: 1px solid var(--cds-border-subtle-01, #e0e0e0);
    border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,.2);
    font: 400 12px/1.4 "IBM Plex Sans", system-ui, sans-serif;
    color: var(--cds-text-primary, #161616); text-align: start; line-height: 1.4;
  }
  :host(:hover) .card, :host(:focus-within) .card { display: block; }
  .head { display: flex; gap: 8px; justify-content: space-between; align-items: baseline; }
  .file { font-weight: 600; max-width: 180px; overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; unicode-bidi: plaintext; }
  .meta, .hint { color: var(--cds-text-secondary, #525252); font-size: 11px; }
  .section { color: var(--cds-text-secondary, #525252); font-size: 11px; margin-top: 2px; }
  .preview { margin-top: 6px; color: var(--cds-text-secondary, #525252); font-style: italic; }
  .hint { margin-top: 6px; color: var(--cds-link-primary, #0f62fe); }
</style>
<button type="button" aria-haspopup="dialog"></button>
<span class="card" role="tooltip">
  <span class="head"><span class="file"></span><span class="meta"></span></span>
  <span class="section"></span>
  <span class="preview"></span>
  <span class="hint">Click to view source →</span>
</span>`;

export class CugaCiteElement extends HTMLElement {
  static get observedAttributes() {
    return ["n", "filename", "page", "scope", "section", "preview"];
  }

  connectedCallback() {
    if (!this.shadowRoot) {
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = TEMPLATE;
      root.querySelector("button")!.addEventListener("click", (e) => {
        e.stopPropagation();
        this.dispatchEvent(
          new CustomEvent(CITE_CLICK_EVENT, {
            bubbles: true,
            composed: true, // crosses ai-chat's shadow boundaries
            detail: { n: Number(this.getAttribute("n") || 0) },
          }),
        );
      });
    }
    this.render();
  }

  attributeChangedCallback() {
    if (this.shadowRoot) this.render();
  }

  private render() {
    const root = this.shadowRoot!;
    const n = this.getAttribute("n") || "";
    const button = root.querySelector("button")!;
    button.textContent = n;
    button.setAttribute("aria-label", `Source ${n}: ${this.getAttribute("filename") || ""}`);
    root.querySelector(".file")!.textContent = this.getAttribute("filename") || "";
    const scope = this.getAttribute("scope") === "session" ? "session" : "agent";
    const page = this.getAttribute("page") || "";
    root.querySelector(".meta")!.textContent = [scope, page].filter(Boolean).join(" · ");
    root.querySelector(".section")!.textContent = this.getAttribute("section") || "";
    const preview = this.getAttribute("preview") || "";
    root.querySelector(".preview")!.textContent = preview ? `“${preview}…”` : "";
  }
}

export function registerCiteElement(): void {
  if (typeof window !== "undefined" && !customElements.get("cuga-cite")) {
    customElements.define("cuga-cite", CugaCiteElement);
  }
}
```

- [ ] **Step 4: `MessageSources.tsx` (footer)**

```tsx
import React from "react";
import { pageLabel, scopeLabel, type MessageSource } from "./types";

const MAX_VISIBLE = 8;

export function MessageSources({
  sources,
  onOpen,
}: {
  sources: MessageSource[];
  onOpen: (n: number) => void;
}) {
  if (!sources?.length) return null;
  const visible = sources.slice(0, MAX_VISIBLE);
  const hidden = sources.length - visible.length;
  return (
    <div className="cuga-msg-sources" role="list" aria-label="Answer sources">
      <span className="cuga-msg-sources__label">Sources</span>
      {visible.map((s) => (
        <button
          key={s.n}
          role="listitem"
          className="cuga-msg-sources__card"
          onClick={() => onOpen(s.n)}
          title={`${s.filename} — ${scopeLabel(s.scope)}`}
        >
          <span className="cuga-msg-sources__badge">{s.n}</span>
          <span className="cuga-msg-sources__file" dir="auto">{s.filename}</span>
          {pageLabel(s) && <span className="cuga-msg-sources__page">{pageLabel(s)}</span>}
          <span className={`cuga-msg-sources__dot cuga-msg-sources__dot--${s.scope}`} />
        </button>
      ))}
      {hidden > 0 && (
        <button className="cuga-msg-sources__card cuga-msg-sources__more"
                onClick={() => onOpen(visible.length + 1)}>
          +{hidden} more
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 5: `SourcesPanel.tsx` + `SourcesPanel.css`**

```tsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AILabel, AILabelContent } from "@carbon/react";
import { pageLabel, scopeLabel, type MessageSource } from "./types";
import "./SourcesPanel.css";

/** Split snippet into plain/mark segments for the retrieving-query terms.
 * Pure segmentation — never builds HTML strings, so hostile snippets are inert. */
export function highlightSegments(
  snippet: string,
  query?: string,
): Array<{ text: string; mark: boolean }> {
  const terms = (query || "")
    .split(/\s+/)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .filter((t) => t.length >= 3);
  if (!terms.length || !snippet) return [{ text: snippet, mark: false }];
  const re = new RegExp(`(${terms.join("|")})`, "giu");
  return snippet.split(re).map((part) => ({ text: part, mark: re.test(part) && part.length > 0 }));
}

export interface SourcesPanelProps {
  sources: MessageSource[];
  activeN?: number | null;
  onClose: () => void;
  /** Optional doc opener; when provided a "Open document" button renders.
   * Wire to GET /api/knowledge/documents/file via the host app's api module. */
  onOpenDocument?: (source: MessageSource) => Promise<boolean>;
}

export default function SourcesPanel({ sources, activeN, onClose, onOpenDocument }: SourcesPanelProps) {
  const activeRef = useRef<HTMLDivElement | null>(null);
  const [unavailable, setUnavailable] = useState<Record<number, boolean>>({});
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeN]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const items = useMemo(() => sources ?? [], [sources]);

  return (
    <aside className="cuga-sources-panel" role="complementary" aria-label="Answer sources">
      <header className="cuga-sources-panel__header">
        <button className="cuga-sources-panel__close" onClick={onClose} aria-label="Close sources">✕</button>
        <div>
          <h3>Sources</h3>
          <span className="cuga-sources-panel__subtitle">
            {items.length} source{items.length === 1 ? "" : "s"} · cited in this answer
          </span>
        </div>
        <AILabel autoAlign size="mini" aiText="AI" textLabel="Grounded answer">
          <AILabelContent>
            <p>
              These snippets were retrieved from the knowledge base and cited by the
              agent. Numbers match the [n] markers in the answer; highlights show the
              search terms that surfaced each snippet.
            </p>
          </AILabelContent>
        </AILabel>
      </header>
      <div className="cuga-sources-panel__list">
        {items.length === 0 && (
          <p className="cuga-sources-panel__empty">This answer didn't cite knowledge-base sources.</p>
        )}
        {items.map((s) => (
          <div
            key={s.n}
            ref={s.n === activeN ? activeRef : undefined}
            className={`cuga-sources-panel__item${s.n === activeN ? " is-active" : ""}`}
          >
            <div className="cuga-sources-panel__item-head">
              <span className="cuga-sources-panel__badge">{s.n}</span>
              <span className="cuga-sources-panel__file" dir="auto">{s.filename}</span>
              <span className="cuga-sources-panel__meta">
                {scopeLabel(s.scope)}{pageLabel(s) ? ` · ${pageLabel(s)}` : ""}
              </span>
            </div>
            {s.section_path && <div className="cuga-sources-panel__section">{s.section_path}</div>}
            <blockquote
              className={`cuga-sources-panel__snippet${expanded[s.n] ? " is-expanded" : ""}`}
              onClick={() => setExpanded((e) => ({ ...e, [s.n]: true }))}
            >
              {highlightSegments(s.snippet, s.query).map((seg, i) =>
                seg.mark ? <mark key={i}>{seg.text}</mark> : <span key={i}>{seg.text}</span>,
              )}
            </blockquote>
            <div className="cuga-sources-panel__foot">
              {s.query && <span className="cuga-sources-panel__query">Found for: “{s.query}”</span>}
              {typeof s.score === "number" && (
                <span className="cuga-sources-panel__score" title="retrieval relevance">
                  <i style={{ width: `${Math.round(Math.min(1, Math.max(0, s.score)) * 100)}%` }} />
                </span>
              )}
              {onOpenDocument && (
                <button
                  className="cuga-sources-panel__open"
                  disabled={!!unavailable[s.n]}
                  title={unavailable[s.n] ? "Document no longer in the knowledge base" : undefined}
                  onClick={async () => {
                    const ok = await onOpenDocument(s);
                    if (!ok) setUnavailable((u) => ({ ...u, [s.n]: true }));
                  }}
                >
                  Open document ↗
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
```

```css
/* SourcesPanel.css — Carbon tokens with fallbacks (extension surface has no Carbon CSS) */
.cuga-sources-panel {
  position: fixed; top: 0; right: 0; height: 100%; width: 380px; max-width: 92vw;
  background: var(--cds-layer-01, #fff); border-left: 1px solid var(--cds-border-subtle-01, #e0e0e0);
  box-shadow: -4px 0 12px rgba(0, 0, 0, 0.08); z-index: 1100;
  display: flex; flex-direction: column;
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  animation: cuga-sources-in 160ms ease-out;
}
@keyframes cuga-sources-in { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
.cuga-sources-panel__header { display: flex; gap: 12px; align-items: flex-start; padding: 16px;
  border-bottom: 1px solid var(--cds-border-subtle-01, #e0e0e0); }
.cuga-sources-panel__header h3 { margin: 0; font-size: 16px; font-weight: 600; color: var(--cds-text-primary, #161616); }
.cuga-sources-panel__subtitle { font-size: 12px; color: var(--cds-text-secondary, #525252); }
.cuga-sources-panel__close { margin-left: auto; order: 3; border: none; background: none; cursor: pointer;
  font-size: 14px; color: var(--cds-text-secondary, #525252); padding: 4px 8px; border-radius: 4px; }
.cuga-sources-panel__close:hover { background: var(--cds-layer-hover-01, #e8e8e8); }
.cuga-sources-panel__list { overflow-y: auto; padding: 8px 16px 24px; flex: 1; }
.cuga-sources-panel__empty { color: var(--cds-text-secondary, #525252); font-size: 13px; padding: 16px 0; }
.cuga-sources-panel__item { padding: 12px; margin-top: 8px; border-radius: 6px;
  border: 1px solid var(--cds-border-subtle-01, #e0e0e0); }
.cuga-sources-panel__item.is-active { border-left: 3px solid var(--cds-interactive, #0f62fe);
  background: var(--cds-layer-selected-01, #e0e0e033); }
.cuga-sources-panel__item-head { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.cuga-sources-panel__badge { flex: none; min-width: 18px; height: 18px; border-radius: 9px; text-align: center;
  font: 600 11px/18px "IBM Plex Sans", sans-serif; color: #fff; background: var(--cds-link-primary, #0f62fe); padding: 0 5px; }
.cuga-sources-panel__file { font-weight: 600; font-size: 13px; color: var(--cds-text-primary, #161616);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; unicode-bidi: plaintext; }
.cuga-sources-panel__meta { margin-left: auto; flex: none; font-size: 11px; color: var(--cds-text-secondary, #525252); }
.cuga-sources-panel__section { font-size: 11px; color: var(--cds-text-secondary, #525252); margin: 4px 0 0 26px; }
.cuga-sources-panel__snippet { margin: 8px 0 0; padding: 10px 12px; border-radius: 4px; cursor: pointer;
  background: var(--cds-layer-02, #f4f4f4); font-size: 13px; line-height: 1.5; white-space: pre-wrap;
  color: var(--cds-text-primary, #161616); max-height: 260px; overflow: hidden; position: relative; }
.cuga-sources-panel__snippet.is-expanded { max-height: none; }
.cuga-sources-panel__snippet:not(.is-expanded)::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0;
  height: 32px; background: linear-gradient(transparent, var(--cds-layer-02, #f4f4f4)); }
.cuga-sources-panel__snippet mark { background: var(--cds-highlight, #d0e2ff); color: inherit; border-radius: 2px; }
.cuga-sources-panel__foot { display: flex; align-items: center; gap: 10px; margin-top: 8px; flex-wrap: wrap; }
.cuga-sources-panel__query { font-size: 11px; color: var(--cds-text-secondary, #525252); font-style: italic; }
.cuga-sources-panel__score { width: 56px; height: 4px; border-radius: 2px; background: var(--cds-border-subtle-01, #e0e0e0); overflow: hidden; }
.cuga-sources-panel__score i { display: block; height: 100%; background: var(--cds-support-info, #0043ce); }
.cuga-sources-panel__open { margin-left: auto; border: none; background: none; cursor: pointer;
  color: var(--cds-link-primary, #0f62fe); font-size: 12px; padding: 4px 6px; border-radius: 4px; }
.cuga-sources-panel__open:hover:not(:disabled) { background: var(--cds-layer-hover-01, #e8e8e8); }
.cuga-sources-panel__open:disabled { color: var(--cds-text-disabled, #c6c6c6); cursor: not-allowed; }
/* message footer */
.cuga-msg-sources { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 10px;
  padding-top: 8px; border-top: 1px solid var(--cds-border-subtle-01, #e0e0e0); }
.cuga-msg-sources__label { font-size: 11px; letter-spacing: 0.32px; color: var(--cds-text-secondary, #525252);
  text-transform: uppercase; margin-right: 2px; }
.cuga-msg-sources__card { display: inline-flex; align-items: center; gap: 6px; max-width: 240px;
  border: 1px solid var(--cds-border-subtle-01, #e0e0e0); background: var(--cds-layer-01, #fff);
  border-radius: 14px; padding: 3px 10px 3px 4px; cursor: pointer; font-size: 12px;
  color: var(--cds-text-primary, #161616); }
.cuga-msg-sources__card:hover { background: var(--cds-layer-hover-01, #e8e8e8); }
.cuga-msg-sources__badge { min-width: 16px; height: 16px; border-radius: 8px; text-align: center;
  font: 600 10px/16px "IBM Plex Sans", sans-serif; color: #fff; background: var(--cds-link-primary, #0f62fe); }
.cuga-msg-sources__file { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; unicode-bidi: plaintext; }
.cuga-msg-sources__page { color: var(--cds-text-secondary, #525252); font-size: 11px; }
.cuga-msg-sources__dot { width: 6px; height: 6px; border-radius: 3px; }
.cuga-msg-sources__dot--agent { background: var(--cds-link-primary, #0f62fe); }
.cuga-msg-sources__dot--session { background: var(--cds-support-success, #24a148); }
.cuga-msg-sources__more { padding: 3px 10px; }
```

- [ ] **Step 6: `index.ts` + package export**

```ts
// index.ts
export { registerCiteElement, CITE_CLICK_EVENT } from "./citeElement";
export { injectCitations } from "./injectCitations";
export { MessageSources } from "./MessageSources";
export { default as SourcesPanel, highlightSegments } from "./SourcesPanel";
export * from "./types";
```

`agentic_chat/package.json` exports map — add:

```json
    "./Citations": {
      "import": "./src/citations/index.ts",
      "default": "./src/citations/index.ts"
    }
```

- [ ] **Step 7: Run tests + lint + commit**

Run: `npm run test -w agentic_chat -- --run src/citations` and `npm run lint -w agentic_chat`
Expected: PASS

```bash
git add src/frontend_workspaces/agentic_chat/src/citations src/frontend_workspaces/agentic_chat/package.json
git commit -m "feat(ui): shared citation core — cuga-cite element, sources footer and panel"
```

### Task 11: Carbon chat integration (shipped web UI)

**Files:**
- Modify: `frontend/src/carbon-chat/carbonChatHelpers.ts` (`parseAnswerEventData` returns sources)
- Modify: `frontend/src/carbon-chat/customSendMessage.ts` (Answer/FinalAnswer case)
- Modify: `frontend/src/carbon-chat/customLoadHistory.ts` (Answer replay case)
- Modify: `frontend/src/carbon-chat/CarbonChat.tsx` (element registration, `renderUserDefinedResponse`, click listener, panel host)

- [ ] **Step 1: `carbonChatHelpers.ts`** — extend `parseAnswerEventData` to also surface `sources` from the parsed payload (mirror how `variables` is extracted): return `{ text, variables, sources: parsed.sources ?? [] }`; update its return type.

- [ ] **Step 2: `customSendMessage.ts`** — in the `Answer`/`FinalAnswer` case (~lines 449-511):

```ts
import { injectCitations, type MessageSource } from "agentic_chat/Citations";
// ...
const { text: answerText, sources } = parseAnswerEventData(evt); // now returns sources
const displayText = injectCitations(answerText, sources as MessageSource[]);
const complete_item = {
  response_type: MessageResponseTypes.TEXT,
  text: displayText,
  streaming_metadata: { id: "text-stream" },
};
// after the TEXT complete_item, append a sources footer item when cited:
if (sources?.length) {
  instance.messaging.addMessageChunk({
    partial_response: {
      /* follow the existing complete_item/final_response chunk pattern */
    },
  });
  // concretely: include a second generic item in the final_response message:
  // { response_type: MessageResponseTypes.USER_DEFINED,
  //   user_defined: { type: "cuga_sources", sources } }
}
```

> Implementor: the exact chunk plumbing must follow the file's existing `complete_item` → `final_response` sequence (customSendMessage.ts:483-511) — put the `user_defined` item into the same `final_response.message.output.generic` array as the TEXT item so both live in one assistant message bubble. `MessageResponseTypes.USER_DEFINED = "user_defined"` is exported by `@carbon/ai-chat`.

- [ ] **Step 3: `customLoadHistory.ts`** — the `Answer` replay case (~lines 83-183) does the identical transform: parse payload → `injectCitations` → TEXT item text; push `{ response_type: "user_defined", user_defined: { type: "cuga_sources", sources } }` as an additional generic item on the same `HistoryItem`. Citations now survive full page reloads.

- [ ] **Step 4: `CarbonChat.tsx`** — host wiring:

```tsx
import { registerCiteElement, CITE_CLICK_EVENT, SourcesPanel, MessageSources,
         type MessageSource } from "agentic_chat/Citations";
import * as api from "../api";

registerCiteElement(); // module scope — once per page

// component state:
const [sourcesPanel, setSourcesPanel] = useState<{ sources: MessageSource[]; activeN: number | null } | null>(null);
const lastSourcesByMessage = useRef<Map<string, MessageSource[]>>(new Map());

// listener (composed event escapes ai-chat shadow DOM):
useEffect(() => {
  const onCite = (e: Event) => {
    const n = (e as CustomEvent).detail?.n ?? null;
    const all = Array.from(lastSourcesByMessage.current.values());
    const sources = all.length ? all[all.length - 1] : [];
    setSourcesPanel({ sources, activeN: n });
  };
  document.addEventListener(CITE_CLICK_EVENT, onCite);
  return () => document.removeEventListener(CITE_CLICK_EVENT, onCite);
}, []);

// renderUserDefinedResponse prop on <ChatCustomElement>:
renderUserDefinedResponse={(state) => {
  const item = state.messageItem as any;
  if (item?.user_defined?.type === "cuga_sources") {
    const sources = item.user_defined.sources as MessageSource[];
    // remember per message so chip clicks resolve the right source set
    lastSourcesByMessage.current.set((state.fullMessage as any)?.id ?? String(Date.now()), sources);
    return <MessageSources sources={sources} onOpen={(n) => setSourcesPanel({ sources, activeN: n })} />;
  }
  return undefined;
}}

// panel (sibling of ChatCustomElement):
{sourcesPanel && (
  <SourcesPanel
    sources={sourcesPanel.sources}
    activeN={sourcesPanel.activeN}
    onClose={() => setSourcesPanel(null)}
    onOpenDocument={async (s) => {
      try {
        const blob = await api.getKnowledgeDocumentFile(
          s.scope as "agent" | "session", s.filename, threadId ?? undefined);
        window.open(URL.createObjectURL(blob), "_blank");
        return true;
      } catch { return false; }
    }}
  />
)}
```

> Refinement for correct chip→sources mapping across multiple messages: since a chip click only carries `n`, resolve against the sources of the message the chip lives in. The `user_defined` render path gives you `state.fullMessage` — store `message.id → sources`. For the chip, walking `e.composedPath()` to find the nearest ai-chat message container id is brittle; instead have `injectCitations` optionally stamp a `msg` attribute: extend the function signature `injectCitations(text, sources, messageKey?)` adding ` msg="<key>"` to each element, pass the backend message id (or a per-turn uuid you already generate) from `customSendMessage`/`customLoadHistory`, and read `(e.target as HTMLElement).getAttribute("msg")` in the listener. Implement this variant — it is deterministic in both surfaces.

- [ ] **Step 5: Verify visually**

Run: `bash scripts/build_frontend.sh && uv run cuga start demo`, upload a doc (Manage → Knowledge), ask a question answerable from it.
Expected: answer shows `[1]` superscript chips; hover shows the card; click opens the panel scrolled to the source with highlighted terms; sources footer renders; reload the page and re-open the thread → chips and footer persist.

- [ ] **Step 6: Commit**

```bash
git add src/frontend_workspaces/frontend/src/carbon-chat
git commit -m "feat(ui): citation chips and sources panel in carbon chat with history replay"
```

### Task 12: CardManager integration (extension side-panel surface)

**Files:**
- Modify: `agentic_chat/src/CardManager.tsx` (Answer render, ~lines 1236-1267; payload parse ~826-929)
- Modify: `agentic_chat/src/CardManager.css` (import nothing — footer styles come from `SourcesPanel.css` which `SourcesPanel.tsx` imports; add nothing unless lint complains)
- Modify: `agentic_chat/src/App.tsx` (panel host)

- [ ] **Step 1: Parse sources in the Answer step** — where the event envelope `{ data, variables?, active_policies? }` is parsed (CardManager.tsx:826-929), also pull `sources` and thread it to the Answer renderer.

- [ ] **Step 2: Chips + footer in the Answer renderer** (CardManager.tsx:1236-1267):

```tsx
import { registerCiteElement, injectCitations, MessageSources,
         type MessageSource } from "./citations";
registerCiteElement(); // module scope

// in the Answer branch, before marked():
const withCites = injectCitations(answerText, (sources ?? []) as MessageSource[], stepMessageKey);
const renderedContent = marked(withCites) as string;
// below the dangerouslySetInnerHTML answer div:
{sources?.length ? (
  <MessageSources
    sources={sources}
    onOpen={(n) =>
      window.dispatchEvent(new CustomEvent("cugaOpenSources", { detail: { sources, n } }))}
  />
) : null}
```

(`marked` passes unknown inline HTML tags through untouched — `<cuga-cite …></cuga-cite>` survives to the DOM and upgrades. The chip's own `cuga-cite-click` event bubbles in light DOM here; App-level listener below handles both event names.)

- [ ] **Step 3: Panel host in `App.tsx`** — alongside the existing `KnowledgeSidePanel` mount (App.tsx:237-249):

```tsx
import { SourcesPanel, CITE_CLICK_EVENT, type MessageSource } from "./citations";

const [sourcesView, setSourcesView] = useState<{ sources: MessageSource[]; activeN: number | null } | null>(null);
const messageSourcesRef = useRef<Map<string, MessageSource[]>>(new Map()); // filled via cugaOpenSources detail

useEffect(() => {
  const open = (e: Event) => {
    const { sources, n } = (e as CustomEvent).detail ?? {};
    if (sources) setSourcesView({ sources, activeN: n ?? null });
  };
  const chip = (e: Event) => {
    const el = e.target as HTMLElement;
    const key = el?.getAttribute?.("msg") || "";
    const sources = messageSourcesRef.current.get(key) ?? [];
    setSourcesView({ sources, activeN: (e as CustomEvent).detail?.n ?? null });
  };
  window.addEventListener("cugaOpenSources", open);
  document.addEventListener(CITE_CLICK_EVENT, chip);
  return () => {
    window.removeEventListener("cugaOpenSources", open);
    document.removeEventListener(CITE_CLICK_EVENT, chip);
  };
}, []);

{sourcesView && (
  <SourcesPanel sources={sourcesView.sources} activeN={sourcesView.activeN}
                onClose={() => setSourcesView(null)} />
)}
```

CardManager must publish each answer's sources into that map — dispatch `window.dispatchEvent(new CustomEvent("cugaSourcesUpdate", { detail: { key: stepMessageKey, sources } }))` when rendering an Answer with sources, and add the corresponding listener in App.tsx to fill `messageSourcesRef`. (Same window-CustomEvent bus the app already uses for `variablesUpdate`, CardManager.tsx:388-397.)

- [ ] **Step 4: Verify + commit**

Run: `npm run lint -w agentic_chat && npm run test -w agentic_chat -- --run`. Manual check via extension dev flow if available; otherwise rely on the shared-core tests (rendering primitives are identical to Task 11's verified path).

```bash
git add src/frontend_workspaces/agentic_chat/src/CardManager.tsx src/frontend_workspaces/agentic_chat/src/App.tsx
git commit -m "feat(ui): citation chips and sources panel in extension CardManager surface"
```

Known accepted limitation (document in PR): the extension surface loses card structure on history reload today (plain-text messages) — citations there are live-turn only, same as every other step card. The shipped web UI persists them fully.

### Task 13: Settings UI (agent toggle + session toggle)

**Files:**
- Modify: `agentic_chat/src/KnowledgeConfig.tsx` (type :279-333; Settings tab tiles :2428-2546)
- Modify: `frontend/src/ManagePage.tsx` (`AgentConfig["knowledge"]` :123-165; `DEFAULT_KNOWLEDGE_CONFIG` :172-209)
- Modify: `frontend/src/api.ts` (session settings calls, near :472-484)
- Modify: `agentic_chat/src/KnowledgeSidePanel.tsx` (session toggle)
- Modify: `frontend/src/ChatLanding.tsx` + `agentic_chat/src/App.tsx` (plumb `citations_enabled` from `getAgentContext`)

- [ ] **Step 1: Agent-level toggle**

`KnowledgeConfigValues` (KnowledgeConfig.tsx:279-333): add `citations_enabled?: boolean;`. `ManagePage.tsx` `AgentConfig` knowledge shape + `DEFAULT_KNOWLEDGE_CONFIG`: add `citations_enabled: true` (both files carry the "MUST stay in sync" comment — honor it).

Settings tab — new `Tile` after the session-level tile (pattern-match the agent-level tile at KnowledgeConfig.tsx:2473-2482):

```tsx
<Tile style={{ marginTop: "0.5rem", ...(citationsEnabled ? { borderLeft: "3px solid var(--cds-support-success)" } : {}) }}>
  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem" }}>
    <div>
      <div style={{ fontWeight: 600, fontSize: "0.875rem" }}>Citations</div>
      <div style={{ fontSize: "0.75rem", color: "var(--cds-text-secondary)" }}>
        Number knowledge sources in answers ([1]) with clickable snippets
      </div>
    </div>
    <Toggle
      id="knowledge-citations-enabled"
      hideLabel
      labelText="Citations"
      labelA="Off"
      labelB="On"
      size="sm"
      disabled={!knowledgeEnabled}
      toggled={knowledgeConfig?.citations_enabled ?? true}
      onToggle={(checked: boolean) =>
        onKnowledgeConfigChange({ ...knowledgeConfig, citations_enabled: checked })}
    />
  </div>
</Tile>
```

with the derived gate near the siblings (:511-513): `const citationsEnabled = knowledgeEnabled && (knowledgeConfig?.citations_enabled ?? true);`. Persistence is free — the field flows through the existing `patchManageConfigDraftKnowledge` autosave (it is now a `KnowledgeConfig` dataclass field, Task 3).

- [ ] **Step 2: api.ts session-settings calls** (next to `getKnowledgeSettings`, :472-484):

```ts
export async function getSessionKnowledgeSettings(threadId: string): Promise<{ overrides: Record<string, unknown> }> {
  const res = await knowledgeApiFetch(`/api/knowledge/session/settings`, { method: "GET" }, threadId);
  if (!res.ok) throw new Error(`session settings: ${res.status}`);
  return res.json();
}

export async function patchSessionKnowledgeSettings(
  threadId: string, patch: { citations_enabled?: boolean },
): Promise<{ overrides: Record<string, unknown> }> {
  const res = await knowledgeApiFetch(
    `/api/knowledge/session/settings`,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) },
    threadId,
  );
  if (!res.ok) throw new Error(`session settings patch: ${res.status}`);
  return res.json();
}
```

(match `knowledgeApiFetch`'s actual signature for passing `X-Thread-ID`, api.ts:417-430).

- [ ] **Step 3: Session toggle in `KnowledgeSidePanel.tsx`** — in the "This Conversation" section (:251-313), add props `citationsEnabled?: boolean` (agent-level default, from agent context) and render when knowledge + citations master are on:

```tsx
const [sessionCitations, setSessionCitations] = useState<boolean | null>(null); // null = follow agent default
useEffect(() => {
  if (!threadId) return;
  api.getSessionKnowledgeSettings(threadId)
    .then((r) => setSessionCitations((r.overrides?.citations_enabled as boolean | undefined) ?? null))
    .catch(() => setSessionCitations(null));
}, [threadId]);

{knowledgeEnabled && citationsEnabled !== false && (
  <label className="ksp-session-citations">
    <input
      type="checkbox"
      checked={sessionCitations ?? true}
      onChange={async (e) => {
        const next = e.target.checked;
        setSessionCitations(next);
        try { await api.patchSessionKnowledgeSettings(threadId, { citations_enabled: next }); }
        catch { setSessionCitations(!next); }
      }}
    />
    <span>Citations for this chat</span>
    <small>Show numbered sources under answers</small>
  </label>
)}
```

(Styled via `KnowledgeSidePanel.css` to match its existing rows — this panel is deliberately plain-CSS, not Carbon.) Pass `citationsEnabled` from the host: `ChatLanding.tsx` and `agentic_chat/src/App.tsx` already consume `getAgentContext()` knowledge flags (:563-565 / :99-103) — read the new `citations_enabled` field there and forward it.

- [ ] **Step 4: Verify + commit**

Run: `npm run lint -w agentic_chat && cd src/frontend_workspaces/frontend && npx tsc --noEmit` (match the repo's typecheck script if one exists).
Manual: Manage → Knowledge → Settings shows the toggle; flipping autosaves (draft PATCH 200 with no reindex tile); chat side panel shows the session toggle; flipping it makes the next answer citation-free while old answers keep chips.

```bash
git add src/frontend_workspaces/agentic_chat/src/KnowledgeConfig.tsx \
        src/frontend_workspaces/agentic_chat/src/KnowledgeSidePanel.tsx \
        src/frontend_workspaces/agentic_chat/src/App.tsx \
        src/frontend_workspaces/frontend/src/ManagePage.tsx \
        src/frontend_workspaces/frontend/src/api.ts \
        src/frontend_workspaces/frontend/src/ChatLanding.tsx
git commit -m "feat(ui): citations toggles — agent-level in Manage, per-session in chat panel"
```

### Task 14: Docs + end-to-end verification

**Files:**
- Modify: `src/cuga/backend/knowledge/KNOWLEDGE_PIPELINE.md` (new "## Citations & Sources" section)

- [ ] **Step 1: Document** — add a section covering: lifecycle diagram (copy §1.1), the two stamping seams, marker contract, resolver location, `Answer` payload shape, settings matrix (TOML key / `DYNACONF_KNOWLEDGE__CITATIONS_ENABLED` / UI toggles / `CugaAgent(enable_citations=...)` / session PATCH route), and the edge-case table rows 8-10 (restart/reindex behavior).

- [ ] **Step 2: Full-suite verification (run each, record output)**

```bash
uv run ruff check src tests
uv run pytest tests/unit -q
npm run test -w agentic_chat -- --run
bash scripts/build_frontend.sh
```

Manual E2E script (demo mode, `uv run cuga start demo`):
1. Upload two PDFs to agent knowledge; ask a question answered by doc A → expect `[1]` chip, footer, panel with highlighted snippet, "Open document" works.
2. Ask a multi-hop question needing both docs → expect `[1][2]` from different files; hop-2 re-retrieval of doc A's chunk reuses its id (verify via `/api/knowledge/search` response `cite_id`s in devtools).
3. Follow-up question answerable from turn 1's chunks without new retrieval → expect citations still resolve (ledger hit).
4. Reload the browser, reopen the thread → chips + sources footer replay from history.
5. Toggle session citations OFF in the side panel → next answer plain; previous answers untouched. Toggle back ON.
6. Manage → toggle agent-level OFF, publish → no reindex prompt appears; chat answers have no citations; `GET /api/knowledge/settings` shows `citations_enabled: false`.
7. SDK smoke:
```python
import asyncio
from cuga import CugaAgent

async def main():
    agent = CugaAgent(enable_knowledge=True)
    await agent.initialize()
    await agent.knowledge.ingest("demo_knowledge_docs/<some.pdf>", scope="agent")
    result = await agent.invoke("What does the document say about X?")
    print(result.answer)      # contains [1]
    print(result.sources)     # [{n, cite_id, filename, page, snippet, ...}]

asyncio.run(main())
```

- [ ] **Step 3: Commit**

```bash
git add src/cuga/backend/knowledge/KNOWLEDGE_PIPELINE.md
git commit -m "docs(knowledge): citations & sources pipeline reference"
```

---

# Part 4 — Explicitly deferred (do NOT build now)

| Deferred | Why |
|---|---|
| Char-offset / bbox-precise snippet highlighting | Requires storing Docling `prov` at ingest + full reindex of existing corpora. Query-term highlighting delivers the "why was this chosen" UX without it. Revisit only with customer pull. |
| Streaming citations (chips appearing token-by-token) | The final answer is not token-streamed today; resolution-at-end matches the actual transport. |
| Durable DB table for the ledger | Snapshot-in-event + restart rehydration covers the real failure mode; a table adds migration surface for no user-visible gain (MemorySaver state is equally process-local). |
| Citations in intermediate reasoning steps / CodeAgent output | Reasoning steps already show raw search results; citing is a final-answer concern. |
| Per-scope citation toggles (`citations_agent_enabled` / `citations_session_enabled`) | One agent-level flag + per-session override covers every voiced need; scope-split doubles the matrix for hypothetical value. |
| Shared SSE event-name enum across the 3 duplicated frontend switches | Real debt, orthogonal to this feature — don't scope-creep. |

# Self-review checklist (run after implementation, before PR)

- [ ] Grep `cite_id` end-to-end: stamped in both seams, present in envelope docstring comment, consumed by resolver, surfaced in snapshots, rendered by both UIs.
- [ ] Flip every toggle combination (agent on/off × session unset/on/off) and confirm effective behavior matches §1.2 enablement row and edge rows 14-16.
- [ ] Confirm no `[knowledge]` profile TOML gained a citations key and `vector_config_hash()` is unchanged (test guards it).
- [ ] Confirm `Answer` events for citation-free answers carry no `sources` key (wire terseness).
- [ ] Confirm raw `[sN]` markers never reach any UI surface (resolver runs on every FinalAnswerNode branch — test each sender path manually via chat, task, and SDK).
- [ ] `uv run ruff check` clean; both frontends typecheck; all new unit tests green.
