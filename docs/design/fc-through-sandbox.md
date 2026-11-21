# Function calling through the sandbox

Status: merged in #777 (2026-10-18). Follow-ups are tracked in #976.

## Decision

`cuga_lite_execution_mode = "function_calling"` keeps native function calling on the
model side (tools via `bind_tools`, `tool_calls` in, one `ToolMessage` per call id out)
and executes the calls through the existing CodeAct sandbox instead of a separate
`tool_exec` runner. A native turn is translated into a short internal Python block,
the sandbox runs it, and the block's variables are read back into the replies.

Why: every CUGA guarantee already lives at the sandbox step — tool approval, the
pre-execute VERIFY gate, variables, output trimming, the block timeout, budgets, the
tracker, recovery. A second executor had to re-earn each one; after three review
rounds of #777 five gaps remained (approval refused to start, no recovery, no
variables, no observed shapes, no todos). One pipeline closes them at once.

## Shape

- `adapter/fc_actions.py` — `plan_tool_calls` (calls → block + plan) and
  `replies_from_execution` (variables → `ToolMessage`s). Rules that keep the tool side
  identical to a direct call: identifier names are called as themselves (approval and
  VERIFY see the real name), other names go through an injected `_fc_tools` lookup,
  arguments are `**{...}` of JSON values only, one `try` per call, results kept through
  `_fc_keep` so nothing is dropped, non-executable calls answered at planning time.
- `graph_adapter.execute_call_model_fc` — routes to the sandbox with the block as
  `script` and the plan under `cuga_lite_metadata["fc_pending"]`; runs the CodeAct
  approval check on the block first.
- `sandbox_node` — when a plan is pending: local executor, listing variables kept,
  replies instead of the "Execution output:" message at every exit (VERIFY revise,
  normal, failure), plan cleared on every exit, reflection skipped.
- Feature off: the branches above are gated on a pending plan, which a CodeAct turn
  never writes, and every new parameter defaults to the old behavior. The golden in
  `cuga_lite/tests/test_feature_off_snapshot.py` pins one CodeAct run, recorded
  before this change: final state, messages (type, content, tool calls) and every
  outbound turn. Since #976 it also checks that no tools are bound and pins the whole
  system prompt (`snapshots/codeact_feature_off_system_prompt.txt`). That file was
  recorded after this change merged, so it guards later changes; it cannot show this
  change left the prompt alone. It covers that scenario only (one tool; no apps,
  `find_tools`, todos, policies, few-shots, memory, agent spawning, forced autonomous
  mode or tool binding), not every CodeAct path. CodeAct did change in
  three ways; see "CodeAct changes that came with function calling" in
  `docs/readme/configuration-guide.md`.

## Deliberate differences from the direct runner

- Timeout is per block (whole batch), as for CodeAct; finished results are kept.
- Results persist as variables (`tool_result_<call id>`), visible to later turns and
  in `InvokeResult.variables`.
- Approval follows CodeAct's check (text match on the block); the former
  refuse-to-start guard is gone. Since #981 that check fails closed in both modes:
  when the policy check cannot run, the run pauses for approval.

## Also in this round (Vakra readiness)

- Provider-safe aliases (#882, merged into this branch): the translator resolves an
  alias to the real name before planning, so the block, approval and VERIFY see the
  real tool while the reply keeps the alias the provider knows. 6 of 10 Vakra test
  domains have a tool name over 64 characters.
- `find_tools` is bound in function-calling mode whenever prepare enabled it, since the
  FC prompt advertises it; its listing is replied, never kept as a variable.
- The FC model call has the recovery CodeAct has, in native terms: a Groq
  `failed_generation` becomes a real call, a retryable rejection is retried once, a
  second failure ends the turn with a clear error, anything else propagates.

## Follow-ups

Tracked in #976.

1. A `run_python` tool backed by the same sandbox for data-heavy steps.
2. Per-thread dynamic binding: remember the shortlist and discovered tools, query on
   the current task rather than the first message.
3. Function-calling wording for VERIFY, reflection and the empty-reply nudge.
