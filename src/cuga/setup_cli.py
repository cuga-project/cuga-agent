"""Terminal provider setup using CUGA's existing model profiles and .env loader."""

import argparse
from dataclasses import dataclass
import json
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


CONNECTION_MESSAGES = {
    "ready": "Provider connection verified.",
    "authentication": "Authentication was rejected. Check the API key and access permissions.",
    "not_found": "The model or endpoint was not found. Check the model identifier and endpoint URL.",
    "request": "The provider rejected the configuration. Check the model and provider-specific settings.",
    "quota": "The provider reported a rate or quota limit. Check your allowance or retry later.",
    "timeout": "The connection timed out. Check the endpoint or retry.",
    "network": "Could not reach the provider. Check the endpoint and network connection.",
    "tls": "Certificate verification failed. Check the endpoint certificate and local trust configuration.",
    "dependency": "A provider dependency could not be loaded. Check the CUGA installation.",
    "unknown": "Check your model, endpoint and credentials.",
}
CONNECTION_RESULT_PREFIX = "CUGA_SETUP_RESULT:"


@dataclass(frozen=True)
class ConnectionResult:
    ok: bool
    reason: str = "unknown"

    def __bool__(self):
        return self.ok

    @property
    def message(self) -> str:
        return CONNECTION_MESSAGES.get(self.reason, CONNECTION_MESSAGES["unknown"])


def connection_failure_reason(error: Exception) -> str:
    """Classify provider errors without returning exception text or response bodies."""
    seen = set()
    fallback = "unknown"
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        status = getattr(error, "status_code", None) or getattr(
            getattr(error, "response", None), "status_code", None
        )
        if status in (401, 403):
            return "authentication"
        if status == 404:
            return "not_found"
        if status == 429:
            return "quota"
        if status in (400, 422):
            return "request"
        name = type(error).__name__.lower()
        if "ssl" in name or "certificate" in name:
            return "tls"
        if isinstance(error, TimeoutError) or "timeout" in name:
            return "timeout"
        if isinstance(error, ImportError):
            return "dependency"
        if "connection" in name or "connecterror" in name:
            fallback = "network"
        error = error.__cause__ or error.__context__
    return fallback


def validate_connection() -> int:
    try:
        from cuga.config import settings
        from cuga.backend.llm.models import LLMManager

        model_settings = dict(settings.agent.code.model)
        model_settings.update(timeout=30, max_tokens=64)
        model = LLMManager().get_model(model_settings)
        model.invoke("Reply with: CUGA is ready.")
    except Exception as error:
        reason = connection_failure_reason(error)
        print(CONNECTION_RESULT_PREFIX + json.dumps({"reason": reason}))
        return 1
    return 0


def test_connection(values: dict[str, str]) -> ConnectionResult:
    """Check the model client in a bounded subprocess; never echo provider output."""
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
    except subprocess.TimeoutExpired:
        return ConnectionResult(False, "timeout")
    except OSError:
        return ConnectionResult(False, "dependency")
    if result.returncode == 0:
        return ConnectionResult(True, "ready")
    output = getattr(result, "stdout", b"")
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    for line in reversed(output.splitlines()):
        if line.startswith(CONNECTION_RESULT_PREFIX):
            try:
                reason = json.loads(line[len(CONNECTION_RESULT_PREFIX) :])["reason"]
                if isinstance(reason, str) and reason in CONNECTION_MESSAGES:
                    return ConnectionResult(False, reason)
            except (ValueError, KeyError, TypeError):
                break
    return ConnectionResult(False)


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


def configure(path: Path) -> bool:
    from cuga.setup_terminal import run_setup

    values = run_setup(path)
    if values is None:
        print("Setup cancelled; existing configuration was preserved.")
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
        return validate_connection()
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
        result = test_connection({})
        if not result:
            print(f"Connection test failed. {result.message}", file=sys.stderr)
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
            from cuga.setup_terminal import TerminalUI

            choice = TerminalUI().choose(
                "Existing configuration",
                "A provider is already configured. Keep it or update the connection?",
                [("keep", "Keep existing configuration"), ("change", "Change provider configuration")],
                default="keep",
            )
            if choice != "change":
                return 0 if choice == "keep" else 1
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
