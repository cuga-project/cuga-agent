"""Pure function: synthesize the CodeAct turns for a slash-dispatched skill.

When a user types ``/<skill> args`` we don't want the model to re-decide
whether to load the skill — we want it to see "the skill is already loaded,
now act on it." CugaLite is CodeAct: the planner emits Python code blocks in
``AIMessage.content``; the sandbox runs them and appends the output as a
``HumanMessage(content="Execution output:\\n{output}")``
(see ``cuga_agent_core/graph/graph_nodes.py:execution_output_text`` and
``cuga_lite/adapter/sandbox_node.py``). Tool calls and ``ToolMessage`` are not
part of this conversation protocol — ``shared_nodes.py:create_call_model_node``
intentionally has only ``is_human`` / ``is_ai`` branches.

So our synthesis forges the three turns CugaLite would have produced
organically if the planner had decided to ``await load_skill(...)`` from a
Python code block, so the planner sees the skill body in its very next
invocation with no extra LLM round-trip:

    1. HumanMessage(raw_input)            — what the user typed verbatim
    2. AIMessage(content="```python …```") — the forged code-block turn
    3. HumanMessage("Execution output:\\n" + wrapped_body)
                                          — the executor-style return

The args are already in ``raw_input`` AND substituted into ``wrapped_body``
via ``$ARGUMENTS`` in SKILL.md, so no trailing args turn is needed.

The AIMessage carries ``additional_kwargs={'invoked_via': 'slash', ...}`` so
the trajectory / Langfuse layer can tag the synthetic turn (these are stripped
by ``shared_nodes.py`` before going to the model, so they don't affect the
wire payload).

This module is a pure function with no graph, registry, or persistence
dependencies, so the synthesis logic can be unit-tested in isolation.
"""

from __future__ import annotations

from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    execution_output_text,
)


def synthesize_skill_invocation(
    *,
    raw_input: str,
    raw_args: str,
    resolved_name: str,
    wrapped_body: str,
) -> List[BaseMessage]:
    """Return the three-message CodeAct sequence representing a slash skill invocation."""
    code_block = f"```python\nresult = await load_skill({resolved_name!r}, {raw_args!r})\nprint(result)\n```"
    return [
        HumanMessage(content=raw_input),
        AIMessage(
            content=code_block,
            additional_kwargs={
                "invoked_via": "slash",
                "raw_input": raw_input,
                "resolved_name": resolved_name,
            },
        ),
        HumanMessage(content=execution_output_text(wrapped_body)),
    ]
