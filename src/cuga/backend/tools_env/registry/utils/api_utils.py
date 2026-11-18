import json
import os
import time
from typing import Dict, List, Optional

import aiohttp

from cuga.backend.tools_env.registry.utils.types import AppDefinition
from cuga.config import settings
from loguru import logger
from cuga.backend.activity_tracker.tracker import ActivityTracker

tracker = ActivityTracker()

# Registry discovery targets a local service, so a short connect timeout is enough to
# detect that it is down without stalling a turn for the aiohttp default (5 minutes).
REGISTRY_CONNECT_TIMEOUT_SECONDS = 3.0
REGISTRY_REQUEST_TIMEOUT_SECONDS = 10.0
# After a failed discovery, skip probing the registry for this long and serve the last
# successful app list (or external apps) instead.
REGISTRY_FAILURE_COOLDOWN_SECONDS = 30.0

_last_registry_apps: Dict[str, List[AppDefinition]] = {}
_registry_failed_at: Dict[str, float] = {}


def registry_client_timeout() -> aiohttp.ClientTimeout:
    """Return the timeout used for registry discovery requests."""
    return aiohttp.ClientTimeout(
        total=REGISTRY_REQUEST_TIMEOUT_SECONDS, connect=REGISTRY_CONNECT_TIMEOUT_SECONDS
    )


def get_agent_id() -> Optional[str]:
    """
    Get the current agent ID from environment variable.

    Returns:
        Agent ID if set, None otherwise
    """
    return os.environ.get("AGENT_ID")


def get_registry_base_url() -> str:
    """
    Get the base URL for the registry server.

    If registry_host is configured in settings, use it.
    Otherwise, default to http://localhost:{registry_port}

    Returns:
        str: Base URL for the registry server (without trailing slash)
    """
    if hasattr(settings.server_ports, 'registry_host') and settings.server_ports.registry_host:
        registry_host = settings.server_ports.registry_host
        # Remove trailing slash if present
        return registry_host.rstrip('/')
    else:
        return f'http://localhost:{settings.server_ports.registry}'


async def get_apis(app_name: str, agent_id: Optional[str] = None):
    """
    Execute an asynchronous GET request to retrieve APIs from the registry.

    Args:
        app_name: Name of the application
        agent_id: Optional agent ID for database mode (uses AGENT_ID env var if not provided)

    Returns:
        dict: The JSON response data as a Python dictionary

    Raises:
        Exception: If the request fails or the response is not valid JSON
    """
    all_tools = {}

    # Get tools from tracker
    try:
        logger.debug("calling get_apis")
        tools = tracker.get_tools_by_server(app_name)
        if not settings.advanced_features.registry:
            logger.debug("Registry is not enabled, using external tools")
            return tools
        if tools:
            return tools
    except Exception as e:
        logger.warning(e)

    # Get tools from API
    registry_base = get_registry_base_url()
    url = f'{registry_base}/applications/{app_name}/apis?include_response_schema=true'

    # Add agent_id parameter if provided or available from environment
    if agent_id is None:
        agent_id = get_agent_id()
    if agent_id:
        url += f'&agent_id={agent_id}'

    headers = {'accept': 'application/json'}

    try:
        async with aiohttp.ClientSession(timeout=registry_client_timeout()) as session:
            async with session.get(url, headers=headers) as response:
                # Check if the request was successful
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Request failed with status {response.status}: {error_text}")

                # Parse JSON response
                json_data = await response.json()
                if json_data:
                    all_tools.update(json_data)
                return all_tools

    except Exception as e:
        if len(all_tools) > 0:
            logger.warning("registry is not running, using external apps")
            return all_tools
        else:
            logger.error("Error while calling registry to get apps")
            raise e


async def get_apps(agent_id: Optional[str] = None) -> List[AppDefinition]:
    """
    Execute an asynchronous GET request to retrieve applications from the registry.

    Args:
        agent_id: Optional agent ID for database mode (uses AGENT_ID env var if not provided)

    Returns:
        List of AppDefinition objects

    Raises:
        Exception: If the request fails or the response is not valid JSON
    """
    logger.debug("Calling get apps")

    registry_base = get_registry_base_url()
    url = f'{registry_base}/applications'

    # Add agent_id parameter if provided or available from environment
    if agent_id is None:
        agent_id = get_agent_id()
    if agent_id:
        url += f'?agent_id={agent_id}'

    headers = {'accept': 'application/json'}
    external_apps = tracker.apps
    if not settings.advanced_features.registry:
        logger.debug("Registry is not enabled, using external apps")
        return external_apps
    logger.debug(f"External apps are {external_apps}")

    failed_at = _registry_failed_at.get(url)
    if failed_at is not None and time.monotonic() - failed_at < REGISTRY_FAILURE_COOLDOWN_SECONDS:
        logger.debug("Registry discovery recently failed, skipping probe during cooldown")
        last_apps = _last_registry_apps.get(url)
        if last_apps is not None:
            return list(last_apps) + list(external_apps)
        if len(external_apps) > 0:
            return external_apps
        raise ConnectionError("Registry is unavailable (discovery failed recently)")

    try:
        async with aiohttp.ClientSession(timeout=registry_client_timeout()) as session:
            async with session.get(url, headers=headers) as response:
                logger.debug("Recieved responses")
                # Check if the request was successful
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Request failed with status {response.status}: {error_text}")

                # Parse JSON response
                json_data = await response.json()
                registry_apps = [AppDefinition(**p) for p in json_data]
                _last_registry_apps[url] = registry_apps
                _registry_failed_at.pop(url, None)
                return list(registry_apps) + list(external_apps)
    except Exception as e:
        _registry_failed_at[url] = time.monotonic()
        last_apps = _last_registry_apps.get(url)
        if last_apps is not None:
            logger.warning("registry is not reachable, using last known apps")
            return list(last_apps) + list(external_apps)
        if len(external_apps) > 0:
            logger.warning("registry is not running, using external apps")
            return external_apps
        else:
            logger.error("Error while calling registry to get apps")
            raise e


async def count_total_tools() -> int:
    """Count total number of tools across all apps.

    Returns:
        Total number of tools available
    """
    try:
        # If registry is not enabled, count tracker tools
        if not settings.advanced_features.registry:
            total_count = 0
            for server_name, tools_list in tracker.tools.items():
                total_count += len(tools_list)
            logger.debug(f"Total tracker tools count: {total_count}")
            return total_count

        # Otherwise, count tools from registry
        apps = await get_apps()
        total_count = 0

        for app in apps:
            try:
                apis = await get_apis(app.name)
                if apis:
                    total_count += len(apis.keys())
            except Exception as e:
                logger.debug(f"Could not count tools for app {app.name}: {e}")
                continue

        logger.debug(f"Total registry tools count: {total_count}")
        return total_count
    except Exception as e:
        logger.warning(f"Error counting total tools: {e}")
        return 0


def read_json_file(file_path):
    """
    Read and parse a JSON file from the specified path.

    Args:
        file_path (str): Path to the JSON file

    Returns:
        dict: The parsed JSON data
    """
    try:
        with open(file_path, 'r') as file:
            data = json.load(file)
        return data
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {file_path}")
    except Exception as e:
        print(f"Error reading file: {e}")
