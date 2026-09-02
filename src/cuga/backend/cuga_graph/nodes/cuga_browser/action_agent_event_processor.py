import asyncio
import json
from typing import Any, Callable, Dict, List

from langchain_core.messages import ToolCall
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.browser_env.browser.gym_obs.http_stream_comm import ChromeExtensionCommunicatorProtocol
from cuga.backend.browser_env.tools.providers import BrowserToolImplProvider
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import (
    click,
    go_back,
    select_option,
    Alert,
    open_app,
)
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import type as typeaction

tracker = ActivityTracker()


class ActionAgentEventProcessor:
    def __init__(self, page, tool_handlers: Dict[str, Callable[[Any], None]]):
        """
        Initializes the processor with a page context and a dictionary of tool handlers.

        :param page: The page context for tool actions.
        :param tool_handlers: A dictionary mapping tool names to handler functions.
        """
        self.page = page
        self.tool_handlers = tool_handlers
        self.feedback_log = []  # List to collect feedback for unrecognized or failed tool actions

    @staticmethod
    def get_element_name(elements, element_bid):
        matching_names = [item["Name"] for item in elements if item["ID"] == element_bid]
        element_name = matching_names[0] if len(matching_names) > 0 else ""
        return element_name

    def process_action_agent(
        self, page, elements: List[Dict], tool_calls: List[ToolCall], session_id=None
    ) -> str:
        """
        Processes the action agent event to retrieve content and handle any tool calls.

        :param event: Dictionary containing the ActionAgent event details.
        :return: Tuple with content string and a boolean indicating if there was a tool call.
        """

        # Retrieve the latest prediction message
        logger.debug(f"tool_calls raw: {tool_calls}")

        # Process and play tool calls if they exist
        if len(tool_calls) > 0:
            tool_calls = self.clean_tool_calls(tool_calls)
            content = f"Tool calls:\n{tool_calls}"

            for tool in tool_calls:
                element_bid = tool.get("args", {}).get("bid", None)
                element_name = ActionAgentEventProcessor.get_element_name(elements, element_bid)
                self.play_tool(page, tool, element_name, session_id)

        return content

    async def process_action_agent_async(
        self,
        page,
        elements: List[Dict],
        tool_calls: List[ToolCall],
        tool_provider: BrowserToolImplProvider,
        session_id=None,
        page_data=None,
        communicator: ChromeExtensionCommunicatorProtocol | None = None,
    ) -> str:
        """
        Processes the action agent event to retrieve content and handle any tool calls.

        :param event: Dictionary containing the ActionAgent event details.
        :return: Tuple with content string and a boolean indicating if there was a tool call.
        """

        # Retrieve the latest prediction message
        logger.debug(f"tool_calls raw: {tool_calls}")

        # Process and play tool calls if they exist
        if len(tool_calls) > 0:
            tool_calls = self.clean_tool_calls(tool_calls)
            content = f"Tool calls:\n{tool_calls}"

            for tool in tool_calls:
                tool.get("args", {}).get("bid", None)
                element_name = ""
                await self.play_tool_async(
                    page,
                    tool,
                    element_name,
                    tool_provider,
                    session_id,
                    page_data=page_data,
                    communicator=communicator,
                )

        return content

    @staticmethod
    def clean_tool_calls(tool_calls, event=None):
        if not isinstance(tool_calls, list):
            return tool_calls
        # Its every element beside the 'cur_state' key
        # for call in tool_calls:
        #     if 'args' in call and 'state' in call['args']:
        #         if 'cur_state' in call['args']['state']:
        #             cur_state = call['args']['state'].pop('cur_state', None)
        #         try:
        #             call['args']['state']['env'] = event['ActionAgent']['env']
        #         except:
        #             print(
        #                 f"Error: \n\n tool_calls = \n {tool_calls} \n\n call['args']['state'] = \n {call['args']['state']}")
        #         # call['args']['state']['env'] = env

        return tool_calls

    #
    # @observe(capture_input=False)
    # def play_tool(self, page, tool_def: Dict[str, Any], element_name, session_id=None):
    #     langfuse_context.update_current_trace(
    #         session_id=session_id
    #     )
    #     """
    #     Plays a tool action based on the tool definition and collects feedback if it fails.
    #
    #     :param tool_def: Dictionary containing tool action details.
    #     """
    #     langfuse_context.update_current_trace(name=tool_def.get("name"),
    #                                           input={"tool_def": tool_def, "element_name": element_name})
    #     action_name = tool_def.get("name").lower()
    #     args = tool_def.get("args")
    #     logger.info("Playing tool *name*: {}, arguments: {}".format(action_name, json.dumps(args)))
    #     try:
    #         if action_name == "click":
    #             click.invoke(input=tool_def['args'], config={"configurable": {"page": page, 'demo_mode': 'all_blue'}})
    #             return
    #         elif action_name == "type":
    #             typeaction.invoke(input=tool_def['args'],
    #                               config={"configurable": {"page": page, 'demo_mode': 'all_blue'},
    #                                       })
    #         elif action_name == "answer":
    #             answer.invoke(input=tool_def['args'],
    #                           config={"configurable": {"page": page, 'demo_mode': 'all_blue'},
    #                                   })
    #         else:
    #             self.collect_feedback(action_name, element_name, args, "Unrecognized tool action")
    #             return
    #
    #         self.collect_feedback(action_name, element_name, args, "")
    #     except Exception as e:
    #         self.collect_feedback(action_name, element_name, args, str(e))

    async def play_tool_async(
        self,
        page,
        tool_def: Dict[str, Any],
        element_name,
        tool_provider: BrowserToolImplProvider,
        session_id=None,
        page_data=None,
        communicator: ChromeExtensionCommunicatorProtocol | None = None,
    ):
        """
        Plays a tool action based on the tool definition and collects feedback if it fails.

        :param tool_def: Dictionary containing tool action details.
        """

        action_name = tool_def.get("name").lower()
        args = tool_def.get("args")
        logger.info("Playing tool *name*: {}, arguments: {}".format(action_name, json.dumps(args)))
        res = None
        try:
            # Build config with page data if available
            config = {"configurable": {"page": page, 'demo_mode': 'off', 'tool_impl': tool_provider}}
            if page_data:
                config["configurable"]["page_data"] = page_data
                # Get communicator from app state
                if communicator:
                    config["configurable"]["communicator"] = communicator

            if action_name == "go_back":
                res = await go_back.ainvoke(input=tool_def['args'], config=config)
            elif action_name == "click":
                res = await click.ainvoke(input=tool_def['args'], config=config)
            elif action_name == "open_app":
                res = await open_app.ainvoke(input=tool_def['args'], config=config)
            elif action_name == "type":
                res = await typeaction.ainvoke(
                    input=tool_def['args'],
                    config=config,
                )
            elif action_name == "select_option":
                res = await select_option.ainvoke(
                    input=tool_def['args'],
                    config=config,
                )
            elif action_name == "update_plan":
                pass
            else:
                self.collect_feedback(action_name, element_name, args, "Unrecognized tool action")
                return
            await asyncio.sleep(4)
            tracker.actions_count += 1
            if isinstance(res, Alert):
                self.collect_feedback(
                    action_name, element_name, args, error_message=res.message, is_alert=True
                )
            else:
                self.collect_feedback(action_name, element_name, args, error_message="")

        except Exception as e:
            self.collect_feedback(action_name, element_name, args, str(e))

    def collect_feedback(
        self, action_name: str, element_name: str, args: Any, error_message: str, is_alert=False
    ):
        """
        Collects feedback for unrecognized or error-prone tool actions.

        :param action_name: The name of the tool action.
        :param args: The arguments for the tool action.
        :param error_message: The error message or reason for feedback collection.
        """
        feedback_entry = {
            "action": action_name,
            "status": "alert"
            if is_alert
            else ("error" if error_message and len(error_message) > 0 else "success"),
            "element_id": args['bid'] if 'bid' in args else "",
            "element_name": element_name,
            "message": error_message,
        }
        self.feedback_log.append(feedback_entry)
        # langfuse_context.update_current_trace(output= self.feedback_log)

        logger.debug(f"Feedback collected for action '{action_name}'")


# Example tool handler functions
# def click_handler(args, page):
#     click.invoke(input=args, config={"configurable": {"page": page}})
#
#
# def type_handler(args, page):
#     typeaction.invoke(input=args, config={"configurable": {"page": page}})

# Usage
