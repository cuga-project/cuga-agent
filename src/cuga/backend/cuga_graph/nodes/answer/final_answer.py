import json
from typing import Literal, Dict, Callable, Optional

from langchain_core.messages import AIMessage
from langgraph.types import Command
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.answer.answer_function import apply_answer_function
from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import FinalAnswerAgent
from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.prompts.load_prompt import FinalAnswerOutput
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    create_save_reuse_action,
    create_get_more_utterances,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.config import settings
from cuga.backend.cuga_graph.utils.harmony import strip_harmony_tokens
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames, ActionIds, MessagePrefixes

tracker = ActivityTracker()

# Feature flag for human-in-the-loop functionality
ENABLE_SAVE_REUSE = settings.features.save_reuse


class HumanInTheLoopHandler:
    """Simple handler for human-in-the-loop interactions"""

    def __init__(self):
        self._action_handlers: Dict[str, Callable] = {
            ActionIds.SAVE_REUSE: self._handle_save_reuse,
            ActionIds.SAVE_REUSE_INTENT: self._handle_save_reuse_intent,
        }

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
        return Command(update=state.model_dump(), goto=NodeNames.END)

    def add_action_handler(self, action_id: str, handler: Callable):
        """Add a custom action handler"""
        self._action_handlers[action_id] = handler

    def _handle_save_reuse(self, state: AgentState, node_name: str) -> Command:
        """Handle save/reuse action - get more utterances"""
        state.hitl_action = create_get_more_utterances()
        state.sender = node_name
        return Command(update=state.model_dump(), goto=NodeNames.SUGGEST_HUMAN_ACTIONS)

    def _handle_save_reuse_intent(self, state: AgentState, node_name: str) -> Command:
        """Handle save/reuse intent - go to reuse agent"""
        state.sender = node_name
        return Command(update=state.model_dump(), goto=NodeNames.REUSE_AGENT)


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
    def finalize_answer(state, answer_function: Optional[Callable[[str], str]] = None) -> None:
        """Ordered post-processing of the delivered answer — the single seam
        every terminal branch runs before END:

        1. harmony-token strip — so the function receives clean text
           (the same strip inside citation resolution then no-ops)
        2. answer function — opt-in deterministic ``(str) -> str``
           (``[final_answer].function`` or an SDK-injected callable)
        3. citation resolution

        The function runs before citation resolution so it never sees
        resolved [n] chips, and its output still gets sanitized/resolved.
        It is applied once per delivered answer: the supervisor forward
        branch deliberately skips it (the sub-agent already applied it).
        """
        text = state.final_answer or ""
        if "<|" in text:
            state.final_answer = strip_harmony_tokens(text)
        apply_answer_function(state, answer_function)
        FinalAnswerNode.apply_citation_resolution(state)

    @staticmethod
    def apply_citation_resolution(state) -> None:
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
            resolved, sources = resolve_citations(text, ledger)
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
    ) -> Command[Literal["__end__", "SuggestHumanActions", "ReuseAgent"]]:
        # Handle human responses (only if HITL is enabled)
        if ENABLE_SAVE_REUSE and state.sender == NodeNames.WAIT_FOR_RESPONSE:
            return hitl_handler.handle_human_response(state, name)

        # Handle direct chat calls (no processing needed)
        if state.sender == NodeNames.CHAT_AGENT:
            state.sender = name
            final_answer_content = state.chat_agent_messages[-1].content
            state.final_answer = final_answer_content
            FinalAnswerNode.finalize_answer(state, answer_function)
            final_answer_output = FinalAnswerOutput(
                thoughts=["Chat response provided directly."], final_answer=state.final_answer
            )
            state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
            tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
            return Command(update=state.model_dump(), goto=NodeNames.END)

        # Handle TaskAnalyzerAgent when final_answer is already set (no apps matched)
        if state.sender == NodeNames.TASK_ANALYZER_AGENT and state.final_answer:
            state.sender = name
            FinalAnswerNode.finalize_answer(state, answer_function)
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
            FinalAnswerNode.finalize_answer(state, answer_function)
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
                # chips. Citation resolution alone is idempotent by design.
                FinalAnswerNode.apply_citation_resolution(state)
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
                final_answer_output = FinalAnswerOutput(
                    thoughts=[],
                    final_answer="",
                )
                state.messages.append(AIMessage(content=final_answer_output.model_dump_json(), name=name))
                tracker.collect_step(step=Step(name=name, data=final_answer_output.model_dump_json()))
                return Command(update=state.model_dump(), goto=NodeNames.END)

        # Main processing: generate final answer
        await FinalAnswerNode._generate_final_answer(state, agent, name, answer_function)

        # Route based on sender (only suggest human actions if HITL is enabled)
        # Allow save/reuse from both PlanControllerAgent (task decomposition mode) and ChatAgent (chat mode)
        if ENABLE_SAVE_REUSE and state.sender == NodeNames.PLAN_CONTROLLER_AGENT:
            state.hitl_action = create_save_reuse_action()
            state.sender = name
            return Command(update=state.model_dump(), goto=NodeNames.SUGGEST_HUMAN_ACTIONS)
        else:
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
        FinalAnswerNode.finalize_answer(state, answer_function)
        final_answer_output.final_answer = state.final_answer
