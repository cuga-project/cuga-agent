import os

# stdlib
from importlib import import_module
from pathlib import Path
from sys import platform
from urllib.parse import urlparse

# third-party imports
import dynaconf
from dotenv import find_dotenv, load_dotenv
from dynaconf import Dynaconf, Validator
from loguru import logger

# ---------------------------------------------------------------------------
# Package root & path helper (must be defined BEFORE first use)
# ---------------------------------------------------------------------------

# Get the package root from path_store
PACKAGE_ROOT = Path(os.environ.get("CUGA_PACKAGE_ROOT", Path(__file__).parent.resolve()))
LOGGING_DIR = os.environ.get("CUGA_LOGGING_DIR", os.path.join(PACKAGE_ROOT, "./logging"))
TRAJECTORY_DATA_DIR = os.path.join(LOGGING_DIR, "trajectory_data")
TRACES_DIR = os.path.join(LOGGING_DIR, "traces")
# Databases directory (sibling to logging)
DBS_DIR = os.environ.get("CUGA_DBS_DIR", os.path.join(PACKAGE_ROOT, "./dbs"))
# Define all path variables at the top (with environment variable overrides)
ENV_FILE_PATH = os.getenv("ENV_FILE_PATH") or os.path.join(PACKAGE_ROOT, "..", "..", ".env")


# Helper function to find config files with existence check
def _find_config_file(filename: str, env_var_name: str) -> str:
    """Find config file, checking existence in getcwd first, then package root."""
    # First check environment variable
    env_path = os.getenv(env_var_name)
    if env_path and os.path.exists(env_path):
        return env_path

    # Check in current working directory
    cwd_path = os.path.join(os.getcwd(), filename)
    if os.path.exists(cwd_path):
        return cwd_path

    # Fall back to package root
    package_path = os.path.join(PACKAGE_ROOT, filename)
    return package_path  # Return even if it doesn't exist for consistency


SETTINGS_TOML_PATH = _find_config_file("settings.toml", "SETTINGS_TOML_PATH")
EVAL_CONFIG_TOML_PATH = _find_config_file("eval_config.toml", "EVAL_CONFIG_TOML_PATH")
CONFIGURATIONS_DIR = os.environ.get("CUGA_CONFIGURATIONS_DIR", os.path.join(PACKAGE_ROOT, "configurations"))
MODELS_DIR = os.path.join(CONFIGURATIONS_DIR, "models")
MODES_DIR = os.path.join(CONFIGURATIONS_DIR, "modes")
MEMORY_DIR = os.path.join(CONFIGURATIONS_DIR, "memory")


# from feature_flags import FeatureFlags as flags

# 1) Let users (or CI) force a path when needed
if os.getenv("ENV_FILE"):
    load_dotenv(os.getenv("ENV_FILE"), override=True)
else:
    # 2) Try to find it when working from a git clone (searches up from CWD)
    path = find_dotenv(filename=".env", usecwd=True)
    if not path:
        # 3) Try again when your code is used as an installed package
        #    (searches up from the module’s location)
        path = find_dotenv(filename=".env", usecwd=False)

    load_dotenv(path, override=False)

for key, value in os.environ.items():
    if key.startswith("WA_"):
        new_key = key[3:]
        os.environ[new_key] = value

app_mapping = {
    urlparse(os.getenv("WA_REDDIT")).netloc: "reddit",
    urlparse(os.getenv("WA_SHOPPING")).netloc: "shopping",
    urlparse(os.getenv("WA_SHOPPING_ADMIN")).netloc: "shopping_admin",
    urlparse(os.getenv("WA_GITLAB")).netloc: "gitlab",
    urlparse(os.getenv("WA_WIKIPEDIA")).netloc: "wikipedia",
    urlparse(os.getenv("WA_MAP")).netloc: "map",
    urlparse(os.getenv("WA_HOMEPAGE")).netloc: "homepage",
}


# Load your settings


def get_all_paths(config, parent_key=""):
    """
    Recursively traverse a nested dictionary and generate all paths with keys separated by dots.

    Args:
        nested_dict (dict): The nested dictionary to traverse.
        parent_key (str): The accumulated path of keys (used during recursion).

    Returns:
        list: A list of all paths as strings.
    """
    paths = []
    for key, value in config.items():
        current_path = f"{parent_key}.{key}" if parent_key else key
        if isinstance(value, dict):
            paths.extend(get_all_paths(value, current_path))
        else:
            paths.append(current_path)
    return paths


# Use PACKAGE_ROOT for all file paths to ensure they work regardless of where the app is started
validators = [
    Validator("eval_config.headless", default=False),
    Validator("features.local_sandbox", default=True),
    Validator("features.forced_apps", default=None),
    Validator("features.thoughts", default=True),
    Validator("features.code_generation", default="accurate"),
    Validator("advanced_features.registry", default=True),
    Validator("features.task_decomposition", default=False),
    Validator("advanced_features.langfuse_tracing", default=False),
    Validator("advanced_features.benchmark", default="default"),
    Validator("advanced_features.tracker_enabled", default=False),
    Validator("advanced_features.lite_mode", default=False),
    Validator("advanced_features.lite_mode_tool_threshold", default=15),
    Validator("advanced_features.enable_memory", default=False),
    Validator("advanced_features.enable_fact", default=False),
    Validator("advanced_features.decomposition_strategy", default="flexible"),
    Validator("advanced_features.local_sandbox", default=True),
    Validator("advanced_features.message_window_limit", default=20),
    Validator("advanced_features.max_input_length", default=50000),
    Validator("advanced_features.e2b_sandbox_mode", default="per-session"),
    Validator("advanced_features.enable_web_search", default=False),
    Validator("features.chat", default=True),
    Validator("features.memory_provider", default="mem0"),
    Validator("playwright_args", default=[]),
    Validator("server_ports.registry_host", default=None),
]
base_settings = Dynaconf(
    root_path=PACKAGE_ROOT,
    settings_files=[
        SETTINGS_TOML_PATH,
        ENV_FILE_PATH,
        EVAL_CONFIG_TOML_PATH,
    ],
    validators=validators,
)
logger.info("Running cuga in *{}* mode".format(base_settings.features.cuga_mode))
if base_settings.advanced_features.tracker_enabled:
    logger.info("✅ tracker enabled - logs and trajectory data will be saved")
else:
    logger.warning("tracker disabled - logs and trajectory data will not be saved")
# Read and sanitize the model settings filename (Windows users sometimes include quotes)
default_llm = os.environ.get("AGENT_SETTING_CONFIG", "settings.openai.toml")
default_llm = default_llm.strip().strip('"').strip("'")
logger.info("loaded llm settings *{}*".format(default_llm))

# Resolve absolute config file paths
models_file_path = os.path.join(MODELS_DIR, default_llm)
modes_file_path = os.path.join(MODES_DIR, f"{base_settings.features.cuga_mode}.toml")

logger.info(f"Models config path: {models_file_path}")
logger.info(f"Mode config path:   {modes_file_path}")

if base_settings.advanced_features.enable_memory:
    memory_file_path = os.path.join(
        MEMORY_DIR, f"memory_settings.{base_settings.features.memory_provider}.toml"
    )
    tips_extractor_file_path = os.path.join(MEMORY_DIR, "memory_settings.tips_extractor.toml")

    logger.info(f"Memory config path:   {memory_file_path}")
    logger.info(f"Memory tips extractor config path:   {tips_extractor_file_path}")

# Fail fast with clear error if files are missing (helps especially on Windows)
if os.getenv("CUGA_STRICT_CONFIG", "1") == "1":
    if not os.path.isfile(models_file_path):
        raise FileNotFoundError(
            "Could not find models configuration file: "
            f"{models_file_path}. If you are on Windows and set AGENT_SETTING_CONFIG in CMD, "
            "do not include surrounding quotes."
        )
    if not os.path.isfile(modes_file_path):
        raise FileNotFoundError(f"Could not find mode configuration file: {modes_file_path}.")

    if base_settings.advanced_features.enable_memory:
        if not os.path.isfile(memory_file_path):
            raise FileNotFoundError(f"Could not find memory configuration file: {memory_file_path}.")

        if not os.path.isfile(tips_extractor_file_path):
            raise FileNotFoundError(
                f"Could not find tips extractor configuration file: {tips_extractor_file_path}."
            )

settings_files = [
    SETTINGS_TOML_PATH,
    ENV_FILE_PATH,
    EVAL_CONFIG_TOML_PATH,
    models_file_path,
    modes_file_path,
]

if base_settings.advanced_features.enable_memory:
    settings_files.extend([memory_file_path, tips_extractor_file_path])

settings = Dynaconf(
    root_path=PACKAGE_ROOT,
    settings_files=settings_files,
    validators=validators,
)

# Add default enable format in each model configuration
paths = get_all_paths(settings, "")
platform_paths = [k for k in paths if "model.platform" in k]
for k in platform_paths:
    settings.validators.register(
        Validator(
            k.lower().replace("platform", "enable_format"),
            default=True
            if "watsonx" in getattr(settings, k.lower())
            or "rits" in getattr(settings, k.lower())
            or "groq" in getattr(settings, k.lower())
            else False,
        )
    )

# raises after all possible errors are evaluated
try:
    settings.validators.validate_all()
except dynaconf.ValidationError as e:
    accumulative_errors = e.details
    logger.warning(accumulative_errors)


def get_class(class_path):
    """Dynamically import and return a class by its full path."""
    module_name, class_name = class_path.rsplit(".", 1)
    module = import_module(module_name)
    return getattr(module, class_name)


# `envvar_prefix` = export envvars with `export DYNACONF_FOO=bar`.
# `settings_files` = Load these files in the order.
def get_user_data_path():
    if platform == "darwin":  # macOS
        return os.path.expanduser(os.getenv("MAC_USER_DATA_PATH", ""))
    elif platform == "win32":  # Windows
        return os.getenv("WINDOWS_USER_DATA_PATH", "")
    elif platform.startswith("linux"):  # Linux/Unix systems
        return os.path.expanduser(os.getenv("LINUX_USER_DATA_PATH", ""))
    else:
        raise Exception("Unsupported OS")


def get_app_name_from_url(curr_url):
    if not curr_url:
        return "N/A"
    parsed_url = urlparse(curr_url)
    host_with_port = f"{parsed_url.hostname}:{parsed_url.port}"
    return app_mapping.get(host_with_port, parsed_url.hostname)


if __name__ == "__main__":
    model = settings.agent.task_decomposition.model
