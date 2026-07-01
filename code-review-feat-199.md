# Code Review — `feat/199-spawn-sub-agents-at-runtime`

Branch goal: allow the parent CugaLite agent to spawn ad-hoc SubCuga subagents at runtime, stream their progress back to the UI, and ship several companion improvements (image analysis, PDF/PPTX conversion tools, workspace file upload, skills refactor).

---

## Table of Contents

1. [Topic A — `agent_spawn` module (core spawning engine)](#a)
2. [Topic B — AgentLoop unified SSE queue](#b)
3. [Topic C — PrepareNode wiring: spawn + system tools injection](#c)
4. [Topic D — Skills system refactor](#d)
5. [Topic E — New system tools (analyze_image, pdf_to_images, pptx_to_images)](#e)
6. [Topic F — Workspace upload (backend + frontend)](#f)
7. [Topic G — GraphAdapter + CugaLiteNode changes](#g)
8. [Topic H — System prompt (`mcp_prompt.jinja2`)](#h)
9. [Topic I — Test coverage](#i)

---

## <a name="a"></a>A. `agent_spawn` module — core spawning engine

**Files:** `src/cuga/backend/agent_spawn/__init__.py`, `runtime.py`, `tools.py`, `prompt_utils.py`

### A1. Package surface (`__init__.py`)

```
src/cuga/backend/agent_spawn/__init__.py
```

Exports four things: `SpawnAgentRuntime`, `clear_runtime_caches`, `create_spawn_tools`, `format_available_agents_block`. The docstring says enable via `settings.toml: [agent_spawn] enabled = true`. Simple, intentional.

---

### A2. `runtime.py` — `SpawnAgentRuntime`

**Module-level globals:**

```python
# runtime.py:21
_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar("_spawn_depth", default=0)
```
A `ContextVar` tracks recursion depth. Because subagents run in the same event loop as the parent (`asyncio.create_task`), a plain `int` would be shared across concurrent fibers. `ContextVar` is isolated per async task — the right primitive here.

```python
# runtime.py:24-27
_SPAWN_INTERNAL_TOOL_NAMES: frozenset[str] = frozenset({
    "spawn_agent", "get_agent_result", "load_skill", "find_tools", "create_update_todos",
})
```
Tools that must never propagate to a subagent. Prevents recursive spawning (`spawn_agent`, `get_agent_result`), avoids the skill loading machinery re-triggering inside a sub-context (`load_skill`), and keeps the subagent lean by dropping orchestration-only meta-tools.

```python
# runtime.py:29-42
_event_callback: Optional[Callable[[str, dict], None]] = None

def set_event_callback(cb): ...
def _emit(event_name, data): ...
```
A single module-level callback slot. `AgentLoop.run_stream` sets it before streaming and clears it in `finally`. This is a deliberate global — the process only runs one top-level stream at a time, and the pattern avoids threading the callback through every layer. The `try/except` in `_emit` ensures that a broken UI callback never kills a running agent.

```python
# runtime.py:44-47
_spawn_futures: Dict[str, Any] = {}
_agent_cache: Dict[frozenset, Any] = {}
```
Two process-level caches. `_spawn_futures` is the shared store for async fire-and-forget results (keyed by `future_id`). `_agent_cache` caches compiled `CugaAgent` instances keyed by the frozenset of tool names — avoids rebuilding the LangGraph on every spawn.

**`SpawnAgentRuntime.__init__`:**

```python
# runtime.py:52-60
def __init__(self, parent_structured_tools, parent_config=None, spawn_futures_ref=None):
    self._parent_structured_tools = parent_structured_tools
    self._parent_config = parent_config or {}
    self._spawn_futures = spawn_futures_ref if spawn_futures_ref is not None else _spawn_futures
```
`spawn_futures_ref` lets tests inject an isolated dict rather than touching the global — this is the testability hook.

**`SpawnAgentRuntime.from_parent` (classmethod):**

```python
# runtime.py:63-76
@classmethod
def from_parent(cls, parent_config=None, spawn_futures_ref=None, parent_structured_tools=None):
    filtered = [
        t for t in (parent_structured_tools or [])
        if t.name not in _SPAWN_INTERNAL_TOOL_NAMES
    ]
    return cls(filtered, parent_config, spawn_futures_ref)
```
"Fresh eyes" pattern: the subagent inherits all the parent's execution tools (filesystem, app tools, MCP tools) but not the orchestration tools. Filtering happens here at construction time, not deep inside execute — clean separation.

**`_build_agent`:**

```python
# runtime.py:84-97
def _build_agent(self, tools):
    no_cache = os.environ.get("CUGA_AGENT_SPAWN_NO_CACHE")
    if not no_cache:
        cache_key = frozenset(t.name for t in tools)
        cached = _agent_cache.get(cache_key)
        if cached is not None:
            return cached
    from cuga.sdk import CugaAgent
    agent = CugaAgent(tools=tools)
    if not no_cache:
        _agent_cache[cache_key] = agent
    return agent
```
Lazy import of `CugaAgent` (avoids circular imports at module load). `CUGA_AGENT_SPAWN_NO_CACHE` is an escape hatch for tests that need fresh agents. Cache key is a frozenset of tool **names** — if two spawns happen with the same set of tool names (regardless of order), they reuse the same compiled graph. This is safe because the agent graph is stateless; per-request state comes from the `thread_id`.

**`_run_stream`:**

```python
# runtime.py:104-121
async def _run_stream(self, agent, task, thread_id, cfg, spawn_id=""):
    final_answer = ""
    forward = getattr(settings.agent_spawn, "forward_sync_subagent_events", True)
    async for chunk in agent.stream(task, thread_id=thread_id, config=cfg):
        state_dict = chunk[1] if isinstance(chunk, tuple) else chunk
        if not isinstance(state_dict, dict):
            continue
        node_data = next(iter(state_dict.values()), None)
        if not isinstance(node_data, dict):
            continue
        if forward and "script" in node_data:
            _emit("CodeAgent", {**node_data, "subagent": "SubCuga", "spawn_id": spawn_id})
        candidate = node_data.get("final_answer")
        if candidate:
            final_answer = candidate
    return final_answer
```
Streams the subagent's LangGraph output, scans each chunk for `"script"` (CugaLite code-execution step) and `"final_answer"`. Only `final_answer` is returned to the parent. Code execution steps are forwarded as `CodeAgent` events so the UI can render them in real time. `forward_sync_subagent_events` defaults true — a config knob to silence sub-agent chatter without code changes.

**`execute` (sync mode):**

```python
# runtime.py:123-153
async def execute(self, task, spawn_id=""):
    depth = _spawn_depth.get()
    max_depth = getattr(settings.agent_spawn, "max_spawn_depth", 2)
    if depth >= max_depth:
        return f"[SpawnError] max_spawn_depth={max_depth} exceeded"
    ...
    token = _spawn_depth.set(depth + 1)
    try:
        agent = self._build_agent(tools)
        answer = await self._run_stream(agent, task, thread_id, invoke_cfg, spawn_id=spawn_id)
    finally:
        _spawn_depth.reset(token)
    ...
    return answer
```
Depth guard at the top: returns a sentinel string rather than raising — the parent agent gets a readable error and can react. `_spawn_depth.set()` / `.reset(token)` is the correct ContextVar pattern (token-based reset, not set-to-previous). The `finally` block ensures depth is decremented even if the subagent raises.

**`execute_async` (fire-and-forget mode):**

```python
# runtime.py:158-167
async def execute_async(self, task):
    future_id = f"future_{uuid4().hex[:8]}"
    self._spawn_futures[future_id] = {"status": "running", "result": None, "error": None}
    asyncio.create_task(self._execute_and_store(future_id, task))
    return future_id
```
The parent gets a `future_id` immediately and can spawn multiple subagents before collecting results. `asyncio.create_task` schedules work on the running event loop. `_execute_and_store` catches all exceptions so the future dict always ends up in a terminal state (`done` or `error`), never hung on `running`.

---

### A3. `tools.py` — LangChain tool definitions

**`SpawnAgentInput` / `GetAgentResultInput`:**

```python
# tools.py:13-29
class SpawnAgentInput(BaseModel):
    task: str = Field(..., max_length=4000, ...)
    mode: str = Field(default="sync", ...)

class GetAgentResultInput(BaseModel):
    future_id: str = Field(...)
    timeout: float = Field(default=60.0, ...)
```
`max_length=4000` on `task` — prevents the parent from accidentally dumping the entire conversation into the subagent's context. `timeout=60.0` in `get_agent_result` is a sane default; the agent can override it.

**`create_spawn_tools` factory:**

```python
# tools.py:32-82
def create_spawn_tools(spawn_futures, parent_config=None, parent_structured_tools=None):
    async def spawn_agent(task="", mode="sync"):
        rt = SpawnAgentRuntime.from_parent(
            parent_config, spawn_futures_ref=spawn_futures,
            parent_structured_tools=parent_structured_tools,
        )
        if mode == "async":
            return await rt.execute_async(task)
        return await rt.execute(task)

    async def get_agent_result(future_id, timeout=60.0):
        ...
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            entry = spawn_futures[future_id]
            if entry["status"] == "done":
                return entry.get("result") or ""
            if entry["status"] == "error":
                return f"[SpawnError] {entry.get('error', 'unknown error')}"
            await asyncio.sleep(0.5)
        return f"[SpawnTimeout] ..."
```
Factory pattern: closures capture `spawn_futures`, `parent_config`, and `parent_structured_tools` at construction time (inside `prepare_node`). A new `SpawnAgentRuntime` is built per call — intentional, since each call needs a fresh thread_id. `get_agent_result` polls at 0.5 s intervals — simple polling loop, acceptable because this is an async context and doesn't block the event loop.

**`StructuredTool.from_function` wiring:**

```python
# tools.py:65-82
spawn_tool = StructuredTool.from_function(
    coroutine=spawn_agent,
    name="spawn_agent",
    description=...,
    args_schema=SpawnAgentInput,
)
result_tool = StructuredTool.from_function(
    coroutine=get_agent_result,
    name="get_agent_result",
    description=...,
    args_schema=GetAgentResultInput,
)
return [spawn_tool, result_tool]
```
`coroutine=` (not `func=`) tells LangChain these are async — required so the agent's `await` calls route correctly through the executor.

---

### A4. `prompt_utils.py`

```python
# prompt_utils.py:6-14
def format_available_agents_block() -> str:
    return (
        "**Ad-hoc subagent spawning is available.** When a skill or task instructs you to use a subagent, "
        "call `await spawn_agent(task=\"<full task description and context>\")`. "
        ...
        "For multiple independent subtasks, always prefer parallel spawning: "
        "call `await spawn_agent(..., mode='async')` for each subtask first ..."
    )
```
Short, injected into the system prompt's `## Sub-Agents` section. The key instruction: "Pass everything the subagent needs in the task string" — this is critical because subagents start with zero conversation history.

---

## <a name="b"></a>B. AgentLoop — unified SSE queue

**File:** `src/cuga/backend/cuga_graph/utils/agent_loop.py`

### B1. `_spawn_to_stream_event`

```python
# agent_loop.py:263-302
def _spawn_to_stream_event(name: str, data: dict) -> Optional["StreamEvent"]:
    if name == "SpawnAgent":
        payload = json.dumps({"type": "start", "agent_name": ..., "task": ..., "spawn_id": ...})
        return StreamEvent(name="SubAgent", data=payload)
    if name == "SpawnAgentResult":
        payload = json.dumps({"type": "result", ...})
        return StreamEvent(name="SubAgent", data=payload)
    if name == "CodeAgent":
        # CugaLite state uses 'script'; frontend expects 'code'
        safe_data = {}
        script = data.get("script")
        if script and isinstance(script, str):
            safe_data["code"] = script
        ...
        return StreamEvent(name="SubAgent", data=payload)
    return None
```
Three runtime events map to one SSE event type (`SubAgent`) with a `type` discriminator. The key rename: CugaLite state uses `"script"` internally but the frontend Carbon chat component expects `"code"` — this translation lives here. Unknown events return `None` (silently dropped), which is safe.

### B2. `run_stream` — producer-consumer queue

The old implementation was a simple `async for event in self.get_stream(...)`. The new one introduces a unified queue:

```python
# agent_loop.py:719-807
async def run_stream(self, state=None, resume=None):
    from cuga.backend.agent_spawn import runtime as _spawn_runtime

    unified_queue: asyncio.Queue = asyncio.Queue()
    _SPAWN_TAG, _GRAPH_TAG, _DONE_TAG = "spawn", "graph", "done"

    def _on_spawn_event(name, data):
        unified_queue.put_nowait((_SPAWN_TAG, name, data))

    if agent_spawn_enabled:
        _spawn_runtime.set_event_callback(_on_spawn_event)

    async def _feed_graph():
        exc_to_raise = None
        try:
            async for graph_event in self.get_stream(state, resume):
                await unified_queue.put((_GRAPH_TAG, graph_event))
        except Exception as exc:
            exc_to_raise = exc
        finally:
            await unified_queue.put((_DONE_TAG, exc_to_raise))

    graph_task = asyncio.create_task(_feed_graph())
    ...
    try:
        while True:
            item = await unified_queue.get()
            tag = item[0]
            if tag == _DONE_TAG: ...break or raise
            if tag == _SPAWN_TAG: ...yield spawn SSE
            # _GRAPH_TAG: ...yield graph SSE (existing logic)
    finally:
        _spawn_runtime.set_event_callback(None)
        if not graph_task.done():
            graph_task.cancel()
```

**Why a queue?** The graph stream (`get_stream`) is an `async for` — it naturally blocks waiting for the next graph event. But subagent events can arrive at any time during graph execution via `_emit()` (a synchronous callback called inside the subagent's async task). To interleave both without blocking either, the graph runs as a background `asyncio.Task` that pushes into the queue, and spawn events are pushed by the callback. The `run_stream` loop drains the unified queue.

**`_DONE_TAG` sentinel:** `_feed_graph` always puts `(_DONE_TAG, exc_or_None)` in its `finally` block — the consumer loop terminates cleanly regardless of whether the graph raised.

**`finally` cleanup:** The event callback is always cleared (prevents stale references), and the graph task is cancelled if still running (prevents coroutine leaks on early client disconnect).

**Drain after done:** After `_DONE_TAG`, any remaining spawn events in the queue are drained before yielding the final output. This ensures the last subagent's events reach the UI even if the parent graph finishes before the subagent's callback fires.

---

## <a name="c"></a>C. PrepareNode — tool injection and wiring

**File:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/adapter/prepare_node.py`

### C1. `_is_subagent` guard

```python
# prepare_node.py:~337-344
from cuga.backend.agent_spawn.runtime import _spawn_depth as _agent_spawn_depth

_is_subagent = _agent_spawn_depth.get() > 0
_inherit_parent_tools = getattr(settings.agent_spawn, "inherit_parent_tools", False)
if _is_subagent and not _inherit_parent_tools:
    skills_cfg_on = False
```
Because subagents run in the same Python process, `prepare_node` is called again when a subagent is initialized. The `_spawn_depth` ContextVar is already >0 at that point (set by `execute`). This guard disables skills loading for subagents unless `inherit_parent_tools` is explicitly enabled. Without this, subagents would re-discover and re-register skills, causing double-registration and potentially recursive skill-triggered spawning.

### C2. Skills callable tools registered into `_tools_context`

```python
# prepare_node.py:~348-358
_skill_callable_tools = [t for t in skill_tools if t.name != "load_skill"]
...
for _sk_tool in skill_tools:
    _sk_fn = _sk_tool.coroutine if (hasattr(_sk_tool, "coroutine") and _sk_tool.coroutine) else _sk_tool.func
    if _sk_fn:
        adapter._tools_context[_sk_tool.name] = make_tool_awaitable(_sk_fn)
```
Previously, `load_skill` was the only skill tool. Now skills can declare callable Python tools via `tool_definitions` frontmatter (see Section D). Those tools need to be in `_tools_context` so the sandbox executor can call `await <tool_name>(...)`. The `_skill_callable_tools` list (excluding `load_skill`) is also passed as `parent_structured_tools` to `create_spawn_tools` so subagents can use them.

### C3. Spawn tools injection (after all tools are registered)

```python
# prepare_node.py:~408-430
if not _is_subagent:
    _parent_structured_tools_for_subagent = (
        list(tools_for_execution) + _skill_callable_tools + list(_runtime_bundle.prompt_tools)
    )
    agent_spawn_tools = create_spawn_tools(
        spawn_futures=adapter._spawn_futures,
        parent_config=config,
        parent_structured_tools=_parent_structured_tools_for_subagent,
    )
    tools_for_prompt.extend(agent_spawn_tools)
    for _st in agent_spawn_tools:
        _stfn = _st.coroutine if (hasattr(_st, "coroutine") and _st.coroutine) else _st.func
        if _stfn:
            adapter._tools_context[_st.name] = make_tool_awaitable(_stfn)
```
Spawn tools are injected **after** all other tools are registered. This is intentional: `_parent_structured_tools_for_subagent` must be the complete list of tools the parent has — building it before other tools are registered would give the subagent an incomplete toolset. Done only for the parent (`not _is_subagent`).

### C4. `analyze_image` injection — gated by `multimodal.enabled`

```python
# prepare_node.py
_multimodal_enabled = getattr(settings.multimodal, "enabled", True)

if _multimodal_enabled:
    from cuga.backend.tools.image_analysis import create_analyze_image_tool

    _analyze_image_tool = create_analyze_image_tool()
    tools_for_prompt.append(_analyze_image_tool)
    _analyze_image_fn = _analyze_image_tool.coroutine
    adapter._tools_context["analyze_image"] = make_tool_awaitable(_analyze_image_fn)
    logger.info("analyze_image: vision system tool injected ...")
else:
    logger.debug("analyze_image: skipped (multimodal.enabled = false)")
```
Controlled by the new `[multimodal] enabled` flag in `settings.toml` (default `true`). When `false`, the tool is neither added to `tools_for_prompt` (so it never appears in the LLM's tool list) nor registered in `_tools_context` (so calling it from a code block would raise an `AttributeError`). `getattr(..., True)` is a safety default in case the config key is absent in older deployments.

Setting in `settings.toml`:
```toml
[multimodal]
# Master toggle for multi-modal system tools.
# analyze_image is injected when enabled = true.
# pdf_to_images and pptx_to_images are additionally gated by skills.enabled.
enabled = true
```

### C5. `pdf_to_images` / `pptx_to_images` injection — gated by `multimodal.enabled` AND `skills.enabled`

```python
# prepare_node.py
if _multimodal_enabled and skills_cfg_on:
    from cuga.backend.server.workspace_sandbox import _host_workspace_root
    from cuga.backend.tools.pdf_to_images import create_pdf_to_images_tool
    from cuga.backend.tools.pptx_to_images import create_pptx_to_images_tool

    _img_thread_id = _runtime_thread_id_for_fs

    def _resolve_to_host(path: str) -> str:
        """Translate a virtual /workspace/ path or bare filename to a real host path."""
        p = path.strip()
        workspace_root = _host_workspace_root(_img_thread_id)
        for prefix in ("/workspace/", "workspace/"):
            if p.startswith(prefix):
                return str(workspace_root / p[len(prefix):])
        if p in ("/workspace", "workspace"):
            return str(workspace_root)
        candidate = workspace_root / p
        if candidate.exists():
            return str(candidate)
        return p

    def _wrap_with_workspace(fn):
        @functools.wraps(fn)
        def _wrapped(*args, **kwargs):
            if args:
                args = (_resolve_to_host(args[0]),) + args[1:]
            for key in ("pdf", "pptx"):
                if key in kwargs:
                    kwargs[key] = _resolve_to_host(kwargs[key])
            return fn(*args, **kwargs)
        return _wrapped

    # ... register _pdf_to_images_tool and _pptx_to_images_tool
else:
    logger.debug(
        "pdf_to_images/pptx_to_images: skipped "
        f"(multimodal.enabled={_multimodal_enabled}, skills.enabled={skills_cfg_on})"
    )
```
**Why double-gated (multimodal AND skills)?** These tools exist to support skill-driven workflows — a skills SKILL.md instructs the agent to render a PPTX, convert it to images, then call `analyze_image` on each slide for visual QA. Without skills enabled, there's no workflow that would coherently drive this pipeline. Loading the tools in a skills-off environment adds tool prompt noise and import overhead for zero benefit.

`pdf_to_images` and `pptx_to_images` are synchronous (blocking I/O against the host filesystem). They run in the backend server process, not the sandbox. The sandbox agent works with virtual `/workspace/` paths, but the server process has no `/workspace/` mount — paths must be resolved to the real host filesystem location for the current thread.

`_wrap_with_workspace` wraps the tool function before registering it in `_tools_context`. The resolver checks known virtual prefixes, probes the workspace root for bare filenames, and falls through unchanged for already-absolute paths. The closure over `_img_thread_id` (= `_runtime_thread_id_for_fs`) locks in the correct thread-scoped workspace root at registration time. The entire resolver block is now inside the `if _multimodal_enabled and skills_cfg_on:` guard, so it's not defined at all when skipped.

### C6. `upload_context` plumbing

```python
# prepare_node.py:~596-601
upload_context = _cfg.get("upload_context")
if upload_context:
    effective_instructions = (
        f"{upload_context}\n\n{effective_instructions}" if effective_instructions else upload_context
    )
```
The frontend can pass an `upload_context` in the configurable dict (set by `AgentLoop` when workspace files have been uploaded). It's prepended to the agent's instructions so it sees the upload summary before the main instruction text.

### C7. Knowledge awareness — `assemble_system_prompt_section`

```python
# prepare_node.py:~670-703
assembled = await assemble_system_prompt_section(
    engine,
    agent_id,
    awareness_thread_id,
    base_instructions=effective_instructions,
    agent_config_hash=knowledge_config_hash,
    search_config=_search_cfg,
)
if assembled.has_knowledge:
    effective_instructions = assembled.text
```
Previous code: three separate calls (`format_knowledge_context` + `get_knowledge_summary` + `knowledge_instructions.md` read). Now consolidated into a single `assemble_system_prompt_section` seam. Returns a dataclass with `has_knowledge`, `text`, and `prompt_hash` for observability logging. Also fixed draft-mode config lookup to handle multi-tenant `draft_knowledge_configs` dict correctly.

---

## <a name="d"></a>D. Skills system refactor

**Files:** `src/cuga/backend/skills/loader.py`, `registry.py`, `guidance.py`, `tools.py`

### D1. Single configurable skills root

**Before:** `get_skill_search_roots` returned a list of 4+ paths (global legacy → global agents → local .cuga/skills → local .agents/skills), scanning all of them and letting later paths override earlier ones.

**After:** `get_skill_root` returns exactly one `Path` based on `settings.skills.root`:

```python
# loader.py:52-78
VALID_SKILL_ROOTS = frozenset({"cuga", "agents", "global_agents", "global_cuga"})

def get_skill_root(cuga_folder, *, root=None, ...):
    preset = (root or _settings_skill_root()).strip().lower()
    if preset not in VALID_SKILL_ROOTS:
        raise ValueError(...)
    if preset == "global_agents": return Path(...)
    if preset == "global_cuga": return Path(...)
    if preset == "agents": return agents_root / "skills"
    return cuga_root / "skills"
```
Reason: the multi-root scan caused hard-to-debug silent overrides. Single root makes the active skills directory deterministic and observable.

### D2. Jinja2 injection sanitization

```python
# loader.py:28-39
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)

def _sanitize_for_prompt(value: str, field: str, source: Path) -> str:
    sanitized = _JINJA_RE.sub("", value)
    if sanitized != value:
        logger.warning(f"Skill {source}: {field!r} contained Jinja2 template syntax ...")
    return sanitized
```
Skill names and descriptions land verbatim in the Jinja2 system prompt template. A malicious or misconfigured SKILL.md could inject `{{ ... }}` or `{% ... %}` blocks and execute arbitrary template logic. This sanitizes both `name` and `description` fields before storing them in `SkillEntry`.

Additionally, skill names are checked for path traversal:
```python
# loader.py (inside _parse_skill_file)
if re.search(r'[/\\]|\.\.', name_str):
    raise ValueError(f"unsafe skill name {name_str!r}: path separators ...")
```
Skill names are used to build `/workspace/skills/<name>/` paths inside the sandbox, so `../../etc` in a name would be a path traversal attack.

### D3. Error handling flip in `_parse_skill_file`

**Before:** validation ran after `try/except`, so parse errors were swallowed and validation errors were raised (opposite of what you want).
**After:** validation moved inside the `try` block:
```python
# loader.py:124-138
def _parse_skill_file(path: Path) -> SkillEntry | None:
    try:
        frontmatter, body = parse_markdown_with_frontmatter(str(path))
        name = frontmatter.get("name")
        description = frontmatter.get("description")
        if not name or not description:
            raise ValueError("missing name or description in frontmatter")
        name_str = _sanitize_for_prompt(...)
        ...
        return SkillEntry(...)
    except Exception as e:
        logger.warning(f"Skipping invalid skill file {path}: {e}")
        return None
```
Now any failure (parse error, missing field, injection) returns `None` and logs a warning — consistent behavior.

### D4. Process-level skill discovery cache

```python
# loader.py:140-147
_discover_skills_cache: dict[tuple, List[SkillEntry]] = {}

def clear_skills_cache() -> None:
    _discover_skills_cache.clear()
```
`discover_skills` is called on every request (inside `prepare_node`). Since skill files don't change at runtime, caching the scan result process-wide is safe and avoids repeated directory walks. `clear_skills_cache` is exported for tests that need fresh state.

### D5. `tool_definitions` in SkillEntry and `skills/tools.py`

```python
# registry.py:23
tool_definitions: tuple[dict, ...] = ()  # raw dicts from the tools: frontmatter block
```
A skill can now declare Python functions in its SKILL.md frontmatter:
```yaml
tools:
  - name: prime_factorize
    module: cuga.backend.skills.number_theory_tools.prime_factorizer
    function: prime_factorize
    description: "Factorize n and compute number-theoretic properties"
```
These are loaded by `_build_callable_tools_from_entry` in `skills/tools.py`:
```python
# tools.py:17-43
def _build_callable_tools_from_entry(entry: SkillEntry) -> list[StructuredTool]:
    out: list[StructuredTool] = []
    for raw in entry.tool_definitions:
        mod = importlib.import_module(raw["module"])
        fn = getattr(mod, raw["function"], None)
        if asyncio.iscoroutinefunction(fn):
            kwargs["coroutine"] = fn
        else:
            kwargs["func"] = fn
        out.append(StructuredTool.from_function(**kwargs))
    return out
```
This is how the number theory skill ships `prime_factorize` and `solve_crt` as first-class tools the agent can call directly — rather than the agent having to write Python code to call them.

### D6. `guidance.py` — extracted prompt constants

```python
# guidance.py
LOAD_SKILL_GUIDANCE = "..."     # install precedence rules
LOAD_SKILL_COMPANIONS = "..."   # companion file usage
LOAD_SKILL_PLAYBOOK = "..."     # what a skill body may contain
LOAD_SKILL_COMMAND_NORMALIZATION = "..."  # uv/pip/npm rules
AVAILABLE_SKILLS_USAGE = "..."  # how to trigger load_skill
```
Previously, all this text was inlined in `registry.py` and `tools.py` as multi-hundred-line string literals. Extracting to `guidance.py` makes them independently testable, diffable, and reusable (e.g., `LOAD_SKILL_COMMAND_NORMALIZATION` builds on `SANDBOX_UV_COMMAND_NORMALIZATION` from `sandbox_uv.py`).

---

## <a name="e"></a>E. New system tools

**Files:** `src/cuga/backend/tools/image_analysis.py`, `pdf_to_images.py`, `pptx_to_images.py`

### E1. `analyze_image` — vision tool with automatic fallback

```python
# image_analysis.py:33-37
_KNOWN_NON_VISION_PATTERNS = (
    "gpt-oss",
    "falcon",
)
```
Known text-only model substrings checked at call time to skip the primary model entirely and avoid wasting the HTTP timeout.

```python
# image_analysis.py:87-104
if not _skip_primary:
    try:
        primary_llm = LLMManager().get_model(settings.agent.code.model)
        msg = HumanMessage(content=multimodal_content)
        result = await primary_llm.ainvoke([msg])
        return result.content
    except Exception as exc:
        logger.info(f"analyze_image: primary model rejected vision content ...")
        # falls through to fallback
```
Attempt 1: use the same model the agent is already configured to use. If it raises (e.g., a model that returns an error for multimodal input), falls through to attempt 2.

```python
# image_analysis.py:108-154
fallback_model = os.environ.get("IMAGE_ANALYSIS_MODEL", "").strip()
...
loop = asyncio.get_event_loop()
response = await loop.run_in_executor(None, lambda: litellm.completion(**completion_args))
```
Attempt 2: LiteLLM with `IMAGE_ANALYSIS_MODEL` env var. `litellm.completion` is synchronous — `run_in_executor` offloads it to the thread pool so it doesn't block the event loop. `litellm.drop_params = True` allows unknown params to be silently dropped (needed for provider compatibility).

Image encoding: all images (local files, URLs) are converted to `data:<media>;base64,...` URLs. URL images are downloaded with a browser User-Agent header to avoid 403s from CDNs.

### E2. `pdf_to_images` — dual-backend PDF converter

```python
# pdf_to_images.py:88-110
def pdf_to_images(pdf, prefix, dpi=150, first_page=None, last_page=None):
    pdf_path = _resolve(pdf)
    try:
        return _convert_pymupdf(pdf_path, str(prefix_path), dpi, first_page, last_page)
    except ImportError:
        pass
    if shutil.which("pdftoppm"):
        return _convert_pdftoppm(...)
    return "ERROR: No PDF-to-image backend available. ..."
```
Try PyMuPDF first (pure Python, `uv pip install pymupdf`); fall back to `pdftoppm` (Poppler, system binary). Returns a newline-separated list of generated file paths — the agent can feed these directly to `analyze_image`.

### E3. `pptx_to_images` — two-pipeline PPTX converter

Primary pipeline: PPTX → PDF via LibreOffice → JPEG via PyMuPDF/pdftoppm.
Fallback pipeline: python-pptx + Pillow render directly from XML.

LibreOffice is located by: `shutil.which("soffice")` → common macOS paths (`/Applications/LibreOffice.app/...`) → Homebrew paths → Linux paths. The fallback handles layout geometry, text, and embedded images but not complex gradients or custom fonts — explicitly documented in the module docstring.

---

## <a name="f"></a>F. Workspace upload (backend + frontend)

**Files:** `src/cuga/backend/server/workspace_upload.py`, `workspace_sandbox.py`, `src/frontend_workspaces/frontend/src/ChatLanding.tsx`

### F1. `workspace_upload.py` — backend upload handler

**Filename sanitization:**
```python
# workspace_upload.py:33-43
def sanitize_upload_filename(filename: str) -> str:
    name = Path((filename or "").strip()).name   # strip directory components
    if not name or name.startswith("."):
        raise ValueError("Invalid filename")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(...)
    safe_stem = re.sub(r"[^\w.-]", "_", stem, flags=re.ASCII)
    ...
```
`Path(...).name` strips any directory components (prevents `../../../etc/passwd` uploads). Only `{.json, .jsonl, .ndjson}` are allowed. Stem is sanitized to ASCII word characters, hyphens, and dots.

**Content validation:**
```python
# workspace_upload.py:46-62
def validate_upload_content(data: bytes, filename: str) -> None:
    text = data.decode("utf-8")   # must be valid UTF-8
    if suffix == ".json":
        json.loads(text)           # must be valid JSON
    if suffix in {".jsonl", ".ndjson"}:
        for line in text.splitlines():
            if line.strip():
                json.loads(line)   # each non-empty line must parse
```
Content is validated before writing to disk — rejects binary files masquerading as JSON.

**Collision avoidance:**
```python
# workspace_upload.py:65-72
def _unique_upload_name(safe_name, manifest):
    existing = {f.get("name") for f in manifest.get("files", [])}
    if safe_name not in existing:
        return safe_name
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    return f"{stem}_{secrets.token_hex(4)}{suffix}"
```
Uses `secrets.token_hex` (cryptographically random) so collisions can't be predicted or exploited.

**Path construction uses `child_path_under`:**
```python
# workspace_upload.py:87-96
def _uploads_root_host(tid: str) -> Path:
    return child_path_under(thread_workspace_root(tid), UPLOADS_SUBDIR)

def _upload_file_host_path(tid: str, safe_name: str) -> Path:
    if Path(safe_name).name != safe_name or safe_name in (".", ".."):
        raise ValueError("Invalid filename")
    return child_path_under(_uploads_root_host(tid), safe_name)
```
All path operations go through `child_path_under` (from the existing `filesystem/paths.py`), which enforces that the resolved path stays under the allowed root. Belt-and-suspenders on top of the filename sanitization above.

### F2. `workspace_sandbox.py` — fixes and `get_sandbox_env_description`

```python
# workspace_sandbox.py:23-38
def get_sandbox_env_description() -> str:
    mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
    if mode == "opensandbox":
        return "Linux (Ubuntu, Docker container)"
    sys_name = _platform.system()
    ...
```
New function injected into the system prompt as `sandbox_env_info`. The agent uses it to decide which OS-specific commands to emit (e.g., `brew` vs `apt`).

`workspace_tree_is_sandbox_backed` and `workspace_tree_is_native_backed` were previously gated on `settings.skills.enabled` — a bug, since the workspace tree should be visible regardless of whether skills are enabled. Both predicates now check only the sandbox/execution mode settings.

### F3. Frontend — drag-drop upload + refresh

```tsx
// ChatLanding.tsx:~254-256
const JSON_UPLOAD_SUFFIXES = [".json", ".jsonl", ".ndjson"];
const filterJsonUploadFiles = (files: File[]) =>
  files.filter((file) => JSON_UPLOAD_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix)));
```
Client-side filtering mirrors the server-side allowlist.

**Drag-and-drop zone (workspace panel):**
```tsx
// ChatLanding.tsx:~1344-1395
onDragLeave={(e) => {
  const rect = e.currentTarget.getBoundingClientRect();
  const { clientX: x, clientY: y } = e;
  if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
    setWorkspaceDragOver(false);
  }
}}
```
The `onDragLeave` guard checks the mouse position against the panel bounds. This prevents the drag state from flashing when the cursor moves over a child element (which fires `dragLeave` on the parent). Standard drag-and-drop fix.

**Upload handler:**
```tsx
// ChatLanding.tsx:~825-851
const handleWorkspaceUpload = useCallback(async (files: File[]) => {
  await Promise.all(files.map(async (file) => {
    const res = await api.uploadWorkspaceFile(file, tid);
    if (!res.ok) { ... throw new Error(...) }
  }));
  addToast("success", ...);
  await fetchWorkspaceTree();
}, [addToast, effectiveChatThreadId, fetchWorkspaceTree]);
```
All files upload in parallel (`Promise.all`). On completion, `fetchWorkspaceTree` is called to refresh the workspace panel without a full page reload.

**`fetchWorkspaceTree` now accepts `forceRefresh`:**
```tsx
// ChatLanding.tsx:~713-728
const fetchWorkspaceTree = useCallback(async (forceRefresh = false) => {
  if (forceRefresh) setWorkspaceTreeLoading(true);
  const res = await api.getWorkspaceTree(effectiveChatThreadId || undefined, forceRefresh);
  ...
}, [addToast, effectiveChatThreadId]);
```
The manual Refresh button (new `<Renew>` icon) calls `fetchWorkspaceTree(true)`, which shows the loading skeleton and passes `forceRefresh` to the API so the server bypasses any cache. Without the flag the existing call on mount/thread-change keeps working unchanged.

---

## <a name="g"></a>G. `GraphAdapter` + `CugaLiteNode` changes

**Files:** `graph_adapter.py`, `cuga_lite_node.py`

### G1. `spawn_futures_ref` on `AgentGraphAdapter`

```python
# graph_adapter.py:57, 70-71
def __init__(self, ..., spawn_futures_ref=None):
    ...
    self._spawn_futures: Dict[str, Any] = spawn_futures_ref if spawn_futures_ref is not None else {}
```
The adapter now owns the `_spawn_futures` dict. This dict is passed into `create_spawn_tools` in `prepare_node` so that the tools and the adapter share the same future store for the life of the request. Tests inject a local dict via `spawn_futures_ref`.

### G2. Removal of `execution_complete = True` in HITL denial

```python
# cuga_lite_node.py:~80 (removed line)
- state.execution_complete = True
```
When a user denied a tool via HITL, the node previously set `execution_complete = True` before routing to `FINAL_ANSWER_AGENT`. This was incorrect — `execution_complete` is a signal used by the graph's conditional edges to determine whether to loop back. Setting it during denial caused the state machine to behave as if the agent had finished normally, potentially masking re-entry paths. The fix: simply route to `FINAL_ANSWER_AGENT` without touching `execution_complete`.

### G3. Evolve multi-user context propagation

```python
# cuga_lite_node.py:~392-415
_evolve_user_id = normalize_evolve_identifier(state.user_id)
_evolve_namespace_id = (state.service_scope or {}).get("tenant_id") or None
_evolve_session_id = state.thread_id or None
await EvolveIntegration.save_trajectory(
    messages_snapshot, task_id, success,
    user_id=_evolve_user_id,
    namespace_id=_evolve_namespace_id,
    session_id=_evolve_session_id,
)
```
Trajectory saves now include per-user/tenant identifiers. `normalize_evolve_identifier` is imported from `evolve.integration` — presumably normalizes empty strings to `None` or applies length limits.

---

## <a name="h"></a>H. System prompt — `mcp_prompt.jinja2`

**File:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompts/mcp_prompt.jinja2`

### H1. Sub-Agents section

```jinja2
{% if agents_enabled %}
## Sub-Agents
{{ agents_prompt_section }}
> **AGENTS — WHEN TO SPAWN:** When a skill says "USE SUBAGENTS" ...
> **Ad-hoc (most common):** `await spawn_agent(task="<complete task description with all context>")`
> **Async / parallel:** `fid = await spawn_agent(task="...", mode="async")` then `await get_agent_result(fid)`
{% endif %}
```
The `agents_enabled` flag and `agents_prompt_section` are populated by `prepare_node` only for parent agents. The `>` blockquote is deliberate — it signals to the LLM that these are rules, not descriptive content.

Tool list entry:
```jinja2
{% if agents_enabled %}
- **spawn_agent(task, name?, mode)**: Spawn a subagent with fresh context.
- **get_agent_result(future_id, timeout)**: Retrieve an async subagent result.
{% endif %}
```

### H2. Todo planning threshold change

**Before:**
> "If the user's task is **complex** (more than one substantial step, multiple API calls...)"

**After:**
> "If the task requires **even one round-trip plus reasoning** — for example, look up X and then use the result to do Y..."

The threshold was lowered. A task with a single lookup + downstream decision now triggers a todo plan. This was changed to reduce cases where the agent jumped into tool calls on tasks that turned out to require multiple steps, causing mid-flight course corrections.

### H3. `sandbox_env_info` injection

```jinja2
{% if sandbox_env_info %}
- **Execution environment:** {{ sandbox_env_info }} — tailor any OS-specific commands...
{% endif %}
```
Populated by `get_sandbox_env_description()` from `workspace_sandbox.py`. Tells the agent whether it's in Linux Docker, macOS, etc., so it issues the right package manager commands.

### H4. `run_command` output contract

```
returns **stdout only** on success; `\n[stderr]\n...` is appended **only on failure**
— not a dict. Parse JSON with `json.loads(out.split('\n[stderr]\n', 1)[0].strip())`
```
Added explicit instruction on `run_command`'s output format after agents were observed trying to access `out["raw_output"]` or failing to handle `[stderr]` being appended. The parse recipe in the prompt eliminates that ambiguity.

---

## <a name="i"></a>I. Test coverage

### I1. `tests/unit/test_agent_spawn.py`

509 lines covering:

- **Config defaults:** `max_spawn_depth == 2`, `forward_sync_subagent_events is True`
- **Tool builder:** async vs sync function detection, invalid module → `ToolDefinitionError`, missing function → `ToolDefinitionError`, unknown `args_schema` → `ToolDefinitionError`
- **`SkillEntry.tool_definitions` parsing:** SKILL.md frontmatter with a `tools:` block parses into `SkillEntry.tool_definitions`
- **Prompt template:** `agents_enabled` flag renders the Sub-Agents section
- **`_spawn_depth` ContextVar:** `_is_subagent` is False at depth 0, True at depth 1
- **`clear_runtime_caches`:** cache cleared between test runs
- **`SpawnAgentRuntime.from_parent`:** spawn/skill tools filtered from parent tools

### I2. `tests/unit/test_skill_loader.py`, `test_sandbox_uv_guidance.py`

Skill loader: single-root resolution, Jinja2 sanitization, path traversal rejection, error handling in `_parse_skill_file`.

### I3. `tests/e2e/skills/`

End-to-end skill tests: `test_skills_e2e.py`, `test_skills_sdk_e2e.py`, `test_skills_presentation_e2e.py`, `test_skills_real_e2e.py`. These test the full `load_skill → install → execute` chain including `tool_definitions`.

### I4. `tests/unit/test_analyze_image_tool.py`

Unit tests for `analyze_image`: known non-vision model skip, primary model success, primary model fallback to `IMAGE_ANALYSIS_MODEL`, missing fallback model raises.

### I5. `tests/unit/test_workspace_upload.py`, `test_workspace_sandbox.py`

Upload: filename sanitization (path traversal, dotfiles, unsupported extension, ASCII normalization), content validation (invalid JSON, invalid JSONL line, valid UTF-8 NDJSON), collision avoidance via `_unique_upload_name`.

Sandbox: `get_sandbox_env_description` returns correct strings for opensandbox/native/macOS/Linux modes, workspace tree backing predicates without skills gate.

---

## Cross-cutting decisions to probe in review

| # | Decision | Where | Why it was done this way |
|---|---|---|---|
| 1 | `ContextVar` for spawn depth | `runtime.py:21` | Per-async-task isolation in shared event loop |
| 2 | Process-level agent cache (`_agent_cache`) | `runtime.py:47` | Avoid re-compiling LangGraph on every spawn |
| 3 | Single module-level event callback | `runtime.py:29-42` | Only one top-level stream at a time; avoids threading callback through layers |
| 4 | Spawn tools injected **after** all other tools | `prepare_node.py` | `parent_structured_tools` must be complete at construction |
| 5 | `_is_subagent` disables skills by default | `prepare_node.py` | Prevents recursive skill loading and double-registration |
| 6 | Queue-based SSE merge in `run_stream` | `agent_loop.py:719+` | Interleave async graph events + sync spawn callbacks without blocking |
| 7 | Single skills root vs. multi-root | `loader.py:52-78` | Determinism; multi-root caused silent hard-to-debug overrides |
| 8 | Jinja2 sanitization in skill names/descriptions | `loader.py:28-39` | Prompt injection via malicious SKILL.md |
| 9 | `_wrap_with_workspace` inside the gate block | `prepare_node.py` | Closure needs `_img_thread_id` (known at prepare-node time); not defined unless both gates pass |
| 10 | `execution_complete` removed from HITL denial | `cuga_lite_node.py:~80` | Was mis-signaling the graph's conditional routing logic |
| 11 | `analyze_image` gated by `multimodal.enabled` | `settings.toml`, `prepare_node.py` | Allows disabling vision capability without code changes; default `true` |
| 12 | `pdf_to_images`/`pptx_to_images` double-gated by `multimodal.enabled` AND `skills.enabled` | `prepare_node.py` | These tools serve skill-driven pipelines only; loading without skills adds noise and import cost for no benefit |
