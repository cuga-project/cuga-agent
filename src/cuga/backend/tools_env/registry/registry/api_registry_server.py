import json
import os
from contextlib import asynccontextmanager
from json import JSONDecodeError
from fastapi import FastAPI, HTTPException, Query
from pathlib import Path
from mcp.types import TextContent
from pydantic import BaseModel  # Import BaseModel for request body
from typing import Dict, Any, List, Optional  # Add Any for flexible args/return
from fastapi.responses import JSONResponse
from cuga.config import PACKAGE_ROOT
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.tools_env.registry.config.config_loader import (
    load_service_configs,
    load_service_configs_from_db,
)
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
from loguru import logger
from cuga.config import settings

tracker = ActivityTracker()

# Global cache for agent-specific registries
agent_registries: Dict[str, tuple[MCPManager, ApiRegistry]] = {}
database_mode = False
default_agent_id = "cuga-default"


# --- Pydantic Models ---
class FunctionCallRequest(BaseModel):
    """Request body model for calling a function."""

    app_name: str  # name of the app to call
    function_name: str  # The name of the function to call
    args: Dict[str, Any]  # Arguments for the function


class FunctionCallOnboardRequest(BaseModel):
    """Request body model for calling a function."""

    app_name: str  # name of the app to call
    schemas: List[dict]  # The name of the function to call


# Default configuration file
DEFAULT_MCP_SERVERS_FILE = os.path.join(
    PACKAGE_ROOT, "backend", "tools_env", "registry", "config", "mcp_servers.yaml"
)


# Function to get configuration filename
def get_config_filename():
    """Get config filename from environment, handling 'none' for database mode."""
    config_path = os.environ.get("MCP_SERVERS_FILE", DEFAULT_MCP_SERVERS_FILE)

    # Handle database mode
    if config_path.lower() == "none":
        logger.info("MCP_SERVERS_FILE set to 'none' - using database mode")
        return "none"

    resolved_path = Path(config_path).resolve()
    logger.info(f"MCP_SERVERS_FILE: {resolved_path}")
    if not resolved_path.exists():
        raise FileNotFoundError(f"MCP servers configuration file not found: {resolved_path}")
    return resolved_path


def _config_path_to_str():
    try:
        result = get_config_filename()
        return str(result) if result != "none" else "none"
    except FileNotFoundError:
        return None


def _get_agent_id():
    """Get agent ID from environment variable, default to 'cuga-default'."""
    return os.environ.get("AGENT_ID", "cuga-default")


async def _get_or_create_registry(
    agent_id: str, retry_on_empty: bool = False
) -> tuple[MCPManager, ApiRegistry]:
    """Get or create registry for a specific agent (with caching).

    Args:
        agent_id: The agent ID to get/create registry for
        retry_on_empty: If True and DB returns empty config, retry once after a short delay
    """
    global agent_registries, database_mode

    if agent_id in agent_registries:
        logger.debug(f"Using cached registry for agent: {agent_id}")
        return agent_registries[agent_id]

    logger.info(f"Creating new registry for agent: {agent_id}")

    if database_mode:
        services = await load_service_configs_from_db(agent_id)

        # If empty and retry requested, wait and try once more (handles race conditions)
        if not services and retry_on_empty:
            logger.info(f"Config empty for agent {agent_id}, retrying after 1 second...")
            import asyncio

            await asyncio.sleep(1)
            services = await load_service_configs_from_db(agent_id)
            if services:
                logger.info(f"Retry successful - loaded {len(services)} services for agent {agent_id}")
            else:
                logger.warning(f"Retry failed - config still empty for agent {agent_id}")
    else:
        # In YAML mode, all agents share the same config
        config_file = get_config_filename()
        services = load_service_configs(str(config_file))

    manager = MCPManager(config=services)
    reg = ApiRegistry(client=manager)
    await reg.start_servers()

    agent_registries[agent_id] = (manager, reg)
    return manager, reg


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_manager, registry, database_mode, default_agent_id
    config_file = get_config_filename()

    # Check if using database mode
    if config_file == "none":
        database_mode = True
        default_agent_id = _get_agent_id()
        print(f"Using database mode with default agent: {default_agent_id}")
        print("Multi-agent support enabled - pass agent_id query parameter to switch agents")

        # Initialize default agent
        mcp_manager, registry = await _get_or_create_registry(default_agent_id)
    else:
        database_mode = False
        print(f"Using configuration file: {config_file}")
        services = load_service_configs(str(config_file))
        mcp_manager = MCPManager(config=services)
        registry = ApiRegistry(client=mcp_manager)
        await registry.start_servers()

    yield

    # Cleanup: close all agent registries
    for agent_id, (mgr, reg) in agent_registries.items():
        logger.info(f"Cleaning up registry for agent: {agent_id}")


# --- FastAPI Server Setup ---
app = FastAPI(
    title="API Registry",
    description="A FastAPI server to register and query API/Application metadata",
    version="0.1.1",  # Incremented version
    lifespan=lifespan,
)


# --- API Endpoints ---


# -- Application Endpoints --
@app.get("/applications", tags=["Applications"])
async def list_applications(
    agent_id: Optional[str] = Query(None, description="Agent ID (database mode only)"),
):
    global registry, database_mode, default_agent_id
    """
    Retrieve a list of all registered applications and their descriptions.
    In database mode, optionally specify agent_id to get tools for a specific agent.
    """
    if database_mode and agent_id:
        _, reg = await _get_or_create_registry(agent_id)
        return await reg.show_applications()
    return await registry.show_applications()


# -- API Endpoints --
@app.get("/applications/{app_name}/apis", tags=["APIs"])
async def list_application_apis(
    app_name: str,
    include_response_schema: bool = False,
    agent_id: Optional[str] = Query(None, description="Agent ID (database mode only)"),
):
    global registry, database_mode, default_agent_id
    """
    Retrieve the list of API definitions for a specific application.
    In database mode, optionally specify agent_id to get tools for a specific agent.
    """
    try:
        if database_mode and agent_id:
            _, reg = await _get_or_create_registry(agent_id)
            return await reg.show_apis_for_app(app_name, include_response_schema)
        return await registry.show_apis_for_app(app_name, include_response_schema)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in list_application_apis for '{app_name}': {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {type(e).__name__}: {str(e)}")


@app.get("/apis", tags=["APIs"])
async def list_all_apis(
    include_response_schema: bool = False,
    agent_id: Optional[str] = Query(None, description="Agent ID (database mode only)"),
):
    global registry, database_mode, default_agent_id
    """
    Retrieve a list of all API definitions across all registered applications.
    In database mode, optionally specify agent_id to get tools for a specific agent.
    """
    if database_mode and agent_id:
        _, reg = await _get_or_create_registry(agent_id)
        return await reg.show_all_apis(include_response_schema)
    return await registry.show_all_apis(include_response_schema)


class AuthAppsRequest(BaseModel):
    apps: List[str]


@app.post("/api/authenticate_apps", tags=["APIs"])
async def authenticate_apps(request: AuthAppsRequest):
    """
    auth_apps
    """
    return await registry.auth_apps(request.apps)


@app.post("/functions/onboard", tags=["Functions"])
async def onboard_function(request: FunctionCallOnboardRequest):
    global registry, mcp_manager
    mcp_manager.schemas[request.app_name] = request.schemas
    return {"status": f"Loaded successfully {len(request.schemas)} tools"}


# --- ENDPOINT for Calling Functions ---
@app.post("/functions/call", tags=["Functions"])
async def call_mcp_function(
    request: FunctionCallRequest,
    trajectory_path: Optional[str] = None,
    agent_id: Optional[str] = Query(None, description="Agent ID (database mode only)"),
):
    global registry, mcp_manager, database_mode

    """
    Calls a named function via the underlying MCP client, passing provided arguments.
    In database mode, optionally specify agent_id to use tools for a specific agent.

    - **name**: The exact name of the function to execute.
    - **args**: A dictionary containing the arguments required by the function.
    - **agent_id**: (Optional) Agent ID to use in database mode
    """
    print(f"Received request to call function: {request.function_name} with args: {request.args}")
    try:
        # Get the appropriate registry for the agent
        if database_mode and agent_id:
            mcp_mgr, reg = await _get_or_create_registry(agent_id)
        else:
            mcp_mgr, reg = mcp_manager, registry

        apis = await reg.show_apis_for_app(request.app_name)
        api_info = apis.get(request.function_name, {})
        is_secure = api_info.get("secure", False)
        logger.debug(f"is_secure: {is_secure}")
        if trajectory_path:
            settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": True}}, merge=True)
            tracker.collect_step_external(
                Step(name="api_call", data=request.model_dump_json()), full_path=trajectory_path
            )
        result: TextContent = await reg.call_function(
            app_name=request.app_name,
            function_name=request.function_name,
            arguments=request.args,
            auth_config=mcp_mgr.auth_config.get(request.app_name) if is_secure else None,
        )

        # Check if this is an /auth/token endpoint call and update stored token
        # Only do this when benchmark is "appworld"
        if settings.advanced_features.benchmark == "appworld":
            api_path = api_info.get("path", "")
            is_auth_token_endpoint = api_path.endswith("/auth/token") or "/auth/token" in api_path

            if is_auth_token_endpoint and not isinstance(result, dict):
                # Successful token fetch - extract and store the token
                try:
                    # Result is TextContent list, extract the text
                    if result and len(result) > 0:
                        result_text = result[0].text if hasattr(result[0], 'text') else str(result[0])
                        try:
                            result_json = (
                                json.loads(result_text) if isinstance(result_text, str) else result_text
                            )
                            if isinstance(result_json, dict) and "access_token" in result_json:
                                token = result_json["access_token"]
                                # Update the auth manager's stored token
                                if registry.auth_manager:
                                    registry.auth_manager._tokens[request.app_name] = token
                                    logger.info(
                                        f"✅ Updated stored token for {request.app_name} from /auth/token endpoint"
                                    )
                                else:
                                    logger.debug(
                                        f"Auth manager not available to store token for {request.app_name}"
                                    )
                            else:
                                logger.debug(
                                    f"Token response for {request.app_name} does not contain 'access_token': {result_json}"
                                )
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.debug(f"Could not parse token response as JSON: {result_text}, error: {e}")
                except Exception as e:
                    logger.warning(f"Failed to extract and store token from /auth/token response: {e}")

        if isinstance(result, dict):
            # If it's an error dict, extract and prioritize the detailed error message
            if result.get("status") == "exception":
                error_message = result.get("message", "Unknown error")
                logger.error(f"Function call returned error: {error_message}")

                # Extract detailed message from error_detail if available
                error_detail = result.get("error_detail", {})
                if error_detail and isinstance(error_detail, dict):
                    response_body = error_detail.get("response_body")
                    if response_body:
                        if isinstance(response_body, dict):
                            # Prioritize "message" field, then "detail", then format the whole dict
                            if "message" in response_body:
                                detailed_msg = response_body["message"]
                                result["message"] = detailed_msg
                                logger.error(f"  Detailed error message: {detailed_msg}")
                            elif "detail" in response_body:
                                detailed_msg = response_body["detail"]
                                result["message"] = detailed_msg
                                logger.error(f"  Detailed error message: {detailed_msg}")
                        elif isinstance(response_body, str):
                            # If response_body is a string, use it as the detailed message
                            result["message"] = response_body
                            logger.error(f"  Detailed error message: {response_body}")
                        logger.error(f"  Full error detail: {error_detail}")
                else:
                    # Even if no error_detail, check if message already contains detailed info
                    # and ensure it's properly set
                    if error_message and error_message != "Unknown error":
                        logger.error(f"  Error message: {error_message}")

            tracker.collect_step_external(
                Step(name="api_response", data=json.dumps(result)), full_path=trajectory_path
            )
            return JSONResponse(status_code=result.get("status_code", 500), content=result)
        else:
            result_json = None
            logger.debug(result)
            if result and result[0]:
                result_json = result[0].text
                try:
                    result_json = json.loads(result[0].text)
                except JSONDecodeError:
                    pass
            if result[0].text == "[]":
                result_json = []
            final_response = result_json
        logger.debug(f"Final response: {final_response}")
        tracker.collect_step_external(
            Step(
                name="api_response",
                data=json.dumps(final_response) if not isinstance(final_response, str) else final_response,
            ),
            full_path=trajectory_path,
        )
        return final_response
    except HTTPException as e:
        logger.error(f"HTTPException in call_mcp_function: {e}")
        logger.error(f"  Status Code: {e.status_code}")
        logger.error(f"  Detail: {e.detail}")
        raise e
    except Exception as e:
        # Catch any other unexpected errors during the process
        import traceback

        error_traceback = traceback.format_exc()
        logger.error(f"Unexpected error in call_mcp_function endpoint: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Full traceback:\n{error_traceback}")

        print(f"\n{'=' * 60}")
        print("UNEXPECTED ERROR in call_mcp_function endpoint")
        print(f"{'=' * 60}")
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")
        print(f"Function: {request.function_name}")
        print(f"App: {request.app_name}")
        print(f"Args: {request.args}")
        print("\nFull Traceback:")
        print(error_traceback)
        print(f"{'=' * 60}\n")

        raise HTTPException(
            status_code=500, detail=f"Internal server error processing function call: {str(e)}"
        )


@app.post("/reload")
async def reload_config(
    agent_id: Optional[str] = Query(None, description="Agent ID to reload (database mode only)"),
):
    """
    Reload MCP config from file or database and reinitialize registry.
    In database mode, optionally specify agent_id to reload a specific agent (or all if not specified).
    """
    global mcp_manager, registry, database_mode, default_agent_id, agent_registries
    config_path = _config_path_to_str()

    try:
        # Check if using database mode
        if config_path == "none":
            if agent_id:
                # Reload specific agent
                logger.info(f"Reloading from database for agent: {agent_id}")
                # Clear cache for this agent
                if agent_id in agent_registries:
                    del agent_registries[agent_id]
                # Recreate registry for this agent with retry on empty
                await _get_or_create_registry(agent_id, retry_on_empty=True)
                # If this is the default agent, update global registry
                if agent_id == default_agent_id:
                    mcp_manager, registry = agent_registries[agent_id]

                # Check for initialization errors
                current_manager, _ = agent_registries.get(agent_id, (None, None))
                errors = current_manager.initialization_errors if current_manager else {}

                response = {
                    "status": "ok" if not errors else "partial",
                    "source": f"database (agent: {agent_id})",
                    "agent_id": agent_id,
                }
                if errors:
                    response["errors"] = errors
                    response["message"] = f"{len(errors)} tool(s) failed to initialize"

                return response
            else:
                # Reload all agents (clear cache)
                logger.info("Reloading all agents from database")
                agent_ids = list(agent_registries.keys())
                agent_registries.clear()
                # Recreate default agent
                mcp_manager, registry = await _get_or_create_registry(default_agent_id)
                return {
                    "status": "ok",
                    "source": "database (all agents cleared)",
                    "reloaded_agents": agent_ids,
                }
        else:
            # YAML mode - reload from file
            if not config_path:
                raise HTTPException(status_code=500, detail="MCP config file not found")
            logger.info(f"Reloading from file: {config_path}")
            services = load_service_configs(config_path)
            new_manager = MCPManager(config=services)
            new_registry = ApiRegistry(client=new_manager)
            await new_registry.start_servers()
            mcp_manager = new_manager
            registry = new_registry
            logger.info("Registry reloaded from %s", config_path)
            return {"status": "ok", "source": config_path, "tool_count": len(services)}
    except Exception as e:
        logger.exception("Reload failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clear_cache")
async def clear_agent_cache(
    agent_id: Optional[str] = Query(None, description="Agent ID to clear (or all if not specified)"),
):
    """
    Clear the agent registry cache. Useful in database mode when you want to force reload.
    """
    global agent_registries, database_mode

    if not database_mode:
        raise HTTPException(status_code=400, detail="Cache clearing only available in database mode")

    try:
        if agent_id:
            if agent_id in agent_registries:
                del agent_registries[agent_id]
                logger.info(f"Cleared cache for agent: {agent_id}")
                return {"status": "ok", "message": f"Cache cleared for agent: {agent_id}"}
            else:
                return {"status": "ok", "message": f"No cache found for agent: {agent_id}"}
        else:
            count = len(agent_registries)
            agent_registries.clear()
            logger.info(f"Cleared cache for {count} agents")
            return {"status": "ok", "message": f"Cache cleared for {count} agents"}
    except Exception as e:
        logger.exception("Cache clear failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reset")
async def reset():
    """Reset the registry state, including clearing all stored authentication tokens."""
    if registry.auth_manager:
        registry.auth_manager.clear_tokens()
        logger.info("Cleared all stored authentication tokens")
    registry.auth_manager = None
    logger.info("Registry reset completed")


@app.get("/functions/get_schema/{call_name}", tags=["Functions"])
async def get_mcp_function_schema(request: FunctionCallRequest):
    """
    Calls a named function via the underlying MCP client, passing provided arguments.

    - **name**: The exact name of the function to execute.
    - **args**: A dictionary containing the arguments required by the function.
    """
    pass


# -- Root Endpoint --
@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to the API Registry. See /docs for API documentation."}


#
# # --- Setup command line argument parser ---
# def parse_arguments():
#     parser = argparse.ArgumentParser(description="API Registry server")
#     parser.add_argument("--config",
#                         default=DEFAULT_MCP_SERVERS_FILE,
#                         help=f"MCP servers configuration JSON file (default: {DEFAULT_MCP_SERVERS_FILE})")
#     return parser.parse_args()


# --- Main Execution Block ---
if __name__ == "__main__":
    import uvicorn

    # args = parse_arguments()
    # # Set environment variable for the lifespan function to use
    # os.environ["MCP_SERVERS_FILE"] = args.config

    # print(f"Starting API Registry server with config: {args.config}...")

    uvicorn.run(app, host="127.0.0.1", port=settings.server_ports.registry)
