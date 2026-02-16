import json
import os
from contextlib import asynccontextmanager
from json import JSONDecodeError
from fastapi import FastAPI, HTTPException
from pathlib import Path
from mcp.types import TextContent
from pydantic import BaseModel  # Import BaseModel for request body
from typing import Dict, Any, List, Optional  # Add Any for flexible args/return
from fastapi.responses import JSONResponse
from cuga.config import PACKAGE_ROOT
from cuga.backend.activity_tracker.tracker import ActivityTracker, Step
from cuga.backend.tools_env.registry.config.config_loader import load_service_configs
from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager
from cuga.backend.tools_env.registry.registry.api_registry import ApiRegistry
from loguru import logger
from cuga.config import settings

tracker = ActivityTracker()


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
    resolved_path = Path(os.environ.get("MCP_SERVERS_FILE", DEFAULT_MCP_SERVERS_FILE)).resolve()
    logger.info(f"MCP_SERVERS_FILE: {resolved_path}")
    if not resolved_path.exists():
        raise FileNotFoundError(f"MCP servers configuration file not found: {resolved_path}")
    return resolved_path


def _config_path_to_str():
    try:
        return str(get_config_filename())
    except FileNotFoundError:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_manager, registry
    config_file = get_config_filename()
    print(f"Using configuration file: {config_file}")
    services = load_service_configs(str(config_file))
    mcp_manager = MCPManager(config=services)
    registry = ApiRegistry(client=mcp_manager)
    await registry.start_servers()
    yield


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
async def list_applications():
    global registry
    """
    Retrieve a list of all registered applications and their descriptions.
    """
    return await registry.show_applications()


# -- API Endpoints --
@app.get("/applications/{app_name}/apis", tags=["APIs"])
async def list_application_apis(app_name: str, include_response_schema: bool = False):
    global registry
    """
    Retrieve the list of API definitions for a specific application.
    """
    try:
        return await registry.show_apis_for_app(app_name, include_response_schema)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error in list_application_apis for '{app_name}': {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {type(e).__name__}: {str(e)}")


@app.get("/apis", tags=["APIs"])
async def list_all_apis(include_response_schema: bool = False):
    global registry
    """
    Retrieve a list of all API definitions across all registered applications.
    """
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
async def call_mcp_function(request: FunctionCallRequest, trajectory_path: Optional[str] = None):
    global registry, mcp_manager

    """
    Calls a named function via the underlying MCP client, passing provided arguments.

    - **name**: The exact name of the function to execute.
    - **args**: A dictionary containing the arguments required by the function.
    """
    print(f"Received request to call function: {request.function_name} with args: {request.args}")
    try:
        global mcp_manager
        apis = await registry.show_apis_for_app(request.app_name)
        api_info = apis.get(request.function_name, {})
        is_secure = api_info.get("secure", False)
        logger.debug(f"is_secure: {is_secure}")
        if trajectory_path:
            settings.update({"ADVANCED_FEATURES": {"TRACKER_ENABLED": True}}, merge=True)
            tracker.collect_step_external(
                Step(name="api_call", data=request.model_dump_json()), full_path=trajectory_path
            )
        result: TextContent = await registry.call_function(
            app_name=request.app_name,
            function_name=request.function_name,
            arguments=request.args,
            auth_config=mcp_manager.auth_config.get(request.app_name) if is_secure else None,
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
async def reload_config():
    """Reload MCP config from file and reinitialize registry (for manager mode)."""
    global mcp_manager, registry
    config_path = _config_path_to_str()
    if not config_path:
        raise HTTPException(status_code=500, detail="MCP config file not found")
    try:
        services = load_service_configs(config_path)
        new_manager = MCPManager(config=services)
        new_registry = ApiRegistry(client=new_manager)
        await new_registry.start_servers()
        mcp_manager = new_manager
        registry = new_registry
        logger.info("Registry reloaded from %s", config_path)
        return {"status": "ok", "config_file": config_path}
    except Exception as e:
        logger.exception("Reload failed: %s", e)
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
