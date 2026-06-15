# Branch Changes: `feat/199-spawn-sub-agents-at-runtime`

All numbered changes below correspond to commits on this branch relative to `main`.

---

## 1. Frontend dead-code removal

**Commit:** `261727d`

Deleted orphaned files from `frontend_workspaces/` — the `agentic_chat` Carbon integration, legacy streaming manager, sidepanel config modals (memory, model, agent-human), and the `carbon-chat/scenarios.ts` alias. Cleaned up unused imports in `App.tsx`, `ConfigHeader`, and `ManagePage`.

---

## 2. Supervisor pydantic iteration fix

**Commit:** `5bd8644`

Fixed `_agent_card_description` in `a2a_protocol.py` — iterating a pydantic model in v1 yields `(field, value)` tuples, not strings. Switched to `model_dump(exclude_none=True)` for `AgentCapabilities` and used `.name`/`.id` attributes for `AgentSkill` objects instead of passing them directly to `str.join`.

---

## 3. `agent_spawn` backend module

**Commit:** `38740cb`

New package at `src/cuga/backend/agent_spawn/` with seven files:

- `loader.py` — discovers `AGENT.md` descriptor files from a configured directory
- `registry.py` — `AgentDescriptorRegistry` that indexes agents by name
- `runtime.py` — async spawn runtime with `ContextVar` depth tracking to prevent recursive spawning
- `tool_builder.py` — builds `StructuredTool` wrappers for `spawn_agent` / `get_agent_result`
- `tools.py` — async implementations of both tools
- `prompt_utils.py` — `format_available_agents_block` for system-prompt injection
- `__init__.py` — re-exports the public surface

---

## 4. Config validators and default settings

**Commit:** `9cb3fc0`

Added Dynaconf validators in `config.py` for the new `agent_spawn.*` keys. Added an `[agent_spawn]` section to `settings.toml` with defaults (`enabled`, `agents_dir`, `inherit_parent_tools`, `max_spawn_depth`, `forward_sync_subagent_events`). Also raised `tool_call_timeout` from 30 s → 120 s to accommodate sub-agent round-trips.

---

## 5. Wire spawn tools and prompt into `cuga_lite`

**Commit:** `8e739d9`

Hooked the new module into the main agent graph:

- `prepare_node.py` — discovers agents, registers `spawn_agent` / `get_agent_result` in `tools_context`; skips when running inside a sub-agent (`_is_subagent` guard)
- `cuga_lite_graph.py` — creates the `spawn_futures` closure dict and passes it to `AgentGraphAdapter`
- `prompt_utils.py` + `mcp_prompt.jinja2` — renders the `## Sub-Agents` section and tool stubs in the system prompt when agents are enabled

---

## 6. SubAgent SSE events streamed through `AgentLoop`

**Commit:** `a0a39a7`

Extended `agent_loop.py` to forward sub-agent lifecycle events to the SSE stream:

- `_spawn_to_stream_event()` converts `SpawnAgent`, `SpawnAgentResult`, and `CodeAgent` runtime events into `SubAgent` SSE `StreamEvent` objects
- `run_stream()` registers an `asyncio.Queue` callback with the spawn runtime before the graph loop starts, drains the queue between steps and after completion, and clears the callback in a `finally` block

---

## 7. Real-time sub-agent step streaming fix

**Commit:** `e4b78a2`

Replaced the per-step queue drain in `agent_loop.py` with a unified `asyncio.Queue` fed by a background task running the parent graph stream. Sub-agent events are now yielded immediately as each step completes (instead of being batched until the parent tool call returns). Added `graph_task` cleanup in the `finally` block.

---

## 8. Code executor configurable timeout

**Commit:** `bc9abd3`

Both execution paths in `code_executor.py` now read `settings.advanced_features.tool_call_timeout` instead of the hardcoded 30 s, preventing silent timeouts for slow sub-agent tool calls.

---

## 9. Skills: parse `tool_definitions` from frontmatter

**Commit:** `07446fb`

`skills/loader.py` now reads the `tools:` YAML list from a `SKILL.md` frontmatter, validates each entry has `name`/`module`/`function` keys, and populates `SkillEntry.tool_definitions`. Malformed entries are skipped with a warning.

---

## 10. Skills: `agents:` frontmatter key for bundled sub-agents

**Commit:** `037c21d`

Added an `agents:` key to `SKILL.md` frontmatter — a list of relative directory paths each containing an `AGENT.md`. `_parse_skill_agents()` in `loader.py` resolves and parses each path. `SkillEntry` gains `agent_descriptors`. In `prepare_node.py`, skill-embedded descriptors are merged with directory-discovered agents (directory wins on name collision), so `agent_spawn.enabled=False` no longer blocks skill-declared agents.

---

## 11. Frontend: SubAgent SSE event handling

**Commit:** `725e7f7`

Two new files in `carbon-chat/`:

- `customSendMessage.ts` — accumulates `start`/`step`/`result` payloads per agent name into `collectedSteps`; flushes pending steps when switching agents; renders each iteration as a `<details>` block inside the parent reasoning step
- `customLoadHistory.ts` — mirrors the live-stream logic so previously completed sub-agent work re-renders correctly on page load

---

## 12. Frontend dist bundle rebuild

**Commit:** `8e1f984`

Rebuilt the compiled frontend bundle after the SubAgent SSE changes — replaces the `main.118728e` / `vendors.b6b2c62` content-hash chunks with `main.692684` / `vendors.b64eb4`.

---

## 13. Performance: cache `discover_skills` results

**Commit:** `aec554d`

Added a process-level `_discover_skills_cache` dict in `skills/loader.py` keyed by resolved search-root tuple, avoiding repeated `rglob` scans. Exposed `clear_skills_cache()` for tests and hot-reload. Cache is bypassed when `CUGA_AGENT_SPAWN_NO_CACHE=1` is set.

---

## 14. Performance: cache compiled `CugaAgent` and static tools

**Commit:** `41c6584`

Added `_agent_cache` (keyed by name + model + tool-name frozenset) and `_static_tools_cache` (keyed by descriptor name) in `agent_spawn/runtime.py` to reuse compiled LangGraph graphs and tool objects across spawns. Added `prewarm_agent_for_entry()` async coroutine that forces graph compilation off the event loop. Added `clear_runtime_caches()` for teardown. All caches respect `CUGA_AGENT_SPAWN_NO_CACHE=1`.

---

## 15. Performance: background pre-warm tasks in `prepare_node`

**Commit:** `3392d98`

After building the `AgentDescriptorRegistry`, `prepare_node.py` now schedules one `asyncio.create_task(prewarm_agent_for_entry(...))` per descriptor. Tasks run concurrently with the parent LLM call so graph compilation is hidden behind network latency, eliminating first-spawn compilation delay.

---

## 16. Production `number_theory` skill and tools

**Commit:** `0a28783`

Added the bundled `number_theory` skill at `.agents/skills/number_theory/`:

- `SKILL.md` — declares `agents: [agents/prime_factorizer, agents/modular_solver]`
- `AGENT.md` descriptors for both agents pointing at new production tool modules
- `agent_spawn/number_theory_tools/prime_factorizer.py` — Miller-Rabin + Pollard-rho factorizer, Euler totient, Möbius function, squarefree check
- `agent_spawn/number_theory_tools/modular_solver.py` — CRT solver supporting non-coprime moduli via extended GCD

---

## 17. Tests: unit and integration suite for `agent_spawn`

**Commits:** `aa628e6`, `80c0832`, `e37bfe9`

- `tests/unit/test_agent_spawn.py` — 868-line suite covering loader, registry, runtime depth guard, tool_builder, and both spawn tools
- `tests/integration/test_agent_spawn_integration.py` — integration tests verifying a spawned sub-agent runs and events forward correctly
- `tests/fixtures/agents/` — `data_analyst`, `prime_factorizer`, and `modular_solver` fixture agents with `AGENT.md` descriptors and tool implementations

---

## 18. Tests: e2e number-theory sub-agent tests

**Commit:** `1fdd49f`

`tests/e2e/test_agent_spawn_number_theory.py` — two live-LLM e2e scenarios:

1. Single-agent: asks for φ(720720), asserts 138240 in the answer
2. Two-agent: factorize 9699690, solve a 3-equation CRT system, check divisibility

Includes `_contains_number()` helper that normalises digit-group separators (comma, space, narrow no-break space) so LLM formatting variants don't cause false negatives.

---

## 19. Tests: skill-based agent spawning tests

**Commit:** `0241e7e`

- `tests/unit/test_skill_loader.py` — 5 unit tests for `agents:` key parsing (valid load, missing path skipped, absent key yields empty tuple, multiple agents, fixture round-trip)
- `tests/integration/test_agent_spawn_integration.py` (additions) — skill-embedded agents merge into registry, `spawn_agent` tool is callable end-to-end with correct mock format, production `number_theory` SKILL.md loads and all `tool_definitions` are importable
- `tests/e2e/test_skill_agent_spawn.py` — e2e suite mirroring number-theory tests but driven exclusively through skill discovery with `agent_spawn.enabled=False`
- `tests/fixtures/skills/number_theory/SKILL.md` — fixture skill using relative paths to reuse the existing fixture agents

---

## 20. Untrack internal planning docs

**Commit:** `53745b6`

Removed `agent-spawning-proposal.md`, `agent-spawning-implementation-plan.md`, and `skills-fixes-and-tests.md` from git tracking (files kept on disk in `docs/`).
