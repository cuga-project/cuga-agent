"""Provider configuration, invocation, and JSON recovery for the verifier.

The verifier owns prompts and decisions; this module owns model transport.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage
from loguru import logger
from pydantic import BaseModel, ValidationError

from cuga.backend.llm.models import LLMManager
from cuga.config import settings

from .errors import PromptVerificationError


DEFAULT_PROMPT_VERIFIER_MODEL_NAME = "aws/gpt-oss-120b"
PROMPT_VERIFIER_MODEL_NAME = (
    os.environ.get("PROMPT_VERIFIER_MODEL_NAME", "").strip()
    or DEFAULT_PROMPT_VERIFIER_MODEL_NAME
)

# Claude Haiku 4.5 supports manual extended thinking rather than the newer
# adaptive/effort API. Its maximum output budget is 64k tokens. Reserve 1,024
# tokens for the verifier's required JSON answer and give every remaining token
# to thinking. Environment variables can override these limits.
PROMPT_VERIFIER_CLAUDE_MAX_TOKENS = int(os.environ.get("PROMPT_VERIFIER_CLAUDE_MAX_TOKENS", "64000"))
PROMPT_VERIFIER_CLAUDE_RESPONSE_TOKEN_RESERVE = int(
    os.environ.get("PROMPT_VERIFIER_CLAUDE_RESPONSE_TOKEN_RESERVE", "1024")
)
PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS = int(
    os.environ.get(
        "PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS",
        str(PROMPT_VERIFIER_CLAUDE_MAX_TOKENS - PROMPT_VERIFIER_CLAUDE_RESPONSE_TOKEN_RESERVE),
    )
)


def _validated_claude_thinking_budget() -> tuple[int, int]:
    """Return (max_tokens, thinking_budget_tokens) for Haiku extended thinking.

    Anthropic requires ``budget_tokens >= 1024`` and ``budget_tokens < max_tokens``.
    Haiku 4.5 supports up to 64k total output tokens. Keep the verifier-specific
    defaults at that ceiling while preserving a small visible-output reserve for
    the required JSON decision.
    """
    max_tokens = PROMPT_VERIFIER_CLAUDE_MAX_TOKENS
    thinking_budget = PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS

    if max_tokens <= 1024 or max_tokens > 64000:
        raise PromptVerificationError(
            f"PROMPT_VERIFIER_CLAUDE_MAX_TOKENS must be in [1025, 64000]; got {max_tokens}"
        )
    if thinking_budget < 1024 or thinking_budget >= max_tokens:
        raise PromptVerificationError(
            "PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS must be >= 1024 "
            "and strictly less than PROMPT_VERIFIER_CLAUDE_MAX_TOKENS; "
            f"got budget={thinking_budget} max_tokens={max_tokens}"
        )
    return max_tokens, thinking_budget


def _is_claude_model_name(model_name: str) -> bool:
    """Whether a provider model alias denotes Claude/Anthropic."""
    return "claude" in str(model_name or "").lower()


def _is_gemini_model_name(model_name: str) -> bool:
    """Whether a provider model alias denotes a Gemini model."""
    return "gemini" in str(model_name or "").lower()


def _is_gpt5_model_name(model_name: str) -> bool:
    """Whether a provider model alias denotes a GPT-5-family model."""
    return "gpt-5" in str(model_name or "").lower()


def _runtime_model_name(model: Any) -> str:
    return str(getattr(model, "model_name", "") or getattr(model, "model", "") or PROMPT_VERIFIER_MODEL_NAME)


def _verifier_model_settings() -> dict[str, Any]:
    """Clone CUGA transport/auth settings and apply verifier-only model settings.

    Claude is reached through the same OpenAI-compatible provider transport used
    by CUGA. Do not forward settings that are specific to GPT reasoning models or
    OpenAI sampling penalties. Gemini 3.x uses ``reasoning_effort`` through the
    OpenAI-compatible API and should keep its default sampling settings, so inherited
    ``temperature``/``top_p``/``top_k`` knobs are removed. GPT-5-family reasoning
    models also require inherited sampling knobs such as ``temperature`` and
    ``top_p`` to be removed when reasoning is enabled.
    """
    configured = settings.agent.code.model
    to_dict = getattr(configured, "to_dict", None)
    if callable(to_dict):
        model_settings = dict(to_dict())
    elif isinstance(configured, dict):
        model_settings = dict(configured)
    else:
        model_settings = dict(configured)

    model_settings["model"] = PROMPT_VERIFIER_MODEL_NAME

    if _is_claude_model_name(PROMPT_VERIFIER_MODEL_NAME):
        # Haiku 4.5 uses Anthropic manual extended thinking. CUGA's extra_params
        # is the provider-specific passthrough merged into the OpenAI-compatible
        # client kwargs, so place the Anthropic ``thinking`` object there.
        #
        # Extended thinking is incompatible with non-default temperature/top_k
        # and with ordinary top_p tuning. Use temperature=1 (Anthropic's allowed
        # default while thinking is enabled) and omit the other sampling knobs.
        max_tokens, thinking_budget = _validated_claude_thinking_budget()
        model_settings["max_tokens"] = max_tokens
        model_settings["temperature"] = 1.0
        model_settings["top_p"] = None
        model_settings.pop("top_k", None)
        for key in (
            "reasoning_effort",
            "frequency_penalty",
            "presence_penalty",
        ):
            model_settings.pop(key, None)

        extra_params = model_settings.get("extra_params")
        sanitized_extra_params = dict(extra_params) if isinstance(extra_params, dict) else {}
        for key in (
            "reasoning_effort",
            "top_p",
            "top_k",
            "frequency_penalty",
            "presence_penalty",
            "response_format",
            "thinking",
        ):
            sanitized_extra_params.pop(key, None)
        sanitized_extra_params["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }
        model_settings["extra_params"] = sanitized_extra_params

    elif _is_gemini_model_name(PROMPT_VERIFIER_MODEL_NAME):
        # Gemini 3.x reasoning is controlled through the OpenAI-compatible
        # reasoning_effort parameter bound in _get_model(). Google recommends
        # leaving Gemini 3.x sampling at model defaults, so do not inherit
        # CUGA temperature/top_p/top_k settings. Also remove any native Gemini
        # thinking controls so they cannot conflict with reasoning_effort.
        for key in (
            "temperature",
            "top_p",
            "top_k",
            "candidate_count",
            "reasoning_effort",
            "thinking_level",
            "thinking_budget",
            "thinking",
        ):
            model_settings.pop(key, None)

        extra_params = model_settings.get("extra_params")
        if isinstance(extra_params, dict):
            sanitized_extra_params = dict(extra_params)
            for key in (
                "temperature",
                "top_p",
                "top_k",
                "candidate_count",
                "reasoning_effort",
                "thinking_level",
                "thinking_budget",
                "thinking",
            ):
                sanitized_extra_params.pop(key, None)
            model_settings["extra_params"] = sanitized_extra_params

    elif _is_gpt5_model_name(PROMPT_VERIFIER_MODEL_NAME):
        # GPT-5 / GPT-5 mini support configurable reasoning effort, but the
        # pre-5.1 family rejects sampling fields such as temperature/top_p/logprobs
        # when reasoning is enabled. CUGA's base model settings normally include
        # temperature and top_p, so strip them from both the top-level settings
        # and provider passthrough before constructing the verifier model.
        for key in (
            "temperature",
            "top_p",
            "logprobs",
            "top_logprobs",
        ):
            model_settings.pop(key, None)

        extra_params = model_settings.get("extra_params")
        if isinstance(extra_params, dict):
            sanitized_extra_params = dict(extra_params)
            for key in (
                "temperature",
                "top_p",
                "logprobs",
                "top_logprobs",
            ):
                sanitized_extra_params.pop(key, None)
            model_settings["extra_params"] = sanitized_extra_params

    return model_settings


def _get_model(*, reasoning_effort: Literal["low", "medium", "high"] = "high"):
    """Get the dedicated verifier model using CUGA's existing provider transport.

    GPT-5-family models, GPT-OSS, and Gemini receive the OpenAI-compatible
    ``reasoning_effort`` knob. The default GPT-OSS verifier uses
    ``reasoning_effort="high"``. Claude Haiku 4.5 instead receives
    Anthropic manual extended thinking with the verifier's maximum configured
    thinking budget.
    """
    model_settings = _verifier_model_settings()
    model = LLMManager().get_model(model_settings)
    model_name = _runtime_model_name(model)

    if (
        "gpt-oss" in model_name.lower()
        or "gpt-oss" in PROMPT_VERIFIER_MODEL_NAME.lower()
        or _is_gpt5_model_name(model_name)
        or _is_gpt5_model_name(PROMPT_VERIFIER_MODEL_NAME)
        or _is_gemini_model_name(model_name)
        or _is_gemini_model_name(PROMPT_VERIFIER_MODEL_NAME)
    ):
        return model.bind(reasoning_effort=reasoning_effort)
    return model


def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model text, including fenced JSON."""
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    if not stripped:
        return None

    # Fast path: the whole response is JSON.
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Common model format: ```json ... ``` or ``` ... ```.
    fence_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fence_match is not None:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Last resort: scan for the first decodable JSON object embedded in prose.
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _extract_json_from_message(raw_message: Any) -> dict[str, Any] | None:
    """Recover a JSON object from an AIMessage's content/metadata."""
    if raw_message is None:
        return None

    content = getattr(raw_message, "content", None)

    if isinstance(content, dict):
        return content

    if isinstance(content, str):
        parsed = _extract_json_object_from_text(content)
        if parsed is not None:
            return parsed

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                # Some providers return structured content blocks.
                if isinstance(item.get("json"), dict):
                    return item["json"]
                for key in ("text", "content", "arguments", "input"):
                    value = item.get(key)
                    if isinstance(value, dict):
                        return value
                    if isinstance(value, str):
                        parsed = _extract_json_object_from_text(value)
                        if parsed is not None:
                            return parsed
            elif isinstance(item, str):
                parsed = _extract_json_object_from_text(item)
                if parsed is not None:
                    return parsed

    additional_kwargs = getattr(raw_message, "additional_kwargs", None) or {}

    # Older OpenAI function_call representation.
    function_call = additional_kwargs.get("function_call")
    if isinstance(function_call, dict):
        arguments = function_call.get("arguments")
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            parsed = _extract_json_object_from_text(arguments)
            if parsed is not None:
                return parsed

    # ReasoningChatOpenAI preserves reasoning_content in additional_kwargs.
    reasoning_content = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning_content, str):
        parsed = _extract_json_object_from_text(reasoning_content)
        if parsed is not None:
            return parsed

    return None


def _extract_structured_args(
    raw_message: Any,
) -> dict[str, Any] | None:
    """Recover structured-output arguments from a raw LangChain AIMessage."""

    # Preferred LangChain representation.
    tool_calls = getattr(raw_message, "tool_calls", None)

    if tool_calls:
        first_call = tool_calls[0]

        if isinstance(first_call, dict):
            args = first_call.get("args")
        else:
            args = getattr(first_call, "args", None)

        if isinstance(args, dict):
            return args

        if isinstance(args, str):
            parsed = _extract_json_object_from_text(args)
            if parsed is not None:
                return parsed

    # OpenAI-compatible raw tool-call representation.
    additional_kwargs = getattr(raw_message, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls", [])

    if raw_tool_calls:
        first_call = raw_tool_calls[0]
        if isinstance(first_call, dict):
            function = first_call.get("function", {})
            if isinstance(function, dict):
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    return arguments
                if isinstance(arguments, str):
                    parsed = _extract_json_object_from_text(arguments)
                    if parsed is not None:
                        return parsed

    # Some gpt-oss/OpenAI-compatible paths answer with ordinary JSON content
    # rather than emitting the requested structured tool call.
    return _extract_json_from_message(raw_message)


def _plain_json_messages(
    *,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    retry: bool,
) -> list[BaseMessage]:
    """Append provider-neutral JSON-schema instructions to verifier messages."""
    schema_json = json.dumps(
        schema.model_json_schema(),
        ensure_ascii=False,
    )
    prefix = (
        "The previous structured-output attempt did not produce a recoverable decision. " if retry else ""
    )
    return [
        *messages,
        HumanMessage(
            content=(
                f"{prefix}Return ONLY one JSON object that matches the following "
                "JSON schema exactly. Do not use markdown, code fences, commentary, "
                "or any text outside the JSON object.\n\n"
                f"JSON_SCHEMA:\n{schema_json}"
            )
        ),
    ]


async def _plain_json_invoke(
    *,
    model: Any,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    label: str,
    retry: bool = False,
) -> tuple[Any, dict[str, Any] | None]:
    """Invoke the model without function calling and recover one JSON object.

    This is the primary structured-output path for Claude aliases. CUGA already
    avoids native OpenAI JSON-schema formatting for Claude/Bedrock because that
    format can be translated to unsupported Bedrock ``output_config`` fields.
    Prompting for schema-conforming JSON keeps the verifier on the provider's
    ordinary chat path and remains compatible with OpenAI-style proxy transports.
    """
    plain_messages = _plain_json_messages(
        messages=messages,
        schema=schema,
        retry=retry,
    )
    log_method = logger.warning if retry else logger.debug
    log_method(
        "Invoking {} as plain JSON for {}.",
        _runtime_model_name(model),
        label,
    )
    response = await model.ainvoke(plain_messages)
    return response, _extract_json_from_message(response)


async def _plain_json_retry(
    *,
    model: Any,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    label: str,
) -> dict[str, Any] | None:
    """Retry once without function calling and request a JSON object only."""
    _, parsed = await _plain_json_invoke(
        model=model,
        messages=messages,
        schema=schema,
        label=label,
        retry=True,
    )
    return parsed


async def invoke_verifier_decision(
    *,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    verification_id: str,
    candidate_kind: str,
    candidate_hash: str,
) -> BaseModel:
    """Invoke the configured verifier model and return a validated decision.

    Provider-specific settings, structured-output transport, and JSON fallback
    stay here; callers only supply a prompt and the expected decision schema.
    """
    model = _get_model(reasoning_effort="high")
    model_name = _runtime_model_name(model)
    label = "raw-candidate source-context verification decision"

    if _is_claude_model_name(model_name):
        _, raw_args = await _plain_json_invoke(
            model=model, messages=messages, schema=schema, label=label
        )
        if raw_args is None:
            raw_args = await _plain_json_retry(model=model, messages=messages, schema=schema, label=label)
        if raw_args is None:
            raise PromptVerificationError(
                f"Could not recover {schema.__name__} JSON from Claude verifier output"
            )
        try:
            return schema.model_validate(raw_args)
        except ValidationError as exc:
            logger.warning(
                "Claude verifier returned JSON that did not match the decision "
                "schema: {}. Retrying once as plain JSON.",
                exc,
            )
            retry_args = await _plain_json_retry(model=model, messages=messages, schema=schema, label=label)
            if retry_args is None:
                raise PromptVerificationError(
                    f"Claude verifier returned invalid {schema.__name__} JSON"
                ) from exc
            return schema.model_validate(retry_args)

    result = await model.with_structured_output(schema, method="function_calling", include_raw=True).ainvoke(
        messages
    )
    parsed = result.get("parsed")
    if parsed is not None:
        return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)

    raw_args = _extract_structured_args(result.get("raw"))
    if raw_args is None:
        raw_args = await _plain_json_retry(model=model, messages=messages, schema=schema, label=label)
    if raw_args is None:
        raise PromptVerificationError(
            f"Could not recover {schema.__name__} JSON. Original parsing error: {result.get('parsing_error')}"
        )
    return schema.model_validate(raw_args)
