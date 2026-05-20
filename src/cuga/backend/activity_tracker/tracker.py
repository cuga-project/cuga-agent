import copy
import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional
import time

import pandas as pd


from cuga.backend.cuga_graph.nodes.api.code_agent.model import CodeAgentOutput

from cuga.backend.tools_env.registry.utils.types import AppDefinition
from cuga.backend.utils.id_utils import mask_with_timestamp, random_id_with_timestamp
from cuga.config import TRAJECTORY_DATA_DIR, settings
from langchain_core.tools import StructuredTool
from loguru import logger
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, Field

AGENT_ANALYTICS = True
try:
    from agent_analytics.instrumentation.utils import AIEventRecorder
    from agent_analytics_core.interfaces.annotations import DataAnnotation
except Exception:
    AGENT_ANALYTICS = False
    logger.warning("Ignoring agent analytics")


class MergeResult(BaseModel):
    folder_name: str
    merged_task_ids: List[str]


class Prompt(BaseModel):
    role: str
    value: str


class Step(BaseModel):
    name: Optional[str] = ""
    plan: Optional[str] = ""
    prompts: List[Prompt] = Field(default_factory=list)
    data: Optional[str] = ""
    task_decomposition: Optional[str] = ""
    current_url: Optional[str] = ""
    action_formatted: Optional[str] = ""
    action_type: Optional[str] = ""
    action_args: Optional[Any] = ""
    observation_before: Optional[str] = ""
    image_before: Optional[str] = ""


class TasksMetadata(BaseModel):
    task_ids: List[str]
    description: Optional[str] = ""
    experiment_name: str
    experiment_folder: str
    created_at: str


class ActivityTracker(object):
    _instance = None
    start_time: float = 0
    user_id: str = ""
    intent: str = ""
    session_id: str = ""
    run_id: str = ""
    dataset_name: str = ""
    prompts: List[Prompt] = []
    current_date: Optional[str] = None
    pi: Optional[str] = None
    eval: Any = None
    final_answer: Optional[str] = None
    task_id: str = "default"
    actions_count: int = 0
    token_usage: int = 0
    steps: List[Step] = []
    images: List[str] = []
    score: float = 0.0
    tools: Dict[str, List[StructuredTool]] = {}
    apps: List[AppDefinition] = []
    # Task management attributes
    tasks: Dict[str, Dict[str, Any]] = {}
    experiment_folder: Optional[str] = None
    tasks_metadata: Optional[TasksMetadata] = None
    # Base directory configuration
    _base_dir: str = TRAJECTORY_DATA_DIR

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ActivityTracker, cls).__new__(cls)
        return cls._instance

    async def invoke_tool(self, server_name: str, tool_name: str, args: dict):
        if server_name not in self.tools:
            raise ValueError(f"Server '{server_name}' not found")

        # Find the tool by name
        for tool in self.tools[server_name]:
            if tool.name == tool_name:
                result = await tool.ainvoke(args)
                logger.debug(f"type of {type(result)}")
                # logger.debug(f"Tool output call {result.con}")
                # Check if result is JSON parseable
                if isinstance(result, CallToolResult):
                    result = result.content[0]
                    if isinstance(result, TextContent):
                        result = result.text
                if isinstance(result, str):
                    try:
                        res = json.loads(result)
                        logger.debug("json res worked!")
                        return res
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("no json tool output !!")
                        # Not valid JSON, return original result
                        return result
                else:
                    logger.debug(f"answer is not str answer is of type {type(result)}")
                    # Result is not a string, return as-is
                    return result

        # Tool not found
        available_tools = [tool.name for tool in self.tools[server_name]]
        raise ValueError(
            f"Tool '{tool_name}' not found in server '{server_name}'. Available tools: {available_tools}"
        )

    def invoke_tool_sync(self, server_name: str, tool_name: str, args: dict):
        """Synchronous version of invoke_tool to avoid async/sync context issues"""
        import asyncio
        import concurrent.futures

        if server_name not in self.tools:
            raise ValueError(f"Server '{server_name}' not found")

        # Find the tool by name
        for tool in self.tools[server_name]:
            if tool.name == tool_name:
                # Try synchronous invoke first
                try:
                    result = tool.invoke(args)  # Use synchronous invoke
                except RuntimeError as e:
                    if "event loop is already running" in str(e):
                        # We're in an async context, need to handle this differently
                        try:
                            # Check if we have a running loop
                            asyncio.get_running_loop()

                            # We're in an async context, create a new thread to run the async function
                            def run_in_new_loop():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    # Use async invoke in the new loop
                                    async def async_invoke():
                                        return await tool.ainvoke(args)

                                    return new_loop.run_until_complete(async_invoke())
                                finally:
                                    new_loop.close()

                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(run_in_new_loop)
                                result = future.result()
                        except RuntimeError:
                            # No running loop, use asyncio.run
                            import asyncio

                            async def async_invoke():
                                return await tool.ainvoke(args)

                            result = asyncio.run(async_invoke())
                    else:
                        raise
                # logger.debug(f"type of {type(result)}")
                # logger.debug(f"Tool output call {result}")
                # Check if result is JSON parseable
                if isinstance(result, CallToolResult):
                    result = result.content[0]
                    if isinstance(result, TextContent):
                        result = result.text
                if isinstance(result, str):
                    try:
                        res = json.loads(result)
                        logger.debug("json res worked!")
                        return res
                    except (json.JSONDecodeError, TypeError):
                        logger.debug("no json tool output !!")
                        # Not valid JSON, return original result
                        return result
                else:
                    logger.debug(f"answer is not str answer is of type {type(result)}")
                    # Result is not a string, return as-is
                    return result

        # Tool not found
        available_tools = [tool.name for tool in self.tools[server_name]]
        raise ValueError(
            f"Tool '{tool_name}' not found in server '{server_name}'. Available tools: {available_tools}"
        )

    def get_tools_by_server(self, server_name: str) -> Dict[str, Dict]:
        tools = self.tools
        if server_name not in tools:
            return {}
        server_tools = {}
        for tool in tools[server_name]:
            tool_config = {
                "app_name": server_name,
                "secure": False,
                "api_name": tool.name,
                "path": '',
                "method": '',
                "description": tool.description or '',
                "parameters": tool.args_schema.model_json_schema(),
                "response_schemas": 'Any',
                "canary_string": '',
            }

            server_tools[tool.name] = tool_config

        return server_tools

    def set_tools(self, tools: List[StructuredTool]):
        """
        Detects application prefixes and assigns server_name to tool metadata.
        Returns list of AppDefinition objects for all detected applications.
        Optionally fills self.tools dictionary with server_name grouped tools.

        - For tools with metadata=None OR server_name=None: assigns detected app name or 'default'
        - For tools with existing server_name: leaves unchanged

        Args:
            tools (list): List of tool objects with .name and .metadata attributes
            self_tools (dict, optional): Dictionary to fill with server_name grouped tools

        Returns:
            List[AppDefinition]: List of app definitions with tools description
        """

        self.tools = {}
        # logger.debug(f"tools:  {tools}")

        # Common prefixes to exclude (HTTP methods, etc.)
        excluded_prefixes = {'get', 'post', 'put', 'delete', 'patch', 'head', 'options', 'trace'}

        # Step 1: Extract tool names for analysis (only for tools that need server_name assignment)
        tools_to_process = [
            tool for tool in tools if tool.metadata is None or tool.metadata.get("server_name", None) is None
        ]
        tool_names = [tool.name for tool in tools_to_process]
        # Step 2: Find potential prefixes and count occurrences
        prefix_candidates = {}

        for tool_name in tool_names:
            # Split by underscore and take the first part as potential prefix
            if '_' in tool_name:
                potential_prefix = tool_name.split('_')[0].lower()

                # Skip if it's an excluded prefix
                if potential_prefix not in excluded_prefixes:
                    if potential_prefix not in prefix_candidates:
                        prefix_candidates[potential_prefix] = []
                    prefix_candidates[potential_prefix].append(tool_name)

        # Step 3: Filter prefixes that appear in multiple tools (consistency check)
        detected_applications = {}
        for prefix, tool_list in prefix_candidates.items():
            if len(tool_list) > 1:  # Prefix appears in multiple tools - consistent!
                detected_applications[prefix.upper()] = tool_list

        # Step 4: Assign server_name to metadata for tools that need it
        for tool in tools:
            # Only process tools with metadata=None OR server_name=None
            if tool.metadata is None or tool.metadata.get("server_name", None) is None:
                tool_name = tool.name
                server_name = 'default_app'  # Default assignment

                # Check if this tool belongs to any detected application
                for app_name, app_tools in detected_applications.items():
                    if tool_name in app_tools:
                        server_name = app_name
                        break

                # Initialize metadata if it's None, otherwise just update server_name
                if tool.metadata is None:
                    tool.metadata = {"server_name": server_name}
                else:
                    tool.metadata["server_name"] = server_name

        # Step 5: Fill self.tools dictionary if provided
        for tool in tools:
            # Get server_name from tool metadata
            server_name = tool.metadata.get('server_name')

            # Skip tools without server_name metadata
            if server_name is None:
                raise Exception("Tool server name is none!")

            # Initialize list for this server if it doesn't exist
            if server_name not in self.tools:
                self.tools[server_name] = []
            # Add tool to the appropriate server group
            self.tools[server_name].append(tool)

        # Step 6: Collect all unique server_names and their associated tools
        app_tools_map = {}
        for tool in tools:
            if tool.metadata is not None:
                server_name = tool.metadata.get("server_name")
                if server_name:
                    if server_name not in app_tools_map:
                        app_tools_map[server_name] = []
                    app_tools_map[server_name].append(tool)

        # Step 7: Create AppDefinition objects
        app_definitions = []
        for app_name, tool_list in app_tools_map.items():
            tools_description = "Available tools:\n" + "\n".join(
                f"{tool.name}: {tool.description}" if tool.description else f"{tool.name}:"
                for tool in sorted(tool_list, key=lambda x: x.name)
            )

            app_def = AppDefinition(name=app_name, description=tools_description, url=None)
            app_definitions.append(app_def)

        self.apps = app_definitions

    def set_base_dir(self, base_dir: str) -> None:
        """
        Set the base directory for logging trajectory data.

        Args:
            base_dir (str): The base directory path for storing experiment data
        """
        self._base_dir = base_dir
        logger.info(f"Base directory set to: {self._base_dir}")

    def get_base_dir(self) -> str:
        """
        Get the current base directory for logging trajectory data.

        Returns:
            str: The current base directory path
        """
        return self._base_dir

    def get_current_trajectory_path(self) -> Optional[str]:
        """
        Get the full path of the current experiment folder.

        Returns:
            Optional[str]: The full path of the experiment folder, or None if no experiment is active.
        """
        if self.experiment_folder:
            return os.path.join(self._base_dir, self.experiment_folder, self.task_id + ".json")
        return ""

    def generate_session_id(self):
        self.session_id = random_id_with_timestamp(full_date=True)

    def generate_run_id(self):
        self.run_id = random_id_with_timestamp(full_date=True)

    def reset(self, intent, task_id="default"):
        self.token_usage = 0
        self.start_time = time.time()
        self.current_date = None
        self.pi = None
        self.prompts = []
        self.run_id = ""
        self.steps = []
        self.images = []
        self.actions_count = 0
        self.final_answer = None
        self.task_id = task_id
        self.intent = intent
        self.user_id = None

    def reload_steps(self, task_id: Optional[str] = None) -> bool:
        """
        Reload steps from the current experiment's task JSON file.

        Args:
            task_id (str, optional): Task ID to reload. If None, uses current task_id.

        Returns:
            bool: True if steps were successfully reloaded, False otherwise.
        """
        # Use provided task_id or fall back to current task_id
        target_task_id = task_id if task_id is not None else self.task_id

        if not target_task_id or target_task_id == "default":
            logger.error("No valid task_id provided for reloading steps")
            return False

        # Get the trajectory path for the specified task
        self.task_id = target_task_id
        trajectory_path = self.get_current_trajectory_path()

        if not trajectory_path:
            logger.error(f"No trajectory path found for task_id: {target_task_id}")
            return False

        if not os.path.exists(trajectory_path):
            logger.error(f"Trajectory file does not exist: {trajectory_path}")
            return False

        try:
            # Read the JSON file
            with open(trajectory_path, 'r', encoding='utf-8') as f:
                trajectory_data = json.load(f)

            # Extract steps from the JSON
            steps_data = trajectory_data.get('steps', [])

            # Convert dictionaries back to Step objects
            reloaded_steps = []
            for step_dict in steps_data:
                try:
                    step = Step(**step_dict)
                    reloaded_steps.append(step)
                except Exception as e:
                    logger.warning(f"Failed to convert step data to Step object: {e}")
                    continue

            # Update current steps
            self.steps = reloaded_steps

            logger.info(f"Successfully reloaded {len(reloaded_steps)} steps for task_id: {target_task_id}")
            return True

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error reading trajectory file {trajectory_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error while reloading steps: {e}")
            return False

    def start_experiment(
        self, task_ids: List[str], experiment_name: str, description: Optional[str] = ""
    ) -> str:
        """
        Start a new experiment with given task IDs.

        Args:
            task_ids (List[str]): List of task IDs for this experiment
            experiment_name (str): Name of the experiment
            description (str, optional): Description of the experiment

        Returns:
            str: The experiment folder name
        """
        # Generate experiment folder name using mask_with_timestamp
        self.experiment_folder = mask_with_timestamp(experiment_name, full_date=True)

        # Create metadata
        self.tasks_metadata = TasksMetadata(
            task_ids=task_ids,
            description=description,
            experiment_name=experiment_name,
            experiment_folder=self.experiment_folder,
            created_at=datetime.now().isoformat(),
        )

        # Only create files and directories if tracker is enabled
        if settings.advanced_features.tracker_enabled:
            # Create directory structure
            experiment_dir = os.path.join(self._base_dir, self.experiment_folder)
            os.makedirs(experiment_dir, exist_ok=True)

            # Save metadata to file
            metadata_path = os.path.join(experiment_dir, "metadata.json")
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(self.tasks_metadata.model_dump(), f, indent=2, ensure_ascii=False)

            # Initialize empty files
            self._initialize_experiment_files(experiment_dir)

        # Reset tasks dictionary
        self.tasks = {}
        return self.experiment_folder

    def _initialize_experiment_files(self, experiment_dir: str) -> None:
        """Initialize empty result files for the experiment."""
        # Define column order for CSV
        columns = [
            'task_id',
            'site',
            'intent',
            'agent_answer',
            'eval',
            'score',
            'exception',
            'num_steps',
            'fail_category',
            'agent_v',
        ]

        # Create empty results.csv
        results_csv_path = os.path.join(experiment_dir, "results.csv")
        df = pd.DataFrame(columns=columns)
        df.to_csv(results_csv_path, index=False, encoding='utf-8')

        # Create empty results.json
        results_json_path = os.path.join(experiment_dir, "results.json")
        with open(results_json_path, 'w', encoding='utf-8') as f:
            json.dump({}, f, indent=2, ensure_ascii=False)

        # Create empty .progress file
        progress_path = os.path.join(experiment_dir, ".progress")
        with open(progress_path, 'w', encoding='utf-8') as f:
            f.write("")

    def collect_prompt(self, role: str, value: str):
        self.prompts.append(Prompt(role=role, value=value))

    def collect_tokens_usage(self, count: int) -> None:
        """
        Increases the number of tokens used.

        Args:
            count (int): The number of times the token is used.
        """
        self.token_usage += count

    def collect_image(self, img: str) -> None:
        if not img:
            return
        # Ensure the image string is compatible with OpenAI vision API: must be a valid URL or a data URL.
        if img.startswith("data:image") or img.startswith("http://") or img.startswith("https://"):
            self.images.append(img)
        else:
            # Assume raw base64 PNG data; prepend appropriate data URL header.
            self.images.append(f"data:image/png;base64,{img}")

    def collect_step(self, step: Step) -> None:
        """
        Collects a step, adding it to the steps list.

        Args:
            step (Step): The description of the step to collect.
        """

        data_json = None
        try:
            data_json = json.loads(step.data)

        except Exception:
            pass

        # Attach any collected prompts to this step so they are persisted
        if getattr(self, "prompts", None):
            try:
                step.prompts = list(self.prompts)
            except Exception:
                # Ensure prompts never break logging
                step.prompts = []
        # Attach the most recent captured image (if any) to the step
        if getattr(self, "images", None):
            try:
                # Use the last captured screenshot as the "before" image
                step.image_before = self.images[-1]
            except Exception:
                step.image_before = None
        if AGENT_ANALYTICS:
            if step.name == "TaskAnalyzerAgent":
                AIEventRecorder.record_data_annotation(
                    name=step.name,
                    annotation_type=DataAnnotation.Type.RAW_TEXT,
                    annotation_title="Intent",
                    annotation_content=self.intent,
                )
            if step.name == "CodeAgent":
                res_obj = CodeAgentOutput(**json.loads(step.data))
                AIEventRecorder.record_data_annotation(
                    name="CodeAgent",
                    annotation_type=DataAnnotation.Type.CODE_GENERATION,
                    annotation_title="Generated Code",
                    annotation_content="\n" + res_obj.code,
                )
                AIEventRecorder.record_data_annotation(
                    name="CodeAgent",
                    annotation_type=DataAnnotation.Type.CODE_SNIPPET,
                    annotation_title="Code output",
                    annotation_content="\n" + res_obj.execution_output,
                )
                AIEventRecorder.record_data_annotation(
                    name="CodeAgent",
                    annotation_type=DataAnnotation.Type.RAW_TEXT,
                    annotation_title="Output summary",
                    annotation_content="\n" + res_obj.summary,
                )
            else:
                if data_json and isinstance(data_json, dict):
                    if data_json.get('thoughts', None):
                        AIEventRecorder.record_data_annotation(
                            name=step.name,
                            annotation_type=DataAnnotation.Type.THOUGHT,
                            annotation_title=step.name,
                            annotation_content=f"{data_json.get('thoughts', None)}",
                        )
                    if len(list(data_json.keys())) == 1 and isinstance(
                        data_json[list(data_json.keys())[0]], str
                    ):
                        AIEventRecorder.record_data_annotation(
                            name=step.name,
                            annotation_type=DataAnnotation.Type.RAW_TEXT,
                            annotation_title=step.name,
                            annotation_content=f"\n\n{data_json[list(data_json.keys())[0]]}",
                        )
                    else:
                        AIEventRecorder.record_data_annotation(
                            name=step.name,
                            annotation_type=DataAnnotation.Type.RAW_TEXT,
                            annotation_title=step.name,
                            annotation_content=json.dumps(data_json),
                        )
                else:
                    AIEventRecorder.record_data_annotation(
                        name=step.name,
                        annotation_type=DataAnnotation.Type.RAW_TEXT,
                        annotation_title=step.name,
                        annotation_content=f"{step.data}",
                    )
                if step.image_before:
                    AIEventRecorder.record_data_annotation(
                        name=step.name,
                        annotation_type=DataAnnotation.Type.MULTIMODAL_DATA,
                        annotation_title="Image",
                        annotation_content=f"{step.image_before}",
                    )

        step.prompts = copy.deepcopy(self.prompts)
        self.prompts = []
        self.steps.append(step)
        if settings.advanced_features.tracker_enabled:
            self.to_file()
        self.prompts = []

    def collect_step_external(self, step: Step, full_path: Optional[str] = None) -> None:
        """
        Collects a step and saves it to a separate log file in a directory
        specified by an environment variable.

        The path is retrieved from os.environ['current_folder_path'].
        The steps are saved to a file named 'recordinglg.json' in that directory.

        Args:
            step (Step): The Step object to collect.
            full_path (Optional[str]): The full file path to save to. If None, the step is skipped.

        TODO: Properly handle None full_path case - either provide a default path or make the
        calling code always provide a valid path. Currently returns early if None to avoid errors.
        """
        try:
            if not settings.advanced_features.tracker_enabled:
                return

            # TODO: Handle None full_path properly - either use a default path or require callers to provide one
            if not full_path:
                logger.debug("Skipping external step collection: full_path is None")
                return

            if not os.path.exists(os.path.dirname(full_path)):
                logger.error(
                    f"External path directory not found or does not exist: {os.path.dirname(full_path)}"
                )
                return

            step.prompts = copy.deepcopy(self.prompts)
            self.prompts = []
            self.steps.append(step)
            self._to_file_external_append(full_path, step)
            logger.info(f"Step appended to external file: {full_path}")

        except Exception as e:
            logger.error(f"Failed to collect and save external step: {e}")

    def _to_file_external_append(self, full_path: str, new_step: Step):
        """
        Append a new step to an existing JSON file or create a new file if it doesn't exist.

        This method reads the existing file, appends the new step, and saves it back.

        Args:
            full_path (str): The full file path to save/append to.
            new_step (Step): The new step to append.
        """
        try:
            # Check if file exists and read existing data
            if os.path.exists(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    try:
                        existing_data = json.load(f)
                        # Ensure the existing data has the expected structure
                        if not isinstance(existing_data, dict) or 'steps' not in existing_data:
                            logger.warning(f"Invalid JSON structure in {full_path}, creating new file")
                            existing_data = None
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON in {full_path}, creating new file: {e}")
                        existing_data = None
            else:
                existing_data = None

            # If no valid existing data, create new structure
            if existing_data is None:
                data_to_save = {
                    "intent": self.intent,
                    "dataset_name": self.dataset_name,
                    "actions_count": self.actions_count,
                    "task_id": self.task_id,
                    "eval": self.eval,
                    "steps": [new_step.model_dump()],
                    "score": self.score,
                }
            else:
                # Update existing data with new step
                existing_data["steps"].append(new_step.model_dump())
                # Update other fields that might have changed
                existing_data.update(
                    {
                        "intent": self.intent,
                        "dataset_name": self.dataset_name,
                        "actions_count": self.actions_count,
                        "task_id": self.task_id,
                        "eval": self.eval,
                        "score": self.score,
                    }
                )
                data_to_save = existing_data

            # Write the updated data back to file
            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(
                    data_to_save,
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

        except Exception as e:
            logger.error(f"Failed to append step to file {full_path}: {e}")
            raise

    def collect_score(self, score: float) -> None:
        """
        Collects a step, adding it to the steps list.

        Args:
            score (str): The description of the step to collect.
        """
        self.score = score
        if settings.advanced_features.tracker_enabled:
            self.to_file()

    def collect_step_with_pass(self) -> None:
        """
        Placeholder for collecting a step.
        """
        pass

    def to_file(self):
        """Save current task data to file in the experiment directory."""
        if self.experiment_folder:
            # Save to experiment directory
            source_dir = os.path.join(self._base_dir, self.experiment_folder)
        else:
            # Fallback to original behavior
            source_dir = "logging{}".format("_" + self.dataset_name if self.dataset_name else "")

        os.makedirs(source_dir, exist_ok=True)

        filename = self.task_id if self.task_id != "default" else self.session_id
        filepath = os.path.join(source_dir, f"{filename}.json")

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(
                {
                    "intent": self.intent,
                    "dataset_name": self.dataset_name,
                    "actions_count": self.actions_count,
                    "task_id": self.task_id,
                    "eval": self.eval,
                    "steps": [d.model_dump() for d in self.steps],
                    "score": self.score,
                },
                f,
                ensure_ascii=False,
                indent=4,
            )

    def finish_task(
        self,
        task_id: str,
        site: str,
        intent: str,
        agent_answer: Optional[str] = None,
        eval: Optional[str] = None,
        score: Optional[float] = None,
        exception: Optional[bool] = None,
        num_steps: Optional[int] = None,
        fail_category: Optional[str] = None,
        agent_v: Optional[str] = None,
        duration: Optional[int] = None,
        total_llm_calls: Optional[int] = None,
        total_tokens: Optional[int] = None,
        total_cost: Optional[float] = None,
        total_cache_input_tokens: Optional[int] = None,
    ) -> str:
        """
        Mark a task as finished and update result files.

        Args:
            task_id (str): Required unique identifier for the task
            site (str): Required site name
            intent (str): Task intent/description
            agent_answer (str, optional): Agent's answer
            eval (str, optional): Evaluation details
            score (float, optional): Task score
            exception (bool, optional): Whether an exception occurred
            num_steps (int, optional): Number of steps taken
            fail_category (str, optional): Category of failure if applicable
            agent_v (str, optional): Agent version

        Returns:
            str: The ID of the finished task
        """
        if not self.experiment_folder:
            raise ValueError("No experiment started. Call start_experiment() first.")

        # Calculate number of api calls
        api_calls_num = len([step for step in self.steps if "api_call" in step.name])
        # Add task to internal storage
        self.tasks[task_id] = {
            "site": site,
            "intent": intent,
            "agent_answer": agent_answer,
            "eval": eval,
            "score": score,
            "exception": exception,
            "num_steps": num_steps if num_steps is not None else len(self.steps),
            "fail_category": fail_category,
            "agent_v": agent_v,
            "duration": duration if duration is not None else time.time() - self.start_time,
            "total_llm_calls": total_llm_calls,
            "total_tokens": self.token_usage if not total_tokens else total_tokens,
            "api_calls": api_calls_num,
            "total_cost": total_cost,
            "total_cache_input_tokens": total_cache_input_tokens,
        }

        # Update result files only if tracker is enabled
        if settings.advanced_features.tracker_enabled:
            self._update_result_files()
            self._add_to_progress_file(task_id)

        return task_id

    def _update_result_files(self) -> None:
        """Update both JSON and CSV result files."""
        if not self.experiment_folder:
            return

        experiment_dir = os.path.join(self._base_dir, self.experiment_folder)

        # Update results.json
        results_json_path = os.path.join(experiment_dir, "results.json")
        with open(results_json_path, 'w', encoding='utf-8') as f:
            json.dump(self.tasks, f, indent=2, ensure_ascii=False)

        # Update results.csv
        self._save_csv(experiment_dir)

    def _save_csv(self, experiment_dir: str) -> None:
        """Save current tasks to CSV file using pandas."""
        # Define the column order
        columns = [
            'task_id',
            'site',
            'intent',
            'agent_answer',
            'eval',
            'score',
            'exception',
            'num_steps',
            'fail_category',
            'agent_v',
            'duration',
            'total_llm_calls',
            'total_tokens',
            'api_calls',
            'total_cost',
            'total_cache_input_tokens',
        ]

        if not self.tasks:
            # Create empty DataFrame with headers if no tasks
            df = pd.DataFrame(columns=columns)
        else:
            # Convert tasks dictionary to list of dictionaries for DataFrame
            data = []
            for task_id, task_data in self.tasks.items():
                row = {'task_id': task_id}
                row.update(task_data)
                data.append(row)

            # Create DataFrame
            df = pd.DataFrame(data)

            # Reorder columns to match the desired order
            df = df.reindex(columns=columns)

        # Save to CSV
        results_csv_path = os.path.join(experiment_dir, "results.csv")
        df.to_csv(results_csv_path, index=False, encoding='utf-8')

    def _add_to_progress_file(self, task_id: str) -> None:
        """Add a task ID to the .progress file."""
        if not self.experiment_folder:
            return

        progress_path = os.path.join(self._base_dir, self.experiment_folder, ".progress")
        with open(progress_path, 'a', encoding='utf-8') as f:
            f.write(task_id + '\n')

    def update_task(
        self,
        task_id: str,
        site: Optional[str] = None,
        intent: Optional[str] = None,
        agent_answer: Optional[str] = None,
        eval: Optional[str] = None,
        score: Optional[float] = None,
        exception: Optional[bool] = None,
        num_steps: Optional[int] = None,
        fail_category: Optional[str] = None,
        agent_v: Optional[str] = None,
    ) -> bool:
        """
        Update an existing task.

        Args:
            task_id (str): ID of the task to update
            site (str, optional): New site
            intent (str, optional): New intent
            agent_answer (str, optional): New agent answer
            eval (str, optional): New evaluation
            score (float, optional): New score
            exception (bool, optional): New exception status
            num_steps (int, optional): New number of steps
            fail_category (str, optional): New fail category
            agent_v (str, optional): New agent version

        Returns:
            bool: True if task was updated, False if task not found
        """
        if task_id not in self.tasks:
            return False

        # Update only provided fields
        if site is not None:
            self.tasks[task_id]["site"] = site
        if intent is not None:
            self.tasks[task_id]["intent"] = intent
        if agent_answer is not None:
            self.tasks[task_id]["agent_answer"] = agent_answer
        if eval is not None:
            self.tasks[task_id]["eval"] = eval
        if score is not None:
            self.tasks[task_id]["score"] = score
        if exception is not None:
            self.tasks[task_id]["exception"] = exception
        if num_steps is not None:
            self.tasks[task_id]["num_steps"] = num_steps
        if fail_category is not None:
            self.tasks[task_id]["fail_category"] = fail_category
        if agent_v is not None:
            self.tasks[task_id]["agent_v"] = agent_v

        if settings.advanced_features.tracker_enabled:
            self._update_result_files()
        return True

    def remove_task(self, task_id: str) -> bool:
        """
        Remove a task from the results.

        Args:
            task_id (str): ID of the task to remove

        Returns:
            bool: True if task was removed, False if task not found
        """
        if task_id in self.tasks:
            del self.tasks[task_id]
            if settings.advanced_features.tracker_enabled:
                self._update_result_files()
            return True
        return False

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific task by ID.

        Args:
            task_id (str): ID of the task to retrieve

        Returns:
            Dict containing task data or None if not found
        """
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all tasks.

        Returns:
            Dict containing all tasks
        """
        return self.tasks.copy()

    def find_tasks_by_score(self, score: float) -> Dict[str, Dict[str, Any]]:
        """
        Find all tasks with a specific score.

        Args:
            score (float): Score to search for

        Returns:
            Dict containing matching tasks
        """
        return {task_id: task for task_id, task in self.tasks.items() if task.get("score") == score}

    def find_tasks_by_site(self, site: str) -> Dict[str, Dict[str, Any]]:
        """
        Find all tasks with a specific site.

        Args:
            site (str): Site to search for

        Returns:
            Dict containing matching tasks
        """
        return {task_id: task for task_id, task in self.tasks.items() if task.get("site") == site}

    def find_tasks_by_exception(self, exception: bool) -> Dict[str, Dict[str, Any]]:
        """
        Find all tasks with specific exception status.

        Args:
            exception (bool): Exception status to search for

        Returns:
            Dict containing matching tasks
        """
        return {task_id: task for task_id, task in self.tasks.items() if task.get("exception") == exception}

    def find_tasks_by_agent_version(self, agent_v: str) -> Dict[str, Dict[str, Any]]:
        """
        Find all tasks with a specific agent version.

        Args:
            agent_v (str): Agent version to search for

        Returns:
            Dict containing matching tasks
        """
        return {task_id: task for task_id, task in self.tasks.items() if task.get("agent_v") == agent_v}

    def clear_all_tasks(self) -> None:
        """Remove all tasks from result files."""
        self.tasks = {}
        if self.experiment_folder and settings.advanced_features.tracker_enabled:
            self._update_result_files()
            # Clear progress file
            progress_path = os.path.join(self._base_dir, self.experiment_folder, ".progress")
            with open(progress_path, 'w', encoding='utf-8') as f:
                f.truncate(0)

    def get_task_count(self) -> int:
        """
        Get the total number of tasks.

        Returns:
            int: Number of tasks
        """
        return len(self.tasks)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get basic statistics about the tasks.

        Returns:
            Dict containing task statistics
        """
        if not self.tasks:
            return {"total_tasks": 0}

        stats = {
            "total_tasks": len(self.tasks),
            "tasks_with_exceptions": len([t for t in self.tasks.values() if t.get("exception") is True]),
            "tasks_without_exceptions": len([t for t in self.tasks.values() if t.get("exception") is False]),
            "unique_sites": len(set(t.get("site") for t in self.tasks.values() if t.get("site"))),
            "unique_agent_versions": len(
                set(t.get("agent_v") for t in self.tasks.values() if t.get("agent_v"))
            ),
        }

        # Score statistics
        scores = [t.get("score") for t in self.tasks.values() if t.get("score") is not None]
        if scores:
            stats["average_score"] = sum(scores) / len(scores)
            stats["min_score"] = min(scores)
            stats["max_score"] = max(scores)

        return stats

    def get_dataframe(self) -> pd.DataFrame:
        """
        Get all tasks as a pandas DataFrame.

        Returns:
            pd.DataFrame: DataFrame containing all tasks
        """
        columns = [
            'task_id',
            'site',
            'intent',
            'agent_answer',
            'eval',
            'score',
            'exception',
            'num_steps',
            'fail_category',
            'agent_v',
        ]

        if not self.tasks:
            return pd.DataFrame(columns=columns)

        data = []
        for task_id, task_data in self.tasks.items():
            row = {'task_id': task_id}
            row.update(task_data)
            data.append(row)

        df = pd.DataFrame(data)
        return df.reindex(columns=columns)

    def _copy_task_json_files(
        self,
        source_folders: List[str],
        target_folder: str,
        selected_task_ids: List[str],
        base_dir: str = None,
    ) -> None:
        """
        Copy individual task JSON files from source folders to target folder.

        Args:
            source_folders (List[str]): List of source experiment folder names
            target_folder (str): Target experiment folder name
            selected_task_ids (List[str]): List of task IDs to copy
            base_dir (str, optional): Base directory. If None, uses instance base_dir
        """
        if base_dir is None:
            base_dir = self._base_dir

        target_dir = os.path.join(base_dir, target_folder)

        copied_files = 0
        skipped_files = 0

        for task_id in selected_task_ids:
            file_found = False

            # Look for the task JSON file in each source folder
            for folder_name in source_folders:
                source_dir = os.path.join(base_dir, folder_name)
                source_file = os.path.join(source_dir, f"{task_id}.json")

                if os.path.exists(source_file):
                    target_file = os.path.join(target_dir, f"{task_id}.json")

                    try:
                        # Copy the file
                        shutil.copy2(source_file, target_file)
                        logger.debug(f"Copied {task_id}.json from {folder_name}")
                        copied_files += 1
                        file_found = True
                        break  # Found and copied, move to next task

                    except Exception as e:
                        logger.error(f"Failed to copy {task_id}.json from {folder_name}: {e}")

            if not file_found:
                logger.warning(f"Task JSON file {task_id}.json not found in any source folder")
                skipped_files += 1

        logger.info(f"Task JSON files - Copied: {copied_files}, Skipped: {skipped_files}")

    def merge_experiments(
        self,
        experiment_folders: List[str],
        output_experiment_name: str,
        description: Optional[str] = "Merged experiments",
        output_folder: Optional[str] = None,
    ) -> MergeResult:
        """
        Merge multiple experiment folders, preferring tasks with score 1.0 over 0.0.
        Also copies individual task JSON files from source experiments.

        Args:
            experiment_folders (List[str]): List of experiment folder names to merge
            output_experiment_name (str): Name for the merged experiment
            description (str, optional): Description for the merged experiment

        Returns:
            MergeResult: Contains folder_name and merged_task_ids
        """
        logger.info(f"Starting merge of {len(experiment_folders)} experiments")

        # Create new experiment for merged results
        merged_folder = self.start_experiment(
            task_ids=[],  # Will be populated with merged task IDs
            experiment_name=output_experiment_name,
            description=description,
        )

        merged_tasks = {}
        all_task_ids = set()
        task_source_mapping = {}  # Track which folder each task came from

        # First pass: collect all tasks and identify duplicates
        for folder_name in experiment_folders:
            folder_path = os.path.join(self._base_dir, folder_name)
            results_json_path = os.path.join(folder_path, "results.json")

            if not os.path.exists(results_json_path):
                logger.warning(f"Results file not found in {folder_name}, skipping")
                continue

            try:
                with open(results_json_path, 'r', encoding='utf-8') as f:
                    folder_tasks = json.load(f)

                logger.info(f"Processing {len(folder_tasks)} tasks from {folder_name}")

                for task_id, task_data in folder_tasks.items():
                    all_task_ids.add(task_id)

                    if task_id not in merged_tasks:
                        # First occurrence of this task
                        merged_tasks[task_id] = {**task_data, 'source_experiment': folder_name}
                        task_source_mapping[task_id] = folder_name
                        logger.debug(f"Added new task {task_id} from {folder_name}")
                    else:
                        # Task already exists, apply preference logic
                        existing_score = merged_tasks[task_id].get('score', 0.0)
                        new_score = task_data.get('score', 0.0)
                        if existing_score == 1.0 and new_score != 1.0:
                            # Keep existing (perfect score)
                            should_replace = False
                        elif existing_score != 1.0 and new_score == 1.0:
                            # Replace with perfect score
                            should_replace = True
                        elif existing_score == new_score:
                            # Same score, keep existing (first found)
                            should_replace = False
                        else:
                            # Different scores, prefer higher
                            should_replace = new_score > existing_score

                        if should_replace:
                            merged_tasks[task_id] = {**task_data, 'source_experiment': folder_name}
                            task_source_mapping[task_id] = folder_name
                            logger.debug(
                                f"Replaced task {task_id}: {existing_score} -> {new_score} from {folder_name}"
                            )
                        else:
                            logger.debug(
                                f"Kept existing task {task_id}: score {existing_score} vs {new_score}"
                            )

            except Exception as e:
                logger.error(f"Error processing {folder_name}: {e}")
                continue

        # Update the merged experiment with final task list
        self.tasks = merged_tasks

        # Update metadata with actual task IDs
        if self.tasks_metadata:
            self.tasks_metadata.task_ids = list(all_task_ids)

            # Save updated metadata
            experiment_dir = os.path.join(self._base_dir, merged_folder)
            metadata_path = os.path.join(experiment_dir, "metadata.json")
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(self.tasks_metadata.model_dump(), f, indent=2, ensure_ascii=False)

        # Update result files with merged data only if tracker is enabled
        if settings.advanced_features.tracker_enabled:
            self._update_result_files()

            # Update progress file with all task IDs
            for task_id in merged_tasks.keys():
                self._add_to_progress_file(task_id)

        # Copy individual task JSON files only if tracker is enabled
        if settings.advanced_features.tracker_enabled:
            logger.info("Copying individual task JSON files...")
            selected_task_ids = list(merged_tasks.keys())
            self._copy_task_json_files(experiment_folders, merged_folder, selected_task_ids)

        logger.success(f"Successfully merged {len(merged_tasks)} tasks into {merged_folder}")
        logger.info(f"Source experiments: {experiment_folders}")
        score_distribution = {}
        source_distribution = {}
        for task_data in merged_tasks.values():
            score = task_data.get('score', 0.0)
            source = task_data.get('source_experiment', 'unknown')
            score_distribution[score] = score_distribution.get(score, 0) + 1
            source_distribution[source] = source_distribution.get(source, 0) + 1

        logger.info(f"Score distribution in merged results: {score_distribution}")
        logger.info(f"Source distribution in merged results: {source_distribution}")

        # Return MergeResult
        return MergeResult(folder_name=merged_folder, merged_task_ids=list(merged_tasks.keys()))

    def list_experiment_folders(self, base_path: Optional[str] = None) -> List[str]:
        """
        List all available experiment folders.

        Args:
            base_path (str, optional): Base directory to search for experiments.
                                     If None, uses instance base_dir

        Returns:
            List[str]: List of experiment folder names
        """
        if base_path is None:
            base_path = self._base_dir

        if not os.path.exists(base_path):
            logger.warning(f"Base path {base_path} does not exist")
            return []

        folders = []
        for item in os.listdir(base_path):
            item_path = os.path.join(base_path, item)
            if os.path.isdir(item_path):
                # Check if it looks like an experiment folder (has metadata.json)
                metadata_path = os.path.join(item_path, "metadata.json")
                if os.path.exists(metadata_path):
                    folders.append(item)

        logger.info(f"Found {len(folders)} experiment folders")
        return sorted(folders)

    @staticmethod
    def list_experiment_folders_static(base_path: str = "./logging/trajectory_data") -> List[str]:
        """
        Static method to list all available experiment folders.

        Args:
            base_path (str): Base directory to search for experiments

        Returns:
            List[str]: List of experiment folder names
        """
        if not os.path.exists(base_path):
            logger.warning(f"Base path {base_path} does not exist")
            return []

        folders = []
        for item in os.listdir(base_path):
            item_path = os.path.join(base_path, item)
            if os.path.isdir(item_path):
                # Check if it looks like an experiment folder (has metadata.json)
                metadata_path = os.path.join(item_path, "metadata.json")
                if os.path.exists(metadata_path):
                    folders.append(item)

        logger.info(f"Found {len(folders)} experiment folders")
        return sorted(folders)

    def get_experiment_progress(self, experiment_folder_name: str) -> Dict[str, Any]:
        """
        Get the progress of a specific experiment.

        Args:
            experiment_folder_name (str): The name of the experiment folder.

        Returns:
            Dict[str, Any]: A dictionary containing 'total_tasks', 'completed_tasks', and 'uncompleted_task_ids'.
                             Returns default values if files are not found or errors occur.
        """
        experiment_dir = os.path.join(self._base_dir, experiment_folder_name)
        metadata_path = os.path.join(experiment_dir, "metadata.json")
        progress_path = os.path.join(experiment_dir, ".progress")

        total_tasks = 0
        completed_tasks = 0
        all_task_ids = set()
        completed_task_ids = set()

        # Read total tasks from metadata.json
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                all_task_ids = set(metadata.get('task_ids', []))
                total_tasks = len(all_task_ids)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error reading metadata.json for {experiment_folder_name}: {e}")
        else:
            logger.warning(f"metadata.json not found for experiment: {experiment_folder_name}")

        # Read completed tasks from .progress
        if os.path.exists(progress_path):
            try:
                with open(progress_path, 'r', encoding='utf-8') as f:
                    completed_task_ids = set(line.strip() for line in f if line.strip())
                completed_tasks = len(completed_task_ids)
            except IOError as e:
                logger.error(f"Error reading .progress file for {experiment_folder_name}: {e}")
        else:
            logger.info(f".progress file not found for experiment: {experiment_folder_name}")

        uncompleted_task_ids = list(sorted(list(all_task_ids - completed_task_ids)))

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "uncompleted_task_ids": uncompleted_task_ids,
        }
