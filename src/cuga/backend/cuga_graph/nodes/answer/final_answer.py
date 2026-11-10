import json
import asyncio
from typing import Literal, Dict, Callable, Optional

from langchain_core.messages import AIMessage
from langgraph.types import Command
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.answer.answer_function import apply_answer_function
from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import FinalAnswerAgent
from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.prompts.load_prompt import FinalAnswerOutput
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.config import settings
from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames, MessagePrefixes

tracker = ActivityTracker()


class HumanInTheLoopHandler:
    """Simple handler for human-in-the-loop interactions"""

    def __init__(self):
        self._action_handlers: Dict[str, Callable] = {}

    def handle_human_response(self, state: AgentState, node_name: str) -> Command:
        """Handle any human response based on action_id"""
        action_id = state.hitl_response.action_id

        if action_id in self._action_handlers:
            return self._action_handlers[action_id](state, node_name)

        # Default fallback — terminal path to END. Citation resolution only:
        # this continuation resumes an answer that _generate_final_answer
        # already finalized before routing to SUGGEST_HUMAN_ACTIONS, so
        # re-running the answer function here would apply it twice
        # (single-application contract; same reasoning as the supervisor
        # forward branch). Citation resolution is idempotent by design.
        FinalAnswerNode.apply_citation_resolution(state)
        state.final_answer_finalized = True
        return Command(update=state.model_dump(), goto=NodeNames.END)

    def add_action_handler(self, action_id: str, handler: Callable):
        """Add a custom action handler"""
        self._action_handlers[action_id] = handler


_checker_llm = None
# A slow checker (a hung model call, the NLI model's first download) never holds up the answer.
_CITATION_CHECK_TIMEOUT_S = 30.0


def _citation_check_llm():
    """The agent's model for the ``llm`` citation checker, created once."""
    global _checker_llm
    if _checker_llm is None:
        from cuga.backend.knowledge_llm_bridge import CugaChatGenerator

        _checker_llm = CugaChatGenerator()
    return _checker_llm


class FinalAnswerNode(BaseNode):
    def __init__(
        self,
        final_answer_agent: FinalAnswerAgent,
        answer_function: Optional[Callable[[str], str]] = None,
    ):
        super().__init__()
        self.final_answer_agent = final_answer_agent
        self.hitl_handler = HumanInTheLoopHandler()
        agent = self.final_answer_agent
        name = self.final_answer_agent.name
        hitl_handler = self.hitl_handler
        if answer_function is not None:
            logger.info(
                f"final answer: function '{getattr(answer_function, '__name__', answer_function)}' active"
            )

        async def node(state: AgentState):
            return await FinalAnswerNode.node_handler(
                state, agent=agent, name=name, hitl_handler=hitl_handler, answer_function=answer_function
            )

        self.node = node

    @staticmethod
    async def finalize_answer(state, answer_function: Optional[Callable[[str], str]] = None) -> None:
        """Ordered post-processing of the delivered answer — the single seam
        every terminal branch runs before END:

        1. harmony-token strip — so the function receives clean text
           (the same strip inside citation resolution then no-ops)
        2. answer function — opt-in deterministic ``(str) -> str``
           (``[final_answer].function`` or an SDK-injected callable)
        3. citation check (``knowledge.citation_check``; off by default)
        4. citation resolution, with each source's ``support`` verdict when checked

        The function runs before citation resolution so it never sees
        resolved [n] chips, and its output still gets sanitized/resolved.
        It is applied once per delivered answer: the supervisor forward
        branch deliberately skips it (the sub-agent already applied it).
        """
        text = state.final_answer or ""
        if "<|" in text:
            state.final_answer = strip_harmony_tokens(text)
        apply_answer_function(state, answer_function)
        verdicts = await FinalAnswerNode.check_citation_support(state)
        FinalAnswerNode.apply_citation_resolution(state, verdicts)
        state.final_answer_finalized = True

    @staticmethod
    async def check_citation_support(state) -> dict[str, str]:
        """Verdict per cite_id (``supported`` / ``contradicted`` / ``not_stated``) from the
        configured checker; ``{}`` when nothing is cited or the check fails.

        With the check off, only citations reused from an earlier turn are checked, with the
        agent's model: they are kept only when supported, and a follow-up answered from the
        previous turn's results would otherwise lose them all. An answer citing only this
        turn's sources makes no extra call."""
        try:
            from cuga.backend.knowledge.sources import (
                effective_citation_check,
                effective_citations_enabled,
                get_ledger,
                has_citation_markers,
            )

            mode = effective_citation_check()
            text = state.final_answer or ""
            if not state.thread_id or not has_citation_markers(text):
                return {}
            if not effective_citations_enabled(state.thread_id):
                return {}
            ledger = get_ledger(state.thread_id, create=False)
            if ledger is None:
                return {}

            reused_only = mode == "off"
            if reused_only:
                from cuga.backend.knowledge.citation_check import claims_by_marker

                cited = {cid for _, ids in claims_by_marker(text) for cid in ids}
                if not any(ledger.get(c) is not None and not ledger.retrieved_this_turn(c) for c in cited):
                    return {}
                mode = "llm"

            def source_text(cite_id: str) -> Optional[str]:
                # Any id still in the ledger, earlier turns included: a supported
                # earlier-turn citation is kept (apply_citation_resolution).
                record = ledger.get(cite_id)
                if record is None or (reused_only and ledger.retrieved_this_turn(cite_id)):
                    return None
                return record.text

            from cuga.backend.knowledge.citation_check import check_citations

            generate = _citation_check_llm().generate if mode == "llm" else None
            verdicts = await asyncio.wait_for(
                check_citations(text, source_text, mode, generate=generate), _CITATION_CHECK_TIMEOUT_S
            )
            if verdicts:
                logger.info("citation check ({}): {}", mode, verdicts)
            return verdicts
        except (TimeoutError, asyncio.TimeoutError):  # distinct classes before Python 3.11
            logger.warning(
                "citation check ({}) took over {}s; answer delivered without verdicts",
                mode,
                _CITATION_CHECK_TIMEOUT_S,
            )
            return {}
        except Exception:
            logger.exception("citation check failed; answer delivered without verdicts")
            return {}

    @staticmethod
    def apply_citation_resolution(state, verdicts: Optional[dict] = None) -> None:
        """Rewrite [sN] ledger markers in final_answer into per-message [n]
        display numbers and attach self-contained source snapshots.

        Must run AFTER variable placeholder replacement and AFTER output
        formatters, and must never break answer delivery — any failure
        leaves the text as-is.

        Idempotent: a second call (e.g. the supervisor callback forwarding an
        already-resolved last_planner_answer) sees resolved [n] markers instead
        of [sN] and keeps the sources the first call produced, rather than
        clearing them.
        """
        try:
            import re as _re

            from cuga.backend.knowledge.sources import (
                effective_citations_enabled,
                get_ledger,
                has_citation_markers,
                resolve_citations,
            )

            text = state.final_answer or ""
            # Defence in depth. The decode boundary (normalize_response) is what
            # actually keeps framing out of delivered text; this catches an
            # answer assembled from some other source, and costs one substring
            # check when there is nothing to do.
            if "<|" in text:
                text = strip_harmony_tokens(text)
                state.final_answer = text
            if not has_citation_markers(text):
                # No [sN] markers to resolve. Two cases land here: a genuinely
                # uncited answer, and an ALREADY-resolved one (the supervisor
                # path re-enters with last_planner_answer that already carries
                # [n] chips + sources). Keep sources only when the current text
                # still references them via resolved [n] markers; otherwise clear
                # so stale prior sources never ride an uncited answer.
                already_resolved = bool(state.sources) and _re.search(r"\[\d+\]", text) is not None
                if not already_resolved:
                    state.sources = []
                return
            if state.thread_id and effective_citations_enabled(state.thread_id):
                ledger = get_ledger(state.thread_id, create=True)
            else:
                # Feature off (agent flag or session override): strip mode —
                # markers are removed rather than resolved, sources stay [].
                ledger = None
            from cuga.backend.knowledge.citation_check import SUPPORTED

            verified = {cid for cid, verdict in (verdicts or {}).items() if verdict == SUPPORTED}
            resolved, sources = resolve_citations(text, ledger, verified)
            for source in sources:
                if verdicts and source.get("cite_id") in verdicts:
                    source["support"] = verdicts[source["cite_id"]]
            state.final_answer = resolved
            state.sources = sources
        except Exception:
            # stale turn-N sources must not ride an unresolved turn-N+1 answer
            state.sources = []
            logger.exception("citation resolution failed; delivering unresolved answer")

    @staticmethod
    async def node_handler(
        state: AgentState,
        agent: FinalAnswerAgent,
        name: str,
        hitl_handler: HumanInTheLoopHandler,
        answer_function: Optional[Callable[[str], str]] = None,
    ) -> Command[Literal["__end__", "SuggestHumanActions"]]:
        # Handle direct chat calls (no processing needed)
        if state.sender == NodeNames.CHAT_AGENT:
            state.sender = name
            final_answer_content = state.chat_agent_messages[-1].content
            state.final_answer = final_answer_content
            await FinalAnswerNode.finalize_answer(state, answer_function)
            final_answer_output = FinalAnswerOutput(
                thoughts=["Chat response provided directly."], final_answer=state.final_answer
            )
            state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
            tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
            return Command(update=state.model_dump(), goto=NodeNames.END)

        # Handle EntryRouter when final_answer is already set (no apps matched)
        if state.sender == NodeNames.ENTRY_ROUTER and state.final_answer:
            state.sender = name
            await FinalAnswerNode.finalize_answer(state, answer_function)
            final_answer_output = FinalAnswerOutput(
                thoughts=[
                    "No applications matched the request. Providing available applications information."
                ],
                final_answer=state.final_answer,
            )
            state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
            tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
            return Command(update=state.model_dump(), goto=NodeNames.END)
        if state.sender == NodeNames.CUGA_LITE:
            state.sender = name
            await FinalAnswerNode.finalize_answer(state, answer_function)
            final_answer_output = FinalAnswerOutput(
                thoughts=[],
                final_answer=state.final_answer,
            )
            state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
            tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
            return Command(update=state.model_dump(), goto=NodeNames.END)

        # Handle supervisor callback - forward answer without regeneration (especially for lite mode)
        if state.sender == NodeNames.CUGA_SUPERVISOR:
            state.sender = name
            # Use final_answer if available, otherwise use last_planner_answer
            # For lite mode, the supervisor already generated the final answer
            answer_to_forward = state.final_answer or state.last_planner_answer or ""
            if answer_to_forward:
                state.final_answer = answer_to_forward
                # Forward-only: the sub-agent's FinalAnswerNode already ran
                # finalize_answer (function applied, citations resolved), so
                # re-applying the function here would feed it resolved [n]
                # chips. Citation resolution alone is idempotent by design. An answer that still
                # carries [sN] markers is checked first, as finalize_answer would.
                FinalAnswerNode.apply_citation_resolution(
                    state, await FinalAnswerNode.check_citation_support(state)
                )
                state.final_answer_finalized = True
                final_answer_output = FinalAnswerOutput(
                    thoughts=[],
                    final_answer=state.final_answer,
                )
                state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
                tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
                return Command(update=state.model_dump(), goto=NodeNames.END)
            else:
                # Fallback: if no answer found, still forward empty answer to avoid regeneration
                logger.warning(
                    "Supervisor callback: no final_answer or last_planner_answer found, forwarding empty answer"
                )
                state.final_answer = ""
                FinalAnswerNode.apply_citation_resolution(state)
                state.final_answer_finalized = True
                final_answer_output = FinalAnswerOutput(
                    thoughts=[],
                    final_answer="",
                )
                state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
                tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
                return Command(update=state.model_dump(), goto=NodeNames.END)

        # Main processing: generate final answer
        await FinalAnswerNode._generate_final_answer(state, agent, name, answer_function)
        return Command(update=state.model_dump(), goto=NodeNames.END)

    @staticmethod
    async def _generate_final_answer(
        state: AgentState,
        agent: FinalAnswerAgent,
        name: str,
        answer_function: Optional[Callable[[str], str]] = None,
    ):
        """Generate and process the final answer"""
        # Run the agent
        response = await agent.run(state)
        state.messages.append(response)

        # Parse and process output
        final_answer_output = FinalAnswerOutput(**json.loads(response.content))

        # Add to chat if enabled. No sanitizing here: harmony framing is removed
        # at the decode boundary (adapter.normalize_response), so every copy
        # taken from the model response is already clean.
        if settings.features.chat:
            chat_message = f"{MessagePrefixes.ANSWER_PREFIX}{final_answer_output.final_answer}"
            state.append_to_last_chat_message(chat_message)

        # Track the step (already clean — see the chat copy above).
        tracker.collect_step(Step(name=name, data=final_answer_output.model_dump_json()))

        # Replace variables and update state
        final_answer_output.final_answer = state.variables_manager.replace_variables_placeholders(
            final_answer_output.final_answer
        )
        state.final_answer = final_answer_output.final_answer

        # Apply the answer function and resolve [sN] citation markers into
        # display numbers (must be the last mutation of final_answer; chat
        # history above keeps raw ids).
        await FinalAnswerNode.finalize_answer(state, answer_function)
        final_answer_output.final_answer = state.final_answer
