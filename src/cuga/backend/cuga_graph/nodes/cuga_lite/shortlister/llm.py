"""The LLM shortlister — today's behavior, behind the strategy interface.

This is the default and is deliberately a faithful port: same prompt template,
same ``ShortListerOutputLite`` schema, same model, same composed query string.
Callers already drop names that match no tool, so nothing is filtered here.
"""

from __future__ import annotations

from typing import ClassVar, Optional

from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import (
    ShortlistCandidate,
    ShortlistRequest,
    ShortlistResult,
)


def compose_query(query: str, task_context: Optional[str]) -> str:
    """Join a step query with the task context exactly as CugaLite always has.

    Kept identical to ``helpers/find_tools._compose_find_tools_shortlister_query``
    so the prompt text does not change now that the two parts travel separately.
    """
    q = (query or "").strip()
    init = (task_context or "").strip()
    if not init:
        return q
    return f"Query: {q}\nTask context (initial user message): {init}"


def top_k_instruction(top_k: int) -> str:
    """The instruction the bind-time cap has always injected."""
    return (
        f"Return the {top_k} most relevant tools (or fewer if not enough are relevant), "
        "ordered best-first by relevance. Do not exceed this count."
    )


class LLMShortlister:
    """Ranks tools by asking the model, using the shortlister prompt."""

    name: ClassVar[str] = "llm"

    async def shortlist(self, request: ShortlistRequest) -> ShortlistResult:
        from cuga.backend.llm.models import LLMManager
        from cuga.backend.cuga_graph.nodes.api.shortlister_agent.prompts.load_prompt import (
            ShortListerOutputLite,
        )
        from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
        from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils
        from cuga.backend.llm.utils.helpers import create_chat_prompt_from_templates

        if not request.tools:
            return ShortlistResult()

        if request.instructions is not None:
            instructions = request.instructions
        elif request.top_k:
            instructions = top_k_instruction(request.top_k)
        else:
            instructions = ""

        prompt = create_chat_prompt_from_templates(
            system_path='../prompts/shortlister/system.jinja2',
            message_templates=[
                (
                    'human',
                    """
                Current Apps: {all_apps}
                Current Available Tools: {all_tools}
                """,
                ),
                ('ai', 'Sure, now give me the intent'),
                ('human', '{input}'),
            ],
        )
        tools_as_dict, apps_as_dict = PromptUtils._build_shortlister_payload(request.tools, request.apps)

        model = request.llm or LLMManager().get_model(settings.agent.code.model)
        chain = BaseAgent.get_chain(prompt, model, ShortListerOutputLite)

        # Hallucinated tool names are an LLM failure mode, so bounded retries
        # live with the LLM strategy. A cosine ranker draws names from the
        # candidate list and cannot invent one, so it neither needs nor pays
        # for this. Implementation is PromptUtils' (#546/#549), reused as-is.
        details, invalid_names = await PromptUtils._ainvoke_shortlister_with_name_validation(
            chain=chain,
            query=compose_query(request.query, request.task_context),
            apps_as_dict=apps_as_dict,
            tools_as_dict=tools_as_dict,
            base_instructions=instructions,
            valid_names={t.name for t in request.tools},
            run_config=request.run_config,
        )

        candidates = [
            ShortlistCandidate(
                name=getattr(detail, "name", ""),
                score=float(getattr(detail, "relevance_score", 0.0) or 0.0),
                reasoning=str(getattr(detail, "reasoning", "") or ""),
            )
            for detail in details
        ]
        note = PromptUtils._format_filtered_tool_names_note(invalid_names)
        return ShortlistResult(candidates=candidates, notes=[note] if note else [])
