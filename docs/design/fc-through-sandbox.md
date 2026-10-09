# Function calling through the sandbox

Status: implemented on the `feature/native-function-calling-mode` branch (PR #777), 2026-10-09.

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
- Feature off: no branch above is reachable (gated on a pending plan a CodeAct turn
  never writes); every new parameter defaults to the old behavior. Golden:
  `tests/snapshots/codeact_feature_off.json`.

## Deliberate differences from the direct runner

- Timeout is per block (whole batch), as for CodeAct; finished results are kept.
- Results persist as variables (`tool_result_<call id>`), visible to later turns and
  in `InvokeResult.variables`.
- Approval follows CodeAct's check (text match on the block), including its
  fail-open on infrastructure errors; the former refuse-to-start guard is gone.

## Follow-ups

1. Bind `find_tools` in function-calling mode (it is stripped unless
   `cuga_lite_bind_tools_include_find_tools` is set, while the prompt advertises it).
2. A `run_python` tool backed by the same sandbox for data-heavy steps.
3. Per-thread dynamic binding: remember the shortlist and discovered tools, query on
   the current task rather than the first message.
4. Function-calling wording for VERIFY, reflection and the empty-reply nudge.
