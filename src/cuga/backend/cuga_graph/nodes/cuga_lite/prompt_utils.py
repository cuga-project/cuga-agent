"""
Prompt utilities for CugaLite - handles prompt creation and tool discovery.
"""

import functools
import json
from typing import Any, List, Optional
from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import AppDefinition
from cuga.backend.llm.utils.helpers import create_chat_prompt_from_templates
import os


class Tool(BaseModel):
    """
    Represents a matching tool with its name, input schema, reasoning, output schema, params_doc, and response_doc.
    """

    name: str = Field(..., description="The name of the tool.")
    input_: dict = Field(
        ...,
        alias="input",
        description="The input parameters/schema for the tool as a dictionary.",
    )
    reasoning: str = Field(
        ...,
        description="The reasoning from the shortlister agent explaining why this tool is relevant.",
    )
    output_schema: dict = Field(
        default_factory=dict,
        description="The output/response schema for the tool as a dictionary.",
    )
    params_doc: str = Field(
        default="",
        description="Documentation string describing the tool's parameters in a formatted way.",
    )
    response_doc: str = Field(
        default="",
        description="Documentation string describing the tool's response/return value schema.",
    )


class FindToolsOutput(BaseModel):
    """
    Output schema for the find_tools function.
    Returns a list of top 4 matching tools based on a natural language query.
    """

    tools: List[Tool] = Field(
        ...,
        max_length=6,
        description="A list of up to 4 matching tools, ordered by relevance to the query.",
    )


class PromptUtils:
    """Utilities for creating prompts and finding tools."""

    @staticmethod
    def get_tool_params_str(tool: StructuredTool) -> str:
        """Extract params_str (function signature format) for a tool.

        Args:
            tool: The tool to extract params_str from

        Returns:
            String representation of parameters for function signature
        """
        if hasattr(tool, 'args_schema') and tool.args_schema:
            try:
                schema = tool.args_schema.schema()
                properties = schema.get('properties', {})
                required = schema.get('required', [])

                params = []
                for name, prop in properties.items():
                    param_type = prop.get('type', 'Any')

                    type_mapping = {
                        'string': 'str',
                        'integer': 'int',
                        'number': 'float',
                        'boolean': 'bool',
                        'array': 'list',
                        'object': 'dict',
                    }
                    python_type = type_mapping.get(param_type, param_type)

                    if name in required:
                        params.append(f"{name}: {python_type}")
                    else:
                        default_val = prop.get('default', None)
                        if default_val is not None:
                            if isinstance(default_val, str):
                                params.append(f"{name}: {python_type} = \"{default_val}\"")
                            else:
                                params.append(f"{name}: {python_type} = {default_val}")
                        else:
                            params.append(f"{name}: {python_type} = None")

                return ', '.join(params) if params else ''
            except Exception as e:
                logger.debug(
                    f"Failed to parse schema for tool {tool.name if hasattr(tool, 'name') else str(tool)}: {e}"
                )
                return "**kwargs"
        else:
            return "**kwargs"

    @staticmethod
    def get_tool_docs(tool: StructuredTool) -> tuple[str, str]:
        """Extract params_doc and response_doc for a tool.

        Args:
            tool: The tool to extract docs from

        Returns:
            Tuple of (params_doc, response_doc)
        """
        params_doc = "No parameters required"
        response_doc = ""

        response_schemas = {}
        if hasattr(tool, 'func') and hasattr(tool.func, '_response_schemas'):
            response_schemas = tool.func._response_schemas

        param_constraints = {}
        if hasattr(tool, 'func') and hasattr(tool.func, '_param_constraints'):
            param_constraints = tool.func._param_constraints

        if response_schemas and isinstance(response_schemas, dict):
            if 'success' in response_schemas:
                success_schema = json.dumps(response_schemas['success'], indent=4)
                response_doc = f"\n    \n    Returns (on success) - Response Schema:\n{success_schema}"

        if hasattr(tool, 'args_schema') and tool.args_schema:
            try:
                schema = tool.args_schema.schema()
                properties = schema.get('properties', {})
                required = schema.get('required', [])

                params_list = []
                for name, prop in properties.items():
                    param_type = prop.get('type', 'string')
                    type_mapping = {
                        'string': 'str',
                        'integer': 'int',
                        'number': 'float',
                        'boolean': 'bool',
                        'array': 'list',
                        'object': 'dict',
                    }
                    python_type = type_mapping.get(param_type, param_type)

                    desc = prop.get('description', '')
                    required_mark = " (required)" if name in required else " (optional)"

                    constraints = param_constraints.get(name, []) or prop.get('constraints', [])
                    constraints_str = ""
                    if constraints:
                        constraints_str = f" [Constraints: {', '.join(constraints)}]"

                    params_list.append(f"- `{name}`: {python_type}{required_mark} - {desc}{constraints_str}")

                params_doc = "\n".join(params_list) if params_list else "No parameters required"
            except Exception:
                params_doc = "No parameters required"

        return params_doc, response_doc

    @staticmethod
    async def find_tools(
        query: str,
        all_tools: List[StructuredTool],
        all_apps: List[AppDefinition],
        llm: Optional[Any] = None,
    ) -> str:
        """
        Search tools from given applications and return the top 4 matching tools with reasoning.

        This method uses an LLM to analyze available tools from all loaded applications and
        select the most relevant ones based on a natural language query. Each returned tool
        includes detailed reasoning explaining why it was selected, along with parameter
        and response documentation.

        Args:
            query: A natural language query describing what tools are needed.
            all_tools: List of all available tools
            all_apps: List of all available app definitions

        Returns:
            str: A markdown-formatted string containing up to 4 matching tools, each with:
                 - name: The tool name
                 - reasoning: Explanation of why this tool is relevant
                 - parameters: Formatted parameter documentation
                 - response schema: Response/return value schema
        """
        prompt = create_chat_prompt_from_templates(
            system_path='./prompts/shortlister/system.jinja2',
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
        # Serialize tools properly, converting args_schema class to dict
        tools_as_dict = {}
        for tool in all_tools:
            tool_dict = tool.model_dump()
            # Extract and convert args_schema from the tool object (it's an attribute, not in model_dump)
            if hasattr(tool, 'args_schema') and tool.args_schema:
                try:
                    # Try schema() method (Pydantic v1)
                    if hasattr(tool.args_schema, 'schema'):
                        tool_dict['args_schema'] = tool.args_schema.schema()
                    # Try model_json_schema() method (Pydantic v2)
                    elif hasattr(tool.args_schema, 'model_json_schema'):
                        tool_dict['args_schema'] = tool.args_schema.model_json_schema()
                    else:
                        tool_dict['args_schema'] = {}
                except Exception as e:
                    logger.debug(f"Failed to serialize args_schema for tool {tool.name}: {e}")
                    tool_dict['args_schema'] = {}
            else:
                tool_dict['args_schema'] = {}

            # Also ensure response_schemas and param_constraints are included if they exist
            if hasattr(tool, 'func'):
                if hasattr(tool.func, '_response_schemas'):
                    tool_dict['_response_schemas'] = tool.func._response_schemas
                if hasattr(tool.func, '_param_constraints'):
                    tool_dict['_param_constraints'] = tool.func._param_constraints

            tools_as_dict[tool.name] = tool_dict

        apps_as_dict = {app.name: app.model_dump() for app in all_apps}
        from cuga.backend.llm.models import LLMManager
        from cuga.config import settings
        from cuga.backend.cuga_graph.nodes.api.shortlister_agent.prompts.load_prompt import (
            ShortListerOutputLite,
        )
        from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent

        llm_manager = LLMManager()
        model = llm or llm_manager.get_model(settings.agent.code.model)
        chain = BaseAgent.get_chain(prompt, model, ShortListerOutputLite)
        response = await chain.ainvoke(
            {
                "input": query,
                "all_apps": apps_as_dict,
                "all_tools": tools_as_dict,
                "instructions": "",
            }
        )

        enriched_tools = []
        for api_detail in response.result:
            # Find the actual tool to get input schema and output schema
            actual_tool = None
            for t in all_tools:
                if t.name == api_detail.name:
                    actual_tool = t
                    break

            if not actual_tool:
                continue

            params_doc, response_doc = PromptUtils.get_tool_docs(actual_tool)

            # Get input schema from the actual tool
            input_schema = {}
            if hasattr(actual_tool, 'args_schema') and actual_tool.args_schema:
                try:
                    input_schema = actual_tool.args_schema.schema()
                except Exception:
                    input_schema = {}

            # Get output schema from response_schemas if available
            output_schema = {}
            if hasattr(actual_tool, 'func') and hasattr(actual_tool.func, '_response_schemas'):
                response_schemas = actual_tool.func._response_schemas
                if response_schemas and isinstance(response_schemas, dict) and 'success' in response_schemas:
                    raw_output_schema = response_schemas['success']
                    # Ensure output_schema is always a dict
                    if isinstance(raw_output_schema, list):
                        # If it's a list, wrap it in a proper JSON schema format
                        if len(raw_output_schema) > 0 and isinstance(raw_output_schema[0], dict):
                            # List of objects - create array schema with items
                            output_schema = {"type": "array", "items": raw_output_schema[0]}
                        else:
                            # List of primitives - create array schema
                            output_schema = {
                                "type": "array",
                                "items": raw_output_schema[0] if raw_output_schema else {},
                            }
                    elif isinstance(raw_output_schema, dict):
                        output_schema = raw_output_schema
                    else:
                        # Fallback for other types
                        output_schema = {"value": raw_output_schema} if raw_output_schema is not None else {}

            # Use model_dump with by_alias=True to ensure proper field names
            enriched_tool = Tool(
                name=api_detail.name,
                input=input_schema,  # Use alias name
                reasoning=api_detail.reasoning,
                output_schema=output_schema,
                params_doc=params_doc,
                response_doc=response_doc,
            )
            enriched_tools.append(enriched_tool)

        if not enriched_tools:
            return "No matching tools found for your query."

        tool_descriptions = {
            tool.name: getattr(tool, 'description', None)
            for tool in all_tools
            if hasattr(tool, 'description')
        }

        markdown_lines = [
            f"# Found {len(enriched_tools)} Matching Tool(s)\n",
            f"**Query:** {query}\n",
        ]

        for idx, tool in enumerate(enriched_tools, 1):
            markdown_lines.append(f"## {idx}. `{tool.name}`\n")

            tool_description = tool_descriptions.get(tool.name)
            if tool_description:
                markdown_lines.append(f"**Description:** {tool_description}\n")

            markdown_lines.append(f"**Reasoning:** {tool.reasoning}\n")

            if tool.params_doc:
                markdown_lines.append("**Parameters:**\n")
                markdown_lines.append(f"{tool.params_doc}\n")
            else:
                markdown_lines.append("**Parameters:** No parameters required\n")

            if tool.response_doc:
                markdown_lines.append("**Response Schema:**\n")
                markdown_lines.append(f"{tool.response_doc}\n")

            if tool.input_ and tool.input_ != {}:
                markdown_lines.append("**Input Schema:**\n")
                markdown_lines.append(f"```json\n{json.dumps(tool.input_, indent=2)}\n```\n")

            if tool.output_schema and tool.output_schema != {}:
                markdown_lines.append("**Output Schema:**\n")
                markdown_lines.append(f"```json\n{json.dumps(tool.output_schema, indent=2)}\n```\n")

            markdown_lines.append("---\n")

        return "\n".join(markdown_lines)

    @staticmethod
    def create_find_tools_bound(all_tools: List[StructuredTool], all_apps: List[AppDefinition]):
        """Create a bound version of find_tools with all_tools and all_apps pre-bound.

        Args:
            all_tools: List of all available tools
            all_apps: List of all available app definitions

        Returns:
            An async callable that only requires query: str as input and returns a markdown string.
        """
        bound_func = functools.partial(
            PromptUtils.find_tools,
            all_tools=all_tools,
            all_apps=all_apps,
        )

        @functools.wraps(PromptUtils.find_tools)
        async def wrapper(query: str) -> str:
            return await bound_func(query)

        return wrapper


def format_apps_for_prompt(apps) -> list:
    """Normalize app definitions to dicts for Jinja (name, type, description), matching mcp_prompt."""
    processed_apps = []
    if not apps:
        return processed_apps
    for app in apps:
        description = getattr(app, 'description', 'No description available')
        max_length = 1000
        if len(description) > max_length:
            description = description[:max_length] + '...'
        processed_apps.append(
            {
                'name': app.name,
                'type': getattr(app, 'type', 'api'),
                'description': description,
            }
        )
    return processed_apps


def create_mcp_prompt(
    tools,
    base_prompt=None,
    allow_user_clarification=True,
    return_to_user_cases=None,
    instructions=None,
    apps=None,
    task_loaded_from_file=False,
    is_autonomous_subtask=False,
    prompt_template=None,
    enable_find_tools=False,
    enable_todos=False,
    special_instructions=None,
    has_knowledge=False,
):
    """Create a prompt for CodeAct agent that works with MCP tools.

    Args:
        tools: List of available tools
        base_prompt: Optional base prompt to prepend
        allow_user_clarification: If True, agent can ask user for clarification. If False, only final answers allowed.
        return_to_user_cases: Optional list of custom cases (in natural language) when agent should return to user.
                             If None, uses default cases.
        instructions: Optional special instructions to include in the system prompt.
        apps: Optional list of connected apps with their descriptions
        task_loaded_from_file: If True, indicates that the task was loaded from a file
        is_autonomous_subtask: If True, indicates this is an autonomous subtask that should complete without user interaction
        prompt_template: Jinja2 template for the prompt
        enable_find_tools: If True, includes find_tools instructions in the prompt
        enable_todos: If True, includes create_update_todos instructions in the prompt
    """
    processed_tools = []
    if special_instructions is None:
        special_instructions = os.getenv("CUGA_POLICIES_CONTENT", "")

    for tool in tools:
        tool_name = tool.name if hasattr(tool, 'name') else str(tool)
        tool_desc = tool.description if hasattr(tool, 'description') else "No description"

        params_str = PromptUtils.get_tool_params_str(tool)
        params_doc, response_doc = PromptUtils.get_tool_docs(tool)

        processed_tools.append(
            {
                'name': tool_name,
                'description': tool_desc,
                'params_str': params_str,
                'params_doc': params_doc,
                'response_doc': response_doc,
            }
        )

    processed_apps = format_apps_for_prompt(apps)

    prompt = prompt_template.format(
        base_prompt=base_prompt,
        apps=processed_apps,
        allow_user_clarification=allow_user_clarification,
        return_to_user_cases=return_to_user_cases,
        instructions=instructions,
        tools=processed_tools,
        task_loaded_from_file=task_loaded_from_file,
        is_autonomous_subtask=is_autonomous_subtask,
        enable_find_tools=enable_find_tools,
        enable_todos=enable_todos,
        special_instructions=special_instructions,
        has_knowledge=has_knowledge,
    )
    return prompt
