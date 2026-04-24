# WebArena Evaluation for CUGA Agent

## Overview

This PR adds support for running CUGA Agent on the [WebArena](https://webarena.dev/) benchmark — a set of 812 realistic web tasks across 6 websites (Shopping, Shopping Admin, Reddit, GitLab, Map, Wikipedia).

CUGA runs **standalone with its own Playwright browser** and NocodeUI Chrome extension. We bypass WebArena's built-in evaluator and compare answers externally against reference answers from `test.raw.json`.

## Architecture

```
CUGA Agent (EC2)                        WebArena Sites (same EC2, Docker)
    |                                       |
    |  Playwright + NocodeUI extension      |  Shopping      :7770
    |  TaskDecomposition -> PlanController   |  Shopping Admin :7780
    |  -> BrowserPlanner -> ActionAgent      |  Reddit         :9999
    |  click/type/go_back/select_option     |  Map            :3000
    |-------------------------------------->|  Wikipedia      :8888
    |                                       |  GitLab         :8023
    |  <-- page DOM via NocodeUI ---------- |
    |                                       |
    |  run_webarena_test.py                 |
    |  External answer comparison           |
```

### Why standalone with own browser

CUGA's planning pipeline is tightly coupled with its browser layer:
- Uses **NocodeUI**, a Chrome extension that provides element IDs (`bid`), structured page content, and screenshots
- The `BrowserPlannerAgent` expects NocodeUI's output format, not raw AXTree
- Swapping in external AXTree observations would require rewriting the entire observation pipeline

### Why skip WebArena evaluator

1. **Sync/async mismatch** — WebArena's `EvaluatorComb` expects `playwright.sync_api.Page`, but CUGA uses `playwright.async_api.Page`
2. **GPT-4 dependency** — `fuzzy_match` evaluation calls OpenAI GPT-4 internally

Instead, we compare answers externally using `test.raw.json` reference answers: `exact_match`, `must_include`, and `fuzzy_match` (flagged for manual review).

## New Files

### `evaluation/tasks/task.py` — Async WebArena Task

Async task wrapper for CUGA's `BrowserEnvGymAsync`. Key design:

- Uses `WebArenaInstance` from BrowserGym for correct site URLs and credentials (not hardcoded)
- `validate()` is a no-op — returns `(0.0, False, "", {})`, answer comparison done externally
- Login in separate tab per site (reddit, gitlab, shopping, shopping_admin)
- Loads task configs from `webarena` package's `test.raw.json`, substitutes URL placeholders

### `run_webarena_test.py` — Test Runner

Runs CUGA on WebArena tasks sequentially with external answer checking.

```bash
# Run specific tasks
python run_webarena_test.py --task-ids 31 197 267 --output results.json

# Run N random tasks
python run_webarena_test.py --random 10 --output results.json
```

Key features:
- Resets global `ActivityTracker` singleton before each task to prevent state pollution
- `check_answer()` compares agent answer against `test.raw.json` expected values
- Supports `exact_match`, `must_include`, `fuzzy_match` (flagged manual), `program_html` (flagged manual)
- Outputs JSON report with per-task results, accuracy, and timing

### `src/cuga/patches/strip_json_fences.py` — JSON Parsing Patches

Claude via OpenAI-compatible proxy doesn't support native structured output. These 6 monkey-patches fix JSON parsing at multiple levels:

| Patch | Problem | Fix |
|-------|---------|-----|
| `patch_pydantic_json_parse` | Claude wraps JSON in `` ```json ``` `` fences | Strip fences before `model_validate_json`, fix string-to-list type mismatches |
| `patch_langchain_output_parser` | `PydanticOutputParser.parse` can't handle markdown format | Extract JSON from `{...}` blocks, parse markdown `**key**: value` as fallback |
| `patch_json_output_parser` | Same issue for `JsonOutputParser.parse` | Same extraction fallback |
| `patch_openai_chat_model` | Fences in raw LLM response reach parsers | Strip fences from `ChatOpenAI._generate/_agenerate` output |
| `patch_pydantic_output_parser_parse_obj` | Claude returns `"a. b. c"` instead of `["a","b","c"]` | Fix string-to-list, fill missing required fields |
| `patch_parse_json_markdown` | Claude returns reasoning prose before JSON object | Extract JSON by finding first `{` and matching `}` when standard parsing fails |

The last patch (`patch_parse_json_markdown`) was the most critical — it fixed 8/10 task failures in a single change by patching `langchain_core.utils.json.parse_json_markdown`, the root function that all LangChain parsers funnel through.

### `docs/webarena-eval.md` — This file

## Modified Files (from original CUGA code)

### `controller.py` — 2 bug fixes

**Bug 1: Duplicate keyword argument (crash)**
```python
# Original — passes tool_provider both positionally and as kwarg
feedback = await AgentRunner.process_event_async(
    ..., self.env.tool_implementation_provider, ...,
    tool_provider=self.env.tool_implementation_provider,  # REMOVED
)
```
This crashes with `TypeError: got multiple values for argument 'tool_provider'`.

**Bug 2: Unguarded dict access (crash)**
```python
# Original — assumes feedback[-1] is always a dict
if len(feedback) > 0 and feedback[-1]['status'] == "alert":

# Fixed — feedback[-1] can be AIMessage
if len(feedback) > 0 and isinstance(feedback[-1], dict) and feedback[-1].get('status') == "alert":
```

Both are bugs in the original CUGA code, not specific to WebArena mode.

### `base_agent.py` — Configurable structured output method

Added `CUGA_STRUCTURED_OUTPUT_METHOD` env var option. When set to `"parser"`, uses `PydanticOutputParser` instead of `with_structured_output(method="json_schema")`.

```python
# Original — hardcoded to json_schema (requires OpenAI native support)
base_chain = prompt_template | llm.with_structured_output(schema, method="json_schema")

# New — configurable, defaults to original behavior
_so_method = os.environ.get("CUGA_STRUCTURED_OUTPUT_METHOD", "json_schema")
if _so_method == "parser":
    _parser = PydanticOutputParser(pydantic_object=schema)
    base_chain = prompt_template | llm | _parser
else:
    base_chain = prompt_template | llm.with_structured_output(schema, method=_so_method)
```

**Why:** Claude via OpenAI-compatible proxy doesn't support `response_format: json_schema`. The `parser` mode lets CUGA work with any LLM that returns JSON text.

### `plan_controller.py` — Registry crash fix

```python
# Original — crashes when no registry service is running
all_apps = await get_apps()

# Fixed — graceful fallback
try:
    all_apps = await get_apps()
except Exception:
    all_apps = []
```

**Why:** In standalone WebArena mode, CUGA's registry service isn't running. Without this fix, every task crashes at the PlanController stage.

### 3 Prompt Templates — JSON output enforcement

**Files:**
- `browser_planner_agent/prompts/system.jinja2`
- `qa_agent/prompts/system.jinja2`
- `classify_task_system.jinja2`

**Original** prompts used vague output descriptions:
```
*Output*:
- thoughts: Step-by-step thoughts.
- next_agent: the selected agent.
```

**Changed** to explicit JSON enforcement with examples:
```
You MUST respond with ONLY a valid JSON object. No markdown, no explanation.

The JSON object must have exactly these keys:
- "thoughts": a list of strings...
- "next_agent": one of "ActionAgent", "QaAgent"...

Example valid response:
{"thoughts": ["I need to search"], "next_agent": "ActionAgent", ...}
```

**Why:** Claude and DeepSeek interpreted the original markdown-style instructions as formatting guidance, returning `**thoughts**: ...` markdown instead of JSON. The explicit JSON examples and constraints fix this.

## Environment Setup

```bash
# WebArena site URLs (localhost when CUGA runs on same EC2 as WebArena)
export WA_SHOPPING="http://localhost:7770"
export WA_SHOPPING_ADMIN="http://localhost:7780/admin"
export WA_REDDIT="http://localhost:9999"
export WA_GITLAB="http://localhost:8023"
export WA_WIKIPEDIA="http://localhost:8888"
export WA_MAP="http://localhost:3000"
export WA_HOMEPAGE="http://localhost:4399"

# LLM (Claude Opus 4.5 via OpenAI-compatible proxy)
export OPENAI_API_KEY="<proxy-key>"
export OPENAI_BASE_URL="http://<proxy-host>:8317/v1"
export MODEL_NAME="claude-opus-4-20250514"

# CUGA mode settings
export CUGA_STRUCTURED_OUTPUT_METHOD=parser
```

### Magento Base URL Fix

The WebArena AMI has Magento's base URL hardcoded in MySQL. When a new EC2 instance launches with a new IP, Magento redirects to the dead old hostname. Must fix on each new instance:

```bash
PRIVATE_IP=$(hostname -I | awk '{print $1}')
docker exec shopping bash -c "mysql -u root magento -e \
  \"UPDATE core_config_data SET value='http://${PRIVATE_IP}:7770/' \
   WHERE path IN ('web/unsecure/base_url','web/secure/base_url');\""
docker exec shopping_admin bash -c "mysql -u root magento -e \
  \"UPDATE core_config_data SET value='http://${PRIVATE_IP}:7780/' \
   WHERE path IN ('web/unsecure/base_url','web/secure/base_url');\""
```

## Test Results

### 10 non-GitLab tasks with Claude Opus 4.5 (all fixes applied)

| Task | Site | Actions | Time | Status | Answer |
|------|------|---------|------|--------|--------|
| 31 | reddit | 8 | 181s | CORRECT | Found user, counted 0 downvoted comments |
| 197 | shopping_admin | 2 | 105s | CORRECT | "$778.20" total of last 5 non-cancelled orders |
| 202 | shopping_admin | 2 | 99s | MANUAL | "May 23, 2023" — matches expected |
| 267 | wikipedia+map | 10 | 213s | CORRECT | "Acadia National Park, 1h23m drive" |
| 281 | shopping | 0 | 90s | WRONG | Found products but missed some names |
| 455 | shopping_admin | 4 | 174s | MANUAL | Disabled product successfully |
| 582 | reddit | 8 | 180s | MANUAL | Created forum with sidebar items |
| 620 | reddit | 6 | 146s | MANUAL | Posted relationship advice |
| 677 | shopping_admin | 3 | 129s | MANUAL | Found processing orders |
| 796 | shopping | 2 | 90s | MANUAL | Address change not supported |

**Result: 3/10 confirmed correct, 0 errors, 6 manual verification, 1 wrong**

### Impact of JSON parse fix (same 10 tasks)

| Metric | Before fix | After fix |
|--------|-----------|-----------|
| Correct | 1 | 3 |
| Wrong | 1 | 1 |
| Errors | 8 | 0 |
| Manual check | 0 | 6 |
| Tasks completed | 2/10 | **10/10** |

## Known Remaining Issues

1. **GitLab container not running** — blocks ~100/812 tasks. Container needs to be started separately.
2. **Browser context crash** — "Execution context was destroyed" on heavy Magento pages. Race condition in CUGA's BrowserEnvGymAsync.
3. **Approaches JSON schema** — LLM returns bare list instead of expected object on some shopping_admin tasks.
