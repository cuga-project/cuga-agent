"""Deterministic mock chat model for load/system tests (``CUGA_MOCK_LLM``)."""

import asyncio
import json
import os
import types
from typing import Any, List, Literal, Optional, Sequence, Union, get_args, get_origin

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from loguru import logger
from pydantic import BaseModel, ConfigDict


def is_mock_llm_enabled() -> bool:
    """Return True when ``CUGA_MOCK_LLM`` is set (load/system tests)."""
    return os.getenv("CUGA_MOCK_LLM", "").lower() in ("true", "1", "yes", "on")


def _message_content(message: Any) -> str:
    if isinstance(message, BaseMessage):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content or "")


def _last_human_text(messages: List[Any]) -> str:
    for message in reversed(messages):
        role = None
        if isinstance(message, HumanMessage):
            role = "human"
        elif isinstance(message, dict):
            role = message.get("role") or message.get("type")
        else:
            role = getattr(message, "type", None)
        if role in ("human", "user", "HumanMessage"):
            return _message_content(message)
    return ""


def _has_execution_output(messages: List[Any]) -> bool:
    """True when the latest human turn is sandbox output (not few-shot history)."""
    last_human = _last_human_text(messages)
    if not last_human:
        return False
    return "Execution output" in last_human


def _execution_succeeded(messages: List[Any]) -> bool:
    """True when the latest sandbox turn completed without an execution error."""
    last_human = _last_human_text(messages)
    if not last_human or "Error during execution" in last_human:
        return False
    return _has_execution_output(messages)


_ACCOUNTS_QUERY_CODE = (
    "```python\n"
    "my_accounts_response = await digital_sales_my_accounts()\n"
    "accounts = my_accounts_response.get(\"accounts\", [])\n"
    "account_count = len(accounts)\n"
    "print(account_count)\n"
    "```"
)


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _mock_value_for_type(annotation: Any) -> Any:
    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin is Literal:
        return get_args(annotation)[0]

    if origin in (list, List):
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        if isinstance(item_type, type) and issubclass(item_type, BaseModel):
            return [_build_mock_instance(item_type)]
        if get_origin(item_type) is Literal:
            return [get_args(item_type)[0]]
        return []

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _build_mock_instance(annotation)

    if annotation in (str, Optional[str]) or annotation is str:
        return "mock"
    if annotation in (int, Optional[int]) or annotation is int:
        return 0
    if annotation in (float, Optional[float]) or annotation is float:
        return 0.0
    if annotation in (bool, Optional[bool]) or annotation is bool:
        return False

    if origin in (dict, dict):
        return {}

    return "mock"


def _build_mock_instance(schema: type[BaseModel]) -> BaseModel:
    values = {}
    for name, field in schema.model_fields.items():
        if not field.is_required():
            continue
        values[name] = _mock_value_for_type(field.annotation)
    return schema.model_construct(**values)


class LoadTestMockChatModel(BaseChatModel):
    """Deterministic chat model for concurrent load/system tests (no external API)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    bound_tools: Optional[List[Any]] = None

    @property
    def _llm_type(self) -> str:
        return "cuga-load-test-mock"

    @property
    def _identifying_params(self) -> dict:
        return {"model": "cuga-load-test-mock"}

    def _pick_content(self, messages: List[Any]) -> str:
        joined = "\n".join(_message_content(m) for m in messages).lower()
        last_human = _last_human_text(messages).lower()

        if "how many accounts did we retrieve" in last_human:
            return "We retrieved 50 accounts."

        if _execution_succeeded(messages):
            return "There are 50 accounts in total."

        if "list all my accounts" in last_human or "how many are there" in last_human:
            return _ACCOUNTS_QUERY_CODE

        if (
            _has_execution_output(messages)
            and "Error during execution" in _last_human_text(messages)
            and ("list all my accounts" in joined or "how many are there" in joined)
        ):
            return _ACCOUNTS_QUERY_CODE

        if any(
            token in joined for token in ("respond with json", "output json", "json object", "json format")
        ):
            return "{}"

        return "50"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        content = self._pick_content(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await asyncio.to_thread(self._generate, messages, stop, run_manager, **kwargs)

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ):
        return self.model_copy(update={"bound_tools": list(tools)})

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        kwargs.pop("method", None)
        kwargs.pop("strict", None)
        if kwargs:
            raise ValueError(f"Received unsupported arguments {kwargs}")

        def _build(_input: Any):
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                parsed = _build_mock_instance(schema)
            else:
                parsed = {}
            if include_raw:
                raw_content = json.dumps(parsed.model_dump() if hasattr(parsed, "model_dump") else parsed)
                return {"parsed": parsed, "raw": AIMessage(content=raw_content)}
            return parsed

        return RunnableLambda(_build)


_load_test_mock_singleton: Optional[LoadTestMockChatModel] = None


def get_load_test_mock_chat_model() -> LoadTestMockChatModel:
    global _load_test_mock_singleton
    if _load_test_mock_singleton is None:
        _load_test_mock_singleton = LoadTestMockChatModel()
        logger.info("Initialized LoadTestMockChatModel (CUGA_MOCK_LLM enabled)")
    return _load_test_mock_singleton


def clone_load_test_mock_chat_model() -> LoadTestMockChatModel:
    """Return a fresh mock instance so per-request parameter updates stay isolated."""
    return get_load_test_mock_chat_model().model_copy(deep=True)
