"""AgentHooks base class, the registry that runs them, and the SDK glue (gated tools, LLM callback)."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger

HOOK_NAMES = (
    "session_start",
    "on_user_message",
    "before_llm_call",
    "after_llm_call",
    "after_plan",
    "before_tool_call",
    "after_tool_call",
    "before_reply",
    "on_task_end",
)


@dataclass
class HookResult:
    message: Optional[str] = None  # meaning depends on the hook (see the package docstring)
    details: List[Dict[str, Any]] = field(default_factory=list)  # free-form, for observers
    answer: Optional[str] = (
        None  # on_user_message: answer without running the agent; before_reply: new answer
    )


@dataclass
class HookEvent:
    hook: str
    thread_id: Optional[str]
    summary: str
    outcome: str  # observe | inject | block | followup | answer | rewrite
    message: Optional[str] = None
    details: List[Dict[str, Any]] = field(default_factory=list)
    answer: Optional[str] = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HookBlocked(Exception):
    """Raised by a hooked tool when a before_tool_call hook blocks the call. The message reaches the model."""


class AgentHooks:
    """Override any of these. Returning None means: continue unchanged."""

    def session_start(self, thread_id: Optional[str]) -> Any:
        return None

    def on_user_message(self, message: str, thread_id: Optional[str]) -> Any:
        return None

    def before_llm_call(self, thread_id: Optional[str]) -> Any:
        return None

    def after_llm_call(self, text: str) -> Any:
        return None

    def after_plan(self, code: str) -> Any:
        return None

    def before_tool_call(self, tool: str, args: Dict[str, Any]) -> Any:
        return None

    def after_tool_call(self, tool: str, args: Dict[str, Any], result: Any) -> Any:
        return None

    def before_reply(self, answer: str) -> Any:
        return None

    def on_task_end(self, answer: str) -> Any:
        return None


OUTCOME_WHEN_MESSAGE = {
    "before_llm_call": "inject",
    "after_tool_call": "note",
    "after_plan": "block",
    "before_tool_call": "block",
    "before_reply": "followup",
}


def _norm(value: Union[None, str, HookResult]) -> HookResult:
    if value is None:
        return HookResult()
    if isinstance(value, str):
        return HookResult(message=value or None)
    return value


def _report_failure(hook: Any, name: str, args: tuple, exc: Exception) -> None:
    """A hook that failed says so to whoever watches it: an optional attribute, which only an audit sets. The run goes on as
    it would without this, and a failure here changes nothing."""
    report = getattr(hook, "_report_failure", None)
    if report is None:
        return
    try:
        report(name, args, exc)
    except Exception as second:
        logger.warning("hook {}.{} failure not reported: {}", type(hook).__name__, name, second)


class HookRegistry:
    """Calls every hook in order and records one HookEvent per hook call."""

    def __init__(self, hooks: Optional[List[AgentHooks]] = None):
        self.hooks: List[AgentHooks] = list(hooks or [])
        self.events: List[HookEvent] = []
        self.listeners: List[Callable[[HookEvent], None]] = []
        self.thread_id: Optional[str] = None
        self._run_start = 0

    def __bool__(self) -> bool:
        return bool(self.hooks)

    def add(self, hook: AgentHooks) -> None:
        self.hooks.append(hook)

    def add_listener(self, fn: Callable[[HookEvent], None]) -> None:
        self.listeners.append(fn)

    def start_run(self) -> None:
        """Mark the start of one invoke(); run_events() returns the events from here on."""
        self._run_start = len(self.events)

    def run_events(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events[self._run_start :]]

    def _call(self, name: str, summary: str, *args: Any) -> HookResult:
        messages: List[str] = []
        details: List[Dict[str, Any]] = []
        answer: Optional[str] = None
        for hook in self.hooks:
            call_args = (answer, *args[1:]) if name == "before_reply" and answer is not None else args
            try:
                res = _norm(getattr(hook, name)(*call_args))
            except HookBlocked:
                raise
            except Exception as exc:  # a broken hook must not break the agent
                logger.warning("hook {}.{} failed: {}", type(hook).__name__, name, exc)
                _report_failure(hook, name, call_args, exc)
                continue
            if res.message:
                messages.append(res.message)
            details.extend(res.details)
            if res.answer is not None and name in ("on_user_message", "before_reply"):
                answer = res.answer  # before_reply: the next hook sees the rewritten answer
        message = "\n\n".join(messages) if messages else None
        if message:
            outcome = OUTCOME_WHEN_MESSAGE.get(name, "observe")
        elif answer is not None:
            outcome = "answer" if name == "on_user_message" else "rewrite"
        else:
            outcome = "observe"
        event = HookEvent(name, self.thread_id, summary, outcome, message, details, answer=answer)
        self.events.append(event)
        for fn in self.listeners:
            try:
                fn(event)
            except Exception as exc:
                logger.warning("hook listener failed: {}", exc)
        return HookResult(message=message, details=details, answer=answer)

    # -- the nine hooks ------------------------------------------------------------------------------------------
    def session_start(self, thread_id: Optional[str]) -> None:
        self.thread_id = thread_id
        self._call("session_start", f"thread {thread_id}", thread_id)

    def on_user_message(self, message: str, thread_id: Optional[str]) -> HookResult:
        return self._call("on_user_message", message[:120], message, thread_id)

    def before_llm_call(self, thread_id: Optional[str] = None) -> Optional[str]:
        message = self._call("before_llm_call", "model call", thread_id).message
        carried, self._carried = getattr(self, "_carried", []), []  # tool notes that could not be attached
        return "\n\n".join(carried + ([message] if message else [])) or None

    def after_llm_call(self, text: str) -> None:
        self._call("after_llm_call", text[:120], text)

    def after_plan(self, code: str) -> Optional[str]:
        return self._call(
            "after_plan", code.strip().splitlines()[0][:120] if code.strip() else "", code
        ).message

    def before_tool_call(self, tool: str, args: Dict[str, Any]) -> Optional[str]:
        return self._call("before_tool_call", _fmt_call(tool, args), tool, args).message

    def after_tool_call(self, tool: str, args: Dict[str, Any], result: Any) -> Optional[str]:
        """A message is a note about this call: the tool wrapper attaches it to the result the agent sees."""
        return self._call("after_tool_call", _fmt_call(tool, args), tool, args, result).message

    def attach_note(self, result: Any, note: str) -> Any:
        """Put a hook note into the tool result, so it stays in the conversation next to the call it is about.
        Text results get the note appended; dict results get a ``hook_note`` field. Other results (lists,
        objects code may index) are left unchanged and the note goes to the next model call instead."""
        if isinstance(result, str):
            return f"{result}\n\n{note}"
        if isinstance(result, dict):
            prev = result.get("hook_note")
            return {**result, "hook_note": f"{prev}\n{note}" if prev else note}
        self._carried = getattr(self, "_carried", []) + [note]
        return result

    def before_reply(self, answer: str) -> HookResult:
        return self._call("before_reply", answer[:120], answer)

    def on_task_end(self, answer: str) -> None:
        self._call("on_task_end", answer[:120], answer)


def _fmt_call(tool: str, args: Dict[str, Any]) -> str:
    return f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})"[:200]


def wrap_tools_with_hooks(tools: List[BaseTool], registry: HookRegistry) -> List[BaseTool]:
    """before_tool_call / after_tool_call around each tool. A block raises HookBlocked with the hook's message."""

    def wrap(tool: BaseTool) -> BaseTool:
        schema = tool.args_schema
        order = list(getattr(schema, "model_fields", {}) or {}) if isinstance(schema, type) else []

        async def _hooked(*pos: Any, **kwargs: Any) -> Any:
            # CodeAct calls the coroutine directly, so positional arguments arrive here; map them to
            # the schema fields in signature order (an unwrapped tool accepts them the same way).
            if len(pos) > len(order):
                raise TypeError(
                    f"{tool.name}() takes at most {len(order)} positional arguments ({len(pos)} given)"
                )
            kwargs = {**dict(zip(order, pos)), **kwargs}
            args = {
                k: v for k, v in kwargs.items() if v is not None
            }  # schema defaults are not part of the call
            blocked = registry.before_tool_call(tool.name, args)
            if blocked:
                raise HookBlocked(blocked)
            result = await tool.ainvoke(args)
            note = registry.after_tool_call(tool.name, args, result)
            return registry.attach_note(result, note) if note else result

        return StructuredTool.from_function(
            coroutine=_hooked,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            metadata={**(tool.metadata or {}), "cuga_hooks": True},
        )

    return [wrap(t) for t in tools]


def wrap_provider_with_hooks(provider: Any, registry: HookRegistry) -> Any:
    """The same before/after_tool_call wrapping for tools that come from a tool provider
    (``CugaAgent(tool_provider=...)``), which ``wrap_tools_with_hooks`` on ``tools=`` never sees."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface

    class HookedToolProvider(ToolProviderInterface):
        def __init__(self, inner: Any):
            self._inner = inner
            self._wrapped: Dict[int, BaseTool] = {}

        def __getattr__(self, name: str) -> Any:  # initialized, add_tools, ... of the inner provider
            return getattr(self._inner, name)

        def _wrap(self, tools: List[BaseTool]) -> List[BaseTool]:
            out = []
            for t in tools:
                if (t.metadata or {}).get("cuga_hooks"):
                    out.append(t)
                    continue
                if id(t) not in self._wrapped:
                    self._wrapped[id(t)] = wrap_tools_with_hooks([t], registry)[0]
                out.append(self._wrapped[id(t)])
            return out

        async def initialize(self):
            return await self._inner.initialize()

        async def get_apps(self):
            return await self._inner.get_apps()

        async def get_tools(self, app_name: str):
            return self._wrap(await self._inner.get_tools(app_name))

        async def get_all_tools(self):
            return self._wrap(await self._inner.get_all_tools())

    return HookedToolProvider(provider)


class HooksCallback(BaseCallbackHandler):
    """Reports each model output to after_llm_call (observe only)."""

    def __init__(self, registry: HookRegistry):
        self.registry = registry

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            gen = response.generations[0][0]
            text = getattr(getattr(gen, "message", None), "content", None) or getattr(gen, "text", "")
        except Exception:
            return
        if isinstance(text, str) and text.strip():
            self.registry.after_llm_call(text)
