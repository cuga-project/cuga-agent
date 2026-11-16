"""AgentHooks base class, the registry that runs them, and the SDK glue (gated tools, LLM callback)."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Union

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

    def tool_blocked(self, tool: str, args: Dict[str, Any], message: str) -> None:
        """Observe only: a hook stopped this tool call. A hook's own ``before_tool_call`` cannot know what the others said."""
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


@dataclass
class _Run:
    """The hook state of one invoke(). It lives in a context variable, so concurrent invocations of one agent
    each have their own, and it is dropped when the invocation ends."""

    registry: "HookRegistry"
    thread_id: Optional[str]
    events: List[HookEvent] = field(default_factory=list)
    carried: List[str] = field(default_factory=list)  # tool notes that could not be attached


_RUN: ContextVar[Optional[_Run]] = ContextVar("cuga_hooks_run", default=None)


class HookRegistry:
    """Calls every hook in order and records one HookEvent per hook call."""

    # What this registry offers beyond the nine hooks, for hosts that need to know (a hook that must hear about a call
    # that another hook stopped, or about a hook that raised).
    FEATURES = frozenset({"tool_blocked", "failure_report"})

    def __init__(self, hooks: Optional[List[AgentHooks]] = None):
        self.hooks: List[AgentHooks] = list(hooks or [])
        self.listeners: List[Callable[[HookEvent], None]] = []
        self._started_threads: Set[str] = set()
        self._idle = _Run(self, None)  # outside a run: events only go to listeners

    def __bool__(self) -> bool:
        return bool(self.hooks)

    def add(self, hook: AgentHooks) -> None:
        self.hooks.append(hook)

    def add_listener(self, fn: Callable[[HookEvent], None]) -> None:
        self.listeners.append(fn)

    @contextmanager
    def run(self, thread_id: Optional[str]) -> Iterator[None]:
        """One invoke() or stream(): its events, thread id and carried notes stay inside this block (and its asyncio
        tasks)."""
        token = _RUN.set(_Run(self, thread_id))
        try:
            yield
        finally:
            try:
                _RUN.reset(token)
            except ValueError:  # a stream closed from another context (e.g. by the garbage collector)
                pass

    def in_run(self) -> bool:
        run = _RUN.get()
        return run is not None and run.registry is self

    def _run(self) -> _Run:
        return _RUN.get() if self.in_run() else self._idle

    @property
    def thread_id(self) -> Optional[str]:
        return self._run().thread_id

    def set_thread(self, thread_id: Optional[str]) -> None:
        """The thread of the current run (invoke() may create it); a later message on it is not a new session."""
        self._run().thread_id = thread_id
        if thread_id is not None:
            self._started_threads.add(thread_id)

    def thread_started(self, thread_id: Optional[str]) -> bool:
        return thread_id is not None and thread_id in self._started_threads

    def run_events(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._run().events]

    def _emit(self, event: HookEvent) -> None:
        run = self._run()
        if run is not self._idle:
            run.events.append(event)
        for fn in self.listeners:
            try:
                fn(event)
            except Exception as exc:
                logger.warning("hook listener failed: {}", exc)

    def _call(self, name: str, summary: str, *args: Any) -> HookResult:
        messages: List[str] = []
        details: List[Dict[str, Any]] = []
        answer: Optional[str] = None
        for hook in self.hooks:
            call_args = (answer, *args[1:]) if name == "before_reply" and answer is not None else args
            try:
                res = _norm(getattr(hook, name)(*call_args))
            except Exception as exc:  # a broken hook must not break the agent
                if isinstance(exc, HookBlocked) and name == "before_tool_call":
                    # a before_tool_call hook may also block the call by raising HookBlocked: the block is an event too
                    block = "\n\n".join(messages + [str(exc)]) or None
                    self._emit(HookEvent(name, self._run().thread_id, summary, "block", block, details))
                    raise
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
        self._emit(HookEvent(name, self._run().thread_id, summary, outcome, message, details, answer=answer))
        return HookResult(message=message, details=details, answer=answer)

    # -- the nine hooks ------------------------------------------------------------------------------------------
    def session_start(self, thread_id: Optional[str]) -> None:
        self.set_thread(thread_id)
        self._call("session_start", f"thread {thread_id}", thread_id)

    def on_user_message(self, message: str, thread_id: Optional[str]) -> HookResult:
        return self._call("on_user_message", message[:120], message, thread_id)

    def before_llm_call(self, thread_id: Optional[str] = None) -> Optional[str]:
        message = self._call("before_llm_call", "model call", thread_id).message
        run = self._run()
        carried, run.carried = run.carried, []
        return "\n\n".join(carried + ([message] if message else [])) or None

    def after_llm_call(self, text: str) -> None:
        self._call("after_llm_call", text[:120], text)

    def after_plan(self, code: str) -> Optional[str]:
        return self._call(
            "after_plan", code.strip().splitlines()[0][:120] if code.strip() else "", code
        ).message

    def before_tool_call(self, tool: str, args: Dict[str, Any]) -> Optional[str]:
        try:
            message = self._call("before_tool_call", _fmt_call(tool, args), tool, args).message
        except HookBlocked as exc:  # a hook raised to block: every hook is told, then the block goes on
            self._tool_blocked(tool, args, str(exc))
            raise
        if message:
            self._tool_blocked(tool, args, message)
        return message

    def _tool_blocked(self, tool: str, args: Dict[str, Any], message: str) -> None:
        """Tell every hook, to observe only, that a call was stopped. A broken observer does not change the block."""
        for hook in self.hooks:
            observe = getattr(hook, "tool_blocked", None)
            if not callable(observe):
                continue
            try:
                observe(tool, args, message)
            except Exception as exc:
                logger.warning("hook {}.tool_blocked failed: {}", type(hook).__name__, exc)
                _report_failure(hook, "tool_blocked", (tool, args, message), exc)

    def after_tool_call(self, tool: str, args: Dict[str, Any], result: Any) -> Optional[str]:
        """A message is a note about this call: the tool wrapper attaches it to the result the agent sees. When the
        tool raised, ``result`` is the exception and the note goes to the next model call."""
        return self._call("after_tool_call", _fmt_call(tool, args), tool, args, result).message

    def carry_note(self, note: str) -> None:
        """A note for the next model call of this run. Outside invoke() or stream() there is no run to carry it in,
        so it is dropped (a shared store would hand it to another thread)."""
        run = self._run()
        if run is self._idle:
            logger.warning("hook note dropped (tool called outside invoke() or stream()): {}", note[:200])
            return
        run.carried.append(note)

    def attach_note(self, result: Any, note: str) -> Any:
        """Put a hook note into the tool result, so it stays in the conversation next to the call it is about.
        Text results get the note appended; dict results get a ``hook_note`` field. Other results (lists,
        objects code may index) are left unchanged and the note goes to the next model call instead."""
        if isinstance(result, str):
            return f"{result}\n\n{note}"
        if isinstance(result, dict):
            prev = result.get("hook_note")
            return {**result, "hook_note": f"{prev}\n{note}" if prev else note}
        self.carry_note(note)
        return result

    def before_reply(self, answer: str) -> HookResult:
        return self._call("before_reply", answer[:120], answer)

    def on_task_end(self, answer: str) -> None:
        self._call("on_task_end", answer[:120], answer)


def _fmt_call(tool: str, args: Dict[str, Any]) -> str:
    return f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())})"[:200]


# Attributes some providers put on a tool's function; prompt_utils reads them from ``tool.func`` to describe the tool.
_FUNC_ATTRS = ("_response_schemas", "_param_constraints")


def wrap_tools_with_hooks(
    tools: List[BaseTool], registry: HookRegistry, record: bool = False
) -> List[BaseTool]:
    """before_tool_call / after_tool_call around each tool. A block raises HookBlocked with the hook's message.
    A tool that raises still reaches after_tool_call (with the exception as the result), then the error is re-raised.

    ``record``: record each call with ``ToolCallTracker`` (tracked tool calls, Langfuse). Only for tools nothing else
    records: direct LangChain tools of a hooked tool provider (``wrap_provider_with_hooks`` decides).
    """

    def wrap(tool: BaseTool) -> BaseTool:
        schema = tool.args_schema
        order = list(getattr(schema, "model_fields", {}) or {}) if isinstance(schema, type) else []

        def _before(pos: tuple, kwargs: Dict[str, Any]) -> tuple:
            # CodeAct calls the coroutine directly, so positional arguments arrive here; map them to
            # the schema fields in signature order (an unwrapped tool accepts them the same way).
            if len(pos) > len(order):
                raise TypeError(
                    f"{tool.name}() takes at most {len(order)} positional arguments ({len(pos)} given)"
                )
            kwargs = {**dict(zip(order, pos)), **kwargs}
            # Hooks see the call without None values (mostly schema defaults); the tool gets the call as made,
            # so an explicit None for a required or non-None-default field still reaches it.
            args = {k: v for k, v in kwargs.items() if v is not None}
            blocked = registry.before_tool_call(tool.name, args)
            if blocked:
                raise HookBlocked(blocked)
            return kwargs, args

        def _record(args: Dict[str, Any], started: float, result: Any, error: Optional[str]) -> None:
            if not record:
                return
            from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

            ToolCallTracker.record_call(
                tool_name=tool.name,
                arguments=args,
                result=result,
                app_name=(tool.metadata or {}).get("app_name"),
                operation_id=tool.name,
                duration_ms=(time.time() - started) * 1000,
                error=error,
                param_names=order,
            )

        def _failed(args: Dict[str, Any], exc: BaseException) -> str:
            if isinstance(exc, asyncio.CancelledError):
                return "cancelled"  # a cancelled call is not a tool failure: no after_tool_call
            if isinstance(exc, Exception):
                note = registry.after_tool_call(tool.name, args, exc)
                if note:  # the error reaches the agent unchanged; the note goes to the next model call
                    registry.carry_note(note)
            return str(exc)

        def _done(args: Dict[str, Any], result: Any) -> Any:
            note = registry.after_tool_call(tool.name, args, result)
            return registry.attach_note(result, note) if note else result

        async def _hooked(*pos: Any, **kwargs: Any) -> Any:
            kwargs, args = _before(pos, kwargs)
            started, result, error = time.time(), None, None
            try:
                result = await tool.ainvoke(kwargs)
            except BaseException as exc:
                error = _failed(args, exc)
                raise
            finally:
                _record(args, started, result, error)
            return _done(args, result)

        func = None
        if getattr(tool, "func", None) is not None:  # keep sync calls possible, and hooked

            def _hooked_sync(*pos: Any, **kwargs: Any) -> Any:
                kwargs, args = _before(pos, kwargs)
                started, result, error = time.time(), None, None
                try:
                    result = tool.invoke(kwargs)
                except BaseException as exc:
                    error = _failed(args, exc)
                    raise
                finally:
                    _record(args, started, result, error)
                return _done(args, result)

            for attr in _FUNC_ATTRS:  # response schemas and parameter constraints the model is shown
                if hasattr(tool.func, attr):
                    setattr(_hooked_sync, attr, getattr(tool.func, attr))
            func = _hooked_sync

        return StructuredTool.from_function(
            func=func,
            coroutine=_hooked,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            metadata={**(tool.metadata or {}), "cuga_hooks": True},
        )

    return [wrap(t) for t in tools]


def _records_itself(tool: BaseTool) -> bool:
    """A ``@tracked_tool`` async function records its own calls (the same test as the prepare node)."""
    fn = getattr(tool, "coroutine", None) or getattr(tool, "func", None)
    return bool(getattr(fn, "_cuga_tracked", False)) and asyncio.iscoroutinefunction(fn)


def wrap_provider_with_hooks(provider: Any, registry: HookRegistry) -> Any:
    """The same before/after_tool_call wrapping for tools that come from a tool provider
    (``CugaAgent(tool_provider=...)``), which ``wrap_tools_with_hooks`` on ``tools=`` never sees.

    Registry and combined provider tools record their own calls. Direct LangChain tools are recorded by the prepare
    node, which does not see a direct provider behind this wrapper, so their wrapper records them instead."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider

    class HookedToolProvider(ToolProviderInterface):
        _cuga_hooks = True  # a class attribute: an instance attribute lookup would reach the inner provider

        def __init__(self, inner: Any):
            self._inner = inner
            self._wrapped: Dict[int, BaseTool] = {}

        def __getattr__(self, name: str) -> Any:  # initialized, add_tools, ... of the inner provider
            return getattr(self._inner, name)

        def _wrap(self, tools: List[BaseTool]) -> List[BaseTool]:
            direct = isinstance(base_tool_provider(self._inner), DirectLangChainToolsProvider)
            out = []
            for t in tools:
                if (t.metadata or {}).get("cuga_hooks"):
                    out.append(t)
                    continue
                if id(t) not in self._wrapped:
                    record = direct and not _records_itself(t)
                    self._wrapped[id(t)] = wrap_tools_with_hooks([t], registry, record=record)[0]
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


def unwrap_hooked_provider(provider: Any) -> Any:
    """The provider inside ``wrap_provider_with_hooks``, for type checks (ToolGuard's ``unwrap_tool_provider``
    stops at the hooks wrapper). Any other provider is returned as is."""
    return provider._inner if getattr(type(provider), "_cuga_hooks", False) else provider


def has_hooks_layer(provider: Any) -> bool:
    """Whether ``wrap_provider_with_hooks`` is one of the layers around the base provider."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import unwrap_tool_provider

    for _ in range(16):
        if getattr(type(provider), "_cuga_hooks", False):
            return True
        inner = unwrap_tool_provider(provider)
        if inner is provider:
            return False
        provider = inner
    return False


def base_tool_provider(provider: Any) -> Any:
    """The provider under every ToolGuard and hooks layer. A caller's ToolGuard provider ends up as
    ToolGuard(hooks(ToolGuard(base))), which one unwrap of each kind does not reach."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import unwrap_tool_provider

    for _ in range(16):
        inner = unwrap_hooked_provider(unwrap_tool_provider(provider))
        if inner is provider:
            break
        provider = inner
    return provider


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
