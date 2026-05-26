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

            # Try to parse the output as JSON so individual keys can be extracted
            parsed_output = None
            try:
                raw = content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                parsed_output = _json.loads(raw.strip())
            except (ValueError, TypeError):
                pass

            for output_key, process_var in self.output_mapping.items():
                if isinstance(result, dict) and output_key in result:
                    value = result[output_key]
                elif isinstance(parsed_output, dict) and output_key in parsed_output:
                    value = parsed_output[output_key]
                else:
                    value = content

                state.set_process_variable(process_var, value)
                logger.debug(f"Mapped output '{output_key}' to process variable '{process_var}': {value}")

        return task_result

    async def stream(self, state: FlowState, task_input: Optional[str] = None):
        """
        Stream task execution (for real-time updates).

        Args:
            state: Current flow state
            task_input: Optional explicit task input

        Yields:
            Chunks of execution output
        """
        logger.info(f"Streaming task: {self.task_id} ({self.task_name})")

        try:
            # Pre-execution hook
            if self.pre_execute:
                self.pre_execute(state)

            # Prepare input
            if task_input is None:
                task_input = self._prepare_input(state)

            # Stream from CugaAgent
            accumulated_result = []
            async for chunk in self.agent.stream(task_input):
                accumulated_result.append(chunk)
                yield chunk

            # Process final result
            final_result = "".join(str(c) for c in accumulated_result)
            task_result = self._process_output(final_result, state)

            # Record result
            state.record_task_result(self.task_id, task_result)

            # Post-execution hook
            if self.post_execute:
                self.post_execute(state, task_result)

            logger.info(f"Task streaming completed: {self.task_id}")

        except Exception as e:
            logger.error(f"Error streaming task {self.task_id}: {e}")
            error_result = {"status": "failed", "success": False, "error": str(e), "task_id": self.task_id}
            state.record_task_result(self.task_id, error_result)
            yield f"Error: {e}"

    def get_info(self) -> Dict[str, Any]:
        """Get information about this task agent."""
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "has_input_mapping": bool(self.input_mapping),
            "has_output_mapping": bool(self.output_mapping),
            "has_pre_hook": self.pre_execute is not None,
            "has_post_hook": self.post_execute is not None,
            "agent_type": type(self.agent).__name__,
        }


class TaskAgentFactory:
    """
    Factory for creating TaskAgent instances from configuration.

    Simplifies creation of TaskAgents from YAML configuration or
    programmatic definitions.
    """

    @staticmethod
    def create_from_config(task_id: str, config: Dict[str, Any]) -> TaskAgent:
        """
        Create TaskAgent from configuration dict.

        Args:
            task_id: BPMN task element ID
            config: Configuration dict with keys:
                - name: Task name
                - tools: List of tool names or tool instances
                - instructions: Special instructions
                - input_mapping: Input variable mapping
                - output_mapping: Output variable mapping

        Returns:
            Configured TaskAgent instance
        """
        return TaskAgent(
            task_id=task_id,
            task_name=config.get("name", task_id),
            tools=config.get("tools"),
            special_instructions=config.get("instructions"),
            input_mapping=config.get("input_mapping"),
            output_mapping=config.get("output_mapping"),
        )

    @staticmethod
    def create_simple(task_id: str, task_name: str, instructions: str) -> TaskAgent:
        """
        Create a simple TaskAgent with just instructions.

        Args:
            task_id: BPMN task element ID
            task_name: Human-readable task name
            instructions: Task instructions for the agent

        Returns:
            Simple TaskAgent instance
        """
        return TaskAgent(task_id=task_id, task_name=task_name, special_instructions=instructions)


# Made with Bob
