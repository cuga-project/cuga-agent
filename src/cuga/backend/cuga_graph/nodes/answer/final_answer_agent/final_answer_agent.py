import json
from typing import Any, Literal, Union

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from loguru import logger

from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.prompts.load_prompt import (
    FinalAnswerOutput,
    FinalAnswerAppworldOutput,
    appworld_plain_post_llm_runnable,
    is_appworld_action_label,
    load_appworld_final_answer_prompt,
    load_appworld_plain_final_answer_prompt,
    load_appworld_task_classifier_prompt,
    parser,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.llm.errors import ainvoke_with_retry_on_tool_choice_none
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.configurations.instructions_manager import InstructionsManager

instructions_manager = InstructionsManager()
llm_manager = LLMManager()
tracker = ActivityTracker()


class FinalAnswerAgent(BaseAgent):
    def __init__(
        self,
        prompt_template: ChatPromptTemplate,
        llm: BaseChatModel,
        mode: Literal['default', 'appworld', 'appworld_plain'] = 'default',
        tools: Any = None,
    ):
        super().__init__()
        self.name = "FinalAnswerAgent"
        self._mode = mode
        self.classifier_chain = None
        parser = RunnableLambda(FinalAnswerAgent.output_parser)
        parser_default = RunnableLambda(FinalAnswerAgent.default_answer_parser)
        if mode == "default":
            self.chain = BaseAgent.get_chain(prompt_template, llm, wx_json_mode="no_format") | (
                parser_default.bind(name=self.name)
            )
        elif mode == "appworld_plain":
            self.chain = (
                BaseAgent.get_chain(prompt_template, llm, wx_json_mode="no_format")
                | appworld_plain_post_llm_runnable()
                | parser.bind(name=self.name)
            )
            # Classify the task first: AppWorld grades an answer for EVERY task, and for
            # action/update tasks the ground-truth answer is null — so returning any value
            # fails the "answers match" check. A dedicated ACTION-vs-QUERY call is more
            # reliable than relying on the extraction prompt to also self-classify.
            self.classifier_chain = BaseAgent.get_chain(
                load_appworld_task_classifier_prompt(), llm, wx_json_mode="no_format"
            )
        else:
            self.chain = BaseAgent.get_chain(prompt_template, llm, FinalAnswerAppworldOutput) | (
                parser.bind(name=self.name)
            )

    @staticmethod
    def default_answer_parser(result: AIMessage, name):
        result = AIMessage(
            content=FinalAnswerOutput(thoughts=[], final_answer=result.content).model_dump_json(), name=name
        )
        return result

    @staticmethod
    def output_parser(result: Union[FinalAnswerOutput, FinalAnswerAppworldOutput], name) -> Any:
        result = AIMessage(content=json.dumps(result.model_dump()), name=name)
        return result

    async def run(self, input_variables: AgentState) -> AIMessage:
        if settings.features.final_answer:
            data = input_variables.model_dump()
            data["variable_summary"] = input_variables.variables_manager.get_variables_summary(last_n=2)
            data["instructions"] = instructions_manager.get_instructions(self.name)
            if self._mode == "appworld_plain":
                # Pre-decision: action/update tasks expect no return value -> answer "N/A"
                # (eval's _complete_task maps "N/A" to a null answer). Skip extraction for those.
                if getattr(
                    settings.advanced_features, "appworld_classify_action_tasks", True
                ) and await self._is_action_task(data):
                    return self._na_final_answer()
                return await ainvoke_with_retry_on_tool_choice_none(self.chain, data)
            return await self.chain.ainvoke(data)
        else:
            last_variable_name, last_variable = input_variables.variables_manager.get_last_variable()
            return AIMessage(
                content=json.dumps(
                    FinalAnswerOutput(
                        final_answer=input_variables.final_answer
                        if input_variables.sender == "ReuseAgent"
                        else input_variables.last_planner_answer
                        + (
                            f"\n\n{last_variable.description}\n\n---\n\n{input_variables.variables_manager.present_variable(last_variable_name)}"
                            if last_variable_name
                            else ""
                        ),
                        thoughts=["Skipping final answer, using last agent answer"],
                    ).model_dump()
                )
            )

    async def _is_action_task(self, data: dict) -> bool:
        """True if the task is an action/update task (no return value expected).
        Best-effort: on any failure, default to False (QUERY) so we never wrongly
        null out a task that genuinely needed an answer."""
        if self.classifier_chain is None:
            return False
        try:
            msg = await self.classifier_chain.ainvoke(data)
            raw = (msg.content if hasattr(msg, "content") else str(msg)) or ""
            is_action = is_appworld_action_label(raw)
            logger.info(f"FinalAnswer classifier -> {'ACTION (answer=N/A)' if is_action else 'QUERY'}")
            return is_action
        except Exception as e:
            logger.warning(f"FinalAnswer action/query classifier failed, defaulting to QUERY: {e}")
            return False

    def _na_final_answer(self) -> AIMessage:
        """Answer for an action/update task: 'N/A' (eval maps it to a null answer)."""
        out = FinalAnswerAppworldOutput(
            thoughts=["Task classified as action/update — no return value expected."],
            final_answer="N/A",
            final_answer_type="str",
        )
        return AIMessage(content=json.dumps(out.model_dump()), name=self.name)

    @staticmethod
    def create():
        dyna_model = settings.agent.final_answer.model
        if settings.advanced_features.benchmark == "appworld":
            if getattr(settings.advanced_features, "appworld_final_answer_plain", False):
                return FinalAnswerAgent(
                    prompt_template=load_appworld_plain_final_answer_prompt(model_config=dyna_model),
                    mode="appworld_plain",
                    llm=llm_manager.get_model(dyna_model),
                )
            return FinalAnswerAgent(
                prompt_template=load_appworld_final_answer_prompt(model_config=dyna_model),
                mode="appworld",
                llm=llm_manager.get_model(dyna_model),
            )
        else:
            return FinalAnswerAgent(
                prompt_template=load_prompt_simple(
                    "./prompts/system.jinja2",
                    "./prompts/user_msg.jinja2",
                    model_config=dyna_model,
                    format_instructions=BaseAgent.get_format_instructions(parser),
                ),
                llm=llm_manager.get_model(dyna_model),
            )
