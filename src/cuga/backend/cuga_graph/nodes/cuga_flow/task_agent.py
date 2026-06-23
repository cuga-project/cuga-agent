"""
Task Agent - Wrapper around CugaAgent for BPMN task execution.

This module implements the TaskAgent which wraps CugaAgent to provide
a standardized interface for executing BPMN tasks within a flow process.
It handles state management, result recording, and integration with the
FlowAgent orchestration layer.
"""

from typing import Dict, List, Any, Optional, Callable
from loguru import logger

from cuga.sdk import CugaAgent
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_agent_state import FlowState
from langchain_core.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langchain_core.callbacks import BaseCallbackHandler


class TaskAgent:
    """
    Wrapper around CugaAgent for BPMN task execution.

    The TaskAgent provides a standardized interface for executing tasks
    within a BPMN process flow. It:
    - Wraps CugaAgent with flow-specific context
    - Manages task input/output with process variables
    - Records execution results in FlowState
    - Provides hooks for pre/post task execution

    Each BPMN task element can be bound to a TaskAgent instance,
    which executes the task logic using the underlying CugaAgent.
    """

    def __init__(
        self,
        task_id: str,
        task_name: str,
        agent: Optional[CugaAgent] = None,
        tools: Optional[List[BaseTool]] = None,
        model: Optional[BaseChatModel] = None,
        callbacks: Optional[List[BaseCallbackHandler]] = None,
        special_instructions: Optional[str] = None,
        input_mapping: Optional[Dict[str, str]] = None,
        output_mapping: Optional[Dict[str, str]] = None,
        pre_execute: Optional[Callable[[FlowState], None]] = None,
        post_execute: Optional[Callable[[FlowState, Any], None]] = None,
    ):
        """
        Initialize TaskAgent.

        Args:
            task_id: BPMN task element ID
            task_name: Human-readable task name
            agent: Optional pre-configured CugaAgent (if not provided, creates new one)
            tools: Tools to provide to the agent (if creating new agent)
            model: Language model (if creating new agent)
            callbacks: Callback handlers (if creating new agent)
            special_instructions: Task-specific instructions for the agent
            input_mapping: Map process variables to task inputs (e.g., {"amount": "purchase_amount"})
            output_mapping: Map task outputs to process variables (e.g., {"approved": "is_approved"})
            pre_execute: Optional hook called before task execution
            post_execute: Optional hook called after task execution
        """
        self.task_id = task_id
        self.task_name = task_name
        self.input_mapping = input_mapping or {}
        self.output_mapping = output_mapping or {}
        self.pre_execute = pre_execute
        self.post_execute = post_execute

        # Create or use provided agent
        if agent:
            self.agent = agent
        else:
            self.agent = CugaAgent(
                tools=tools, model=model, callbacks=callbacks, special_instructions=special_instructions
            )

        logger.info(f"TaskAgent initialized: {task_id} ({task_name})")

    async def execute(self, state: FlowState, task_input: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute the task using the underlying CugaAgent.

        Args:
            state: Current flow state
            task_input: Optional explicit task input (overrides input mapping)

        Returns:
            Dict containing task execution results
        """
        logger.info(f"Executing task: {self.task_id} ({self.task_name})")

        try:
            # Pre-execution hook
            if self.pre_execute:
                self.pre_execute(state)

            # Prepare task input from process variables
            if task_input is None:
                task_input = self._prepare_input(state)

            # Execute task using CugaAgent
            result = await self.agent.invoke(task_input)

            # Process and map outputs
            task_result = self._process_output(result, state)

            # Record result in state
            state.record_task_result(self.task_id, task_result)

            # Post-execution hook
            if self.post_execute:
                self.post_execute(state, task_result)

            logger.info(f"Task completed: {self.task_id}")
            return task_result

        except Exception as e:
            logger.error(f"Error executing task {self.task_id}: {e}")
            error_result = {"status": "failed", "success": False, "error": str(e), "task_id": self.task_id}
            state.record_task_result(self.task_id, error_result)
            return error_result

    def _prepare_input(self, state: FlowState) -> str:
        """
        Prepare task input from process variables using input mapping.

        Args:
            state: Current flow state

        Returns:
            Formatted input string for the agent
        """
        if not self.input_mapping:
            # No mapping, use generic context
            return f"Execute task: {self.task_name}"

        # Build input from mapped variables
        input_parts = [f"Task: {self.task_name}\n"]

        for task_param, process_var in self.input_mapping.items():
            value = state.get_process_variable(process_var)
            if value is not None:
                input_parts.append(f"{task_param}: {value}")

        return "\n".join(input_parts)

    def _process_output(self, result: Any, state: FlowState) -> Dict[str, Any]:
        """
        Process task output and update process variables using output mapping.

        Args:
            result: Raw result from CugaAgent
            state: Current flow state

        Returns:
            Processed task result dict
        """
        # Extract result content
        if isinstance(result, dict):
            content = result.get("output", result.get("content", str(result)))
        else:
            content = str(result)

        task_result = {
            "status": "completed",
            "success": True,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "output": content,
        }

        # Apply output mapping to process variables
        if self.output_mapping:
            import json as _json

            # Try to parse the output as JSON so individual keys can be extracted.
            # Use JSONDecoder.raw_decode to handle preamble text before the JSON object.
            parsed_output = None
            try:
                raw = content.strip()
                # Strip markdown code fences if present
                if "```" in raw:
                    fence_start = raw.find("```")
                    fence_end = raw.rfind("```")
                    if fence_end > fence_start:
                        inner = raw[fence_start + 3:fence_end].strip()
                        if inner.startswith("json"):
                            inner = inner[4:].strip()
                        raw = inner
                # Find the first JSON object anywhere in the content
                brace_pos = raw.find("{")
                if brace_pos != -1:
                    parsed_output, _ = _json.JSONDecoder().raw_decode(raw, brace_pos)
                else:
                    parsed_output = _json.loads(raw)
            except (ValueError, TypeError):
                pass

            logger.info(f"Task '{self.task_id}' output_mapping — parsed_output: {parsed_output}")

            for output_key, process_var in self.output_mapping.items():
                if isinstance(result, dict) and output_key in result:
                    value = result[output_key]
                elif isinstance(parsed_output, dict) and output_key in parsed_output:
                    value = parsed_output[output_key]
                else:
                    value = content

                state.set_process_variable(process_var, value)
                logger.info(f"  set_process_variable('{process_var}', {value!r})")

        return task_result




# Made with Bob
