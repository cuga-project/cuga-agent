import re
import threading
from datetime import date
from typing import Dict, Any, Optional, Mapping
import hashlib
import json
import os

import httpx
import openai
from langchain_openai import ChatOpenAI, AzureChatOpenAI
from langchain_ibm import ChatWatsonx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from loguru import logger

from cuga.backend.secrets import resolve_secret
from cuga.config import settings


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that preserves non-standard reasoning fields.

    LangChain's _convert_dict_to_message only forwards function_call, tool_calls,
    and audio into additional_kwargs. Models that return reasoning_content (e.g.
    DeepSeek-style or self-hosted reasoning models) have that field silently
    dropped. This subclass rescues it by post-processing the raw response dict.
    """

    def _create_chat_result(
        self,
        response: "dict | openai.BaseModel",
        generation_info: "dict | None" = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)

        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices") or []
        for i, res in enumerate(choices):
            if i >= len(result.generations):
                break
            raw_msg = res.get("message") or {}
            reasoning = raw_msg.get("reasoning_content")
            if reasoning and isinstance(result.generations[i].message, AIMessage):
                result.generations[i].message.additional_kwargs.setdefault("reasoning_content", reasoning)

        return result


try:
    from langchain_litellm import ChatLiteLLM as _ChatLiteLLMBase

    class ReasoningChatLiteLLM(_ChatLiteLLMBase):
        """LiteLLM chat model that preserves ``reasoning_content`` on AIMessage.

        Mirrors :class:`ReasoningChatOpenAI` for backends where the raw completion
        includes ``choices[].message.reasoning_content`` but conversion drops it.
        """

        def _create_chat_result(self, response: Mapping[str, Any]) -> ChatResult:
            result = super()._create_chat_result(response)
            choices = response.get("choices") or []
            for i, res in enumerate(choices):
                if i >= len(result.generations):
                    break
                raw_msg = res.get("message") or {}
                reasoning = raw_msg.get("reasoning_content")
                if reasoning and isinstance(result.generations[i].message, AIMessage):
                    result.generations[i].message.additional_kwargs.setdefault("reasoning_content", reasoning)
            return result

except ImportError:
    ReasoningChatLiteLLM = None  # type: ignore[misc, assignment]

_ENV_REF_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _normalize_secret(val: Optional[str]) -> Optional[str]:
    """If val looks like an env var ref (e.g. GROQ_API_KEY), resolve from os.environ.
    Prevents literal env names from being sent as credentials when resolve_secret
    returns a raw ref (e.g. plain scheme).
    """
    if not val or not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    if _ENV_REF_PATTERN.match(s):
        actual = os.environ.get(s)
        return actual if actual else None
    return s


_current_llm_override: Optional[Dict[str, Any]] = None


def get_current_llm_override() -> Optional[Dict[str, Any]]:
    return _current_llm_override


def set_current_llm_override(override: Optional[Dict[str, Any]]) -> None:
    global _current_llm_override
    _current_llm_override = override


class _ModelSettingsWrap:
    def __init__(self, d: dict):
        self._d = d

    def get(self, k: str, default: Any = None) -> Any:
        return self._d.get(k, default)

    def to_dict(self) -> dict:
        return self._d.copy()


try:
    from langchain_groq import ChatGroq
except ImportError:
    logger.warning("Langchain Groq not installed, using OpenAI instead")
    ChatGroq = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    logger.warning("Langchain Google GenAI not installed, using OpenAI instead")
    ChatGoogleGenerativeAI = None


class LLMManager:
    """Singleton class to manage LLM instances based on agent name and settings"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._models: Dict[str, Any] = {}
            self._pre_instantiated_model: Optional[BaseChatModel] = None
            self._initialized = True

    def convert_dates_to_strings(self, obj):
        if isinstance(obj, dict):
            return {k: self.convert_dates_to_strings(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_dates_to_strings(item) for item in obj]
        elif isinstance(obj, date):
            return obj.isoformat()
        else:
            return obj

    def set_llm(self, model: BaseChatModel) -> None:
        """Set a pre-instantiated model to use for all tasks

        Args:
            model: Pre-instantiated ChatOpenAI or BaseChatModel instance
        """
        if not isinstance(model, BaseChatModel):
            raise ValueError("Model must be an instance of BaseChatModel or its subclass")

        self._pre_instantiated_model = model
        logger.info(f"Pre-instantiated model set: {type(model).__name__}")

    def _update_model_parameters(
        self,
        model: BaseChatModel,
        temperature: float = 0.1,
        max_tokens: int = 1000,
        max_completion_tokens: Optional[int] = None,
    ) -> BaseChatModel:
        """Update model parameters (temperature, max_tokens, and max_completion_tokens) for the task

        Args:
            model: The model to update
            temperature: Temperature setting (default: 0.1)
            max_tokens: Maximum tokens for the task
            max_completion_tokens: Maximum completion tokens for the task (defaults to max_tokens if not provided)

        Returns:
            Updated model with new parameters
        """
        model_kwargs = {}
        if hasattr(model, 'model_kwargs') and model.model_kwargs is not None:
            model_kwargs = model.model_kwargs.copy()

        # Check if this is a reasoning model
        model_name = getattr(model, 'model_name', '') or getattr(model, 'model', '')
        is_reasoning = self._is_reasoning_model(model_name)

        # Update temperature only for non-reasoning models
        if not is_reasoning:
            if hasattr(model, 'temperature'):
                logger.debug(f"Updating model temperature: {temperature}")
                if hasattr(model, 'model_kwargs') and model.model_kwargs is not None:
                    logger.debug(f"Model keys: {model.model_kwargs.keys()}")
                logger.debug(f"Model instance: {type(model)}")
                model.temperature = temperature
            elif 'temperature' in model_kwargs:
                model_kwargs['temperature'] = temperature
        else:
            logger.debug(f"Skipping temperature update for reasoning model: {model_name}")

        # Set max_completion_tokens (defaults to max_tokens if not provided)
        completion_tokens = max_completion_tokens if max_completion_tokens is not None else max_tokens

        # Update max_tokens
        if hasattr(model, 'max_tokens'):
            model.max_tokens = max_tokens
        elif 'max_tokens' in model_kwargs:
            model_kwargs['max_tokens'] = max_tokens

        # Update max_completion_tokens
        if hasattr(model, 'max_completion_tokens'):
            model.max_completion_tokens = completion_tokens
        elif 'max_completion_tokens' in model_kwargs:
            model_kwargs['max_completion_tokens'] = completion_tokens

        # Update model_kwargs if it exists
        if hasattr(model, 'model_kwargs') and model.model_kwargs is not None:
            model.model_kwargs = model_kwargs

        logger.debug(
            f"Updated model parameters: temperature={temperature}, max_tokens={max_tokens}, max_completion_tokens={completion_tokens}"
        )
        return model

    def clear_pre_instantiated_model(self) -> None:
        """Clear the pre-instantiated model and return to normal model creation"""
        self._pre_instantiated_model = None
        logger.info("Pre-instantiated model cleared, returning to normal model creation")

    def _create_cache_key(self, model_settings: Dict[str, Any]) -> str:
        """Create a unique cache key from model settings including resolved values"""
        to_dict = getattr(model_settings, "to_dict", None)
        raw = to_dict() if callable(to_dict) else model_settings
        d = self.convert_dates_to_strings(raw)
        keys_to_delete = [key for key in d if "prompt" in key]

        for key in keys_to_delete:
            del d[key]

        # Add resolved values to ensure cache key reflects actual configuration
        platform = model_settings.get('platform')
        if platform:
            d['resolved_model_name'] = self._get_model_name(model_settings, platform)
            d['resolved_api_version'] = self._get_api_version(model_settings, platform)
            d['resolved_base_url'] = self._get_base_url(model_settings, platform)

        settings_str = json.dumps(d, sort_keys=True)
        return hashlib.md5(settings_str.encode()).hexdigest()

    def _get_model_name(self, model_settings: Dict[str, Any], platform: str) -> str:
        """Get model name: config (JSON) first, then env, then TOML."""
        config_model = model_settings.get("model")
        if config_model and str(config_model).strip():
            return str(config_model).strip()
        toml_model_name = model_settings.get('model_name')

        if platform == "openai":
            # For OpenAI, check environment variables
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                # Default fallback
                default_model = "gpt-4o"
                logger.info(f"No model_name specified, using default: {default_model}")
                return default_model
        elif platform == "groq":
            # For Groq, check environment variables
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment for Groq: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                # Default fallback
                default_model = "openai/gpt-oss-20b"
                logger.info(f"No model_name specified, using default: {default_model}")
                return default_model
        elif platform == "watsonx":
            # For WatsonX, check environment variables
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment for WatsonX: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                # Default fallback for WatsonX
                default_model = "meta-llama/llama-4-maverick-17b-128e-instruct-fp8"
                logger.info(f"No model_name specified for WatsonX, using default: {default_model}")
                return default_model
        elif platform == "azure":
            # For Azure, check environment variables
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment for Azure: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                # Default fallback for Azure
                default_model = "gpt-4o"
                logger.info(f"No model_name specified for Azure, using default: {default_model}")
                return default_model
        elif platform == "google-genai":
            # For Google GenAI, check environment variables
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment for Google GenAI: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                # Default fallback for Google GenAI
                default_model = "gemini-1.5-pro"
                logger.info(f"No model_name specified for Google GenAI, using default: {default_model}")
                return default_model
        elif platform == "openrouter":
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment for OpenRouter: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                default_model = "anthropic/claude-3.5-sonnet"
                logger.info(f"No model_name specified for OpenRouter, using default: {default_model}")
                return default_model
        elif platform == "litellm":
            env_model_name = os.environ.get('MODEL_NAME')
            if env_model_name:
                logger.info(f"Using MODEL_NAME from environment for LiteLLM: {env_model_name}")
                return env_model_name
            elif toml_model_name:
                logger.debug(f"Using model_name from TOML: {toml_model_name}")
                return toml_model_name
            else:
                default_model = "gpt-4o"
                logger.info(f"No model_name specified for LiteLLM, using default: {default_model}")
                return default_model
        else:
            # For other platforms, use TOML or default
            if toml_model_name:
                return toml_model_name
            else:
                raise ValueError(f"model_name must be specified for platform: {platform}")

    def _get_api_version(self, model_settings: Dict[str, Any], platform: str) -> str:
        """Get API version with environment variable override support"""
        if platform == "litellm":
            return ""
        if platform == "openai":
            # Check environment variable first
            env_api_version = os.environ.get('OPENAI_API_VERSION')
            if env_api_version:
                logger.info(f"Using OPENAI_API_VERSION from environment: {env_api_version}")
                return env_api_version

            # Check TOML settings
            toml_api_version = model_settings.get('api_version')
            if toml_api_version:
                # Validate if it's a date type and transform to string
                if isinstance(toml_api_version, date):
                    toml_api_version = toml_api_version.isoformat()
                    logger.debug(f"Converted date to string: {toml_api_version}")
                logger.debug(f"Using api_version from TOML: {toml_api_version}")
                return toml_api_version

            return None
        else:
            # For other platforms, use TOML or default
            api_version = model_settings.get('api_version', "2024-08-06")
            # Validate if it's a date type and transform to string
            if isinstance(api_version, date):
                api_version = api_version.isoformat()
                logger.debug(f"Converted date to string: {api_version}")
            return api_version

    def _get_auth_headers(self, model_settings: Dict[str, Any], platform: str) -> Dict[str, str]:
        """Build auth headers for openai platform. Supports Bearer token and custom header-based auth.

        Config options:
        - auth_token: env var name; value used as Bearer token (Authorization: Bearer <value>)
        - api_key: env var name (alias for auth_token)
        - auth_header: env var name; value is full header value (e.g. "Bearer xyz")
        - default_headers: dict of header_name -> value; values starting with $ are env var names
        Env override: LLM_AUTH_HEADER (full header value, e.g. "Bearer <token>")
        """
        if platform != "openai":
            return {}

        headers: Dict[str, str] = {}
        to_dict = getattr(model_settings, "to_dict", lambda: model_settings)
        d = to_dict() if callable(to_dict) else model_settings

        config_api_key = d.get("api_key")
        if config_api_key and isinstance(config_api_key, str):
            val = _normalize_secret(resolve_secret(config_api_key))
            if val:
                headers["Authorization"] = val if val.lower().startswith("bearer ") else f"Bearer {val}"
                return headers

        if os.environ.get("LLM_AUTH_HEADER"):
            val = _normalize_secret(resolve_secret("LLM_AUTH_HEADER"))
            if val:
                headers["Authorization"] = val if val.lower().startswith("bearer ") else f"Bearer {val}"
                return headers

        token_env = d.get("auth_token") or d.get("api_key") or d.get("apikey_name")
        if token_env:
            token = _normalize_secret(resolve_secret(token_env))
            if token:
                headers["Authorization"] = f"Bearer {token}"
                return headers

        auth_header_env = d.get("auth_header")
        if isinstance(auth_header_env, str):
            val = _normalize_secret(resolve_secret(auth_header_env))
            if val:
                headers["Authorization"] = (
                    val if val.strip().lower().startswith("bearer ") else f"Bearer {val}"
                )
                return headers

        default_headers = d.get("default_headers")
        if isinstance(default_headers, dict):
            for k, v in default_headers.items():
                if isinstance(v, str) and v.startswith("$"):
                    v = _normalize_secret(resolve_secret(v[1:].strip())) or ""
                if v:
                    headers[k] = str(v)

        return headers

    def _get_base_url(self, model_settings: Dict[str, Any], platform: str) -> str:
        """Get base URL: config (JSON) first, then env, then TOML.

        Groq uses its own fixed endpoint — never apply a base URL for it.
        """
        # Groq SDK manages its own endpoint; any URL in config is ignored
        if platform == "groq":
            return None

        config_url = model_settings.get("base_url") or model_settings.get("url")
        if config_url and str(config_url).strip():
            return str(config_url).strip()
        if platform == "openai":
            # Check environment variable first
            env_base_url = os.environ.get('OPENAI_BASE_URL')
            if env_base_url:
                logger.info(f"Using OPENAI_BASE_URL from environment: {env_base_url}")
                return env_base_url

            # Check TOML settings (for litellm compatibility)
            toml_url = model_settings.get('url')
            if toml_url:
                logger.debug(f"Using url from TOML: {toml_url}")
                return toml_url

            # Default to None (uses OpenAI's default endpoint)
            logger.debug("No base URL specified, using OpenAI default endpoint")
            return None
        elif platform == "openrouter":
            env_base_url = os.environ.get('OPENROUTER_BASE_URL')
            if env_base_url:
                logger.info(f"Using OPENROUTER_BASE_URL from environment: {env_base_url}")
                return env_base_url

            # Check TOML settings
            toml_url = model_settings.get('url')
            if toml_url:
                logger.debug(f"Using url from TOML: {toml_url}")
                return toml_url

            # Default to None (will raise error later if not set)
            default_openrouter = "https://openrouter.ai/api/v1"
            logger.debug(
                f"No base URL specified for OpenRouter, will raise error if not set, falling back to: {default_openrouter}"
            )
            return default_openrouter
        elif platform == "litellm":
            env_base_url = os.environ.get('OPENAI_BASE_URL') or os.environ.get('LITELLM_API_BASE')
            if env_base_url:
                logger.info(f"Using base URL from environment for LiteLLM: {env_base_url}")
                return env_base_url
            toml_url = model_settings.get('url')
            if toml_url:
                logger.debug(f"Using url from TOML: {toml_url}")
                return toml_url
            return None
        else:
            # For other platforms, use TOML settings
            return model_settings.get('url')

    def _get_ssl_verify(self, model_settings: Dict[str, Any]) -> "bool | str":
        """Return the ssl verify value for httpx / litellm clients.

        Priority:
        1. disable_ssl flag (model_settings or CUGA_DISABLE_SSL env) → False
        2. ssl_ca_bundle in model_settings → cert path string
        3. CUGA_SSL_CA_BUNDLE env var → cert path string
        4. settings.connections.ssl_ca_bundle (TOML) → cert path string
        5. OPENAI_SSL_VERIFY=false env var → False
        6. Default → True
        """
        disable_ssl = model_settings.get("disable_ssl") or os.environ.get("CUGA_DISABLE_SSL", "").lower() in (
            "true",
            "1",
            "yes",
        )
        if disable_ssl:
            return False

        # Explicit verify=false via legacy env var
        if os.environ.get("OPENAI_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
            return False

        # Per-model CA bundle from config dict
        ca_bundle = model_settings.get("ssl_ca_bundle")
        if ca_bundle and str(ca_bundle).strip():
            return str(ca_bundle).strip()

        # Global env var override
        env_bundle = os.environ.get("CUGA_SSL_CA_BUNDLE", "").strip()
        if env_bundle:
            return env_bundle

        # Global TOML setting via dynaconf
        try:
            toml_bundle = settings.connections.ssl_ca_bundle
            if toml_bundle and str(toml_bundle).strip():
                return str(toml_bundle).strip()
        except Exception:
            pass

        return True

    def _is_reasoning_model(self, model_name: str) -> bool:
        """Check if model is a reasoning model that doesn't support temperature

        OpenAI's reasoning models (o1, o3, gpt-5 series) don't support temperature parameter
        """
        if not model_name:
            return False
        reasoning_prefixes = ('o1', 'o3', 'gpt-5', 'gpt-5.5', 'azure/gpt-5.5')
        return model_name.startswith(reasoning_prefixes)

    def _create_llm_instance(self, model_settings: Dict[str, Any]):
        """Create LLM instance based on platform and settings"""
        platform = model_settings.get('platform')
        temperature = model_settings.get('temperature', 0.7)
        max_tokens = model_settings.get('max_tokens')
        assert max_tokens is not None, "max_tokens must be specified"
        # Handle environment variable overrides
        model_name = self._get_model_name(model_settings, platform)
        api_version = self._get_api_version(model_settings, platform)
        base_url = self._get_base_url(model_settings, platform)
        if platform == "azure":
            api_version = str(model_settings.get('api_version'))
            is_reasoning = self._is_reasoning_model(model_name)

            if is_reasoning:
                logger.debug(f"Creating AzureChatOpenAI reasoning model: {model_name} (no temperature)")
                llm = AzureChatOpenAI(
                    model_version=api_version,
                    timeout=61,
                    api_version="2025-04-01-preview",
                    azure_deployment=model_name + "-" + api_version,
                    max_completion_tokens=max_tokens,
                )
            else:
                logger.debug(f"Creating AzureChatOpenAI model: {model_name} - {api_version}")
                llm = AzureChatOpenAI(
                    timeout=61,
                    azure_deployment=model_name + "-" + api_version,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
        elif platform == "openai":
            is_reasoning = self._is_reasoning_model(model_name)

            openai_params: Dict[str, Any] = {
                "model_name": model_name,
                "max_tokens": max_tokens,
                "timeout": 61,
            }

            if not is_reasoning:
                openai_params["temperature"] = temperature
            else:
                logger.debug(f"Skipping temperature for reasoning model: {model_name}")

            auth_headers = self._get_auth_headers(model_settings, platform)
            if auth_headers:
                openai_params["default_headers"] = auth_headers
                openai_params["openai_api_key"] = "dummy"
            else:
                apikey_ref = model_settings.get("api_key")
                if apikey_ref:
                    openai_params["openai_api_key"] = _normalize_secret(
                        resolve_secret(apikey_ref)
                    ) or os.environ.get(apikey_ref)
                else:
                    apikey_name = model_settings.get("apikey_name")
                    if apikey_name:
                        openai_params["openai_api_key"] = _normalize_secret(
                            resolve_secret(apikey_name)
                        ) or os.environ.get(apikey_name)

            if base_url:
                openai_params["openai_api_base"] = base_url

            ssl_verify = self._get_ssl_verify(model_settings)
            if ssl_verify is not True:
                openai_params["http_client"] = httpx.Client(verify=ssl_verify)
                openai_params["http_async_client"] = httpx.AsyncClient(verify=ssl_verify)

            llm = ReasoningChatOpenAI(**openai_params)
        elif platform == "groq":
            api_key = None
            apikey_ref = model_settings.get("api_key")
            if apikey_ref:
                api_key = _normalize_secret(resolve_secret(apikey_ref)) or os.environ.get(apikey_ref)
            if not api_key:
                api_key = _normalize_secret(resolve_secret("GROQ_API_KEY")) or os.environ.get("GROQ_API_KEY")
            logger.debug(f"Creating Groq model: {model_name}")
            llm = ChatGroq(
                groq_api_key=api_key,
                max_tokens=max_tokens,
                model=model_name,
                temperature=temperature,
            )
        elif platform == "watsonx":
            watsonx_params: Dict[str, Any] = {
                "params": {
                    "temperature": temperature,
                    "max_completion_tokens": max_tokens,
                },
            }

            watsonx_url = model_settings.get("url")
            if watsonx_url:
                watsonx_params["url"] = watsonx_url

            deployment_id = model_settings.get("deployment_id")
            if deployment_id:
                # Deployment id is model-specific configuration representing
                # the id of a deployed model in production
                watsonx_params["deployment_id"] = deployment_id
            else:
                # model id is not used when using a deployment id
                watsonx_params["model_id"] = model_name

            space_id = os.getenv("WATSONX_SPACE_ID")
            project_id = os.getenv("WATSONX_PROJECT_ID")

            if space_id:
                watsonx_params["space_id"] = space_id
            elif project_id:
                watsonx_params["project_id"] = project_id
            else:
                raise ValueError("WatsonX requires WATSONX_SPACE_ID or WATSONX_PROJECT_ID to be set.")

            llm = ChatWatsonx(**watsonx_params)
        elif platform == "rits":
            apikey_name = model_settings.get("apikey_name")
            api_key = _normalize_secret(resolve_secret(apikey_name)) if apikey_name else None
            if not api_key and apikey_name:
                api_key = os.environ.get(apikey_name)
            llm = ChatOpenAI(
                api_key=api_key,
                base_url=model_settings.get('url'),
                max_tokens=max_tokens,
                model=model_name,
                temperature=temperature,
                seed=42,
            )
        elif platform == "rits-restricted":
            api_key = _normalize_secret(resolve_secret("RITS_API_KEY_RESTRICT")) or os.environ.get(
                "RITS_API_KEY_RESTRICT"
            )
            llm = ChatOpenAI(
                api_key=api_key,
                base_url="http://nocodeui.sl.cloud9.ibm.com:4001",
                max_tokens=max_tokens,
                model=model_name,
                top_p=0.95,
                temperature=temperature,
                seed=42,
            )
        elif platform == "google-genai":
            logger.debug(f"Creating Google GenAI model: {model_name}")
            # Build ChatGoogleGenerativeAI parameters

            # Add API key if specified
            # apikey_name = model_settings.get("apikey_name")
            # if apikey_name:
            #     google_params["api_key"] = os.environ.get(apikey_name)

            llm = ChatGoogleGenerativeAI(
                api_key=_normalize_secret(resolve_secret("GOOGLE_API_KEY"))
                or os.environ.get("GOOGLE_API_KEY"),
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        elif platform == "openrouter":
            logger.debug(f"Creating OpenRouter model: {model_name}")
            is_reasoning = self._is_reasoning_model(model_name)

            api_key = _normalize_secret(resolve_secret("OPENROUTER_API_KEY")) or os.environ.get(
                "OPENROUTER_API_KEY"
            )
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY environment variable not set")

            openrouter_params: Dict[str, Any] = {
                "model_name": model_name,
                "max_tokens": max_tokens,
                "timeout": 61,
                "openai_api_key": api_key,
                "openai_api_base": base_url,
            }

            if not is_reasoning:
                openrouter_params["temperature"] = temperature
            else:
                logger.debug(f"Skipping temperature for reasoning model: {model_name}")

            default_headers = {}
            site_url = model_settings.get("site_url") or os.environ.get("OPENROUTER_SITE_URL")
            app_name = model_settings.get("app_name") or os.environ.get("OPENROUTER_APP_NAME")
            if site_url:
                default_headers["HTTP-Referer"] = site_url
            if app_name:
                default_headers["X-Title"] = app_name
            if default_headers:
                openrouter_params["default_headers"] = default_headers

            llm = ReasoningChatOpenAI(**openrouter_params)
        elif platform == "litellm" and ReasoningChatLiteLLM is not None:
            logger.debug(f"Creating LiteLLM model: {model_name}")
            ssl_verify = self._get_ssl_verify(model_settings)

            import litellm

            litellm.drop_params = True
            if ssl_verify is not True:
                litellm.ssl_verify = ssl_verify

            is_reasoning = self._is_reasoning_model(model_name)
            litellm_params: Dict[str, Any] = {
                "model": model_name,
                "max_tokens": max_tokens,
                "drop_params": True,
            }
            if not is_reasoning:
                litellm_params["temperature"] = temperature
            else:
                logger.debug(f"Skipping temperature for reasoning model (litellm): {model_name}")
            # Tell litellm to use the OpenAI-compatible code path without parsing
            # a provider from the model name (e.g. "ibm-granite/granite-4.0-1b"
            # would otherwise be misread as provider=ibm-granite).
            if base_url:
                litellm_params["custom_llm_provider"] = "openai"
            if base_url:
                litellm_params["api_base"] = base_url.rstrip("/")
            auth_headers = self._get_auth_headers(model_settings, "openai")
            if auth_headers and "Authorization" in auth_headers:
                val = auth_headers["Authorization"]
                litellm_params["api_key"] = (
                    val.replace("Bearer ", "", 1) if val.lower().startswith("bearer ") else val
                )
            else:
                apikey_ref = model_settings.get("api_key")
                if apikey_ref:
                    api_key = _normalize_secret(resolve_secret(apikey_ref)) or os.environ.get(apikey_ref)
                else:
                    apikey_name = model_settings.get("apikey_name")
                    api_key = _normalize_secret(resolve_secret(apikey_name)) if apikey_name else None
                    if not api_key:
                        api_key = _normalize_secret(resolve_secret("OPENAI_API_KEY")) or os.environ.get(
                            "OPENAI_API_KEY"
                        )
                    if apikey_name and not api_key:
                        api_key = os.environ.get(apikey_name)
                if api_key:
                    litellm_params["api_key"] = api_key
            llm = ReasoningChatLiteLLM(**litellm_params)
        else:
            raise ValueError(f"Unsupported platform: {platform}")

        return llm

    def get_model(self, model_settings: Dict[str, Any]):
        """Get or create LLM instance for the given model settings

        Args:
            model_settings: Model configuration dictionary (must contain max_tokens)
        """
        override = get_current_llm_override()
        if override:
            to_dict = getattr(model_settings, "to_dict", None)
            d = (to_dict() if callable(to_dict) else dict(model_settings)).copy()
            d.update({k: v for k, v in override.items() if v is not None})
            model_settings = _ModelSettingsWrap(d)

        max_tokens = model_settings.get('max_tokens')
        assert max_tokens is not None, "max_tokens must be specified in model_settings"
        # Check if pre-instantiated model is available
        if self._pre_instantiated_model is not None:
            logger.debug(f"Using pre-instantiated model: {type(self._pre_instantiated_model).__name__}")
            # Update parameters for the task
            updated_model = self._update_model_parameters(
                self._pre_instantiated_model, temperature=0.1, max_tokens=max_tokens
            )
            return updated_model

        # Get resolved values for logging and cache key
        platform = model_settings.get('platform', 'unknown')
        model_name = self._get_model_name(model_settings, platform)
        api_version = self._get_api_version(model_settings, platform)
        base_url = self._get_base_url(model_settings, platform)

        cache_key = self._create_cache_key(model_settings)

        if cache_key in self._models:
            logger.debug(
                f"Returning cached model: {platform}/{model_name} (api_version={api_version}, base_url={base_url})"
            )
            # Update parameters for the task
            cached_model = self._models[cache_key]
            updated_model = self._update_model_parameters(
                cached_model, temperature=0.1, max_tokens=max_tokens, max_completion_tokens=max_tokens
            )
            return updated_model

        # Create new model instance
        logger.debug(
            f"Creating new model: {platform}/{model_name} (api_version={api_version}, base_url={base_url})"
        )
        model = self._create_llm_instance(model_settings)
        self._models[cache_key] = model

        # Update parameters for the task
        updated_model = self._update_model_parameters(model, temperature=0.1, max_tokens=max_tokens)
        return updated_model


def create_llm_from_config(llm_cfg: dict) -> BaseChatModel:
    """Create a fresh LLM instance directly from a UI llm_cfg dict.
    No caching. Used by manage_routes after publish/draft-save.
    When force_env is true or mode is "local", db:// and vault:// refs are ignored so env vars are used.
    In local mode, provider/platform is taken from settings.agent.code.model (e.g. settings.groq.toml).

    Raises ValueError if the LLM cannot be instantiated (e.g. API key unresolvable).
    Callers should catch this and fall back to env/TOML settings.
    """
    if not llm_cfg:
        llm_cfg = {}
    mgr = LLMManager()
    try:
        max_tokens = settings.agent.code.model.get("max_tokens", 16000)
    except Exception:
        max_tokens = 16000
    if not isinstance(max_tokens, int):
        max_tokens = 16000
    api_key = llm_cfg.get("api_key") or None
    _secrets = getattr(settings, "secrets", None)
    use_env = _secrets and (
        getattr(_secrets, "force_env", False) or getattr(_secrets, "mode", "local") == "local"
    )
    if (
        use_env
        and api_key
        and isinstance(api_key, str)
        and (api_key.startswith("db://") or api_key.startswith("vault://") or api_key.startswith("aws://"))
    ):
        api_key = None
    platform = llm_cfg.get("provider") or "openai"
    model = llm_cfg.get("model") or None
    if use_env:
        try:
            code_model = settings.agent.code.model
            if code_model:
                toml_platform = (
                    code_model.get("platform")
                    if hasattr(code_model, "get")
                    else getattr(code_model, "platform", None)
                )
                toml_model = (
                    code_model.get("model")
                    if hasattr(code_model, "get")
                    else getattr(code_model, "model", None)
                )
                if toml_platform:
                    platform = toml_platform
                if toml_model:
                    model = toml_model
                logger.debug(
                    "create_llm_from_config: using agent.code from TOML (local mode) — provider=%s model=%s",
                    platform,
                    model,
                )
        except Exception:
            pass

    # For non-local/non-force_env modes (e.g. vault), verify the API key is actually
    # resolvable before attempting to instantiate. Providers like openai require a key
    # and will raise at construction time if it is missing — which would crash startup.
    if not use_env and platform in ("openai", "azure", "openrouter"):
        apikey_ref = api_key or llm_cfg.get("apikey_name")
        resolved_key = _normalize_secret(resolve_secret(apikey_ref)) if apikey_ref else None
        if not resolved_key:
            # Use platform-specific fallback
            fallback_key = "OPENROUTER_API_KEY" if platform == "openrouter" else "OPENAI_API_KEY"
            resolved_key = _normalize_secret(resolve_secret(fallback_key)) or os.environ.get(fallback_key)
        if not resolved_key:
            raise ValueError(
                f"create_llm_from_config: cannot resolve API key for provider '{platform}' "
                f"(ref={apikey_ref!r}). Ensure the secret is stored in the configured backend."
            )

    settings_dict = {
        "platform": platform,
        "model": model,
        "url": llm_cfg.get("base_url") or None,
        "api_key": api_key,
        "temperature": llm_cfg.get("temperature", 0.1),
        "disable_ssl": llm_cfg.get("disable_ssl", False),
        "ssl_ca_bundle": llm_cfg.get("ssl_ca_bundle") or None,
        "auth_type": llm_cfg.get("auth_type"),
        "auth_header_name": llm_cfg.get("auth_header_name"),
        "max_tokens": max_tokens,
        "streaming": False,
    }
    wrap = _ModelSettingsWrap(settings_dict)
    model = mgr._create_llm_instance(wrap)
    return mgr._update_model_parameters(
        model, temperature=settings_dict["temperature"], max_tokens=max_tokens
    )
