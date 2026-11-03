"""Lifecycle hooks for CUGA agents (experimental).

Subclass ``AgentHooks``, override the hooks you need and pass instances to ``CugaAgent(hooks=[...])``. A hook may
observe, add text to the system prompt, block a code block or a tool call, or send the agent back before it replies.

| Hook               | Called                                   | Return value                                    |
|--------------------|------------------------------------------|-------------------------------------------------|
| session_start      | first message of a thread                | observe                                         |
| on_user_message    | each user message                        | answer → returned without running the agent     |
| before_llm_call    | each model call of the agent loop        | text appended to the system prompt (this call)  |
| after_llm_call     | each model output                        | observe                                         |
| after_plan         | before a CodeAct block runs              | message → the block is not run; model sees it   |
| before_tool_call   | before each tool call                    | message → the call fails with HookBlocked       |
| after_tool_call    | after each tool call                     | message → attached to the tool result           |
| before_reply       | before invoke() returns an answer        | message → new turn; answer → replaces the reply |
| on_task_end        | after the final answer                   | observe                                         |

Each method may return ``None``, a string (the message), or a ``HookResult`` with a message, an ``answer``
(on_user_message, before_reply) and details for observers (e.g. which rule decided). ``HookRegistry`` calls all hooks and emits a ``HookEvent`` per hook call;
add a listener with ``agent.hooks.add_listener(fn)`` to stream events.
"""

from .registry import (
    HOOK_NAMES,
    AgentHooks,
    HookBlocked,
    HookEvent,
    HookRegistry,
    HookResult,
    HooksCallback,
    wrap_provider_with_hooks,
    wrap_tools_with_hooks,
)

__all__ = [
    "HOOK_NAMES",
    "AgentHooks",
    "HookBlocked",
    "HookEvent",
    "HookRegistry",
    "HookResult",
    "HooksCallback",
    "wrap_provider_with_hooks",
    "wrap_tools_with_hooks",
]
