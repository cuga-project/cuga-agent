import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from cuga.backend.cuga_graph.nodes.api.code_agent.model import CodeAgentOutput
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.nodes.api.tasks.summarize_code import summarize_steps
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from loguru import logger
from cuga.configurations.instructions_manager import InstructionsManager

instructions_manager = InstructionsManager()
llm_manager = LLMManager()


class CodeAgent(BaseAgent):
    def __init__(self, llm: BaseChatModel, tools: Any = None):
        super().__init__()
        self.name = "CodeAgent"
        self.code_planner_enabled = settings.advanced_features.code_planner_enabled
        if not self.code_planner_enabled:
            pmt_user_path = "./prompts/user_no_plan.jinja2"
            systempmt_path = "./prompts/system_no_plan.jinja2"
        else:
            pmt_user_path = "./prompts/user.jinja2"
            systempmt_path = (
                "./prompts/system_fast.jinja2"
                if settings.features.code_generation == "fast"
                else "./prompts/system_accurate.jinja2"
            )
        pmt_template = load_prompt_simple(systempmt_path, pmt_user_path)
        self.instructions = instructions_manager.get_instructions(self.name)
        # For CodeAgent, we don't need structured output, just raw text to extract code from
        self.chain = BaseAgent.get_chain(prompt_template=pmt_template, llm=llm, wx_json_mode="no_format")
        self.summary_task = summarize_steps(llm_manager.get_model(settings.agent.final_answer.model))

    @staticmethod
    def output_parser(result: BaseMessage, name) -> BaseMessage:
        result.name = name
        return result

    def get_last_nonempty_line(self, text, limit=5):
        """
        Get the first non-empty JSON line from the end and return it along with the remaining text.

        Args:
            text (str): Input text to process
            limit (int): Maximum number of lines to check from the end (default: 5)

        Returns:
            tuple: (json_text, remaining_text) where:
                   - json_text: The JSON string found from the end, or empty string if none
                   - remaining_text: All text before the JSON line, or original text if no JSON found
        """
        lines = text.split("\n")

        # Iterate from the end to find first non-empty JSON line (limit iterations)
        count = 0
        for i, line in enumerate(reversed(lines)):
            if count >= limit:
                break
            count += 1

            stripped_line = line.strip()

            # Check if line has content and is valid JSON
            if stripped_line:
                try:
                    json_lines = json.loads(stripped_line)
                    # Found valid JSON - calculate the split point
                    json_line_index = len(lines) - 1 - i  # Convert reverse index to forward index

                    # Get text before the JSON line
                    remaining_lines = lines[:json_line_index]
                    remaining_text = "\n".join(remaining_lines)

                    return json_lines, remaining_text
                except (json.JSONDecodeError, ValueError):
                    # Not valid JSON, continue searching
                    continue

        # No valid JSON found, return empty JSON and original text
        return "", text

    def extract_inner_text(self, data):
        try:
            # Parse the JSON string
            return data['messages']

        except json.JSONDecodeError:
            return "Error: Invalid JSON format"
        except Exception as e:
            return f"Error: {str(e)}"

    # Example usage
    def extract_from_json_marker(self, text):
        marker = "```json"
        if marker in text:
            # Find the position of the marker
            start_pos = text.find(marker)
            # Extract everything starting from the marker
            return text[start_pos:].strip()
        return text

    def extract_code_from_response(self, text: str) -> str:
        """
        Extracts all codeblocks from a text string and combines them into a single code string.

        Args:
            text: A string containing zero or more codeblocks, where each codeblock is
                surrounded by triple backticks (```).

        Returns:
            A string containing the combined code from all codeblocks, with each codeblock
                separated by a newline.
        """
        import re

        BACKTICK_PATTERN = r"(?:^|\n)```(.*?)(?:```(?:\n|$))"
        # Find all code blocks in the text using regex
        # Pattern matches anything between triple backticks, with or without a language identifier
        code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)
        if not code_blocks:
            logger.debug("Generated code has no code blocks")
            return text
        # Process each codeblock
        processed_blocks = []
        for block in code_blocks:
            # Strip leading and trailing whitespace
            block = block.strip()

            # If the first line looks like a language identifier, remove it
            lines = block.split("\n")
            if lines and (not lines[0].strip() or " " not in lines[0].strip()):
                # First line is empty or likely a language identifier (no spaces)
                block = "\n".join(lines[1:])

            processed_blocks.append(block)

        # Combine all codeblocks with newlines between them
        combined_code = "\n\n".join(processed_blocks)
        return combined_code

    async def run(self, input_variables: AgentState = None) -> AIMessage:
        context_variables = input_variables.coder_variables
        context_variables_preview = (
            input_variables.variables_manager.get_variables_summary(context_variables)
            if context_variables and len(context_variables) > 0
            else "N/A"
        )

        # Invoke the chain to get code
        response = await self.chain.ainvoke(
            input={
                "coder_task": input_variables.coder_task,
                "api_planner_codeagent_plan": self.extract_from_json_marker(
                    input_variables.api_planner_codeagent_plan
                ),
                "variables_preview": context_variables_preview,
                "api_shortlister_planner_filtered_apis": input_variables.api_shortlister_planner_filtered_apis,
                "current_datetime": input_variables.current_datetime,
                "instructions": self.instructions if self.instructions else "",
            }
        )
        logger.debug(f"Response: {response.content}")
        # Extract code from response (assuming it contains code blocks)
        code = self.extract_code_from_response(response.content)
        logger.debug(f"Generated code: {code}")

        # Run code using CodeExecutor (mode determined by settings)
        try:
            execution_output, _ = await CodeExecutor.eval_for_code_agent(
                code=code,
                state=input_variables,
            )
        except Exception as e:
            logger.error(f"Error running code: {e}")
            execution_output = str(e)

        # Process the output - extract JSON from last line
        out, remaining_text = self.get_last_nonempty_line(execution_output, limit=5)
        steps_summary = []
        if out:
            steps_summary = [remaining_text]

        if not out:
            out = {
                "variable_name": "output_status",
                "value": execution_output,
            }
            logger.warning("Not json output")

        input_variables.variables_manager.add_variable(
            name=out.get("variable_name"),
            description=out.get("description", ""),
            value=out.get("value"),
        )

        final_answer = None
        if settings.features.code_output_summary:
            final_answer = await self.summary_task.ainvoke(
                input={
                    "api_calling_plan": input_variables.api_planner_codeagent_plan,
                    "execution_output": remaining_text[:50000],
                    "variable_summary": input_variables.variables_manager.get_variables_summary(),
                }
            )

        logger.debug(
            f"\nvariable_name: {out.get('variable_name')}\ndescription: {out.get('description', '')}\nvalue: {out.get('value')}\n"
        )

        return AIMessage(
            content=CodeAgentOutput(
                code=code,
                summary=final_answer.content
                if final_answer
                else f"The output of code stored in variable {out.get('variable_name')} - {out.get('description', '')}",
                steps_summary=steps_summary,
                variables=out,
                execution_output=execution_output,
            ).model_dump_json()
        )

    @staticmethod
    def create():
        return CodeAgent(
            llm=llm_manager.get_model(settings.agent.code.model),
        )
