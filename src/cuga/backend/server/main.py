import asyncio
import datetime
import platform
import re
import shutil
import os
import subprocess
import uuid
import yaml
import httpx
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Union, Optional
from pathlib import Path
from cuga.backend.utils.id_utils import random_id_with_timestamp
import traceback
from pydantic import BaseModel, ValidationError
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage
from loguru import logger

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.configurations.instructions_manager import InstructionsManager
from cuga.backend.tools_env.registry.utils.api_utils import get_apps, get_apis
from cuga.cli import start_extension_browser_if_configured
from cuga.backend.browser_env.browser.extension_env_async import ExtensionEnv
from cuga.backend.browser_env.browser.gym_obs.http_stream_comm import (
    ChromeExtensionCommunicatorHTTP,
    ChromeExtensionCommunicatorProtocol,
)
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import format_tools
from cuga.backend.cuga_graph.graph import DynamicAgentGraph
from cuga.backend.cuga_graph.utils.controller import AgentRunner
from cuga.backend.cuga_graph.utils.event_porcessors.action_agent_event_processor import (
    ActionAgentEventProcessor,
)
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse
from cuga.backend.cuga_graph.state.agent_state import AgentState, default_state
from cuga.backend.browser_env.browser.gym_env_async import BrowserEnvGymAsync
from cuga.backend.browser_env.browser.open_ended_async import OpenEndedTaskAsync
from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop, AgentLoopAnswer, StreamEvent, OutputFormat
from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url
from cuga.config import (
    get_app_name_from_url,
    get_user_data_path,
    settings,
    PACKAGE_ROOT,
    LOGGING_DIR,
    TRACES_DIR,
)


try:
    from langfuse.langchain import CallbackHandler
except ImportError:
    try:
        from langfuse.callback.langchain import LangchainCallbackHandler as CallbackHandler
    except ImportError:
        CallbackHandler = None
from fastapi.responses import StreamingResponse, JSONResponse
import json

# Import embedded assets with feature flag
USE_EMBEDDED_ASSETS = os.getenv("USE_EMBEDDED_ASSETS", "false").lower() in ("true", "1", "yes", "on")

if USE_EMBEDDED_ASSETS:
    try:
        from .embedded_assets import embedded_assets

        print("✅ Using embedded assets (enabled via USE_EMBEDDED_ASSETS)")
    except ImportError:
        USE_EMBEDDED_ASSETS = False
        print("❌ Embedded assets enabled but not found, falling back to file system")
else:
    print("📁 Using file system assets (embedded assets disabled)")

try:
    from agent_analytics.instrumentation.configs import OTLPCollectorConfig
    from agent_analytics.instrumentation import agent_analytics_sdk
except ImportError as e:
    logger.warning(f"Failed to import agent_analytics: {e}")
    OTLPCollectorConfig = None
    agent_analytics_sdk = None

# Moved to top of file

# Path constants
TRACE_LOG_PATH = os.path.join(TRACES_DIR, "trace.log")
FRONTEND_DIST_DIR = os.path.join(PACKAGE_ROOT, "..", "frontend_workspaces", "frontend", "dist")
EXTENSION_DIR = os.path.join(PACKAGE_ROOT, "..", "frontend_workspaces", "extension", "releases", "chrome-mv3")
STATIC_DIR_FLOWS_PATH = os.path.join(PACKAGE_ROOT, "backend", "server", "flows")
SAVE_REUSE_PY_PATH = os.path.join(
    PACKAGE_ROOT, "backend", "tools_env", "registry", "mcp_servers", "saved_flows.py"
)

# Create logging directory
if settings.advanced_features.tracker_enabled:
    os.makedirs(LOGGING_DIR, exist_ok=True)


class AppState:
    """A class to hold and manage all application state variables."""

    def __init__(self):
        # Initializing all state variables to None or default values.
        self.tracker: Optional[ActivityTracker] = None
        self.env: Optional[BrowserEnvGymAsync | ExtensionEnv] = None
        self.agent: Optional[DynamicAgentGraph] = (
            None  # Replace Any with your Agent's class type if available
        )
        self.policy_system: Optional[Any] = None  # PolicyConfigurable instance
        self.policy_filesystem_sync: Optional[Any] = None  # PolicyFilesystemSync instance
        # Per-thread cancellation events for concurrent user support
        # Using asyncio.Event for thread-safe cancellation signaling
        self.stop_events: Dict[str, asyncio.Event] = {}
        self.output_format: OutputFormat = (
            OutputFormat.WXO if settings.advanced_features.wxo_integration else OutputFormat.DEFAULT
        )
        self.package_dir: str = PACKAGE_ROOT

        # Set up static directories - use embedded assets if available
        if USE_EMBEDDED_ASSETS:
            try:
                frontend_path, extension_path = embedded_assets.extract_assets()
                self.STATIC_DIR_HTML: Optional[str] = str(frontend_path)
                self.EXTENSION_PATH: Optional[str] = str(extension_path)
                print(f"✅ Using embedded frontend: {self.STATIC_DIR_HTML}")
                print(f"✅ Using embedded extension: {self.EXTENSION_PATH}")
            except Exception as e:
                print(f"❌ Failed to extract embedded assets: {e}")
                self.static_dirs: List[str] = [FRONTEND_DIST_DIR]
                self.STATIC_DIR_HTML: Optional[str] = next(
                    (d for d in self.static_dirs if os.path.exists(d)), None
                )
                self.EXTENSION_PATH: Optional[str] = EXTENSION_DIR
        else:
            self.static_dirs: List[str] = [FRONTEND_DIST_DIR]
            self.STATIC_DIR_HTML: Optional[str] = next(
                (d for d in self.static_dirs if os.path.exists(d)), None
            )
            self.EXTENSION_PATH: Optional[str] = EXTENSION_DIR
        self.STATIC_DIR_FLOWS: str = STATIC_DIR_FLOWS_PATH
        self.save_reuse_process: Optional[asyncio.subprocess.Process] = None
        self.initialize_sdk()

    def initialize_sdk(self):
        """Initializes the analytics SDK and logging."""
        logs_dir_path = TRACES_DIR

        if agent_analytics_sdk is not None and OTLPCollectorConfig is not None:
            os.makedirs(logs_dir_path, exist_ok=True)
            agent_analytics_sdk.initialize_logging(
                tracer_type=agent_analytics_sdk.SUPPORTED_TRACER_TYPES.LOG,
                logs_dir_path=logs_dir_path,
                log_filename="trace",
                config=OTLPCollectorConfig(
                    endpoint="",
                    app_name='cuga',
                ),
            )


# Create a single instance of the AppState class to be used throughout the application.
app_state = AppState()


class ChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    stream: bool = False


def format_time_custom():
    """Formats the current time as HH-MM-SS."""
    now = datetime.datetime.now()
    return f"{now.hour:02d}-{now.minute:02d}-{now.second:02d}"


async def manage_save_reuse_server():
    """Checks for, starts, or restarts the save_reuse server as a subprocess."""
    if not settings.features.save_reuse:
        return

    # Define the path to the save_reuse.py file
    save_reuse_py_path = SAVE_REUSE_PY_PATH

    if not os.path.exists(save_reuse_py_path):
        logger.warning(f"save_reuse.py not found at {save_reuse_py_path}. Server will not be started.")
        return

    # If the process exists and is running, terminate it for a restart.
    if app_state.save_reuse_process and app_state.save_reuse_process.returncode is None:
        logger.info("Restarting save_reuse server...")
        app_state.save_reuse_process.terminate()
        await app_state.save_reuse_process.wait()

    logger.info("Starting save_reuse server...")
    # Assumes the file save_reuse.py contains a FastAPI instance named 'app'
    # and it is intended to be run with uvicorn.
    try:
        app_state.save_reuse_process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            SAVE_REUSE_PY_PATH,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(6)
        logger.info(f"save_reuse server started successfully with PID: {app_state.save_reuse_process.pid}")
    except FileNotFoundError:
        logger.error("Could not find 'uvicorn'. Please ensure it's installed in your environment.")
    except Exception as e:
        logger.error(f"Failed to start save_reuse server: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Asynchronous context manager for application startup and shutdown."""
    logger.info("Application is starting up...")

    # Load hardcoded policies if configured via environment variable
    if os.getenv("CUGA_LOAD_POLICIES", "false").lower() in ("true", "1", "yes", "on"):
        try:
            policies_content = os.getenv("CUGA_POLICIES_CONTENT", "")
            if policies_content:
                logger.info("Loading hardcoded policies")
                instructions_manager = InstructionsManager()
                instructions_manager.set_instructions_from_one_file(policies_content)
                logger.success("✅ Policies loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load policies: {e}")

    # Initialize policy system (only if enabled)
    if settings.policy.enabled:
        try:
            from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
            from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync
            from cuga.backend.cuga_graph.policy.folder_loader import load_policies_from_folder

            app_state.policy_system = PolicyConfigurable.get_instance()
            await app_state.policy_system.initialize()
            logger.info("✅ Policy system initialized")

            # Get .cuga folder path from environment or settings
            cuga_folder = os.getenv("CUGA_FOLDER", settings.policy.cuga_folder)

            # Check if filesystem sync is enabled (can be disabled globally in settings)
            filesystem_sync_enabled = settings.policy.filesystem_sync
            auto_load_enabled = settings.policy.auto_load_policies

            if not filesystem_sync_enabled:
                logger.info("Filesystem sync disabled in settings")
                app_state.policy_filesystem_sync = None
            elif not auto_load_enabled:
                logger.info("Auto-load policies disabled in settings")
                # Initialize sync but don't load
                app_state.policy_filesystem_sync = PolicyFilesystemSync(cuga_folder=cuga_folder)
                logger.info(f"✅ Filesystem sync enabled for {cuga_folder} (auto-load disabled)")
            # Load policies from filesystem if folder exists and auto-load is enabled
            elif os.path.exists(cuga_folder):
                logger.info(f"Loading policies from {cuga_folder}...")
                try:
                    result = await load_policies_from_folder(
                        folder_path=cuga_folder,
                        storage=app_state.policy_system.storage,
                        clear_existing=False,
                    )
                    await app_state.policy_system.initialize()  # Reinitialize after loading
                    logger.info(f"✅ Loaded {result['count']} policies from {cuga_folder}")

                    # Initialize filesystem sync for automatic saving
                    app_state.policy_filesystem_sync = PolicyFilesystemSync(cuga_folder=cuga_folder)
                    logger.info(f"✅ Filesystem sync enabled for {cuga_folder}")

                    # Validate and sync: ensure filesystem and storage are in sync
                    try:
                        sync_result = await validate_and_sync_policies(
                            app_state.policy_system.storage, app_state.policy_filesystem_sync
                        )
                        if sync_result['removed'] or sync_result['added_to_filesystem']:
                            logger.info(
                                f"📊 Sync validation: "
                                f"removed from storage={sync_result['removed']}, "
                                f"added to filesystem={sync_result['added_to_filesystem']}"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to validate and sync policies: {e}")
                except Exception as e:
                    logger.error(f"Failed to load policies from {cuga_folder}: {e}")
                    app_state.policy_filesystem_sync = None
            else:
                logger.info(f"Policy folder {cuga_folder} not found, skipping auto-load")
                app_state.policy_filesystem_sync = None

        except Exception as e:
            logger.warning(f"Failed to initialize policy system: {e}")
            app_state.policy_system = None
            app_state.policy_filesystem_sync = None
    else:
        logger.info("Policy system disabled in settings")
        app_state.policy_system = None
        app_state.policy_filesystem_sync = None

    # Start the save_reuse server if configured

    await manage_save_reuse_server()
    app_state.tracker = ActivityTracker()
    if settings.advanced_features.use_extension:
        app_state.env = ExtensionEnv(
            OpenEndedTaskAsync,
            ChromeExtensionCommunicatorHTTP(),
            feedback=[],
            user_data_dir=get_user_data_path(),
            task_kwargs={"start_url": settings.demo_mode.start_url},
        )
        start_extension_browser_if_configured()
    else:
        app_state.env = BrowserEnvGymAsync(
            OpenEndedTaskAsync,
            headless=False,
            resizeable_window=True,
            interface_mode="none" if settings.advanced_features.mode == "api" else "browser_only",
            feedback=[],
            user_data_dir=get_user_data_path(),
            channel="chromium",
            task_kwargs={"start_url": settings.demo_mode.start_url},
            pw_extra_args=[
                *settings.get("PLAYWRIGHT_ARGS", []),
                f"--disable-extensions-except={app_state.EXTENSION_PATH}",
                f"--load-extension={app_state.EXTENSION_PATH}",
            ],
        )
    await asyncio.sleep(3)
    app_state.tracker.start_experiment(task_ids=['demo'], experiment_name='demo', description="")
    # Reset environment (env is shared but state is per-thread via LangGraph)
    await app_state.env.reset()
    langfuse_handler = (
        CallbackHandler()
        if settings.advanced_features.langfuse_tracing and CallbackHandler is not None
        else None
    )
    app_state.agent = DynamicAgentGraph(
        None, langfuse_handler=langfuse_handler, policy_system=app_state.policy_system
    )
    await app_state.agent.build_graph()

    logger.info("Application finished starting up...")
    url = f"http://localhost:{settings.server_ports.demo}?t={random_id_with_timestamp()}"
    # Set by cli.py only for 'cuga start demo' (not demo_crm)
    if os.getenv("CUGA_DEMO_ADVANCED", "false").lower() in ("true", "1"):
        url += "&mode=advanced"
    if settings.advanced_features.mode == "api" and os.getenv("CUGA_TEST_ENV", "false").lower() not in (
        "true",
        "1",
        "yes",
        "on",
    ):
        try:
            if platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', url], check=False)
            elif platform.system() == 'Windows':  # Windows
                subprocess.run(['cmd', '/c', 'start', '', url], check=False, shell=False)
            else:  # Linux
                subprocess.run(['xdg-open', url], check=False)
        except Exception as e:
            logger.warning(f"Failed to open browser: {e}")
    yield
    logger.info("Application is shutting down...")

    # Terminate the save_reuse server process if it's running
    if app_state.save_reuse_process and app_state.save_reuse_process.returncode is None:
        logger.info("Terminating save_reuse server...")
        app_state.save_reuse_process.terminate()
        await app_state.save_reuse_process.wait()
        logger.info("save_reuse server terminated.")

    # Clean up embedded assets
    if USE_EMBEDDED_ASSETS:
        embedded_assets.cleanup()
        logger.info("Cleaned up embedded assets")


def get_element_names(tool_calls, elements):
    """Extracts element names from tool calls."""
    elements_map = {}
    for tool in tool_calls:
        element_bid = tool.get("args", {}).get("bid", None)
        if element_bid:
            elements_map[element_bid] = ActionAgentEventProcessor.get_element_name(elements, element_bid)
    return elements_map


async def copy_file_async(file_path, new_name):
    """Asynchronously copies a file to a new name in the same directory."""
    try:
        loop = asyncio.get_event_loop()
        if not await loop.run_in_executor(None, os.path.isfile, file_path):
            print(f"Error: File '{file_path}' does not exist.")
            return None
        directory = os.path.dirname(file_path)
        new_file_path = os.path.join(directory, new_name)
        await loop.run_in_executor(None, shutil.copy2, file_path, new_file_path)
        print(f"File successfully copied to '{new_file_path}'")
        return new_file_path
    except Exception as e:
        print(f"Error copying file: {e}")
        return None


async def validate_and_sync_policies(storage, filesystem_sync):
    """
    Validate and sync policies between filesystem and storage on startup.

    Ensures:
    1. Policies in filesystem are in storage
    2. Policies only in storage are saved to filesystem
    3. Policies deleted from filesystem are removed from storage

    Args:
        storage: PolicyStorage instance
        filesystem_sync: PolicyFilesystemSync instance

    Returns:
        Dictionary with sync statistics
    """
    try:
        # Get policy IDs from both sources
        fs_policy_ids = filesystem_sync.get_filesystem_policy_ids()
        storage_policies = await storage.list_policies(enabled_only=False)
        storage_policy_ids = {p.id for p in storage_policies}

        removed_count = 0
        added_to_filesystem_count = 0

        # 1. Remove from storage if deleted from filesystem
        policies_to_remove = storage_policy_ids - fs_policy_ids
        for policy_id in policies_to_remove:
            try:
                await storage.delete_policy(policy_id)
                removed_count += 1
                logger.info(f"🗑️  Removed policy '{policy_id}' from storage (not in filesystem)")
            except Exception as e:
                logger.error(f"Failed to remove policy '{policy_id}': {e}")

        # 2. Save to filesystem if only in storage
        policies_to_save_to_fs = storage_policy_ids - fs_policy_ids
        for policy_id in policies_to_save_to_fs:
            try:
                # Find the policy object
                policy = next((p for p in storage_policies if p.id == policy_id), None)
                if policy:
                    filesystem_sync.save_policy_to_file(policy)
                    added_to_filesystem_count += 1
                    logger.info(f"💾 Saved policy '{policy_id}' to filesystem (was only in storage)")
            except Exception as e:
                logger.error(f"Failed to save policy '{policy_id}' to filesystem: {e}")

        return {
            "removed": removed_count,
            "added_to_filesystem": added_to_filesystem_count,
        }
    except Exception as e:
        logger.error(f"Failed to validate and sync policies: {e}")
        return {"removed": 0, "added_to_filesystem": 0}


async def setup_page_info(state: AgentState, env: ExtensionEnv | BrowserEnvGymAsync):
    """Setup page URL, app name, and description from environment."""
    # Get URL and title
    state.url = env.get_url()
    title = await env.get_title()
    url_app_name = get_app_name_from_url(state.url)
    # Sanitize title
    sanitized_title = re.sub(r'[^\w\s-]', '', title) if title else ""
    sanitized_title = re.sub(r'[-\s]+', '_', sanitized_title).strip('_').lower()
    # Create app name: url + sanitized title
    state.current_app = (
        f"{url_app_name}_{sanitized_title}" if sanitized_title else url_app_name or "unknown_app"
    )
    # Create description
    state.current_app_description = f"web application for '{title}' and url '{url_app_name}'"


async def event_stream(query: str, api_mode=False, resume=None, thread_id: str = None):
    """Handles the main agent event stream."""
    # Create or get cancellation event for this thread
    if thread_id:
        if thread_id not in app_state.stop_events:
            app_state.stop_events[thread_id] = asyncio.Event()
        else:
            # Reset the event for a new stream
            app_state.stop_events[thread_id].clear()

    # Create a local state object - retrieve from LangGraph if resuming or if thread_id exists, otherwise create new
    local_state = None

    # Create local tracker instance
    local_tracker = ActivityTracker()

    # Local observation and info (not shared globally)
    local_obs = None
    local_info = None

    if not resume:
        # Check if we have existing state for this thread_id (for followup questions)
        if thread_id:
            try:
                latest_state_values = app_state.agent.graph.get_state(
                    {"configurable": {"thread_id": thread_id}}
                ).values
                if latest_state_values:
                    # Load existing state for followup questions
                    local_state = AgentState(**latest_state_values)
                    local_state.thread_id = thread_id
                    local_state.input = query  # Update input for the new query
                    local_tracker.intent = query
                    # Apply sliding window to maintain message history limit
                    local_state.apply_message_sliding_window()
                    logger.info(f"Loaded existing state for thread_id: {thread_id} (followup question)")
                else:
                    # No existing state, create new one
                    local_state = default_state(page=None, observation=None, goal="")
                    local_state.input = query
                    local_state.thread_id = thread_id
                    local_tracker.intent = query
                    logger.info(f"Created new state for thread_id: {thread_id}")
            except Exception as e:
                # If state retrieval fails, create new state
                logger.warning(f"Failed to retrieve state for thread_id {thread_id}, creating new state: {e}")
                local_state = default_state(page=None, observation=None, goal="")
                local_state.input = query
                local_state.thread_id = thread_id
                local_tracker.intent = query
        else:
            # No thread_id, create new state
            local_state = default_state(page=None, observation=None, goal="")
            local_state.input = query
            local_state.thread_id = thread_id
            local_tracker.intent = query
    else:
        # For resume, fetch state from LangGraph
        if thread_id:
            latest_state_values = app_state.agent.graph.get_state(
                {"configurable": {"thread_id": thread_id}}
            ).values
            if latest_state_values:
                local_state = AgentState(**latest_state_values)
                local_state.thread_id = thread_id

    if not api_mode:
        local_obs, _, _, _, local_info = await app_state.env.step("")
        pu_answer = await app_state.env.pu_processor.transform(
            transformer_params={"filter_visible_only": True}
        )
        local_tracker.collect_image(pu_answer.img)
        if local_state:
            local_state.elements_as_string = pu_answer.string_representation
            local_state.focused_element_bid = pu_answer.focused_element_bid
            local_state.read_page = pu_answer.page_content
            local_state.url = app_state.env.get_url()
            await setup_page_info(local_state, app_state.env)

    local_tracker.task_id = 'demo'

    langfuse_handler = (
        CallbackHandler()
        if settings.advanced_features.langfuse_tracing and CallbackHandler is not None
        else None
    )

    # Print Langfuse trace ID if tracing is enabled
    if langfuse_handler and settings.advanced_features.langfuse_tracing:
        print(f"Langfuse tracing enabled. Handler created: {langfuse_handler}")
        # The trace ID will be available after the first LLM call
        print("Note: Trace ID will be available after the first LLM operation")

    agent_loop_obj = AgentLoop(
        graph=app_state.agent.graph,
        langfuse_handler=langfuse_handler,
        thread_id=thread_id,
        tracker=local_tracker,
    )
    logger.debug(f"Resume: {resume.model_dump_json() if resume else ''}")

    # Use local_state if available, otherwise None (AgentLoop/LangGraph will handle state retrieval)
    agent_stream_gen = agent_loop_obj.run_stream(state=local_state if not resume else None, resume=resume)

    # Print initial trace ID status
    if langfuse_handler and settings.advanced_features.langfuse_tracing:
        initial_trace_id = agent_loop_obj.get_langfuse_trace_id()
        if initial_trace_id:
            print(f"Initial Langfuse Trace ID: {initial_trace_id}")
        else:
            print("Langfuse Trace ID will be generated after first LLM call")

    try:
        while True:
            # Check cancellation event (non-blocking)
            if thread_id and thread_id in app_state.stop_events and app_state.stop_events[thread_id].is_set():
                logger.info(f"Agent execution stopped by user for thread_id: {thread_id}")
                yield StreamEvent(name="Stopped", data="Agent execution was stopped by user.").format()
                return

            async for event in agent_stream_gen:
                # Check cancellation event during event processing
                if (
                    thread_id
                    and thread_id in app_state.stop_events
                    and app_state.stop_events[thread_id].is_set()
                ):
                    logger.info(
                        f"Agent execution stopped by user during event processing for thread_id: {thread_id}"
                    )
                    yield StreamEvent(name="Stopped", data="Agent execution was stopped by user.").format()
                    return

                if isinstance(event, AgentLoopAnswer):
                    if event.flow_generalized:
                        await manage_save_reuse_server()
                        await app_state.agent.chat.chat_agent.cleanup()
                        await app_state.agent.chat.chat_agent.setup()

                    if event.interrupt and not event.has_tools:
                        # Update local state from graph
                        if thread_id:
                            latest_state_values = app_state.agent.graph.get_state(
                                {"configurable": {"thread_id": thread_id}}
                            ).values
                            if latest_state_values:
                                local_state = AgentState(**latest_state_values)
                        return
                    if event.end:
                        try:
                            local_tracker.finish_task(
                                intent=local_state.input if local_state else "",
                                site="",
                                task_id="demo",
                                eval="",
                                score=1.0,
                                agent_answer=event.answer,
                                exception=False,
                                num_steps=0,
                                agent_v="",
                            )
                        except Exception as e:
                            logger.warning(f"Failed to finish task in tracker: {e}")
                        logger.debug("!!!!!!!Task is done!!!!!!!")

                        # Get variables metadata from state
                        # We need to get the latest state from the graph to ensure we have the variables
                        variables_metadata = {}
                        active_policies = []
                        if thread_id:
                            latest_state_values = app_state.agent.graph.get_state(
                                {"configurable": {"thread_id": thread_id}}
                            ).values

                            if latest_state_values:
                                local_state = AgentState(**latest_state_values)
                                variables_metadata = (
                                    local_state.variables_manager.get_all_variables_metadata()
                                )
                                # Extract active policies from cuga_lite_metadata
                                if local_state.cuga_lite_metadata:
                                    metadata = local_state.cuga_lite_metadata
                                    if metadata.get("policy_matched") or metadata.get("policy_blocked"):
                                        policy_data = {
                                            "type": "policy",
                                            "policy_type": metadata.get("policy_type", "unknown"),
                                            "policy_name": metadata.get("policy_name", "Unknown Policy"),
                                            "policy_blocked": metadata.get("policy_blocked", False),
                                            "policy_matched": metadata.get("policy_matched", False),
                                            "content": metadata.get("response_content")
                                            or metadata.get("content")
                                            or metadata.get("approval_message")
                                            or "",
                                            "metadata": {
                                                "policy_blocked": metadata.get("policy_blocked", False),
                                                "policy_id": metadata.get("policy_id"),
                                                "policy_name": metadata.get("policy_name"),
                                                "policy_type": metadata.get("policy_type", "unknown"),
                                                "policy_reasoning": metadata.get("policy_reasoning", ""),
                                                "policy_confidence": metadata.get("policy_confidence"),
                                                "response_content": metadata.get("response_content")
                                                or metadata.get("content")
                                                or metadata.get("approval_message")
                                                or "",
                                            },
                                        }
                                        active_policies.append(policy_data)
                        final_answer_text = (
                            event.answer
                            if settings.advanced_features.wxo_integration
                            else json.dumps(
                                {
                                    "data": event.answer,
                                    "variables": variables_metadata,
                                    "active_policies": active_policies,
                                }
                            )
                            if event.answer
                            else "Done."
                        )
                        logger.info("=" * 80)
                        logger.info("FINAL ANSWER")
                        logger.info("=" * 80)
                        logger.info(f"{event.answer if event.answer else 'Done.'}")
                        logger.info("=" * 80)
                        yield StreamEvent(
                            name="Answer",
                            data=final_answer_text,
                        ).format(app_state.output_format, thread_id=thread_id)

                        if thread_id:
                            latest_state_values = app_state.agent.graph.get_state(
                                {"configurable": {"thread_id": thread_id}}
                            ).values
                            if latest_state_values:
                                local_state = AgentState(**latest_state_values)
                        try:
                            # Log file operations disabled for test stability
                            pass
                        except Exception as e:
                            logger.warning(f"Failed to move trace logs: {e}")

                        # Add small delay to ensure data is flushed to client
                        await asyncio.sleep(0.1)
                        logger.info(f"Stream finished for thread_id: {thread_id}")
                        return
                    elif event.has_tools:
                        if thread_id:
                            latest_state_values = app_state.agent.graph.get_state(
                                {"configurable": {"thread_id": thread_id}}
                            ).values
                            if latest_state_values:
                                local_state = AgentState(**latest_state_values)

                        if not local_state or not local_state.messages:
                            logger.warning("No state or messages available for tool call")
                            continue

                        msg: AIMessage = local_state.messages[-1]
                        yield StreamEvent(name="tool_call", data=format_tools(msg.tool_calls)).format()

                        feedback = await AgentRunner.process_event_async(
                            local_state.messages[-1].tool_calls,
                            local_state.elements,
                            None if api_mode else app_state.env.page,
                            app_state.env.tool_implementation_provider,
                            session_id="demo",
                            page_data=local_obs,
                            communicator=getattr(app_state.env, "extension_communicator", None),
                        )
                        local_state.feedback += feedback

                        if not api_mode:
                            local_obs, _, _, _, local_info = await app_state.env.step("")
                            pu_answer = await app_state.env.pu_processor.transform(
                                transformer_params={"filter_visible_only": True}
                            )
                            local_tracker.collect_image(pu_answer.img)
                            local_state.elements_as_string = pu_answer.string_representation
                            local_state.focused_element_bid = pu_answer.focused_element_bid
                            local_state.read_page = pu_answer.page_content
                            local_state.url = app_state.env.get_url()

                        if thread_id and local_state:
                            app_state.agent.graph.update_state(
                                {"configurable": {"thread_id": thread_id}}, local_state.model_dump()
                            )
                        agent_stream_gen = agent_loop_obj.run_stream(state=None)
                        break
                else:
                    logger.debug("Yield {}".format(event))
                    if thread_id:
                        latest_state_values = app_state.agent.graph.get_state(
                            {"configurable": {"thread_id": thread_id}}
                        ).values
                        if latest_state_values:
                            local_state = AgentState(**latest_state_values)
                    name = ((event.split("\n")[0]).split(":")[1]).strip()
                    logger.debug("Yield {}".format(event))
                    if name not in ["ChatAgent"]:
                        yield StreamEvent(name=name, data=event).format(
                            app_state.output_format, thread_id=thread_id
                        )
    except Exception as e:
        logger.exception(e)
        logger.error(traceback.format_exc())
        try:
            local_tracker.finish_task(
                intent=local_state.input if local_state else "",
                site="",
                task_id="demo",
                eval="",
                score=0.0,
                agent_answer="",
                exception=True,
                num_steps=0,
                agent_v="",
            )
        except Exception as tracker_error:
            logger.warning(f"Failed to finish task in tracker on error: {tracker_error}")

        yield StreamEvent(name="Error", data=str(e)).format()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if getattr(settings.advanced_features, "use_extension", False):
    print(settings.advanced_features.use_extension)

    def get_communicator() -> ChromeExtensionCommunicatorProtocol:
        comm: ChromeExtensionCommunicatorProtocol | None = getattr(
            app_state.env, "extension_communicator", None
        )
        if not comm:
            raise Exception("Cannot use streaming outside of extension")

        return comm

    @app.get("/extension/command_stream")
    async def extension_command_stream():
        comm = get_communicator()

        async def event_gen():
            while True:
                cmd = await comm.get_next_command()
                yield f"data: {json.dumps(cmd)}\n\n"

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.post("/extension/command_result")
    async def extension_command_result(request: Request):
        comm = get_communicator()
        data = await request.json()
        req_id = data.get("request_id")
        comm.resolve_request(req_id, data)
        return JSONResponse({"status": "ok"})

    @app.post("/extension/agent_query")
    async def extension_agent_query(request: Request):
        body = await request.json()
        query = body.get("query", "")
        request_id = body.get("request_id", None)
        if not query:
            return JSONResponse({"type": "agent_error", "message": "Missing query"}, status_code=400)

        try:
            validate_input_length(query)
        except HTTPException as e:
            return JSONResponse({"type": "agent_error", "message": e.detail}, status_code=e.status_code)

        async def event_gen():
            # Initial processing message
            yield (
                json.dumps(
                    {
                        "type": "agent_response",
                        "content": f"Processing query: {query}\n\n",
                        "request_id": request_id,
                    }
                )
                + "\n"
            )
            try:
                async for chunk in event_stream(
                    query,
                    api_mode=settings.advanced_features.mode == "api",
                    resume=query if isinstance(query, ActionResponse) else None,
                    thread_id=request_id or str(uuid.uuid4()),  # Use request_id as thread_id if available
                ):
                    if chunk.strip():
                        # Remove 'data: ' prefix if present
                        if chunk.startswith("data: "):
                            chunk = chunk[6:]
                        try:
                            chunk_data = json.loads(chunk)
                            content = chunk_data.get("data", chunk)
                        except Exception:
                            content = chunk
                        yield (
                            json.dumps(
                                {"type": "agent_response", "content": content, "request_id": request_id}
                            )
                            + "\n"
                        )
                # Completion message
                yield json.dumps({"type": "agent_complete", "request_id": request_id}) + "\n"
            except Exception as e:
                yield json.dumps({"type": "agent_error", "message": str(e), "request_id": request_id}) + "\n"

        return StreamingResponse(event_gen(), media_type="application/jsonlines")


@app.post("/stream")
async def stream(request: Request):
    """Endpoint to start the agent stream."""
    query = await get_query(request)

    # Get thread_id from header or generate new one
    thread_id = request.headers.get("X-Thread-ID")
    if not thread_id:
        thread_id = str(uuid.uuid4())
        logger.info(f"No X-Thread-ID header found, generated new thread_id: {thread_id}")
    else:
        logger.info(f"Using provided thread_id: {thread_id}")

    return StreamingResponse(
        event_stream(
            query if isinstance(query, str) else None,
            api_mode=settings.advanced_features.mode == "api",
            resume=query if isinstance(query, ActionResponse) else None,
            thread_id=thread_id,
        ),
        media_type="text/event-stream",
    )


@app.post("/stop")
async def stop(request: Request):
    """Endpoint to stop the agent execution for a specific thread."""
    # Get thread_id from header or body
    thread_id = request.headers.get("X-Thread-ID")
    if not thread_id:
        try:
            body = await request.json()
            thread_id = body.get("thread_id")
        except Exception:
            pass

    if thread_id:
        logger.info(f"Received stop request for thread_id: {thread_id}")
        # Create event if it doesn't exist, then set it
        if thread_id not in app_state.stop_events:
            app_state.stop_events[thread_id] = asyncio.Event()
        app_state.stop_events[thread_id].set()
        return {"status": "success", "message": f"Stop request received for thread_id: {thread_id}"}
    else:
        logger.warning("Received stop request without thread_id, stopping all threads")
        # Fallback: stop all threads (for backward compatibility)
        for event in app_state.stop_events.values():
            event.set()
        return {"status": "success", "message": "Stop request received for all threads"}


@app.post("/reset")
async def reset_agent_state(request: Request):
    """Endpoint to reset the agent state to default values."""
    logger.info("Received reset request")
    try:
        # Get thread_id from header
        thread_id = request.headers.get("X-Thread-ID")

        # If no thread_id provided in header, check body (optional, but good for flexibility)
        if not thread_id:
            try:
                body = await request.json()
                thread_id = body.get("thread_id")
            except Exception:
                pass

        if thread_id:
            logger.info(f"Resetting state for thread_id: {thread_id}")
            # Clear stop event for this thread
            if thread_id in app_state.stop_events:
                app_state.stop_events[thread_id].clear()

            # In LangGraph, state is persisted per thread_id. The client should generate a new thread_id
            # for a fresh start. If we need to clear the thread state, we would need to delete it from
            # the checkpointer, but for now we'll just clear the stop flag.
            # The LangGraph state will remain but won't be accessed if client uses a new thread_id.
        else:
            logger.info("No thread_id provided for reset, clearing all thread stop events")
            # Clear all stop events (for backward compatibility)
            for event in app_state.stop_events.values():
                event.clear()

        # Note: We don't reset the agent graph or environment as they are shared resources.
        # State is managed per-thread via LangGraph's checkpointer.

        # Reset tracker experiment if enabled
        # TODO Remove this once we have a proper way to reset the variables manager
        # var_manger = VariablesManager()
        # var_manger.reset()
        logger.info("Agent state reset successfully")
        return {"status": "success", "message": "Agent state reset successfully"}
    except Exception as e:
        logger.error(f"Failed to reset agent state: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reset agent state: {str(e)}")


@app.get("/api/config/tools")
async def get_tools_config():
    """Endpoint to retrieve tools configuration."""
    config_path = os.path.join(
        PACKAGE_ROOT, "backend", "tools_env", "registry", "config", "mcp_servers_crm.yaml"
    )
    try:
        if os.path.exists(config_path):
            loop = asyncio.get_event_loop()

            def _read_file():
                with open(config_path, "r") as f:
                    return yaml.safe_load(f)

            config_data = await loop.run_in_executor(None, _read_file)
            return JSONResponse(config_data)
        else:
            return JSONResponse({"services": [], "mcpServers": {}})
    except Exception as e:
        logger.error(f"Failed to load tools config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load tools config: {str(e)}")


@app.post("/api/config/tools")
async def save_tools_config(request: Request):
    """Endpoint to save tools configuration."""
    config_path = os.path.join(
        PACKAGE_ROOT, "backend", "tools_env", "registry", "config", "mcp_servers_crm.yaml"
    )
    try:
        data = await request.json()
        loop = asyncio.get_event_loop()

        def _write_file():
            with open(config_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        await loop.run_in_executor(None, _write_file)
        logger.info(f"Tools configuration saved to {config_path}")
        return JSONResponse({"status": "success", "message": "Tools configuration saved successfully"})
    except Exception as e:
        logger.error(f"Failed to save tools config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save tools config: {str(e)}")


@app.get("/api/config/model")
async def get_model_config():
    """Endpoint to retrieve model configuration."""
    try:
        return JSONResponse({})
    except Exception as e:
        logger.error(f"Failed to load model config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load model config: {str(e)}")


@app.post("/api/config/model")
async def save_model_config(request: Request):
    """Endpoint to save model configuration (note: this updates environment variables for current session only)."""
    try:
        data = await request.json()
        os.environ["MODEL_PROVIDER"] = data.get("provider", "anthropic")
        os.environ["MODEL_NAME"] = data.get("model", "claude-3-5-sonnet-20241022")
        os.environ["MODEL_TEMPERATURE"] = str(data.get("temperature", 0.7))
        os.environ["MODEL_MAX_TOKENS"] = str(data.get("maxTokens", 4096))
        os.environ["MODEL_TOP_P"] = str(data.get("topP", 1.0))
        logger.info("Model configuration updated (session only)")
        return JSONResponse({"status": "success", "message": "Model configuration updated"})
    except Exception as e:
        logger.error(f"Failed to save model config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save model config: {str(e)}")


@app.get("/api/config/knowledge")
async def get_knowledge_config():
    """Endpoint to retrieve knowledge configuration."""
    try:
        return JSONResponse({})
    except Exception as e:
        logger.error(f"Failed to load knowledge config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load knowledge config: {str(e)}")


@app.post("/api/config/knowledge")
async def save_knowledge_config(request: Request):
    """Endpoint to save knowledge configuration."""
    try:
        await request.json()
        logger.info("Knowledge configuration saved (placeholder)")
        return JSONResponse({"status": "success", "message": "Knowledge configuration saved"})
    except Exception as e:
        logger.error(f"Failed to save knowledge config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save knowledge config: {str(e)}")


@app.get("/api/conversations")
async def get_conversations():
    """Endpoint to retrieve conversation history."""
    try:
        # TODO: Implement actual conversation storage
        # For now, return empty list
        return JSONResponse([])
    except Exception as e:
        logger.error(f"Failed to load conversations: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load conversations: {str(e)}")


@app.post("/api/conversations")
async def create_conversation(request: Request):
    """Endpoint to create a new conversation."""
    try:
        data = await request.json()
        # TODO: Implement actual conversation storage
        conversation = {
            "id": str(uuid.uuid4()),
            "title": data.get("title", "New Conversation"),
            "timestamp": data.get("timestamp", int(datetime.datetime.now().timestamp() * 1000)),
            "preview": data.get("preview", ""),
        }
        logger.info(f"Created conversation: {conversation['id']}")
        return JSONResponse(conversation)
    except Exception as e:
        logger.error(f"Failed to create conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {str(e)}")


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Endpoint to delete a conversation."""
    try:
        # TODO: Implement actual conversation storage
        logger.info(f"Deleted conversation: {conversation_id}")
        return JSONResponse({"status": "success", "message": "Conversation deleted"})
    except Exception as e:
        logger.error(f"Failed to delete conversation: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete conversation: {str(e)}")


@app.get("/api/config/memory")
async def get_memory_config():
    """Endpoint to retrieve memory configuration."""
    try:
        return JSONResponse({})
    except Exception as e:
        logger.error(f"Failed to load memory config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load memory config: {str(e)}")


@app.post("/api/config/memory")
async def save_memory_config(request: Request):
    """Endpoint to save memory configuration."""
    try:
        await request.json()
        logger.info("Memory configuration saved")
        return JSONResponse({"status": "success", "message": "Memory configuration saved"})
    except Exception as e:
        logger.error(f"Failed to save memory config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save memory config: {str(e)}")


@app.get("/api/config/policies")
async def get_policies_config():
    """Endpoint to retrieve policies configuration."""
    # Return early if policies are disabled
    if not settings.policy.enabled:
        return JSONResponse({"enablePolicies": False, "policies": []})

    try:
        from cuga.backend.cuga_graph.policy.storage import PolicyStorage

        # Use the policy system's storage if available, otherwise create a new one
        if app_state.policy_system and app_state.policy_system.storage:
            storage = app_state.policy_system.storage
            logger.info("Using existing policy system storage for GET")
        else:
            # Get policy configuration from settings.toml
            collection_name = settings.policy.collection_name
            milvus_host = settings.policy.milvus_host
            milvus_port = settings.policy.milvus_port
            milvus_uri = settings.policy.milvus_uri

            storage = PolicyStorage(
                collection_name=collection_name,
                host=milvus_host,
                port=milvus_port,
                milvus_uri=milvus_uri,
            )
            await storage.initialize_async()
            logger.info(f"Created new storage instance for GET (collection: {collection_name})")

        # List all policies (this IS async)
        policies_objs = await storage.list_policies(enabled_only=False)

        # Don't disconnect if using the policy system's storage
        if not (app_state.policy_system and app_state.policy_system.storage):
            storage.disconnect()

        # Convert Policy objects to frontend format
        policies = []
        for policy_obj in policies_objs:
            policy_dict = policy_obj.model_dump()
            # Map backend field names to frontend expectations
            frontend_policy = {
                "id": policy_dict["id"],
                "name": policy_dict["name"],
                "description": policy_dict["description"],
                "policy_type": policy_dict["type"],
                "enabled": policy_dict.get("enabled", True),
                "triggers": policy_dict.get("triggers", []),
                "priority": policy_dict.get("priority", 50),
            }

            # Add type-specific fields
            if policy_dict["type"] == "intent_guard":
                frontend_policy["intent_examples"] = policy_dict.get("intent_examples", [])
                frontend_policy["response"] = policy_dict.get("response", {})
                frontend_policy["allow_override"] = policy_dict.get("allow_override", False)
            elif policy_dict["type"] == "playbook":
                frontend_policy["markdown_content"] = policy_dict.get("markdown_content", "")
                frontend_policy["steps"] = policy_dict.get("steps", [])
                frontend_policy["inject_as_system_prompt"] = policy_dict.get("inject_as_system_prompt", True)
            elif policy_dict["type"] == "tool_guide":
                frontend_policy["target_tools"] = policy_dict.get("target_tools", [])
                frontend_policy["target_apps"] = policy_dict.get("target_apps")
                frontend_policy["guide_content"] = policy_dict.get("guide_content", "")
                frontend_policy["prepend"] = policy_dict.get("prepend", False)
            elif policy_dict["type"] == "tool_approval":
                frontend_policy["required_tools"] = policy_dict.get("required_tools", [])
                frontend_policy["required_apps"] = policy_dict.get("required_apps")
                frontend_policy["approval_message"] = policy_dict.get("approval_message")
                frontend_policy["show_code_preview"] = policy_dict.get("show_code_preview", True)
                frontend_policy["auto_approve_after"] = policy_dict.get("auto_approve_after")
            elif policy_dict["type"] == "output_formatter":
                frontend_policy["format_type"] = policy_dict.get("format_type", "markdown")
                frontend_policy["format_config"] = policy_dict.get("format_config", "")

            policies.append(frontend_policy)

        logger.info(f"Loaded {len(policies)} policies from storage")
        return JSONResponse({"enablePolicies": settings.policy.enabled, "policies": policies})
    except Exception as e:
        logger.error(f"Failed to load policies config: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return JSONResponse({"enablePolicies": settings.policy.enabled, "policies": []})


@app.post("/api/config/policies")
async def save_policies_config(request: Request):
    """Endpoint to save policies configuration."""
    # Return early if policies are disabled
    if not settings.policy.enabled:
        return JSONResponse(
            {"status": "error", "message": "Policy system is disabled in settings"},
            status_code=403,
        )

    try:
        from cuga.backend.cuga_graph.policy.storage import PolicyStorage
        from cuga.backend.cuga_graph.policy.models import (
            IntentGuard,
            Playbook,
            ToolGuide,
            ToolApproval,
            OutputFormatter,
            IntentGuardResponse,
            PlaybookStep,
        )

        data = await request.json()
        logger.info(f"Received policy save request with {len(data.get('policies', []))} policies")
        policies = data.get("policies", [])

        # Use the policy system's storage if available, otherwise create a new one
        # Storage will handle all embedding configuration internally
        if app_state.policy_system and app_state.policy_system.storage:
            storage = app_state.policy_system.storage
            logger.info("Using existing policy system storage")
        else:
            # Get basic config from settings, storage will handle embedding config internally
            collection_name = settings.policy.collection_name
            milvus_host = settings.policy.milvus_host
            milvus_port = settings.policy.milvus_port
            milvus_uri = settings.policy.milvus_uri

            # Embedding config - storage will auto-detect dimensions during initialization
            embedding_provider = os.getenv("POLICY_EMBEDDING_PROVIDER") or settings.policy.embedding_provider
            embedding_model = os.getenv("POLICY_EMBEDDING_MODEL") or settings.policy.embedding_model

            storage = PolicyStorage(
                collection_name=collection_name,
                host=milvus_host,
                port=milvus_port,
                milvus_uri=milvus_uri,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                # embedding_dim will be auto-detected during initialize_async()
            )
            await storage.initialize_async()
            logger.info(
                f"Created new storage instance from settings (collection: {collection_name}, "
                f"embedding: {embedding_provider}, dim: {storage.embedding_dim})"
            )

        # Clear existing policies (simple approach - in production, do incremental updates)
        existing_policies = await storage.list_policies(enabled_only=False)
        for policy_obj in existing_policies:
            await storage.delete_policy(policy_obj.id)

        # Add new policies
        for policy_data in policies:
            try:
                policy_type = policy_data.get("policy_type")
                logger.info(f"Processing policy: {policy_data.get('name')} (type: {policy_type})")
                logger.info(f"Triggers data: {policy_data.get('triggers')}")

                # Validate and log keyword trigger operators
                for trigger in policy_data.get('triggers', []):
                    if trigger.get('type') == 'keyword':
                        operator = trigger.get('operator', 'NOT_SET')
                        keywords = trigger.get('value', [])
                        logger.info(f"  Keyword trigger: operator={operator}, keywords={keywords}")

                if policy_type == "intent_guard":
                    # Convert to IntentGuard model
                    response_data = policy_data.get("response", {})
                    policy = IntentGuard(
                        id=policy_data["id"],
                        name=policy_data["name"],
                        description=policy_data["description"],
                        triggers=policy_data["triggers"],
                        intent_examples=policy_data.get("intent_examples", []),
                        response=IntentGuardResponse(
                            response_type=response_data.get("response_type", "natural_language"),
                            content=response_data.get("content", ""),
                        ),
                        allow_override=policy_data.get("allow_override", False),
                        priority=policy_data.get("priority", 50),
                        enabled=policy_data.get("enabled", True),
                    )
                    logger.info(f"Created IntentGuard policy with triggers: {policy.triggers}")
                    # Validate keyword trigger operators after Pydantic parsing
                    for trigger in policy.triggers:
                        if hasattr(trigger, 'type') and trigger.type == 'keyword':
                            operator = getattr(trigger, 'operator', 'NOT_SET')
                            logger.info(
                                f"  IntentGuard keyword trigger validated: operator={operator}, keywords={trigger.value}"
                            )
                elif policy_type == "playbook":
                    # Convert to Playbook model
                    steps_data = policy_data.get("steps", [])
                    steps = [
                        PlaybookStep(
                            step_number=step["step_number"],
                            instruction=step["instruction"],
                            expected_outcome=step["expected_outcome"],
                            tools_allowed=step.get("tools_allowed", []),
                        )
                        for step in steps_data
                    ]

                    policy = Playbook(
                        id=policy_data["id"],
                        name=policy_data["name"],
                        description=policy_data["description"],
                        triggers=policy_data["triggers"],
                        markdown_content=policy_data.get("markdown_content", ""),
                        steps=steps,
                        priority=policy_data.get("priority", 50),
                        enabled=policy_data.get("enabled", True),
                    )
                    logger.info(f"Created Playbook policy with triggers: {policy.triggers}")
                    # Validate keyword trigger operators after Pydantic parsing
                    for trigger in policy.triggers:
                        if hasattr(trigger, 'type') and trigger.type == 'keyword':
                            operator = getattr(trigger, 'operator', 'NOT_SET')
                            logger.info(
                                f"  Playbook keyword trigger validated: operator={operator}, keywords={trigger.value}"
                            )
                elif policy_type == "tool_guide":
                    # Convert to ToolGuide model
                    policy = ToolGuide(
                        id=policy_data["id"],
                        name=policy_data["name"],
                        description=policy_data["description"],
                        triggers=policy_data["triggers"],
                        target_tools=policy_data.get("target_tools", []),
                        target_apps=policy_data.get("target_apps"),
                        guide_content=policy_data.get("guide_content", ""),
                        prepend=policy_data.get("prepend", False),
                        priority=policy_data.get("priority", 50),
                        enabled=policy_data.get("enabled", True),
                    )
                elif policy_type == "tool_approval":
                    # Convert to ToolApproval model
                    policy = ToolApproval(
                        id=policy_data["id"],
                        name=policy_data["name"],
                        description=policy_data["description"],
                        triggers=policy_data["triggers"],
                        required_tools=policy_data.get("required_tools", []),
                        required_apps=policy_data.get("required_apps"),
                        approval_message=policy_data.get("approval_message"),
                        show_code_preview=policy_data.get("show_code_preview", True),
                        auto_approve_after=policy_data.get("auto_approve_after"),
                        priority=policy_data.get("priority", 50),
                        enabled=policy_data.get("enabled", True),
                    )
                elif policy_type == "output_formatter":
                    # Convert to OutputFormatter model
                    policy = OutputFormatter(
                        id=policy_data["id"],
                        name=policy_data["name"],
                        description=policy_data["description"],
                        triggers=policy_data["triggers"],
                        format_type=policy_data.get("format_type", "markdown"),
                        format_config=policy_data.get("format_config", ""),
                        priority=policy_data.get("priority", 50),
                        enabled=policy_data.get("enabled", True),
                        metadata=policy_data.get("metadata", {}),
                    )
                else:
                    logger.warning(f"Unknown policy type: {policy_type}")
                    continue

                # Add to storage (embedding will be generated automatically)
                await storage.add_policy(policy)
                logger.info(f"Saved policy: {policy.id}")

                # Save to filesystem if sync is enabled
                if app_state.policy_filesystem_sync:
                    try:
                        app_state.policy_filesystem_sync.save_policy_to_file(policy)
                        logger.debug(f"Saved policy '{policy.id}' to filesystem")
                    except Exception as e:
                        logger.warning(f"Failed to save policy to filesystem: {e}")

            except Exception as e:
                logger.error(f"Failed to save policy {policy_data.get('id')}: {e}")
                logger.exception(e)
                continue

        # Don't disconnect if using the policy system's storage
        if not (app_state.policy_system and app_state.policy_system.storage):
            storage.disconnect()
            logger.info("Storage disconnected")
        else:
            logger.info("Keeping policy system storage connected")

        logger.info(f"Policies configuration saved: {len(policies)} policies")
        return JSONResponse({"status": "success", "message": f"Saved {len(policies)} policies successfully"})
    except Exception as e:
        logger.error(f"Failed to save policies config: {e}")
        logger.exception(e)
        import traceback

        return JSONResponse(
            {
                "status": "error",
                "message": f"Failed to save policies: {str(e)}",
                "traceback": traceback.format_exc(),
            },
            status_code=500,
        )


@app.get("/api/tools/list")
async def get_tools_list():
    """Endpoint to retrieve detailed list of all available tools."""
    try:
        apps = await get_apps()
        tools_list = []
        apps_list = []

        for app in apps:
            try:
                apis = await get_apis(app.name)
                app_type = getattr(app, "type", "api").upper()

                # Add app to apps list
                apps_list.append({"name": app.name, "type": app_type, "tool_count": len(apis)})

                # Add each tool with its app information
                for tool_name, tool_def in apis.items():
                    tools_list.append(
                        {
                            "name": tool_name,
                            "app": app.name,
                            "app_type": app_type,
                            "description": tool_def.get("description", ""),
                        }
                    )
            except Exception as e:
                logger.warning(f"Failed to get tools for app {app.name}: {e}")
                apps_list.append(
                    {"name": app.name, "type": getattr(app, "type", "api").upper(), "tool_count": 0}
                )

        return JSONResponse({"tools": tools_list, "apps": apps_list})
    except Exception as e:
        logger.error(f"Failed to get tools list: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get tools list: {str(e)}")


@app.get("/api/tools/status")
async def get_tools_status():
    """Endpoint to retrieve tools connection status."""
    try:
        # Get available apps and their tools
        apps = await get_apps()
        tools = []
        internal_tools_count = {}

        for app in apps:
            try:
                apis = await get_apis(app.name)
                tool_count = len(apis)

                # Create tool entry for each app
                tools.append(
                    {
                        "name": app.name,
                        "status": "connected" if tool_count > 0 else "disconnected",
                        "type": getattr(app, "type", "api").upper(),
                    }
                )

                internal_tools_count[app.name] = tool_count
            except Exception as e:
                logger.warning(f"Failed to get tools for app {app.name}: {e}")
                tools.append(
                    {
                        "name": app.name,
                        "status": "error",
                        "type": getattr(app, "type", "api").upper(),
                    }
                )
                internal_tools_count[app.name] = 0

        return JSONResponse({"tools": tools, "internalToolsCount": internal_tools_count})
    except Exception as e:
        logger.error(f"Failed to get tools status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get tools status: {str(e)}")


@app.post("/api/config/mode")
async def save_mode_config(request: Request):
    """Endpoint to save execution mode (fast/balanced) and update agent state lite_mode.
    Note: Mode switching is disabled in hosted environments."""
    try:
        data = await request.json()
        mode = data.get("mode", "balanced")

        # Mode switching disabled - return success without making changes
        logger.info(f"Mode change request received but disabled: {mode}")
        return JSONResponse(
            {
                "status": "success",
                "mode": "balanced",
                "lite_mode": False,
                "message": "Mode switching is disabled. Clone the repo locally to use this feature.",
            }
        )
    except Exception as e:
        logger.error(f"Failed to save mode: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save mode: {str(e)}")


@app.get("/api/agent/state")
async def get_agent_state(request: Request):
    """Endpoint to retrieve agent state for a specific thread."""
    try:
        thread_id = request.headers.get("X-Thread-ID")
        if not thread_id:
            thread_id = request.query_params.get("thread_id")

        if not thread_id:
            raise HTTPException(
                status_code=400,
                detail="thread_id is required (provide via X-Thread-ID header or thread_id query parameter)",
            )

        if not app_state.agent or not app_state.agent.graph:
            raise HTTPException(status_code=503, detail="Agent graph not initialized")

        try:
            state_snapshot = app_state.agent.graph.get_state({"configurable": {"thread_id": thread_id}})

            if not state_snapshot or not state_snapshot.values:
                return JSONResponse(
                    {
                        "thread_id": thread_id,
                        "state": None,
                        "variables": {},
                        "variables_count": 0,
                        "chat_messages_count": 0,
                        "message": "No state found for this thread_id",
                    }
                )

            local_state = AgentState(**state_snapshot.values)
            variables_metadata = local_state.variables_manager.get_all_variables_metadata(
                include_value=False, include_value_preview=True
            )

            return JSONResponse(
                {
                    "thread_id": thread_id,
                    "state": {
                        "input": local_state.input,
                        "url": local_state.url,
                        "current_app": local_state.current_app,
                        "messages_count": len(local_state.messages) if local_state.messages else 0,
                        "chat_messages_count": len(local_state.chat_messages)
                        if local_state.chat_messages
                        else 0,
                        "lite_mode": local_state.lite_mode,
                    },
                    "variables": variables_metadata,
                    "variables_count": len(variables_metadata),
                }
            )
        except Exception as e:
            logger.error(f"Failed to retrieve state for thread_id {thread_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve state: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get agent state: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get agent state: {str(e)}")


@app.get("/api/config/subagents")
async def get_subagents_config():
    """Endpoint to retrieve sub-agents configuration."""
    try:
        return JSONResponse({})
    except Exception as e:
        logger.error(f"Failed to load sub-agents config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load sub-agents config: {str(e)}")


@app.get("/api/apps")
async def get_apps_endpoint():
    """Endpoint to retrieve available apps."""
    try:
        apps = await get_apps()
        apps_data = [
            {
                "name": app.name,
                "description": app.description or "",
                "url": app.url or "",
                "type": getattr(app, "type", "api"),
            }
            for app in apps
        ]
        return JSONResponse({"apps": apps_data})
    except Exception as e:
        logger.error(f"Failed to load apps: {e}")
        return JSONResponse({"apps": []})


@app.get("/api/apps/{app_name}/tools")
async def get_app_tools(app_name: str):
    """Endpoint to retrieve tools for a specific app."""
    try:
        apis = await get_apis(app_name)
        tools_data = [
            {
                "name": tool_name,
                "description": tool_def.get("description", ""),
            }
            for tool_name, tool_def in apis.items()
        ]
        return JSONResponse({"tools": tools_data})
    except Exception as e:
        logger.error(f"Failed to load tools for app {app_name}: {e}")
        return JSONResponse({"tools": []})


@app.post("/api/config/subagents")
async def save_subagents_config(request: Request):
    """Endpoint to save sub-agents configuration."""
    try:
        await request.json()
        logger.info("Sub-agents configuration saved")
        return JSONResponse({"status": "success", "message": "Sub-agents configuration saved"})
    except Exception as e:
        logger.error(f"Failed to save sub-agents config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save sub-agents config: {str(e)}")


@app.post("/api/config/agent-mode")
async def save_agent_mode_config(request: Request):
    """Endpoint to save agent mode (supervisor/single)."""
    try:
        data = await request.json()
        mode = data.get("mode", "supervisor")
        logger.info(f"Agent mode changed to: {mode}")
        return JSONResponse({"status": "success", "mode": mode})
    except Exception as e:
        logger.error(f"Failed to save agent mode: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save agent mode: {str(e)}")


@app.get("/api/agents")
async def get_agents_list():
    """List configured agents (dashboard)."""
    try:
        tools_count = 0
        try:
            apps = await get_apps()
            for app in apps:
                apis = await get_apis(app.name)
                tools_count += len(apis)
        except Exception:
            pass
        logs_url = (
            os.environ.get("CUGA_LOKI_LOGS_URL")
            or os.environ.get("LOKI_URL")
            or "https://grafana.com/docs/loki/latest/"
        )
        latest_version = None
        latest_version_created_at = None
        try:
            from cuga.backend.server.config_store import get_latest_version

            latest_version, latest_version_created_at = get_latest_version()
        except Exception:
            pass
        agents = [
            {
                "id": "cuga-default",
                "description": "Default CUGA agent with policy engine, tools, and chat.",
                "tools_count": tools_count,
                "logs_url": logs_url,
                "latest_version": latest_version,
                "latest_version_created_at": latest_version_created_at,
            }
        ]
        return JSONResponse({"agents": agents})
    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/manage/config")
async def get_manage_config(version: Optional[int] = None):
    """Get agent config (latest or by version)."""
    try:
        from cuga.backend.server.config_store import load_config

        config, ver = load_config(version)
        if config is None:
            return JSONResponse({"config": {}})
        return JSONResponse({"config": config, "version": ver})
    except Exception as e:
        logger.error(f"Failed to load manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/manage/config")
async def save_manage_config(request: Request):
    """Save agent config as a new version."""
    try:
        from cuga.backend.server.config_store import save_config

        data = await request.json()
        config = data.get("config", data)
        ver = save_config(config)
        return JSONResponse({"status": "success", "version": ver})
    except Exception as e:
        logger.error(f"Failed to save manage config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/manage/config/history")
async def get_manage_config_history():
    """List config versions (newest first)."""
    try:
        from cuga.backend.server.config_store import list_versions

        versions = list_versions()
        return JSONResponse({"versions": versions})
    except Exception as e:
        logger.error(f"Failed to list config history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/workspace/tree")
async def get_workspace_tree():
    """Endpoint to retrieve the workspace folder tree."""
    try:
        workspace_path = Path(os.getcwd()) / "cuga_workspace"

        if not workspace_path.exists():
            workspace_path.mkdir(parents=True, exist_ok=True)
            return JSONResponse({"tree": []})

        def build_tree(path: Path, base_path: Path) -> dict:
            """Recursively build file tree."""
            relative_path = str(path.relative_to(base_path.parent))

            if path.is_file():
                return {"name": path.name, "path": relative_path, "type": "file"}
            else:
                children = []
                try:
                    for item in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                        if not item.name.startswith('.'):
                            children.append(build_tree(item, base_path))
                except PermissionError:
                    pass

                return {"name": path.name, "path": relative_path, "type": "directory", "children": children}

        tree = []
        for item in sorted(workspace_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if not item.name.startswith('.'):
                tree.append(build_tree(item, workspace_path))

        return JSONResponse({"tree": tree})
    except Exception as e:
        logger.error(f"Failed to load workspace tree: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load workspace tree: {str(e)}")


@app.get("/api/workspace/file")
async def get_workspace_file(path: str):
    """Endpoint to retrieve a file's content from the workspace."""
    try:
        file_path = Path(path)

        # Security check: ensure the path is within cuga_workspace
        try:
            file_path = file_path.resolve()
            workspace_path = (Path(os.getcwd()) / "cuga_workspace").resolve()
            file_path.relative_to(workspace_path)
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=403, detail="Access denied: Path outside workspace")

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

        # Check file size (limit to 10MB for preview)
        if file_path.stat().st_size > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large to preview (max 10MB)")

        # Read file content
        try:
            loop = asyncio.get_event_loop()

            def _read_file():
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()

            content = await loop.run_in_executor(None, _read_file)
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail="File is not a text file")

        return JSONResponse({"content": content, "path": str(path)})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load file: {str(e)}")


@app.get("/api/workspace/download")
async def download_workspace_file(path: str):
    """Endpoint to download a file from the workspace."""
    try:
        file_path = Path(path)

        # Security check: ensure the path is within cuga_workspace
        try:
            file_path = file_path.resolve()
            workspace_path = (Path(os.getcwd()) / "cuga_workspace").resolve()
            file_path.relative_to(workspace_path)
        except (ValueError, RuntimeError):
            raise HTTPException(status_code=403, detail="Access denied: Path outside workspace")

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="Path is not a file")

        return FileResponse(
            path=str(file_path), filename=file_path.name, media_type='application/octet-stream'
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")


# DISABLED: Delete endpoint commented out for security
# @app.delete("/api/workspace/file")
# async def delete_workspace_file(path: str):
#     """Endpoint to delete a file from the workspace."""
#     try:
#         file_path = Path(path)
#
#         # Security check: ensure the path is within cuga_workspace
#         try:
#             file_path = file_path.resolve()
#             workspace_path = (Path(os.getcwd()) / "cuga_workspace").resolve()
#             file_path.relative_to(workspace_path)
#         except (ValueError, RuntimeError):
#             raise HTTPException(status_code=403, detail="Access denied: Path outside workspace")
#
#         if not file_path.exists():
#             raise HTTPException(status_code=404, detail="File not found")
#
#         if not file_path.is_file():
#             raise HTTPException(status_code=400, detail="Path is not a file")
#
#         # Delete the file
#         file_path.unlink()
#
#         logger.info(f"File deleted successfully: {path}")
#         return JSONResponse({"status": "success", "message": "File deleted successfully"})
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Failed to delete file: {e}")
#         raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")


# DISABLED: Upload endpoint commented out for security
# @app.post("/api/workspace/upload")
# async def upload_workspace_file(file: UploadFile = File(...)):
#     """Endpoint to upload a file to the workspace."""
#     try:
#         # Create workspace directory if it doesn't exist
#         workspace_path = Path(os.getcwd()) / "cuga_workspace"
#         workspace_path.mkdir(exist_ok=True)
#
#         # Sanitize filename and prevent directory traversal
#         safe_filename = Path(file.filename).name
#         if not safe_filename:
#             raise HTTPException(status_code=400, detail="Invalid filename")
#
#         # Prevent overwriting critical files
#         if safe_filename.startswith('.'):
#             raise HTTPException(status_code=400, detail="Hidden files not allowed")
#
#         file_path = workspace_path / safe_filename
#
#         # Check file size (limit to 50MB)
#         file_size = 0
#         content = await file.read()
#         file_size = len(content)
#
#         if file_size > 50 * 1024 * 1024:  # 50MB limit
#             raise HTTPException(status_code=413, detail="File too large (max 50MB)")
#
#         # Write file
#         with open(file_path, 'wb') as f:
#             f.write(content)
#
#         logger.info(f"File uploaded successfully: {safe_filename} ({file_size} bytes)")
#         return JSONResponse(
#             {
#                 "status": "success",
#                 "message": "File uploaded successfully",
#                 "filename": safe_filename,
#                 "path": str(file_path.relative_to(Path("."))),
#                 "size": file_size,
#             }
#         )
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Failed to upload file: {e}")
#         raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")


@app.post("/functions/call", tags=["Registry Proxy"])
async def proxy_function_call(request: Request):
    """
    Proxy endpoint that forwards function call requests to the registry server.
    Exposes the registry's /functions/call endpoint through the main HuggingFace Space URL.
    """
    try:
        registry_url = f"{get_registry_base_url()}/functions/call"

        body = await request.body()
        headers = dict(request.headers)

        host = headers.get('host')

        headers.pop('host', None)
        logger.info(f"Function call request host: {host}")

        trajectory_path = request.query_params.get('trajectory_path')
        params = {}
        if trajectory_path:
            params['trajectory_path'] = trajectory_path

        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(registry_url, content=body, headers=headers, params=params)

            response_headers = dict(response.headers)
            response_headers.pop('content-length', None)
            response_headers.pop('transfer-encoding', None)

            return JSONResponse(
                content=response.json()
                if response.headers.get('content-type', '').startswith('application/json')
                else response.text,
                status_code=response.status_code,
                headers=response_headers,
            )
    except httpx.RequestError as e:
        logger.error(f"Error connecting to registry server: {e}")
        raise HTTPException(status_code=503, detail=f"Registry service unavailable: {str(e)}")
    except Exception as e:
        logger.error(f"Error proxying function call: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to proxy function call: {str(e)}")


def validate_input_length(text: str) -> None:
    """Validate that input text doesn't exceed the maximum allowed length.

    Args:
        text: The input text to validate

    Raises:
        HTTPException: If text exceeds max_input_length
    """
    max_length = settings.advanced_features.max_input_length
    if len(text) > max_length:
        raise HTTPException(
            status_code=413,
            detail=f"Input text is too long. Maximum allowed length is {max_length} characters, "
            f"but received {len(text)} characters. Please reduce the input size.",
        )


async def get_query(request: Request) -> Union[str, ActionResponse]:
    """Parses the incoming request to extract the user query or action."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Request body was not valid JSON.")

    if isinstance(data, dict) and set(data.keys()) == {"query"} and isinstance(data["query"], str):
        query_text = data["query"]
        if not query_text.strip():
            raise HTTPException(status_code=422, detail="`query` may not be empty.")
        validate_input_length(query_text)
        return query_text
    elif isinstance(data, dict) and "action_id" in data:
        try:
            return ActionResponse(**data)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=f"Invalid ActionResponse JSON: {e.errors()}")
    else:
        try:
            chat_obj = ChatRequest.model_validate(data)
            query_text = ""
            for mes in reversed(chat_obj.messages):
                if mes['role'] == 'user':
                    query_text = mes['content']
                    break
            if not query_text.strip():
                raise HTTPException(status_code=422, detail="No user message found or content is empty.")
            validate_input_length(query_text)
            return query_text
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=f"Invalid ChatRequest JSON: {e.errors()}")


@app.get("/flows/{full_path:path}")
async def serve_flows(full_path: str, request: Request):
    """Serves files from the flows directory."""
    file_path = os.path.join(app_state.STATIC_DIR_FLOWS, full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Flow file not found.")


@app.get("/{full_path:path}")
async def serve_react(full_path: str, request: Request):
    """Serves the main React application and its static files."""
    if not app_state.STATIC_DIR_HTML:
        raise HTTPException(status_code=500, detail="Frontend build directory not found.")

    file_path = os.path.join(app_state.STATIC_DIR_HTML, full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)

    index_path = os.path.join(app_state.STATIC_DIR_HTML, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)

    raise HTTPException(status_code=404, detail="Frontend files not found. Did you run the build process?")
