# Skills Feature — Fixes & Test Reference

This document records every fix applied to the skills feature, what bug or gap each one addresses, and the exact CLI command to run each verifying test. Tests are fast (no LLM) unless marked **[Tier 3 — real LLM]**.

---

## Round 1 — Initial Review Fixes

### P0.1 — Broken unit test: npm install assertion

**Problem:** The registry was updated to drop the `cd /tmp &&` prefix from the npm install command but the unit test still asserted the old format, causing it to fail on every run.

**Files changed:** `tests/unit/test_skill_loader.py:83`

**Test:**
```bash
uv run pytest tests/unit/test_skill_loader.py::test_skill_registry_load_skill_emits_install_steps_for_requirements -v
```

---

### P1.1 — Sandbox executors ignored runtime `skills_folder` and `skills_enabled`

**Problem:** All three sandbox executors (`native`, `local`, `opensandbox`) resolved the skills folder from `CUGA_FOLDER` env var / `settings.policy.cuga_folder` at copy/upload time, completely ignoring the runtime `skills_folder` and `skills_enabled` values injected via the graph configurable. E2e tests only passed by coincidence because `monkeypatch.chdir` happened to align the default resolution.

**Files changed:**
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/native/native_sandbox_executor.py`
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/local/local_sandbox_executor.py`
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/opensandbox/opensandbox_executor.py`
- `src/cuga/backend/cuga_graph/nodes/cuga_lite/cuga_lite_graph.py`

**Tests:**
```bash
# Copies skill files when skills_enabled=True and the correct cuga_folder is passed
uv run pytest tests/unit/test_skill_loader.py::test_copy_skills_to_workspace_copies_skill_files -v

# No files copied when skills_enabled=False regardless of folder
uv run pytest tests/unit/test_skill_loader.py::test_copy_skills_to_workspace_is_noop_when_disabled -v

# Graph integration: configurable is wired through correctly
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillsCugaLiteIntegration::test_skills_block_appears_in_cuga_lite_system_prompt" -v
```

---

### P1.2 — Silent non-discovery when skills enabled but no SKILL.md found

**Problem:** When `skills_cfg_on=True` but `discover_skills()` returned an empty list, the graph silently set `skills_enabled=False` with no log output, making it impossible for operators to diagnose a missing or mis-placed SKILL.md.

**Files changed:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/cuga_lite_graph.py`

**Test (run with `-s` to see the warning in output):**
```bash
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillsCugaLiteIntegration::test_graph_completes_without_skills_block_when_no_skills_found" -v -s
```

---

### P1.3 — `enable_shell_tool=False` silently cleared the skills block

**Problem:** `prompt_utils.create_mcp_prompt` unconditionally set `skills_enabled=False` when `enable_shell_tool=False` (the default), with no warning. Users who enabled `skills.enabled=true` in settings but left `enable_shell_tool` at its default would see no skills in the agent with no indication why.

**Files changed:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/prompt_utils.py`

**Test (run with `-s` to see warning):**
```bash
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillsCugaLiteIntegration::test_graph_completes_without_skills_block_when_no_skills_found" -v -s
```

---

### P1.4 — No tests for sandbox skill-copy path

**Problem:** `_copy_skills_to_workspace()` in native and local executors had zero test coverage. A regression there would be invisible.

**Files changed:** `tests/unit/test_skill_loader.py`

**Tests:**
```bash
uv run pytest tests/unit/test_skill_loader.py::test_copy_skills_to_workspace_copies_skill_files -v
uv run pytest tests/unit/test_skill_loader.py::test_copy_skills_to_workspace_is_noop_when_disabled -v
```

---

### P2.1 — SDK Tier 3 results absent from CI summary table

**Problem:** `pytest_terminal_summary` in `conftest.py` only collected `_RESULTS` from `test_skills_llm_e2e`. SDK Tier 3 tests had no reporting hook, so they were invisible in the end-of-session summary table.

**Files changed:**
- `tests/e2e/conftest.py`
- `tests/e2e/test_skills_sdk_e2e.py`

**Verification:** Run Tier 3 SDK tests with `-s` and check the printed summary table at the end:
```bash
uv run pytest tests/e2e/test_skills_sdk_e2e.py -m e2e -v -s  # requires real LLM credentials
```

---

### P2.2 — No test for `load_skill` unknown-name error path

**Problem:** `SkillRegistry.load_skill("nonexistent")` returned a helpful error string listing known skills, but this path had no test. An LLM calling `load_skill` with a slightly wrong name would get an error; we need to guarantee the message is useful enough for the model to self-correct.

**Files changed:** `tests/e2e/test_skills_e2e.py`

**Test:**
```bash
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillRegistry::test_load_skill_unknown_name_returns_helpful_error" -v
```

---

### P2.3 — No test for empty skills directory with `skills_enabled=True`

**Problem:** No test confirmed the graph handles `skills_cfg_on=True` + zero SKILL.md files gracefully (no crash, no phantom skills block in system prompt).

**Files changed:** `tests/e2e/test_skills_e2e.py`

**Test:**
```bash
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillsCugaLiteIntegration::test_graph_completes_without_skills_block_when_no_skills_found" -v
```

---

### P2.4 — No debug log when a skill is overridden during discovery

**Problem:** When a skill from a higher-priority path overrode one from a lower-priority path, no log was emitted. Diagnosing unexpected skill precedence was impossible.

**Files changed:** `src/cuga/backend/skills/loader.py`

**Verification:** Run with `--log-cli-level=DEBUG` to see override messages:
```bash
uv run pytest tests/unit/test_skill_loader.py::test_discover_skills_agents_paths_override_legacy_fallbacks_and_preserve_requirements -v --log-cli-level=DEBUG
```

---

### P2.5 — Skill name not sanitized against path-traversal characters

**Problem:** A SKILL.md with `name: ../../etc/passwd` in its frontmatter would have its name used directly in sandbox file paths (e.g. `/workspace/skills/../../etc/passwd`), potentially pointing outside the intended directory.

**Files changed:** `src/cuga/backend/skills/loader.py`

**Test:**
```bash
uv run pytest tests/unit/test_skill_loader.py::test_skill_name_with_path_traversal_is_rejected -v
```

---

## Round 2 — Architectural Review Fixes

### Fix 1 — Async race condition in `OpenSandboxExecutor` (Critical)

**Problem:** `_sandboxes` and `_skills_config` are class-level dicts with no concurrency protection. Two concurrent coroutines for the same `thread_id` could both see an empty cache, both call the expensive `Sandbox.create`, and one remote container would be orphaned (created and never cleaned up). Additionally, `_skills_config[key]` could be read before it was written if the timing was wrong, causing the sandbox to be created without skills uploaded.

**Files changed:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/opensandbox/opensandbox_executor.py`

**Fix:** Added `_locks: dict[str, asyncio.Lock]` and `_get_key_lock(key)`. `_get_or_create_interpreter` now acquires the per-key lock before the entire cache-check/create/store sequence.

**Tests:**
```bash
# Two concurrent calls → exactly one Sandbox.create
uv run pytest tests/unit/test_opensandbox_executor.py::test_concurrent_creation_calls_sandbox_create_exactly_once -v

# Different thread_ids still each get their own sandbox
uv run pytest tests/unit/test_opensandbox_executor.py::test_different_thread_ids_each_get_own_sandbox -v
```

---

### Fix 2 — Unhandled exception in `_upload_skills_to_sandbox` (High)

**Problem:** `await interpreter.sandbox.files.write_files(entries)` was not wrapped. If the OpenSandbox service returned an error, the exception propagated out of `_get_or_create_interpreter` before `self._sandboxes[key] = interpreter`, meaning the partially-started remote container was never cached and never cleaned up (orphaned). On the next tool call, the graph would try to create another container, repeat the failure, and leak again.

**Files changed:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/opensandbox/opensandbox_executor.py`

**Fix:** `write_files` is now wrapped in try/except inside `_upload_skills_to_sandbox` (logs + re-raises). The caller `_get_or_create_interpreter` catches, logs a clear warning, and caches the sandbox regardless — the agent can continue without skills rather than leaking a container.

**Tests:**
```bash
# Sandbox is in _sandboxes even after write_files raises
uv run pytest tests/unit/test_opensandbox_executor.py::test_sandbox_cached_even_when_upload_fails -v

# Second call returns the cached interpreter, no second Sandbox.create
uv run pytest tests/unit/test_opensandbox_executor.py::test_upload_failure_does_not_prevent_subsequent_tool_use -v
```

---

### Fix 3 — SDK double-appends `.cuga` to `skills_folder` (High)

**Problem:** Both `invoke` and `stream_invoke` in the SDK unconditionally appended `/ ".cuga"` to `self._skills_folder`. If a user passed `skills_folder="/project/.cuga"` (following the documented convention), the SDK produced `/project/.cuga/.cuga`. `discover_skills` then resolved `.agents/skills` relative to the wrong parent, found nothing, and the "no skills found" warning fired — with no indication the path transformation was the cause.

**Files changed:** `src/cuga/sdk.py`

**Fix:** Guard added: `if folder.name != ".cuga": folder = folder / ".cuga"`.

**Tests:**
```bash
# .cuga-suffixed path stays as-is (no double suffix)
uv run pytest "tests/e2e/test_skills_sdk_e2e.py::TestSkillsSdkConfiguration::test_skills_folder_with_cuga_suffix_is_not_double_suffixed" -v

# Plain path correctly gets .cuga appended
uv run pytest "tests/e2e/test_skills_sdk_e2e.py::TestSkillsSdkConfiguration::test_skills_folder_without_cuga_suffix_gets_cuga_appended" -v
```

---

### Fix 4 — Jinja2 prompt injection via skill `description` / `name` fields (Medium-High)

**Problem:** `format_available_skills_block()` rendered skill names and descriptions directly into the Jinja2 system-prompt template with `autoescape=False`. A SKILL.md whose `description:` or `name:` frontmatter contained `{{ }}` or `{% %}` syntax would be evaluated by the template engine, allowing an attacker with filesystem write access to inject arbitrary content into the model's system prompt.

**Files changed:** `src/cuga/backend/skills/loader.py`

**Fix:** Added `_JINJA_RE` pattern and `_sanitize_for_prompt()`. Both `name` and `description` are sanitized at parse time in `_parse_skill_file` before being stored in `SkillEntry`. A warning is logged if sanitization modifies the value.

**Tests:**
```bash
# {{ }} expression syntax stripped from description
uv run pytest tests/unit/test_skill_loader.py::test_jinja_expression_in_description_is_stripped -v

# {% %} block delimiters stripped from description
uv run pytest tests/unit/test_skill_loader.py::test_jinja_block_in_description_is_stripped -v

# {{ }} expression syntax stripped from name
uv run pytest tests/unit/test_skill_loader.py::test_jinja_expression_in_name_is_stripped -v

# Clean description is not modified
uv run pytest tests/unit/test_skill_loader.py::test_clean_description_is_unchanged -v
```

---

### Fix 5 — Stale sandbox skills when `skills_folder` config changes (Medium)

**Problem:** `create_sandbox_tools` wrote `_skills_config[key]` on every call, but `_get_or_create_interpreter` only read it once at sandbox creation time. If a user switched `skills_folder` between conversation turns while the sandbox was still alive, the new config was silently ignored — the old skills remained, and there was no indication to the user that the change had no effect.

**Files changed:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/executors/opensandbox/opensandbox_executor.py`

**Fix:** Added `_active_skills_config` tracking what config was actually used at creation. `create_sandbox_tools` compares the new config against the active one and logs a `logger.warning` directing the user to call `release_sandbox(thread_id)`. `release_sandbox` now also clears `_active_skills_config`, `_skills_config`, and `_locks` for the thread.

**Tests:**
```bash
# Warning logged when config changes for a live sandbox
uv run pytest tests/unit/test_opensandbox_executor.py::test_stale_skills_config_logs_warning -v

# No warning when config is unchanged
uv run pytest tests/unit/test_opensandbox_executor.py::test_no_stale_warning_when_config_unchanged -v

# No warning for a brand-new thread with no sandbox yet
uv run pytest tests/unit/test_opensandbox_executor.py::test_no_stale_warning_for_new_sandbox -v

# release_sandbox clears all four tracking dicts
uv run pytest tests/unit/test_opensandbox_executor.py::test_release_sandbox_clears_all_state -v
```

---

### Fix 6 — `reflection_skills_enabled` staleness across turns (Low — documentation)

**Problem:** `reflection_skills_enabled` is written into LangGraph persisted state at the end of each turn. If a user added or removed a SKILL.md between turns, the reflection agent would read the stale value from the previous turn's state until the next full graph invocation. This was undocumented, making it surprising behavior.

**Files changed:** `src/cuga/backend/cuga_graph/nodes/cuga_lite/cuga_lite_graph.py`

**Fix:** Added an inline comment at the assignment site explaining that staleness across turns is expected and resolves on the next invocation.

No dedicated test (this is a LangGraph state lifecycle property, not a logic bug).

---

### Fix 7 — Install ordering is purely instructional with no verification (Low)

**Problem:** The STEP 1 block told the LLM to run `uv pip install` and `npm install` before anything else, but provided no verification. The LLM had no signal that the install actually succeeded before it proceeded to STEP 2. A slow network, a missing package name, or a version conflict would produce a silent failure.

**Files changed:** `src/cuga/backend/skills/registry.py`

**Fix:** After the pip install block, appends `await run_command('uv pip show <pkgs>')`. After the npm install block, appends `await run_command('npm list <pkgs>')`. These produce explicit output (name, version, location) that the model can read to confirm success before proceeding.

**Tests:**
```bash
# uv pip show present after pip install
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillRegistry::test_pip_install_followed_by_verification_command" -v

# npm list present after npm install
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillRegistry::test_npm_install_followed_by_verification_command" -v

# Verification commands appear after install and before STEP 2
uv run pytest "tests/e2e/test_skills_e2e.py::TestSkillRegistry::test_verification_appears_after_install_and_before_step2" -v
```

---

## Run All New Tests (no LLM, ~2 seconds)

```bash
uv run pytest \
  tests/unit/test_skill_loader.py \
  tests/unit/test_opensandbox_executor.py \
  tests/e2e/test_skills_e2e.py \
  tests/e2e/test_skills_sdk_e2e.py::TestSkillsSdkConfiguration \
  -v
```

---

## Complete New Test Inventory

| Test | File | Fix verified |
|---|---|---|
| `test_skill_registry_load_skill_emits_install_steps_for_requirements` | `test_skill_loader.py` | P0.1 |
| `test_copy_skills_to_workspace_copies_skill_files` | `test_skill_loader.py` | P1.1, P1.4 |
| `test_copy_skills_to_workspace_is_noop_when_disabled` | `test_skill_loader.py` | P1.1, P1.4 |
| `test_skill_name_with_path_traversal_is_rejected` | `test_skill_loader.py` | P2.5 |
| `test_jinja_expression_in_description_is_stripped` | `test_skill_loader.py` | Fix 4 |
| `test_jinja_block_in_description_is_stripped` | `test_skill_loader.py` | Fix 4 |
| `test_jinja_expression_in_name_is_stripped` | `test_skill_loader.py` | Fix 4 |
| `test_clean_description_is_unchanged` | `test_skill_loader.py` | Fix 4 |
| `test_concurrent_creation_calls_sandbox_create_exactly_once` | `test_opensandbox_executor.py` | Fix 1 |
| `test_different_thread_ids_each_get_own_sandbox` | `test_opensandbox_executor.py` | Fix 1 |
| `test_sandbox_cached_even_when_upload_fails` | `test_opensandbox_executor.py` | Fix 2 |
| `test_upload_failure_does_not_prevent_subsequent_tool_use` | `test_opensandbox_executor.py` | Fix 2 |
| `test_stale_skills_config_logs_warning` | `test_opensandbox_executor.py` | Fix 5 |
| `test_no_stale_warning_when_config_unchanged` | `test_opensandbox_executor.py` | Fix 5 |
| `test_no_stale_warning_for_new_sandbox` | `test_opensandbox_executor.py` | Fix 5 |
| `test_release_sandbox_clears_all_state` | `test_opensandbox_executor.py` | Fix 5 |
| `test_skills_block_appears_in_cuga_lite_system_prompt` | `test_skills_e2e.py` | P1.1 |
| `test_load_skill_tool_is_bound_to_model_when_native_tools_enabled` | `test_skills_e2e.py` | P1.1 |
| `test_graph_completes_without_skills_block_when_no_skills_found` | `test_skills_e2e.py` | P1.2, P1.3, P2.3 |
| `test_load_skill_unknown_name_returns_helpful_error` | `test_skills_e2e.py` | P2.2 |
| `test_pip_install_followed_by_verification_command` | `test_skills_e2e.py` | Fix 7 |
| `test_npm_install_followed_by_verification_command` | `test_skills_e2e.py` | Fix 7 |
| `test_verification_appears_after_install_and_before_step2` | `test_skills_e2e.py` | Fix 7 |
| `test_skills_configurable_injected_into_invoke_config` | `test_skills_sdk_e2e.py` | P1.1 |
| `test_skills_folder_with_cuga_suffix_is_not_double_suffixed` | `test_skills_sdk_e2e.py` | Fix 3 |
| `test_skills_folder_without_cuga_suffix_gets_cuga_appended` | `test_skills_sdk_e2e.py` | Fix 3 |
