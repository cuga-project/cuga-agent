import json
import uuid
import time
from uuid import UUID

from langgraph.types import Command

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.browser_env.browser.extension_env_async import ExtensionEnv
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import format_tools
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller_agent.prompts.load_prompt import (
    PlanControllerOutput,
)
from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.prompts.load_prompt import NextAgentPlan
from cuga.backend.cuga_graph.nodes.browser.qa_agent.prompts.load_prompt import QaAgentOutput
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
    TaskDecompositionPlan,
    TaskDecompositionMultiOutput,
)
from cuga.backend.browser_env.browser.gym_env_async import BrowserEnvGymAsync
from cuga.config import settings
from pydantic import TypeAdapter
import logging
from typing import Generator, List, Optional, Union, Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, ToolCall
from langchain_core.outputs import LLMResult
from langgraph.graph.state import CompiledStateGraph
from loguru import logger
from pydantic import BaseModel
from enum import Enum

from cuga.backend.cuga_graph.state.agent_state import AgentState


class OutputFormat(str, Enum):
    WXO = "wxo"
    DEFAULT = "default"


class TokenUsageTracker(AsyncCallbackHandler):
    def __init__(self, tracker: ActivityTracker):
        self.tracker = tracker

    async def on_llm_end(self, response: LLMResult, **kwargs):
        generation = response.generations[0][0].text
        self.tracker.collect_prompt(role="assistant", value=generation)
        self.tracker.collect_tokens_usage(response.llm_output.get("token_usage").get("total_tokens"))

    def split_system_human(self, text):
        """
        Splits text into system and human parts based on 'System: ' and '\nHuman: ' markers.

        Args:
            text (str): Input text to split

        Returns:
            tuple: (system_part, human_part) where markers and newlines are removed
        """
        system_part = ""
        human_part = ""

        # Check if text contains the required markers
        has_system = "System: " in text
        has_human = "\nHuman: " in text

        if has_system and has_human:
            # Find the positions of the markers
            system_pos = text.find("System: ")
            human_pos = text.find("\nHuman: ")

            # Extract system part (remove "System: ")
            if system_pos < human_pos:
                system_start = system_pos + len("System: ")
                system_part = text[system_start:human_pos].strip()

            # Extract human part (remove "\nHuman: ")
            human_start = human_pos + len("\nHuman: ")
            human_part = text[human_start:].strip()

        elif has_system:
            # Only system marker found
            system_pos = text.find("System: ")
            system_start = system_pos + len("System: ")
            system_part = text[system_start:].strip()

        elif has_human:
            # Only human marker found
            human_pos = text.find("\nHuman: ")
            human_start = human_pos + len("\nHuman: ")
            human_part = text[human_start:].strip()

        return system_part, human_part

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        pmt = prompts[0]
        result1 = self.split_system_human(pmt)
        if result1:
            system1, human1 = result1
            self.tracker.collect_prompt(role="system", value=system1)
            self.tracker.collect_prompt(role="human", value=human1)
        else:
            self.tracker.collect_prompt(role="system", value=pmt)


class AgentLoopAnswer(BaseModel):
    """
    Model representing the answer from the agent loop.
    """

    end: bool
    interrupt: bool = False
    answer: Optional[Any] = None
    has_tools: bool = False
    tools: List[ToolCall]
    flow_generalized: Optional[bool] = False


class StreamEvent(BaseModel):
    """
    Model representing a stream event.
    """

    name: str
    data: str

    @staticmethod
    def format_data(data_str: str) -> str:
        """
        - If data_str isn’t valid JSON, returns it unchanged.
        - If it’s a JSON object with exactly one key whose value is a string, returns that string.
        - Otherwise, returns a markdown-formatted JSON code block.
        """
        try:
            parsed: Any = json.loads(data_str)
        except (ValueError, TypeError):
            return data_str

        if isinstance(parsed, dict) and len(parsed.keys()) == 1:
            value = next(iter(parsed.values()))
            if isinstance(value, str):
                return value

        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        return f"```json\n{pretty}\n```"

    @staticmethod
    def parse(formatted_str: str) -> 'StreamEvent':
        """
        Parses a formatted string back into a StreamEvent.
        Handles formats like:
            event: EventName
            data: some data

        Now correctly handles data that contains newlines by parsing from the last \n\n.
        """

        # Find the last occurrence of \n\n to split the string
        last_double_newline = formatted_str.rfind('\n\n')

        if last_double_newline == -1:
            raise ValueError("No double newline (\\n\\n) found in formatted string")

        # Split at the last \n\n - everything before is the event block
        event_block = formatted_str[:last_double_newline].strip()
        lines = event_block.split('\n', 1) if event_block else []

        name = None
        data = None

        # Parse the event block (everything before the last \n\n)
        for line in lines:
            if line.startswith('event: '):
                name = line[7:].strip()  # Remove 'event: '
            elif line.startswith('data: '):
                # For data lines, we need to handle the case where data might span multiple lines
                # Everything after 'data: ' in the event block, plus everything after the last \n\n
                data_start = line[6:]  # Remove 'data: ', preserve any leading spaces

                # Append everything after the last \n\n as part of the data
                remaining_data = formatted_str[last_double_newline + 2 :]
                data = data_start + '\n' + remaining_data if data_start else remaining_data
                break  # Found data line, no need to continue

        # If we didn't find data in the event block, check if everything after last \n\n is data
        if data is None:
            data = formatted_str[last_double_newline + 2 :]

        if name is None:
            raise ValueError("No 'event:' line found in formatted string")
        if data is None:
            data = ""

        return StreamEvent(name=name, data=data)

    @staticmethod
    def format_event(raw_event: str) -> str:
        """
        Takes a string like:
            event: Foo
            data: {...}

        Parses only what comes after 'data: ', runs format_data on it,
        and then reconstructs the full event block.
        """
        if not hasattr(raw_event, "partition"):
            if hasattr(raw_event, "answer"):
                head = "FinalAnswer"
                answer = StreamEvent.format_data(raw_event.answer)
                return f"---\n\n{head}data: \n\n{answer}"

        head, sep, tail = raw_event.partition("data: ")
        if not sep:
            return raw_event

        data_part, nl, rest = tail.partition("\n")
        formatted = StreamEvent.format_data(data_part)
        head = head.split(":")[1]
        return f"---\n\n{head}\n\ndata: {formatted}{nl}{rest}"

    @staticmethod
    def prepare_message(event, thread_id):
        message = {
            "id": f"msg-{uuid.uuid4()}",
            "object": "thread.message.delta",
            "thread_id": thread_id,
            "model": "langgraph-agent",
            "created": int(time.time()),
            "choices": [{"delta": {"role": "assistant", "content": StreamEvent.format_event(event)}}],
        }
        return message

    def format(self, format: OutputFormat = None, **kwargs) -> str:
        """
        Formats the stream event for output.

        :return: Formatted string of the event.
        """
        if format is OutputFormat.WXO:
            thread_id = kwargs.get("thread_id")
            message = StreamEvent.prepare_message(self.data, thread_id)
            return f"data: {json.dumps(message)}\n\n"
        elif format is OutputFormat.DEFAULT:
            if self.name == "Answer":
                return f"event: {self.name}\ndata: {self.data}\n\n"
            return self.data
        return f"event: {self.name}\ndata: {self.data}\n\n"


class AgentLoop:
    """
    A class to handle the agent loop process, managing events, streaming responses,
    and interacting with the compiled state graph.
    """

    def __init__(
        self,
        thread_id: str,
        langfuse_handler: Optional[Any],
        graph: CompiledStateGraph,
        tracker: ActivityTracker,
        env_pointer: Optional[BrowserEnvGymAsync | ExtensionEnv] = None,
        logger_name: str = 'agent_loop',
        policy_system: Optional[Any] = None,
        enable_todos: Optional[bool] = None,
        reflection_enabled: Optional[bool] = None,
        shortlisting_tool_threshold: Optional[int] = None,
        cuga_lite_max_steps: Optional[int] = None,
    ):
        self.env_pointer = env_pointer
        self.thread_id = thread_id
        self.langfuse_handler = langfuse_handler
        self.graph = graph
        self.tracker = tracker
        self.logger = logging.getLogger(logger_name)
        self.policy_system = policy_system
        self.enable_todos = enable_todos
        self.reflection_enabled = reflection_enabled
        self.shortlisting_tool_threshold = shortlisting_tool_threshold
        self.cuga_lite_max_steps = cuga_lite_max_steps

    async def stream_event(self, event: StreamEvent) -> Generator[str, None, None]:
        yield event.format()

    def get_event_message(self, event) -> StreamEvent:
        # When subgraphs=True, event is a tuple: (namespace_tuple, state_dict)
        # namespace_tuple is like () for root or (node_name,) for subgraph
        if isinstance(event, tuple):
            namespace, state_dict = event
            logger.info(f"Subgraph event - namespace: {namespace}, state keys: {list(state_dict.keys())}")

            # Get the node name from state_dict keys
            if not state_dict:
                return StreamEvent(name="unknown", data="")

            node_name = list(state_dict.keys())[0]
            state_data = state_dict[node_name]

            logger.debug(
                f"Node: {node_name}, namespace empty: {not namespace}, state_data keys: {list(state_data.keys()) if isinstance(state_data, dict) else 'not a dict'}"
            )

            # Check if this is from a subgraph (namespace is not empty)
            if namespace:
                logger.info(f"Processing subgraph node: {node_name} from namespace: {namespace}")

                # Handle call_model node from CugaLite subgraph
                if node_name == "call_model":
                    logger.debug("Detected call_model node")
                    # Check if it generated code or text
                    if "script" in state_data and state_data["script"] and state_data["script"].strip():
                        # Code generated - show as CodeAgent with JSON
                        logger.info(f"call_model generated script of length {len(state_data['script'])}")
                        output = {
                            "code": state_data["script"],
                            "execution_output": "",
                            "steps_summary": [],
                            "summary": "Code generated, preparing to execute",
                            "variables": state_data.get("variables_storage", {}),
                        }
                        return StreamEvent(name="CodeAgent", data=json.dumps(output))
                    else:
                        # Text/reasoning output - show as CodeAgent_Reasoning
                        logger.info("call_model generated text response (no code)")
                        messages = state_data.get("chat_messages", [])
                        if messages:
                            last_msg = messages[-1]
                            if hasattr(last_msg, 'content'):
                                content = last_msg.content
                            elif isinstance(last_msg, dict):
                                content = last_msg.get("content", "")
                            else:
                                content = str(last_msg)
                            # Only return if content is not empty
                            if content and content.strip():
                                return StreamEvent(name="CodeAgent_Reasoning", data=content)
                        # Skip empty events
                        logger.debug("Skipping empty call_model event")
                        return StreamEvent(name="", data="")

                # Handle sandbox node from CugaLite subgraph
                elif node_name == "sandbox":
                    logger.info("Detected sandbox node - formatting execution output")

                    # Extract execution output from chat_messages
                    execution_output = ""
                    messages = state_data.get("chat_messages", [])
                    if messages:
                        logger.debug(f"Found {len(messages)} messages in sandbox state")
                        for msg in reversed(messages):
                            # Handle both BaseMessage objects and dicts
                            if hasattr(msg, 'content'):
                                content = msg.content
                            elif isinstance(msg, dict):
                                content = msg.get("content", "")
                            else:
                                continue

                            if "Execution output:" in content:
                                execution_output = content.split("Execution output:\n")[-1]
                                logger.debug(f"Extracted execution output: {execution_output[:100]}...")
                                break

                    # Only return event if we have meaningful execution output
                    if execution_output and execution_output.strip():
                        output = {
                            "code": "",
                            "execution_output": execution_output,
                            "steps_summary": [],
                            "summary": "Code execution completed",
                            "variables": state_data.get("variables_storage", {}),
                        }
                        logger.info(
                            f"Returning sandbox output with execution_output length: {len(execution_output)}"
                        )
                        return StreamEvent(name="CodeAgent", data=json.dumps(output))
                    else:
                        # Skip empty sandbox events
                        logger.debug("Skipping empty sandbox event")
                        return StreamEvent(name="", data="")

                # Default handling for other subgraph nodes
                logger.debug(f"Unhandled subgraph node: {node_name}")
                return StreamEvent(name=str(node_name), data="")

            # Root level node (namespace is empty tuple)
            logger.debug(f"Processing root level node: {node_name}")
            event = state_dict  # Use the state_dict as event for regular processing

        # Regular node handling (when event is a dict)
        first_key = list(event.keys())[0]
        logger.info("Current Node: {}".format(first_key))
        if first_key == "__interrupt__":
            return StreamEvent(name=str(first_key), data="")

        # Skip events with None state (routing commands without updates)
        if event[first_key] is None:
            logger.debug(f"Skipping event with None state for node: {first_key}")
            return StreamEvent(name=str(first_key), data="")

        # Handle subgraph events (CugaLiteSubgraph, CugaLiteCallback) that don't have full AgentState
        # These only have shared keys like chat_messages, final_answer, variables_storage
        event_data = event[first_key]
        is_subgraph_event = first_key in ["CugaLiteSubgraph", "CugaLiteCallback"]

        # Check if this looks like a subgraph event (missing required AgentState fields)
        if not is_subgraph_event and isinstance(event_data, dict):
            is_subgraph_event = "input" not in event_data or "url" not in event_data

        if is_subgraph_event:
            logger.debug(f"Detected subgraph event for node: {first_key}")
            # For subgraph events, extract final_answer or last chat message
            event_val = ""
            if isinstance(event_data, dict):
                # Check if this is any policy event (unified handling)
                metadata = event_data.get("cuga_lite_metadata", {})
                if metadata.get("policy_blocked") or metadata.get("policy_matched"):
                    policy_type = metadata.get("policy_type", "unknown")
                    policy_name = metadata.get("policy_name", "Unknown Policy")

                    logger.info(f"Detected policy event: {policy_name} (type: {policy_type})")

                    # Create a unified Policy event with all metadata as JSON
                    policy_event = {
                        "type": "policy",
                        "policy_type": policy_type,
                        "policy_name": policy_name,
                        "policy_blocked": metadata.get("policy_blocked", False),
                        "policy_matched": metadata.get("policy_matched", False),
                        "content": event_data.get("final_answer", ""),
                        "metadata": metadata,
                    }
                    return StreamEvent(name="Policy", data=json.dumps(policy_event))

                if event_data.get("final_answer"):
                    event_val = event_data["final_answer"]
                elif event_data.get("chat_messages"):
                    last_msg = event_data["chat_messages"][-1]
                    if hasattr(last_msg, 'content'):
                        event_val = last_msg.content
            # Display subgraph completions as CodeAgent for consistency
            return StreamEvent(name="CodeAgent", data=event_val or "")

        state_obj = AgentState(**event[first_key])
        messages = state_obj.messages
        if messages:
            event_val = messages[-1].content
        else:
            event_val = None
        if first_key == "BrowserPlannerAgent":
            event_val = json.dumps(state_obj.previous_steps[-1].model_dump())
        if first_key == "ActionAgent":
            event_val = json.dumps(messages[-1].tool_calls)
        if first_key == 'ReuseAgent':
            event_val = messages[-1].content
        # Override CugaLite to display as CodeAgent for consistency
        if first_key == "CugaLite":
            first_key = "CodeAgent"
        logger.debug("Current Agent: {}".format(list(event.keys())))
        return StreamEvent(name=str(first_key), data=event_val or "")

    def get_stream(self, state, resume=None):
        both_none = state is None and resume is None

        callbacks = [TokenUsageTracker(self.tracker)]
        if settings.advanced_features.langfuse_tracing and self.langfuse_handler is not None:
            callbacks.insert(0, self.langfuse_handler)

        config = {
            "recursion_limit": 135,
            "callbacks": callbacks,
            "configurable": {
                "thread_id": self.thread_id,
            },
        }

        if self.policy_system:
            config["configurable"]["policy_system"] = self.policy_system
        if self.enable_todos is not None:
            config["configurable"]["enable_todos"] = self.enable_todos
        if self.reflection_enabled is not None:
            config["configurable"]["reflection_enabled"] = self.reflection_enabled
        if self.shortlisting_tool_threshold is not None:
            config["configurable"]["shortlisting_tool_threshold"] = self.shortlisting_tool_threshold
        if self.cuga_lite_max_steps is not None:
            config["configurable"]["cuga_lite_max_steps"] = self.cuga_lite_max_steps

        return self.graph.astream(
            state if state else Command(resume=resume.model_dump()) if not both_none else None,
            config=config,
            stream_mode="updates",
            subgraphs=True,
        )

    def get_langfuse_trace_id(self) -> Optional[str]:
        """Get the current Langfuse trace ID if available."""
        if self.langfuse_handler and hasattr(self.langfuse_handler, 'last_trace_id'):
            return self.langfuse_handler.last_trace_id
        return None

    def get_output(self, event):
        logger.debug(f"get_output called with event type: {type(event)}")

        state: AgentState = AgentState(
            **self.graph.get_state({"configurable": {"thread_id": self.thread_id}}).values
        )
        msg: AIMessage = state.messages[-1] if len(state.messages) > 0 else None

        # Handle tuple format from subgraphs=True
        if isinstance(event, tuple):
            namespace, state_dict = event
            event_keys = list(state_dict.keys())
            logger.debug(f"Event is tuple - namespace: {namespace}, keys: {event_keys}")
        else:
            event_keys = list(event.keys())
            logger.debug(f"Event is dict - keys: {event_keys}")

        logger.info("Calling get output {}".format(",".join(event_keys)))

        # Print Langfuse trace ID if available
        trace_id = self.get_langfuse_trace_id()
        if trace_id:
            print(f"Langfuse Trace ID: {trace_id}")
            logger.info(f"Langfuse Trace ID: {trace_id}")
        if "__interrupt__" in event_keys:
            logger.debug("Detected __interrupt__ in event_keys")
            answer = ""
            if msg.tool_calls and len(msg.tool_calls) > 0:
                return AgentLoopAnswer(
                    end=False, interrupt=True, has_tools=True, answer=msg.content, tools=msg.tool_calls
                )
            else:
                return AgentLoopAnswer(end=True, interrupt=True, has_tools=False, answer=answer, tools=[])

        if "ReuseAgent" in event_keys:
            logger.debug("Detected ReuseAgent in event_keys")
            return AgentLoopAnswer(
                end=True,
                has_tools=False,
                answer=f"Done!\n---\n [Click here for an explained walkthrough of the flow](http://localhost:{settings.server_ports.demo}/flows/flow.html)",
                flow_generalized=True,
                tools=msg.tool_calls,
            )

        if "FinalAnswerAgent" in event_keys or "CodeAgent" in event_keys:
            logger.debug(
                f"Detected FinalAnswerAgent or CodeAgent in event_keys. final_answer: {state.final_answer[:100] if state.final_answer else 'None'}..."
            )
            # Check if this is a policy event by looking at cuga_lite_metadata (unified handling)
            answer = state.final_answer
            if hasattr(state, 'cuga_lite_metadata') and state.cuga_lite_metadata:
                metadata = state.cuga_lite_metadata
                if metadata.get('policy_blocked') or metadata.get('policy_matched'):
                    policy_type = metadata.get('policy_type', 'unknown')
                    policy_name = metadata.get('policy_name', 'Unknown Policy')
                    is_blocked = metadata.get('policy_blocked', False)

                    logger.info(
                        f"Wrapping final answer with policy metadata: {policy_name} (type: {policy_type})"
                    )

                    # Generate user-friendly markdown message based on policy type
                    if policy_type == "tool_approval" and metadata.get('approval_required'):
                        # Tool approval message
                        approval_msg = metadata.get(
                            'approval_message', 'This tool requires your approval before execution.'
                        )
                        tools_list = metadata.get('required_tools', [])
                        apps_list = metadata.get('required_apps', [])

                        content_lines = [f"## ✋ {policy_name}", "", approval_msg, ""]

                        if tools_list:
                            if tools_list == ["*"]:
                                content_lines.append("**Tools requiring approval:** All tools")
                            else:
                                content_lines.append(f"**Tools requiring approval:** {', '.join(tools_list)}")
                            content_lines.append("")

                        if apps_list:
                            content_lines.append(f"**Apps requiring approval:** {', '.join(apps_list)}")
                            content_lines.append("")

                        code_preview = metadata.get('code_preview', [])
                        if code_preview:
                            content_lines.append("### Code Preview")
                            content_lines.append("")
                            content_lines.append("```python")
                            content_lines.extend(code_preview)
                            content_lines.append("```")
                            content_lines.append("")

                        content_lines.append("---")
                        content_lines.append("*Please review and approve to continue execution.*")
                        user_content = "\n".join(content_lines)

                    elif is_blocked:
                        # Blocked policy message
                        response_content = metadata.get(
                            'response_content',
                            state.final_answer or 'This action was blocked by a security policy.',
                        )
                        user_content = f"## 🛑 {policy_name}\n\n{response_content}"

                    elif policy_type == "playbook":
                        # Playbook message
                        playbook_content = metadata.get('playbook_content', state.final_answer)
                        if playbook_content and playbook_content != "No answer found":
                            user_content = f"## 📖 {policy_name}\n\n{playbook_content}"
                        else:
                            user_content = f"## 📖 {policy_name}\n\nFollowing the playbook to guide you through this process."

                    elif policy_type == "tool_guide":
                        # Tool guide message
                        user_content = f"## 🔧 {policy_name}\n\nTool descriptions have been enhanced with additional guidelines."

                    else:
                        # Generic policy message
                        user_content = state.final_answer or f"## 📋 {policy_name}\n\nPolicy is active."

                    # Create unified policy event
                    answer = {
                        "type": "policy",
                        "policy_type": policy_type,
                        "policy_name": policy_name,
                        "policy_blocked": is_blocked,
                        "policy_matched": metadata.get('policy_matched', False),
                        "content": user_content,
                        "metadata": metadata,
                    }
                    answer = json.dumps(answer)

            return AgentLoopAnswer(end=True, has_tools=False, answer=answer, tools=msg.tool_calls)
        else:
            logger.debug(
                f"No terminal agent detected. Returning intermediate answer with msg.content: {msg.content[:100] if msg and msg.content else 'None'}..."
            )
            return AgentLoopAnswer(end=False, has_tools=True, answer=msg.content, tools=msg.tool_calls)

    async def run_stream(self, state: Optional[AgentState] = None, resume=None):
        event_stream = self.get_stream(state, resume)
        event = {}
        async for event in event_stream:
            event_msg = self.get_event_message(event)
            # Skip empty events (events with no name or no data)
            if not event_msg.name or (not event_msg.data and event_msg.name != "__interrupt__"):
                logger.debug(
                    f"Skipping empty event: name='{event_msg.name}', data='{event_msg.data[:50] if event_msg.data else ''}'"
                )
                continue
            # logger.debug(f"current event: {event_msg.format()}")
            yield event_msg.format()
        yield self.get_output(event)

    def get_output_of_obj(self, dict):
        msg = ""
        for key, val in dict.items():
            if isinstance(val, list):
                list_items = '\n'.join([f'{i}. {va}' for i, va in enumerate(val)])
                msg += f"**{key}**: {list_items}\n\n"
            else:
                msg += f"**{key}**: {val}\n\n"
        return msg

    async def show_chat_even(self, event: StreamEvent):
        if self.env_pointer and self.env_pointer.chat:
            if event.name == "TaskDecompositionAgent":
                msg = "TaskDecompositionAgent\n:"
                DataType = TypeAdapter(Union[TaskDecompositionPlan, TaskDecompositionMultiOutput])
                task_decomposition_plan = DataType.validate_json(event.data)
                msg += self.get_output_of_obj(task_decomposition_plan.model_dump())
                await self.env_pointer.send_chat_message(
                    role="assistant",
                    content=msg,
                )
            if event.name == "BrowserPlannerAgent":
                msg = "PlannerAgent:\n"
                p = NextAgentPlan(**json.loads(event.data))
                msg += self.get_output_of_obj(p.model_dump())
                await self.env_pointer.send_chat_message(
                    role="assistant",
                    content=msg,
                )
            if event.name == "ActionAgent":
                p = json.loads(event.data)
                await self.env_pointer.send_chat_message(
                    role="assistant", content="Actions:\n {}".format(format_tools(p))
                )
            if event.name == "QaAgent":
                p = QaAgentOutput(**json.loads(event.data))
                await self.env_pointer.send_chat_message(
                    role="assistant", content="{} - {}".format(p.name, p.answer)
                )
            if event.name == "PlanControllerAgent":
                p = PlanControllerOutput(**json.loads(event.data))
                await self.env_pointer.send_chat_message(
                    role="assistant", content="Plan Controller - next subtask is: {}".format(p.next_subtask)
                )

    async def run(self, state: Optional[AgentState] = None, resume=None):
        event_stream = self.get_stream(state, resume)
        event = {}
        async for event in event_stream:
            event_msg = self.get_event_message(event)
            await self.show_chat_even(event_msg)
            # logger.debug(f"current event: {event_msg.format()}")
        return self.get_output(event)
