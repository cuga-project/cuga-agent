# Copyright (c) 2025 LangChain
# Modifications Copyright 2025 CUGA
# Licensed under the MIT License

import inspect
from typing import Any, Awaitable, Callable, Optional, Sequence, Type, TypeVar, Union

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langchain_core.tools import tool as create_tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command
import json
from loguru import logger
from cuga.config import settings
import re

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.llm.models import LLMManager

tracker = ActivityTracker()
llm_manager = LLMManager()


EvalFunction = Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]
EvalCoroutine = Callable[[str, dict[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]


BACKTICK_PATTERN = r'```python(.*?)```'


def extract_and_combine_codeblocks(text: str) -> str:
    """
    Extracts all codeblocks from a text string and combines them into a single code string.

    Args:
        text: A string containing zero or more codeblocks, where each codeblock is
            surrounded by triple backticks (```).

    Returns:
        A string containing the combined code from all codeblocks, with each codeblock
        separated by a newline.

    Example:
        text = '''Here's some code:

        ```python
        print('hello')
        ```
        And more:

        ```
        print('world')
        ```'''

        result = extract_and_combine_codeblocks(text)

        Result:

        print('hello')

        print('world')
    """
    # Find all code blocks in the text using regex
    # Pattern matches anything between triple backticks, with or without a language identifier
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        # Process each codeblock
        processed_blocks = []
        for block in code_blocks:
            # Strip leading and trailing whitespace
            block = block.strip()
            processed_blocks.append(block)

        # Combine all codeblocks with newlines between them
        combined_code = "\n\n".join(processed_blocks)

        # Check if the combined code has print
        if "print(" not in combined_code:
            return ""

        return combined_code

    # No markdown blocks found, check if the text itself is valid Python code
    stripped_text = text.strip()

    # Check if it has print
    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace('await ', ''), '<string>', 'exec')
        return stripped_text
    except SyntaxError:
        return ""


EvalFunction = Callable[[str, dict[str, Any]], tuple[str, dict[str, Any]]]
EvalCoroutine = Callable[[str, dict[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]


async def check_if_asking_to_proceed(content: str) -> bool:
    """
    Uses a simple LLM call to determine if the assistant is asking to proceed
    without explicitly asking for parameter approval.

    Returns:
        bool: True if asking to proceed (should auto-continue), False otherwise
    """
    check_prompt = f"""Determine if the assistant is unnecessarily explaining what they're about to do instead of just doing it.

Answer "yes" ONLY if the response matches these patterns (should auto-proceed):
- Explaining what they will do: "Let's start by reading...", "I will first read...", "Let me do that now..."
- Planning steps: "To do X, I will first do A, then B, then C"
- Announcing actions: "I'll read the contents of...", "I'm going to check..."

Answer "no" if the response:
- Actually executes code (contains code blocks)
- Asks for specific parameter values: "Which email format do you prefer?"
- Asks for explicit approval: "Should I delete this?"
- Presents final answers/results: "Here are the 5 users..."
- Reports errors needing user input

Examples that should return "yes" (auto-proceed):
1. "To determine which users from contacts.txt belong to the CRM system, I will first read the contents of the contacts.txt file and then retrieve the list of contacts from the CRM system. After that, I'll compare the two lists to identify the users that belong to the CRM system.\n\nLet's start by reading the contacts.txt file."
2. "To provide you with accurate information about CUGA, I'll read the contents of the file cuga_knowledge.md in the workspace. Let me do that now."
3. "I'll read the playbook file to understand the process."

Examples that should return "no" (needs user interaction):
1. "Which email template would you like to use - formal or casual?"
2. "Here are the 5 matching contacts: John, Jane, Bob, Alice, Charlie."
3. "I need the account ID to proceed. Which account should I query?"
4. "Should I proceed with deleting these 10 records?"

Assistant response to check:
{content}

Your answer (yes/no):"""

    try:
        checker_model = llm_manager.get_model(settings.agent.code.model)
        response = await checker_model.ainvoke([{"role": "user", "content": check_prompt}])
        decision = response.content.strip().lower()

        logger.debug(f"Proceed check decision: {decision}")
        return decision == "yes"
    except Exception as e:
        logger.warning(f"Error in proceed check: {e}")
        return False


class CodeActState(MessagesState):
    """State for CodeAct agent."""

    script: Optional[str]
    """The Python code script to be executed."""
    context: dict[str, Any]
    """Dictionary containing the execution context with available tools and variables."""


StateSchema = TypeVar("StateSchema", bound=CodeActState)
StateSchemaType = Type[StateSchema]


def create_default_prompt(tools: list[StructuredTool], base_prompt: Optional[str] = None):
    """Create default prompt for the CodeAct agent."""
    tools = [t if isinstance(t, StructuredTool) else create_tool(t) for t in tools]
    prompt = f"{base_prompt}\n\n" if base_prompt else ""
    prompt += """You will be given a task to perform. You should output either
- a Python code snippet that provides the solution to the task, or a step towards the solution. Any output you want to extract from the code should be printed to the console. Code should be output in a fenced code block.
- text to be shown directly to the user, if you want to ask for more information or provide the final answer.

In addition to the Python Standard Library, you can use the following functions:
"""

    for tool in tools:
        prompt += f'''
def {tool.name}{str(inspect.signature(tool.func))}:
    """{tool.description}"""
    ...
'''

    prompt += """

Variables defined at the top level of previous code snippets can be referenced in your code.

Reminder: use Python code snippets to call tools"""
    return prompt


def create_codeact(
    model: BaseChatModel,
    tools: Sequence[Union[StructuredTool, Callable]],
    eval_fn: Union[EvalFunction, EvalCoroutine],
    *,
    prompt: Optional[str] = None,
    state_schema: StateSchemaType = CodeActState,
) -> StateGraph:
    """Create a CodeAct agent.

    Args:
        model: The language model to use for generating code
        tools: List of tools available to the agent. Can be passed as python functions or StructuredTool instances.
        eval_fn: Function or coroutine that executes code in a sandbox. Takes code string and locals dict,
            returns a tuple of (stdout output, new variables dict)
        prompt: Optional custom system prompt. If None, uses default prompt.
            To customize default prompt you can use `create_default_prompt` helper:
            `create_default_prompt(tools, "You are a helpful assistant.")`
        state_schema: The state schema to use for the agent.

    Returns:
        A StateGraph implementing the CodeAct architecture
    """
    tools = [t if isinstance(t, StructuredTool) else create_tool(t) for t in tools]

    if prompt is None:
        prompt = create_default_prompt(tools)

    # Make tools available to the code sandbox
    tools_context = {tool.name: tool.func for tool in tools}

    async def call_model(state: StateSchema) -> Command:
        messages = [{"role": "system", "content": prompt}] + state["messages"]
        # Disable tool calling by binding no tools
        model_without_tools = model
        response = await model_without_tools.ainvoke(messages)
        # Extract and combine all code blocks
        content = response.content
        reasoning_content = response.additional_kwargs.get('reasoning_content')
        tracker.collect_step(step=Step(name="Raw_Assistant_Response", data=content))
        if not content or (reasoning_content and '```python' in reasoning_content):
            content = reasoning_content or content
        code = extract_and_combine_codeblocks(content)
        if code:
            tracker.collect_step(step=Step(name="Assistant_code", data=content))
            logger.debug(
                f"\n{'=' * 50} ASSISTANT CODE {'=' * 50}\n{code}\n{'=' * 50} END ASSISTANT CODE {'=' * 50}"
            )
            return Command(goto="sandbox", update={"messages": [response], "script": code})
        else:
            # No code block found - check if asking to proceed
            tracker.collect_step(step=Step(name="Assistant_nl", data=content))
            planning_response = response.content

            # should_auto_proceed = await check_if_asking_to_proceed(planning_response)
            # Removed dead code: if False and should_auto_proceed check

            return Command(
                update={"messages": [{"role": "assistant", "content": planning_response}], "script": None}
            )

    # If eval_fn is a async, we define async node function.
    if inspect.iscoroutinefunction(eval_fn):

        async def sandbox(state: StateSchema, config: Optional[RunnableConfig] = None):
            existing_context = state.get("context", {})
            context = {**existing_context, **tools_context}
            # Execute the script in the sandbox
            # Pass config to eval_fn if it accepts it
            eval_fn_sig = inspect.signature(eval_fn)
            if 'config' in eval_fn_sig.parameters:
                output, new_vars = await eval_fn(state["script"], context, config=config)
            else:
                output, new_vars = await eval_fn(state["script"], context)
            tracker.collect_step(step=Step(name="User_output", data=output))
            tracker.collect_step(
                step=Step(
                    name="User_output_variables",
                    data=json.dumps(
                        new_vars,
                        default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o),
                    ),
                )
            )

            # 📝 Code Execution Result
            logger.debug(
                f"\n\n------\n\n📝 Execution output:\n\n {output.strip()[: settings.advanced_features.execution_output_max_length]}{'...' if len(output.strip()) > settings.advanced_features.execution_output_max_length else ''} \n\n------\n\n"
            )

            # ──────────────────────────────────────────────────────────────
            # 🔄 Context Update: Merging execution results with existing context
            # ──────────────────────────────────────────────────────────────

            new_context = {**existing_context, **new_vars}

            # ──────────────────────────────────────────────────────────────
            # 📤 Return: Formatting execution results for model consumption
            # ──────────────────────────────────────────────────────────────

            # Return execution output as a user message so the model sees it
            tracker.collect_step(
                step=Step(
                    name="User_return",
                    data=f"Execution output:\n{output.strip()[: settings.advanced_features.execution_output_max_length]}{'...' if len(output.strip()) > settings.advanced_features.execution_output_max_length else ''}",
                )
            )

            return {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Execution output:\n{output.strip()[: settings.advanced_features.execution_output_max_length]}{'...' if len(output.strip()) > settings.advanced_features.execution_output_max_length else ''}",
                    }
                ],
                "context": new_context,
            }
    else:

        def sandbox(state: StateSchema, config: Optional[RunnableConfig] = None):
            existing_context = state.get("context", {})
            context = {**existing_context, **tools_context}
            # Execute the script in the sandbox
            # Pass config to eval_fn if it accepts it
            eval_fn_sig = inspect.signature(eval_fn)
            if 'config' in eval_fn_sig.parameters:
                output, new_vars = eval_fn(state["script"], context, config=config)
            else:
                output, new_vars = eval_fn(state["script"], context)
            new_context = {**existing_context, **new_vars}
            # Return execution output as a user message so the model sees it
            return {
                "messages": [{"role": "user", "content": f"Execution output:\n{output}"}],
                "context": new_context,
            }

    agent = StateGraph(state_schema)
    agent.add_node(call_model, destinations=(END, "sandbox"))
    agent.add_node(sandbox)
    agent.add_edge(START, "call_model")
    agent.add_edge("sandbox", "call_model")
    return agent
