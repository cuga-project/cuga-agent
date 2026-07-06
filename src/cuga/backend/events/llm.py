"""Multi-provider LLM factory for the events layer (ReactRuntime model factory).

Ported from event-agent-ap ``agent/llm.py`` (itself from cuga-apps/_llm.py). Maps
``LLM_PROVIDER`` + ``LLM_MODEL`` to a LangChain ``BaseChatModel``. Providers:

    openai     OPENAI_API_KEY
    anthropic  ANTHROPIC_API_KEY
    rits       RITS_API_KEY
    watsonx    WATSONX_APIKEY + WATSONX_PROJECT_ID|WATSONX_SPACE_ID
    litellm    LITELLM_API_KEY + LITELLM_BASE_URL
    ollama     OLLAMA_BASE_URL (keyless)

The concierge wants a strong tool-caller (DESIGN §7); workers can be cheaper per-agent.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.chat_models import BaseChatModel as _BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs.chat_generation import ChatGeneration
from langchain_core.outputs.chat_result import ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field, model_validator

logger = logging.getLogger("cuga.events.llm")


class RITSChatModel(_BaseChatModel):
    """LangChain-compatible chat model for the internal RITS inference service."""

    MODEL_NAME_MAPPING: Dict[str, str] = {
        "llama-3-3-70b-instruct": "meta-llama/llama-3-3-70b-instruct",
        "gpt-oss-120b": "openai/gpt-oss-120b",
    }
    model_name: str
    base_url: str
    api_key: str
    temperature: float = 0.0
    max_tokens: int = 16000
    bound_tools: Optional[List[Dict[str, Any]]] = Field(default=None)

    @model_validator(mode="after")
    def _ping(self) -> "RITSChatModel":
        try:
            httpx.get(f"{self.base_url}/{self.model_name}/ping",
                      headers={"RITS_API_KEY": self.api_key}, timeout=10.0).raise_for_status()
        except Exception as e:  # noqa: BLE001
            logger.warning("RITS ping failed for '%s': %s — continuing", self.model_name, e)
        return self

    async def _agenerate(self, messages: List[BaseMessage], stop=None, **kwargs: Any) -> ChatResult:
        msgs: List[Dict[str, Any]] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                msgs.append({"role": "system", "content": m.content})
            elif isinstance(m, HumanMessage):
                msgs.append({"role": "user", "content": m.content})
            elif isinstance(m, AIMessage):
                d: Dict[str, Any] = {"role": "assistant", "content": m.content}
                if m.tool_calls:
                    d["tool_calls"] = m.additional_kwargs.get("tool_calls")
                msgs.append(d)
            elif isinstance(m, ToolMessage):
                msgs.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            else:
                msgs.append({"role": "user", "content": m.content})
        url = f"{self.base_url}/{self.model_name}/v1/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.MODEL_NAME_MAPPING.get(self.model_name, self.model_name),
            "messages": msgs, "temperature": self.temperature, "max_tokens": self.max_tokens, **kwargs}
        if self.bound_tools:
            payload["tools"] = self.bound_tools
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers={"RITS_API_KEY": self.api_key},
                                     json=payload, timeout=180.0)
            resp.raise_for_status()
            data = resp.json()
        md = data["choices"][0]["message"]
        extra = {"tool_calls": md["tool_calls"]} if "tool_calls" in md else {}
        return ChatResult(generations=[ChatGeneration(
            message=AIMessage(content=md.get("content") or "", additional_kwargs=extra))])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return asyncio.run(self._agenerate(messages, stop, **kwargs))

    @property
    def _llm_type(self) -> str:
        return "rits-openai-compat"

    def bind_tools(self, tools: Sequence[Union[Dict[str, Any], type, Callable, BaseTool]],
                   **kwargs: Any) -> "RITSChatModel":
        from langchain_core.utils.function_calling import convert_to_openai_tool
        defs = []
        for t in tools:
            if hasattr(t, "name") and hasattr(t, "args"):
                defs.append({"type": "function", "function": {
                    "name": t.name, "description": t.description,
                    "parameters": {"type": "object", "properties": t.args,
                                   "required": list(t.args.keys())}}})
            elif isinstance(t, dict):
                defs.append(t)
            else:
                defs.append(convert_to_openai_tool(t))
        return self.model_copy(update={"bound_tools": defs, **kwargs})


def detect_provider() -> str:
    if os.getenv("RITS_API_KEY"):
        return "rits"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_API_KEY") != "ollama":
        return "openai"
    if os.getenv("WATSONX_APIKEY"):
        return "watsonx"
    if os.getenv("LITELLM_API_KEY"):
        return "litellm"
    return "ollama"


def create_llm(provider: Optional[str] = None, model: Optional[str] = None) -> BaseChatModel:
    """Create a BaseChatModel for the resolved provider (LLM_PROVIDER / auto-detect)."""
    p = (provider or os.getenv("LLM_PROVIDER") or detect_provider()).lower()
    m = model or os.getenv("LLM_MODEL") or None

    if p == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=m or "gpt-4.1", temperature=0)
    if p == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=m or "claude-sonnet-4-6", temperature=0)
    if p == "rits":
        return RITSChatModel(
            model_name=m or "llama-3-3-70b-instruct",
            base_url=os.getenv("RITS_BASE_URL",
                "https://inference-3scale-apicast-production.apps.rits.fmaas.res.ibm.com"),
            api_key=os.environ["RITS_API_KEY"], temperature=0)
    if p == "watsonx":
        from langchain_ibm import ChatWatsonx
        project_id = os.getenv("WATSONX_PROJECT_ID") or os.getenv("WATSONX_SPACE_ID")
        if not (os.getenv("WATSONX_APIKEY") and project_id):
            raise ValueError("watsonx needs WATSONX_APIKEY + WATSONX_PROJECT_ID|WATSONX_SPACE_ID")
        return ChatWatsonx(
            model_id=m or "openai/gpt-oss-120b",
            url=os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
            project_id=project_id, temperature=0, max_tokens=16000)
    if p == "litellm":
        from langchain_litellm import ChatLiteLLM
        return ChatLiteLLM(model=m or "gpt-4o",
                           api_base=os.getenv("LITELLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"),
                           api_key=os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY"),
                           temperature=0)
    if p == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=m or "llama3.1:8b",
                          base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                          temperature=0, num_ctx=65536)
    raise ValueError(f"Unknown LLM provider {p!r} (openai|anthropic|rits|watsonx|litellm|ollama)")


def default_model_factory(spec=None) -> BaseChatModel:
    """The factory ReactRuntime uses by default: per-agent model override, else the deployment LLM."""
    model = getattr(spec, "model", None) if spec is not None else None
    return create_llm(model=model)
