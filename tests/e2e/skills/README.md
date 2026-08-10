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
| `test_palette_skill_invocation.py` | Tier 2: the palette skill is discovered, described, and reachable |
| `test_palette_deck_e2e.py` | Tier 3: the agent drives Palette to a real `.pptx` on disk |
| `conftest.py` | Fixtures: `CaptureChatModel`, `write_skill`, `MinimalToolProvider`, `real_llm` |
| `skills_artifact.py` | Centralised skill definitions reused across test files |

## Running

```bash
# Tier 1 + 2 (fast, no LLM)
uv run pytest tests/e2e/skills/test_skills_e2e.py -v

# All Tier 3 (real LLM required)
uv run pytest tests/e2e/skills/ -m e2e -v -s
```

## The palette deck tests

These are the slowest thing in the repo and the only ones that assert on a
binary artifact. They need an LLM plus two things, each checked and skipped on
rather than failing obscurely:

```bash
make skill-install CUGA=$PWD        # run in the project-palette checkout
export PALETTE_HOME=<palette> RITS_API_KEY=<key>
uv run pytest tests/e2e/skills/test_palette_deck_e2e.py -m e2e -v -s
```

Set `PALETTE_DECK_OUT` to keep the decks where you can open them:

```bash
PALETTE_DECK_OUT=~/Desktop/palette-decks uv run pytest ... -m e2e -s
```

Two routes in, matching how people arrive at a deck:

| Test | Prompt shape | Why it exists |
|---|---|---|
| `test_deck_from_a_bare_request` | *"Build me a deck about …"* | the regression test — see below |
| `test_deck_from_pasted_material` | notes pasted into the chat | the `--context` path, where a host has no file upload |

**Both run two turns**, because the skill's confirmation gate means a deck
cannot happen in one: turn one produces a plan and asks, turn two approves it.
A single-turn version of this test could never pass, and for a while measured
nothing but the harness. `_converse()` threads the accumulated history through
explicitly — `chat_messages` has no `add_messages` reducer, so a second
`ainvoke` replaces the history rather than appending to it.

The prompts are deliberately unscaffolded: they say what a person says and
leave the rest to the agent, rather than naming the commands to run.

It asserts on the artifact rather than the transcript, because a long run gets
context-summarised and that erases the very lines a transcript check would look
for. So: the `.pptx` must be a valid OOXML package with the expected slide
parts, every slide must have rendered to a PNG, and the slide XML must carry
IBM Plex — Palette's renderer forces that typeface, so a deck hand-written with
pptxgenjs defaults could not have it.

It also asserts the plan was shown before the deck was built, since skipping
the confirmation gate is a real failure that still produces a valid `.pptx` —
one the user never agreed to.
