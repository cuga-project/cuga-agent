import json
import uuid
from typing import Literal, Optional, Dict, Callable

from langchain_core.messages import HumanMessage, ToolCall, BaseMessage, ToolMessage
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.shared.base_agent import create_partial
from cuga.backend.cuga_graph.nodes.chat.chat_agent.chat_agent import ChatAgent
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    create_flow_approve,
    create_new_flow_approve,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames, ActionIds

from langgraph.types import Command
from cuga.config import settings


tracker = ActivityTracker()

ENABLE_SAVE_REUSE = settings.features.save_reuse


class ChatHumanInTheLoopHandler:
    """Handler for chat-specific human-in-the-loop interactions"""

    def __init__(self):
        self._action_handlers: Dict[str, Callable] = {
            ActionIds.FLOW_APPROVE: self._handle_tool_execute,
            # Add chat-specific action handlers here
            # Example: ActionIds.TOOL_EXECUTE: self._handle_tool_execute,
            # ActionIds.CHAT_CONTINUE: self._handle_chat_continue,
        }

    def handle_human_response(self, state: AgentState, node_name: str) -> Command:
        """Handle any human response based on action_id"""
        action_id = state.hitl_response.action_id

        if action_id in self._action_handlers:
            return self._action_handlers[action_id](state, node_name)

        # Default fallback for chat - continue to final answer
        return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)

    def add_action_handler(self, action_id: str, handler: Callable):
        """Add a custom action handler"""
        self._action_handlers[action_id] = handler

    def _handle_tool_execute(self, state: AgentState, node_name: str) -> Command:
        """Handle tool execution approval"""
        state.sender = node_name
        return Command(update=state.model_dump(), goto=NodeNames.WAIT_FOR_RESPONSE)

    def _handle_chat_continue(self, state: AgentState, node_name: str) -> Command:
        """Handle continuing chat conversation"""
        state.sender = node_name
        return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)


class ChatNode(BaseNode):
    def __init__(self):
        super().__init__()
        self.chat_agent: Optional[ChatAgent] = None
        self.hitl_handler = ChatHumanInTheLoopHandler()
        self._initialized = False

    @classmethod
    async def create(cls):
        """Factory method to create and initialize the class"""
        instance = cls()
        instance.chat_agent = ChatAgent()
        if settings.features.chat:
            await instance.chat_agent.setup()
        instance.node = create_partial(
            ChatNode.node_handler,
            agent=instance.chat_agent,
            hitl_handler=instance.hitl_handler,
            name=instance.chat_agent.name,
        )
        instance._initialized = True
        return instance

    @staticmethod
    async def _execute_direct_tool_calls(
        state: AgentState,
        agent: ChatAgent,
        initial_response: BaseMessage,
        max_round_trips: int = 4,
    ) -> BaseMessage:
        response = initial_response
        round_trips = 0

        while (
            getattr(response, "tool_calls", None)
            and response.tool_calls
            and round_trips < max_round_trips
            and all(
                agent.should_auto_execute_tool(tool_call.get("name")) for tool_call in response.tool_calls
            )
        ):
            for raw_tool_call in response.tool_calls:
                tool = ToolCall(**raw_tool_call)
                tool_result = await agent.execute_tool(tool)
                state.chat_agent_messages.append(
                    ToolMessage(
                        content=agent._serialize_tool_result(tool_result),
                        tool_call_id=raw_tool_call.get("id", raw_tool_call.get("name", "tool_call")),
                        name=raw_tool_call.get("name"),
                    )
                )

            response = await agent.invoke(state.chat_agent_messages, state)
            state.chat_agent_messages.append(response)
            round_trips += 1

        return response

    @staticmethod
    def format_function_call(func_dict):
        name = func_dict["name"]
        args = func_dict["args"]

        def format_value(v):
            if isinstance(v, str):
                return f"'{v}'"
            elif isinstance(v, (list, dict)):
                return repr(v)
            else:
                return str(v)

        arg_strings = [f"{k}={format_value(v)}" for k, v in args.items()]
        return f"{name}({', '.join(arg_strings)})"

    @staticmethod
    async def node_handler(
        state: AgentState, agent: ChatAgent, hitl_handler: ChatHumanInTheLoopHandler, name: str
    ) -> Command[Literal["FinalAnswerAgent", "TaskAnalyzerAgent", "SuggestHumanActions"]]:
        # Handle human-in-the-loop responses
        if (
            state.sender == NodeNames.WAIT_FOR_RESPONSE
            and state.hitl_response.action_id == ActionIds.FLOW_APPROVE
        ):
            tool = ToolCall(**state.hitl_response.additional_data.tool)
            res = await agent.execute_tool(tool)
            parsed_result = res
            if isinstance(res, str):
                try:
                    parsed_result = json.loads(res)
                except (json.JSONDecodeError, TypeError):
                    # If parsing fails, keep original string
                    parsed_result = res
            # Get tool details
            tool_name = tool.get("name")
            tool_args = tool.get("args")
            # Add to variable manager

            var_name = f"tool_result_{str(uuid.uuid4())[:5]}"
            state.variables_manager.add_variable(
                parsed_result, var_name, f"Result of tool {tool_name} with args {tool_args}"
            )
            state.sender = "ChatAgentTool"
            state.last_planner_answer = state.variables_manager.present_variable(var_name)
            return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)

        if (
            state.sender == NodeNames.WAIT_FOR_RESPONSE
            and state.hitl_response.action_id == ActionIds.NEW_FLOW_APPROVE
        ):
            logger.debug("tool call in chat node")
            tool = ToolCall(**state.hitl_response.additional_data.tool)
            state.input = tool.get("args").get("user_task")
            state.sender = "ChatAgent"
            return Command(update=state.model_dump(), goto=NodeNames.TASK_ANALYZER_AGENT)

        # If chat feature is disabled, go directly to task analyzer
        if not settings.features.chat:
            state.sender = name
            return Command(update=state.model_dump(), goto=NodeNames.TASK_ANALYZER_AGENT)

        # Process chat input
        state.sender = name
        state.chat_agent_messages.append(HumanMessage(content=state.input))
        res: BaseMessage = await agent.invoke(state.chat_agent_messages, state)
        state.chat_agent_messages.append(res)

        if res.tool_calls:
            res = await ChatNode._execute_direct_tool_calls(state, agent, res)

        # Handle tool calls - require human approval
        if ENABLE_SAVE_REUSE and res.tool_calls and res.tool_calls[0].get("name") == "run_new_flow":
            state.final_answer = state.chat_agent_messages[-1].content
            state.sender = name
            state.hitl_action = create_new_flow_approve(tool=res.tool_calls[0])
            return Command(update=state.model_dump(), goto=NodeNames.SUGGEST_HUMAN_ACTIONS)

        if (
            ENABLE_SAVE_REUSE
            and res.tool_calls
            and agent.requires_human_approval(res.tool_calls[0].get("name"))
        ):
            state.final_answer = state.chat_agent_messages[-1].content
            state.sender = name
            state.hitl_action = create_flow_approve(tool=res.tool_calls[0])
            return Command(update=state.model_dump(), goto=NodeNames.SUGGEST_HUMAN_ACTIONS)

        if (
            not ENABLE_SAVE_REUSE
            and res.tool_calls
            and len(res.tool_calls) > 0
            and res.tool_calls[0].get("name") == "execute_task"
        ):
            logger.debug(f"tool call in chat node {res.tool_calls[0]}")
            variables_rel = res.tool_calls[0].get("args").get("relevant_variables")
            if variables_rel and len(variables_rel) > 0:
                state.input = (
                    f"task: {res.tool_calls[0].get('args').get('task')}"
                    + f"\n relevant variables from history: {res.tool_calls[0].get('args').get('relevant_variables')}"
                )
            else:
                state.input = res.tool_calls[0].get("args").get("task")
            return Command(update=state.model_dump(), goto="TaskAnalyzerAgent")
        # Regular chat response - add to messages and continue+
        res.content = state.variables_manager.replace_variables_placeholders(res.content)
        state.messages.append(res)
        tracker.collect_step(
            step=Step(
                name=name,
                data=res.content,
                current_url=state.url,
            )
        )
        state.final_answer = state.chat_agent_messages[-1].content

        return Command(update=state.model_dump(), goto=NodeNames.FINAL_ANSWER_AGENT)
