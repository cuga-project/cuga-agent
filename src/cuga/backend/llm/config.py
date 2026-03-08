"""LLM Configuration Models"""

from typing import Optional, Literal
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for LLM providers.

    This model defines the structure for LLM configuration used throughout the application.
    Currently used as a plain dict, but this model serves as documentation and can be used
    for validation in the future.
    """

    provider: str = Field(
        default="openai",
        description="LLM provider name (e.g., 'openai', 'groq', 'litellm', 'azure', 'google', 'watsonx')",
    )

    model: Optional[str] = Field(default=None, description="Model name/identifier for the provider")

    url: Optional[str] = Field(
        default=None, description="Base URL for the LLM API endpoint", alias="base_url"
    )

    api_key: Optional[str] = Field(
        default=None,
        description=(
            "Credential value or vault reference (e.g., 'vault://secret/path/to/key'). "
            "Used as a Bearer token when auth_type='api_key', or as the raw header value "
            "when auth_type='auth_header'."
        ),
    )

    auth_type: Literal["api_key", "auth_header"] = Field(
        default="api_key",
        description="Authentication type: 'api_key' for Bearer token, 'auth_header' for custom header",
    )

    auth_header_name: str = Field(
        default="Authorization",
        description="Header name used when auth_type='auth_header' (e.g. 'X-API-Key')",
    )

    temperature: Optional[float] = Field(
        default=0.1, ge=0.0, le=2.0, description="Sampling temperature for model responses"
    )

    max_tokens: Optional[int] = Field(
        default=16000, gt=0, description="Maximum number of tokens in the response"
    )

    disable_ssl: bool = Field(
        default=False, description="Whether to disable SSL verification for API requests"
    )

    class Config:
        populate_by_name = True  # Allow both 'url' and 'base_url'
        extra = "allow"  # Allow additional fields for provider-specific configs


class LiteLLMConfig(LLMConfig):
    """Specialized configuration for LiteLLM provider.

    LiteLLM acts as a proxy/gateway to multiple LLM providers and may require
    custom authentication methods.
    """

    provider: Literal["litellm"] = Field(default="litellm", description="Provider must be 'litellm'")

    url: str = Field(..., alias="base_url", description="LiteLLM proxy/gateway URL (required)")

    auth_type: Literal["api_key", "auth_header"] = Field(
        default="auth_header", description="LiteLLM typically uses custom auth headers"
    )


class OpenAIConfig(LLMConfig):
    """Configuration for OpenAI provider."""

    provider: Literal["openai"] = Field(default="openai", description="Provider must be 'openai'")

    model: str = Field(default="gpt-4o", description="OpenAI model name")


class GroqConfig(LLMConfig):
    """Configuration for Groq provider."""

    provider: Literal["groq"] = Field(default="groq", description="Provider must be 'groq'")

    model: str = Field(..., description="Groq model name (required)")


# Made with Bob
