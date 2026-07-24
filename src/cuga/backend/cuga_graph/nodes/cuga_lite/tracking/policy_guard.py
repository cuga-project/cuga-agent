"""Retriever-usage policy enforcement ("ToolGuard-lite").

Hand-written, deterministic runtime enforcement of the one dominant M3
capability_4 policy shape (retriever-only-required vs. retriever-forbidden),
built as a narrow, fast alternative to the full ToolGuardRuntime/toolguard
buildtime pipeline (which needs an LLM call per domain just to produce
RuntimeDomain files - impractical under the deadline this was built for).
See cuga-eval's docs/m3-cap4-policy-investigation-20260723/README.md
section 6 for the full design rationale.

Deliberately independent of TaskToolCallHistory (tracker.py): built to the
same proven "contextvar carries only a per-task key, the real state lives in
a module-level dict" shape (see this repo's own
docs/issues/task-tool-call-history-contextvar-isolation.md for why a plain
contextvar holding the growing/real value does not survive across LangGraph
node dispatches - every dispatch gets its own copy_context()), but as
separate code, so this mechanism's fate isn't tied to that prototype's.
"""

from __future__ import annotations

import contextvars
from typing import Dict, Optional

_policy_task_key_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "retriever_policy_task_key", default=None
)

# Module-level, keyed by task_key (thread_id) - NOT a contextvar, so it
# survives across LangGraph node dispatches (a contextvar mutation made
# inside one dispatch is invisible to another; see module docstring).
_task_policies: Dict[str, str] = {}


class RetrieverPolicyGuard:
    """Blocks tool calls that violate this task's retriever-usage policy.

    - ``register``/``unregister``: called from outside the graph (cuga-eval's
      sdk_eval_helpers.py, once thread_id is known, before the graph runs) to
      record/clear this task's raw policy text.
    - ``bind``: called unconditionally at the top of every
      LocalExecutor.execute() dispatch with the task's thread_id - rebinds
      the contextvar fresh every time rather than relying on it surviving
      from a previous dispatch.
    - ``check_call``: called at each tool-invocation site; looks up the
      current task_key from the contextvar, looks up that task's policy text,
      and returns a block-reason string if this call would violate it, else
      None.
    """

    @staticmethod
    def register(task_key: str, policy_text: Optional[str]) -> None:
        if policy_text:
            _task_policies[task_key] = policy_text

    @staticmethod
    def unregister(task_key: str) -> None:
        _task_policies.pop(task_key, None)

    @staticmethod
    def bind(task_key: Optional[str]) -> None:
        _policy_task_key_context.set(task_key)

    @staticmethod
    def check_call(tool_name: str) -> Optional[str]:
        task_key = _policy_task_key_context.get()
        if not task_key:
            return None
        policy_text = _task_policies.get(task_key)
        if not policy_text:
            return None

        text = policy_text.lower()
        # Same two keyword patterns benchmarks/m3/evaluator/policy_judge.py
        # (the immutable official judge, cuga-eval repo) checks - enforcing
        # against a different criterion than what the judge actually scores
        # would be pointless.
        forbids_other_tools = (
            "do not use any other type of tool" in text or "do not use other types of tool" in text
        )
        forbids_retrievers = "do not use document retriever" in text
        is_retriever_tool = "query_" in tool_name

        if forbids_other_tools and not is_retriever_tool:
            return (
                "Blocked by policy: this task's instructions require using ONLY "
                "document-retriever tools (tool names containing 'query_'). "
                f"'{tool_name}' is not a retriever tool and was not called. "
                "Use a document-retriever tool instead."
            )
        if forbids_retrievers and is_retriever_tool:
            return (
                "Blocked by policy: this task's instructions say NOT to use "
                f"document-retriever tools. '{tool_name}' is a document-retriever "
                "tool and was not called. Use a different type of tool instead."
            )
        return None
