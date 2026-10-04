# large_output_probe

A manual test for `DYNACONF_LARGE_OUTPUT__ENABLED` does the large-output subagent-digestion path actually retrieve a fact buried
in an oversized *unstructured* tool result, instead of the model just seeing
truncated/garbled text?

Not run in CI — it makes real model calls. `judge.py`/`utils.py`/`prompt.py`
are a vendored copy of `cuga-eval`'s `CorrectnessJudge` (used as an
LLM-as-judge fallback below), copied rather than imported across repos.

## Function-calling / hybrid agent

The probe drives the function-calling agent in hybrid mode by default
(`LARGE_OUTPUT_PROBE_EXECUTION_MODE=hybrid|function_calling|codeact`). There,
`large_output` saves an oversized tool result to
`$TMPDIR/cuga_large_output/<thread>/<tool_call_id>.txt` and a search subagent
greps/reads it, with a check round when something is missing
(`cuga_lite/helpers/large_output.py`). The sections further down describe the
earlier CodeAct dispatcher and its ablations, which this branch does not have.

Function-calling and hybrid runs stop before any tool when an enabled
tool-approval policy exists, so isolate policies when running locally:

```bash
DYNACONF_POLICY__CUGA_FOLDER=/tmp/empty_cuga \
DYNACONF_POLICY__POLICY_DB_PATH=/tmp/probe_policies/cuga.db \
DYNACONF_LARGE_OUTPUT__ENABLED=true \
uv run python tests/e2e/large_output_probe/run_probe.py
```

Measured 2026-10-04, `gpt-oss-120b`, hybrid, fresh mode:

| Fixture | `enabled=true` | `enabled=false` (batch truncation) |
|---|---|---|
| easy (145K chars) | 5/5, 85K tokens | 5/5, 470K tokens |
| hard (1.7M chars, 2 decoys/needle) | 5/5, 81K tokens | 4/5, 914K tokens |
| extreme (8.6M chars, 4 decoys/needle) | 5/5, 112K tokens | not run |

## Why an unstructured-text fixture

`large_output_dispatch.py` branches on the *shape* of the oversized tool
result: dict/list gets a cheap "guidance" message (index the variable
yourself), and only a plain unstructured **string** triggers the interesting
path — an auto-spawned subagent that digests the blob and returns a short
answer. Most real tool outputs (API/DB responses) return structured JSON
records, so they rarely reach the subagent path in practice. This probe
builds a standalone fixture that does trigger it: a single tool,
`get_archive_document`, that returns one large block of unstructured prose
(real public-domain novels) with 5 synthetic, unambiguous facts ("[ARCHIVE
NOTE ####]") spliced in at different depths. Each fact has its own question,
so answering it correctly requires having actually found that specific
needle, not just returned a plausible generic answer.

## The data

Three fixture tiers, all built by `build_haystack.py <tier>` from Project
Gutenberg public-domain novels (boilerplate stripped) plus the same 5
synthetic needle facts/questions in every tier — so results are comparable
across tiers. Regenerating any of them re-fetches source text over HTTPS; all
three are checked into `data/` so the probe runs without a network call.

| Tier | File | Chars | Approx. tokens | Source text | Decoys/needle | Env var |
|---|---|---|---|---|---|---|
| easy | `data/haystack.txt` | 145,198 | ~36K | 1 novel (*Alice's Adventures in Wonderland*) | 0 | default |
| hard | `data/haystack_hard.txt` | 1,711,812 | ~428K | 3 novels | 2 | `LARGE_OUTPUT_PROBE_FIXTURE=hard` |
| extreme | `data/haystack_extreme.txt` | 8,468,059 | ~2.1M | 7 novels, disjoint from hard's set | 4 | `LARGE_OUTPUT_PROBE_FIXTURE=extreme` |

extreme's ~2.1M tokens is past every mainstream model's context window on its
own — "paste the whole thing into context" is categorically impossible, not
just expensive, so it's the clearest test of whether retrieval genuinely
works rather than truncation getting lucky.

Each needle is a one-sentence fact (e.g. "the backup generator for Wing C was
serviced on the 14th of September by Priya Nandakumar") with a matching
question and an AND-of-OR list of required substrings for grading. On hard
and extreme, every needle is paired with N near-duplicate decoys elsewhere in
the document — same sentence template, wrong specifics (e.g. real says "Wing
C", a decoy says "Wing A") — so a search that stops at the first hit on a
shared keyword lands on the wrong note; getting the right answer requires
matching the specific identifying detail the question asks about, not just
the topic. Every decoy's identifying values are unique in the document — `build_haystack.py`
enforces this with a build-time collision check — so no question is answerable
from more than one place in the text.

## Running it

Needs a working model/API env (same as this repo's other real-LLM e2e tests,
e.g. `tests/e2e/skills/`) plus:

```bash
DYNACONF_LARGE_OUTPUT__ENABLED=true \
uv run python tests/e2e/large_output_probe/run_probe.py
```

`run_probe.py` refuses to run (raises before making any model calls) if
`large_output.enabled` or `advanced_features.lite_mode` aren't actually on,
since without both the probe would silently fall back to the legacy
truncation path and the result wouldn't say anything about this feature.

- Add `LARGE_OUTPUT_PROBE_FIXTURE=hard` or `=extreme` to run a bigger tier
  (see table above).
- To check the `false` baseline (legacy truncation, expect failures/degraded
  answers), rerun with `DYNACONF_LARGE_OUTPUT__ENABLED` unset or `false`.
- Add `LARGE_OUTPUT_PROBE_MODE=convo` to run in conversational mode — see
  below.
- Add `DYNACONF_LARGE_OUTPUT__UNSTRUCTURED_ACTION=subagent_file` to run the
  file-on-disk ablation: instead of `search_large_output`/`read_large_output`
  closing over the blob in memory (default `unstructured_action="subagent"`),
  the dispatcher writes it to a temp file first and hands the digesting
  subagent the same two tool names backed by real `grep`/`awk` over that path
  — the shape Claude Code's own Grep/Read tools use for an oversized file on
  disk. The subagent still never sees the path itself, only the bound tool
  functions, so there's nothing for it to mistype. See
  `large_output_tools.build_file_tools`.

## Two run modes, and what each isolates

**`fresh` (default).** A brand-new `CugaAgent` per question — new agent
instance, new auto-generated `thread_id`, no shared checkpointer — so a fact
can never leak from one question into another via chat history. This is the
cleanest test of "can the retrieval path find this one needle," with no
conversational confound.

**`convo` (`LARGE_OUTPUT_PROBE_MODE=convo`).** One `CugaAgent`, one thread,
all 5 of a fixture's questions asked as sequential turns — the shape a real
multi-turn user session has. This tests something fresh mode can't: does
retrieval still work, and what does it cost, once prior questions, prior
digested answers, and prior tool-output previews are sitting in growing chat
history. It's a real, currently open gap (see Results below) — legacy
truncation only "passes" by accident here (a frozen preview from turn 1
happening to cover a later question), and the digesting-subagent path
degrades because the parent agent has no equivalent search tool for turns
after the first.

## What "pass" means

Per question, the script prints the raw tool-output length (confirming it
was actually over threshold), the agent's final answer, and whether that
answer contains every required fact (`required_substrings` in
`questions*.json`, an AND-of-OR list, each entry matched as a whole word/
token — not a bare substring — after normalizing case, unicode, and dash
variants). If the substring check fails, it falls back to an LLM-as-judge
call (`CorrectnessJudge` in `judge.py`) before calling the question a FAIL —
this catches answers that got the fact right but phrased it differently than
the fixture text (e.g. "15 Sep" vs. "15 September"). The judge looks for
`API_KEY` (Groq) first, falling back to `OPENAI_API_KEY`/`OPENAI_BASE_URL`/
`MODEL_NAME`.

This is a correctness probe you read by eye, not a CI gate — it makes real
model calls and isn't wired into any automated scoring pipeline.

Each question's `[PASS]`/`[FAIL]` line is followed by a token line (via
`InvokeResult.receipt`, which the script turns on for itself) — includes the
digesting subagent's own tokens, not just the parent agent's, so the numbers
below reflect the true end-to-end cost.

## Files

- `build_haystack.py [easy|hard|extreme]` — generates a tier's
  `data/haystack*.txt` + `data/questions*.json` (default tier: easy).
- `run_probe.py` — runs the probe; see "Running it" above.
- `judge.py`, `utils.py`, `prompt.py` — vendored LLM-as-judge fallback.

## Results: v5 vs. legacy truncation

"v5" is the current state of this branch's `large_output` feature work
(digesting subagent with `search_large_output`/`read_large_output` tools,
minimal system prompt, no duplicate raw-preview dump, and a sandbox
security-validator fix — see git log on this branch for the full history of
how it got here). "legacy" is the feature off entirely
(`DYNACONF_LARGE_OUTPUT__ENABLED=false`, plain truncation at
`execution_output_max_length`, 70,000 chars) — the floor this is measured
against, not a version of the feature itself.

**Fresh mode** (one question per agent — no conversational confound), model
`aws/gpt-oss-120b`:

| | Easy | Hard | Extreme |
|---|---|---|---|
| **legacy** | 2/5 — 264,011 tok | 3/5 — 386,805 tok | 0/5 — 269,903 tok |
| **v5** | 5/5 — ~133K tok | 5/5 — ~186K tok | 5/5 — ~162K tok |

At hard/extreme scale, legacy isn't just worse, it's non-functional: no
needle sits inside the 70,000-char truncation window, so it never has a
chance regardless of position. Its failures split between honest refusals
("I'm unable to locate...") and confident fabrications with no connection to
the real text — both score as 0 either way. v5 finds the right needle by
searching for the question's own distinctive term rather than reading the
whole document, at roughly half legacy's token cost or less.

**Conversational mode** (`LARGE_OUTPUT_PROBE_MODE=convo`, all 5 questions in
one thread), same model, all three tiers combined (15 questions):

| | Success rate | Total tokens |
|---|---|---|
| **legacy — convo** | 4/15 (27%) | 914,343 |
| **v5 — convo** | ~14/15 (93%) | ~485,299 |

v5 still beats legacy on both success rate and cost in conversational mode,
but by a smaller margin than fresh mode — legacy's convo "passes" are
accidental (a truncated preview frozen from turn 1 happening to cover a
later question, not retrieval), while v5's own convo-mode cost is inflated
because the parent agent has no search tool of its own for turns after the
first that already triggered digestion. **Known open gap:** at extreme's
4-decoys-per-needle density, both fresh and convo mode occasionally commit to
a wrong decoy's answer rather than the real needle — cheaply and quickly, not
via the older flailing-and-hallucinating pattern, but still a wrong answer.
The identified fix, not yet implemented: expose the digesting subagent's own
`search_large_output`/`read_large_output` tools directly to the parent agent
for later turns, instead of relying on a prompt note to reimplement search by
hand.
