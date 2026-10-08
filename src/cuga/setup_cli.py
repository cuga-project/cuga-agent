"""Terminal provider setup using CUGA's existing model profiles and .env loader."""

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


# Label, packaged profile, credential, endpoint variable and its default.
PROVIDERS = (
    ("OpenAI", "openai", "OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1"),
    ("OpenRouter", "openrouter", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    ("watsonx", "watsonx", "WATSONX_API_KEY", "WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
    ("Ollama (local)", "ollama", "", "OPENAI_BASE_URL", "http://localhost:11434/v1"),
    ("OpenAI-compatible private endpoint", "openai", "OPENAI_API_KEY", "OPENAI_BASE_URL", ""),
    ("Groq", "groq", "GROQ_API_KEY", "", ""),
    ("Azure OpenAI", "azure", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", ""),
    ("RITS", "rits", "RITS_API_KEY", "RITS_BASE_URL", ""),
    ("MiniMax", "minimax", "MINIMAX_API_KEY", "MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
)


def load_environment(root: Path) -> Path:
    """Respect explicit ENV_FILE and shell values; load stored setup from any cwd."""
    from dotenv import find_dotenv, load_dotenv

    explicit = os.environ.get("ENV_FILE")
    found = find_dotenv(usecwd=True) if not explicit else ""
    path = Path(explicit or found or root / ".env").expanduser().absolute()
    load_dotenv(path, override=bool(explicit))
    # A project .env can supply only tool settings; fill remaining values from local setup.
    local = root / ".env"
    if local.resolve() != path:
        load_dotenv(local, override=False)
    return path


def read_profile(filename: str) -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.10 remains supported for existing source installations.
        from dynaconf.vendor import toml as tomllib

    models = Path(os.getenv("CUGA_CONFIGURATIONS_DIR", Path(__file__).parent / "configurations")) / "models"
    profile = tomllib.loads((models / filename).read_text())["agent"]["code"]["model"]
    if not isinstance(profile, dict) or not profile.get("platform"):
        raise ValueError("Model profile requires a platform")
    return profile


def profile_name() -> str:
    return (
        os.getenv("AGENT_SETTING_CONFIG", "settings.openai.toml").split("#")[0].strip().strip("\"'")
        or "settings.openai.toml"
    )


def missing_configuration() -> list[str]:
    try:
        profile = read_profile(profile_name())
    except (OSError, KeyError, ValueError):
        return ["AGENT_SETTING_CONFIG (model profile unavailable)"]
    missing = []
    if not os.getenv("MODEL_NAME", profile.get("model_name", "")).strip():
        missing.append("MODEL_NAME")
    platform = profile["platform"]
    credential = {
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "watsonx": "WATSONX_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
        "rits": "RITS_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "google-genai": "GOOGLE_API_KEY",
        "wxo": "WXO_API_KEY",
    }.get(platform)
    value = os.getenv(credential, "") if credential else ""
    if platform == "watsonx":
        value = value or os.getenv("WATSONX_APIKEY", "")
    external_secrets = os.getenv("DYNACONF_SECRETS__MODE", "local").lower() != "local" and os.getenv(
        "DYNACONF_SECRETS__FORCE_ENV", "true"
    ).lower() in ("false", "0", "no")
    if credential and not value.strip() and not external_secrets:
        missing.append(credential)
    if platform == "watsonx" and not (os.getenv("WATSONX_PROJECT_ID") or os.getenv("WATSONX_SPACE_ID")):
        missing.append("WATSONX_PROJECT_ID or WATSONX_SPACE_ID")
    for variable in {"azure": ("AZURE_OPENAI_ENDPOINT",), "wxo": ("WXO_INSTANCE_URL",)}.get(platform, ()):
        if not os.getenv(variable, "").strip():
            missing.append(variable)
    return missing


def test_connection(values: dict[str, str]) -> bool:
    """Check the actual model client in a fresh process with a bounded timeout."""
    try:
        environment = {**os.environ, **values}
        # Candidate values must win over the previous explicit .env during validation.
        environment.pop("ENV_FILE", None)
        result = subprocess.run(
            [sys.executable, "-m", "cuga.setup_cli", "--validate"],
            env=environment,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def save_environment(path: Path, values: dict[str, str]) -> None:
    """Atomically update selected keys and preserve unrelated configuration/comments."""
    from dotenv import set_key

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Choose a regular .env file, not a symlink.")
    previous = path.read_text() if path.exists() else "# Local CUGA provider configuration\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(previous)
    try:
        for key, value in values.items():
            set_key(str(temporary), key, value)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prompt_value(label: str, current: str = "", *, secret: bool = False) -> str:
    suffix = " [Enter to keep existing]" if secret and current else f" [{current}]" if current else ""
    while True:
        entered = getpass.getpass(f"{label}{suffix}: ") if secret else input(f"{label}{suffix}: ")
        value = entered.strip() or current
        if value:
            return value
        print("A value is required.")


def configure(path: Path) -> bool:
    print("\nConfigure your CUGA inference provider")
    print(f"Configuration file: {path}")
    for number, provider in enumerate(PROVIDERS, 1):
        print(f"  {number}. {provider[0]}")
    selected = None
    while selected is None:
        answer = input("Provider [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(PROVIDERS):
            selected = PROVIDERS[int(answer) - 1]
        else:
            print("Choose a provider number from the list.")
    label, provider, credential, endpoint, default_endpoint = selected
    filename = f"settings.{provider}.toml"
    same_provider = filename == profile_name()
    profile = read_profile(filename)
    model = os.getenv("MODEL_NAME", "") if same_provider else ""
    values = {
        "AGENT_SETTING_CONFIG": filename,
        "MODEL_NAME": prompt_value("Model identifier", model or profile.get("model_name", "")),
        "DYNACONF_SECRETS__FORCE_ENV": "true",
    }
    if endpoint:
        current = os.getenv(endpoint, "") if same_provider else ""
        value = prompt_value("Endpoint URL", current or default_endpoint or profile.get("url", ""))
        while not valid_endpoint(value):
            print("Enter an http:// or https:// URL without embedded credentials.")
            value = prompt_value("Endpoint URL")
        values[endpoint] = value.rstrip("/")
    if credential:
        current_key = os.getenv(credential, "")
        if provider == "watsonx":
            current_key = current_key or os.getenv("WATSONX_APIKEY", "")
        values[credential] = prompt_value(credential, current_key, secret=True)
        if provider == "watsonx":
            values["WATSONX_APIKEY"] = values[credential]
    else:
        values["OPENAI_API_KEY"] = "ollama"  # pragma: allowlist secret (Local server placeholder.)
    if provider in ("openai", "ollama"):
        values["LLM_AUTH_HEADER"] = ""
    if provider == "watsonx":
        current_scope = "space" if os.getenv("WATSONX_SPACE_ID") else "project"
        scope_name = prompt_value("watsonx scope (project or space)", current_scope)
        while scope_name not in ("project", "space"):
            scope_name = prompt_value("watsonx scope (project or space)", current_scope)
        scope = "WATSONX_SPACE_ID" if scope_name == "space" else "WATSONX_PROJECT_ID"
        values[scope] = prompt_value(scope, os.getenv(scope, ""))
        values["WATSONX_PROJECT_ID" if scope_name == "space" else "WATSONX_SPACE_ID"] = ""
    print("Testing the connection with a short inference request…")
    if not test_connection(values):
        print(
            f"Connection test failed for {label}. Check your model, endpoint and credentials; existing configuration was preserved."
        )
        return False
    save_environment(path, values)
    os.environ.update(values)
    print(f"Connection verified. Saved provider configuration to {path} (permissions 0600).")
    return True


def valid_endpoint(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme in ("http", "https") and bool(url.hostname) and not (url.username or url.password)
    except ValueError:
        return False


def ensure_provider(root: Path) -> bool:
    try:
        path = load_environment(root)
        missing = missing_configuration()
    except (OSError, ValueError):
        print(
            "Could not read configuration. Check the .env file encoding, path and permissions.",
            file=sys.stderr,
        )
        return False
    if not missing:
        return True
    if not sys.stdin.isatty() or os.getenv("CI"):
        print("CUGA provider configuration is incomplete: " + ", ".join(missing), file=sys.stderr)
        print(
            "Run cuga setup in an interactive terminal, or supply a configured .env using ENV_FILE.",
            file=sys.stderr,
        )
        return False
    try:
        configured = configure(path)
        if configured:
            print("In Configure & try it out, ask: What can you help me automate?")
        return configured
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; existing configuration was preserved.", file=sys.stderr)
        return False
    except (OSError, ValueError):
        print("Could not save configuration. Check the .env file path and permissions.", file=sys.stderr)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cuga setup", description="Configure an inference provider locally and test its connection."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate existing configuration and test its connection without prompts or writes.",
    )
    parser.add_argument("--validate", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.validate:
        from cuga.config import settings
        from cuga.backend.llm.models import LLMManager

        model_settings = dict(settings.agent.code.model)
        model_settings.update(timeout=30, max_tokens=64)
        model = LLMManager().get_model(model_settings)
        model.invoke("Reply with: CUGA is ready.")
        return 0
    from cuga.local_setup import prepare_local_manager

    try:
        root = prepare_local_manager()
        path = load_environment(root)
    except (OSError, ValueError):
        print(
            "Could not read configuration. Check the .env file encoding, path and permissions.",
            file=sys.stderr,
        )
        return 1
    if args.check:
        missing = missing_configuration()
        if missing:
            print("Missing configuration: " + ", ".join(missing), file=sys.stderr)
            return 1
        if not test_connection({}):
            print("Connection test failed. Check your model, endpoint and credentials.", file=sys.stderr)
            return 1
        print("Provider connection verified.")
        return 0
    if not sys.stdin.isatty() or os.getenv("CI"):
        print(
            "Interactive setup requires a terminal. Supply configuration and use cuga setup --check.",
            file=sys.stderr,
        )
        return 1
    try:
        if not missing_configuration():
            print("Existing provider configuration detected.")
            if input("Change it? [y/N]: ").strip().lower() not in ("y", "yes"):
                return 0
        if not configure(path):
            return 1
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; existing configuration was preserved.", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Could not save configuration. Check the .env file path and permissions.", file=sys.stderr)
        return 1
    print("Next: cuga start manager. In Configure & try it out, ask: What can you help me automate?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
