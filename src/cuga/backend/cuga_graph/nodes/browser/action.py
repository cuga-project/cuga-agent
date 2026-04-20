import re
from typing import Any, Dict, Union

from langchain_core.messages import AIMessage
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.cuga_graph.nodes.browser.action_agent.action_agent import ActionAgent
from cuga.backend.cuga_graph.nodes.shared.base_node import BaseNode
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.utils.consts import (
    ANSWER_KEYS,
    ARGS_KEY,
    EMPTY_STATE_ID,
    FINAL_ANSWER,
    ID_KEY,
    NO_BID_ACTIONS,
    STATE_KEY,
    UNKNOWN_ID,
)

tracker = ActivityTracker()


def is_valid_element(element: Any) -> bool:
    if isinstance(element, int):
        return True
    if isinstance(element, str):
        if element.isdigit():
            return True
        if FINAL_ANSWER in element:
            return True
        if re.match(r'^[a-zA-Z]\d+$', element):
            return True
    return False


def extract_element_predicted(action_state: Union[Dict[str, Any], Any]) -> Any:
    if isinstance(action_state, dict):
        try:
            return next(iter(action_state.values()))
        except StopIteration:
            return action_state
    return action_state


def process_element(element: str) -> Union[str, int, None]:
    # Remove any surrounding brackets, braces, or whitespace
    element = element.strip('[]{}() \t\n\r')

    # Check if it's a pure digit
    if element.isdigit():
        return int(element)

    # Check if it's in the format of a letter followed by digits
    match = re.match(r'^([a-zA-Z])(\d+)$', element)
    if match:
        return f"{match.group(1)}{match.group(2)}"

    # Check if it contains 'FINAL ANSWER'
    if 'FINAL ANSWER' in element:
        return 'FINAL ANSWER'

    # If we can't process it, return the original element
    return element


def element_fixing_attempt(element: Any) -> Union[str, int, None]:
    if isinstance(element, (int, str)):
        return process_element(str(element))
    elif isinstance(element, dict):
        # If it's a dict, try to process the first value
        try:
            first_value = next(iter(element.values()))
            return process_element(str(first_value))
        except StopIteration:
            return None
    elif isinstance(element, list):
        # If it's a list, try to process the first item
        if element:
            return process_element(str(element[0]))
    return None


def update_call_with_fixed_element(call: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if ARGS_KEY in call and isinstance(call[ARGS_KEY], dict):
            state = call[ARGS_KEY].get(STATE_KEY, {})

            if not isinstance(state, dict):
                call[ARGS_KEY][STATE_KEY] = {ID_KEY: str(state)}
            elif not state:
                call[ARGS_KEY][STATE_KEY] = {ID_KEY: EMPTY_STATE_ID}
            elif ID_KEY not in state:
                # Try to find an ID-like key or use the first item
                id_key = next((k for k in state if ID_KEY in k.lower()), None)
                if id_key:
                    state[ID_KEY] = state.pop(id_key)
                else:
                    first_key, first_value = next(iter(state.items()), (None, None))
                    if first_key is not None:
                        state[ID_KEY] = str(first_value)
                    else:
                        state[ID_KEY] = UNKNOWN_ID

            # Ensure the ID is properly formatted
            if ID_KEY in state:
                state[ID_KEY] = element_fixing_attempt(state[ID_KEY])
            return call
    except Exception as e:
        print(f"Error in update_call_with_fixed_element: {str(e)}")
        print(f"Call structure: {call}")
        return call  # Return the original call if we can't process it


class ActionNode(BaseNode):
    def __init__(self, action_agent: ActionAgent):
        super().__init__()
        self.action_agent = action_agent
        agent = self.action_agent
        name = self.action_agent.name

        def node(state: AgentState) -> AgentState:
            return ActionNode.node_handler(state, agent=agent, name=name)

        self.node = node

    @staticmethod
    def node_handler(state: AgentState, agent: ActionAgent, name: str) -> AgentState:
        result: AIMessage = agent.run(state)

        if not result.tool_calls:
            logger.warning("No tool calls by action agent")
            state.sender = name
            state.messages.append(result)
            return state

        for call in result.tool_calls:
            tracker.collect_step(
                step=Step(
                    name=name,
                    observation_before=state.elements_as_string,
                    action_type=call["name"],
                    action_args=call["args"],
                    action_formatted="{}({})".format(
                        call["name"], ", ".join(f"{k}={v}" for k, v in call['args'].items())
                    ),
                )
            )

            if call["name"] in NO_BID_ACTIONS:
                if 'human_in_the_loop' in call["name"]:
                    for key in ANSWER_KEYS:
                        # Added check for 'state' key and create if not present
                        if 'state' not in call["args"]:
                            call["args"]['state'] = {}

                        if key in call["args"]:
                            call["args"]['state'][key] = call["args"].pop(key)

                if call["name"] == "update_plan":
                    try:
                        if 'state' in call['args']:
                            reason_key = (
                                'reason'
                                if 'reason' in call['args']['state']
                                else next(iter(call['args']['state']), None)
                            )
                            if reason_key:
                                state.update_plan_reason = call['args']['state'][reason_key]
                            else:
                                logger.warn(
                                    f"Warning: No valid reason key found in call['args']['state']: {call['args']['state']}"
                                )
                        else:
                            reason_key = (
                                'reason' if 'reason' in call['args'] else next(iter(call['args']), None)
                            )
                            if reason_key:
                                state.update_plan_reason = call['args'][reason_key]
                            else:
                                logger.warn(
                                    f"Warning: No valid reason key found in call['args']: {call['args']}"
                                )
                    except Exception as e:
                        logger.error(f"Error in update_plan handling: {str(e)}")
                        logger.error(f"call['args']: {call['args']}")
                continue

            # call = update_call_with_fixed_element(call)

            action_state = call["args"]

            if call["name"] == "answer":
                result.content = f"FINAL ANSWER \n {action_state}"
                state.messages.append(result)
                state.sender = "END"
                return state
            element_predicted = extract_element_predicted(action_state)
            if not is_valid_element(element_predicted):
                feedback = {
                    "action": call["name"],
                    "status": "error",
                    "message": f"ERROR: You have predicted to perform {call['name']} of {element_predicted} as the next action. Please provide the element ID!",
                    "update_reason": (
                        state.update_plan_reason if state.update_plan_reason else "No reason provided"
                    ),
                }
                state.feedback.append(feedback)
                if len(result.tool_calls) == 1:
                    state.messages.append(AIMessage(content=state.observation, name="call_tool"))
                    state.sender = "ActionAgent"
                    return state
                else:
                    # Delete this call
                    result.tool_calls.remove(call)

            # Process valid element_predicted
            if isinstance(element_predicted, str):
                if 'FINAL ANSWER' in element_predicted:
                    state.observation = element_predicted
                    state.messages.append(AIMessage(content=element_predicted, name="END"))
                    state.sender = "END"
                    return state
        result.name = name
        state.messages.append(result)
        state.sender = name
        return state
