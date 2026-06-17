# Skills E2E Tests

End-to-end tests for the skills component. No real LLM required for Tier 1 and Tier 2.

## Test tiers

| Tier | What it tests | LLM? |
|------|---------------|------|
| 1 | Component APIs directly (discovery, registry, tool creation) | No |
| 2 | Full `CugaLiteGraph` with `CaptureChatModel` — asserts on what reached the model | No |
| 3 | Full graph with the project's real configured LLM (`@pytest.mark.e2e`) | Yes |

## Files

| File | Coverage |
|------|----------|
| `test_skills_e2e.py` | Tier 1+2: discovery, registry, tool creation, graph wiring |
| `test_skills_llm_e2e.py` | Tier 3 via raw graph |
| `test_skills_sdk_e2e.py` | Tier 1 SDK config + Tier 3 via `CugaAgent` |
| `test_skills_real_e2e.py` | Tier 3 against real public skills (Vercel) |
| `test_skills_presentation_e2e.py` | Tier 3 pptx demo — produces a real `.pptx` file |
| `conftest.py` | Fixtures: `CaptureChatModel`, `write_skill`, `MinimalToolProvider`, `real_llm` |
| `skills_artifact.py` | Centralised skill definitions reused across test files |

## Running

```bash
# Tier 1 + 2 (fast, no LLM)
uv run pytest tests/e2e/skills/test_skills_e2e.py -v

# All Tier 3 (real LLM required)
uv run pytest tests/e2e/skills/ -m e2e -v -s
```
