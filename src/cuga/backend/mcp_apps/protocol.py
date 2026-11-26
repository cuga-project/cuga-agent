"""MCP Apps (SEP-1865) host support: shared, dependency-free helpers.

An MCP Apps tool declares ``_meta.ui.resourceUri`` (a ``ui://`` resource with
MIME type ``text/html;profile=mcp-app``). When the agent calls such a tool,
CUGA treats the call as an *interaction*:

1. The registry returns the tool result wrapped in an envelope
   (``ENVELOPE_KEY``) instead of plain text.
2. The tool wrapper in the agent raises :class:`McpAppInteractionPause`, which
   ends the code block (it is a ``BaseException``, so agent code cannot catch it).
3. The CugaLite sandbox node turns the pause into a human-in-the-loop action;
   the chat UI renders the app in a sandboxed iframe.
4. The app calls its server's tools through CUGA (``visibility`` must include
   ``"app"``). When it is done it sends ``ui/update-model-context`` and
   ``ui/message``; the UI resumes the agent, which receives the outcome as the
   result of the original tool call.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable, Mapping, Optional

UI_MIME_TYPE = "text/html;profile=mcp-app"
UI_SCHEME = "ui://"
ENVELOPE_KEY = "__mcp_app__"
ACTION_ID = "mcp_app_interaction"
DEFAULT_VISIBILITY: tuple[str, ...] = ("model", "app")
# Sent by a chat client that renders MCP Apps (the CUGA web chat) on ``/stream``.
CLIENT_HEADER = "X-CUGA-MCP-Apps"

# Whether the current run's client can render MCP Apps. Off by default: the SDK, API callers and
# channels get an MCP Apps tool's ordinary result, as before; only a client that declares support
# gets the form (MCP Apps hosts without UI support fall back to the plain tool result).
_ui_host: ContextVar[bool] = ContextVar("cuga_mcp_apps_ui_host", default=False)


def set_ui_host(enabled: bool):
    """Declare whether this task's client renders MCP Apps; returns a token for ``reset_ui_host``."""
    return _ui_host.set(bool(enabled))


def reset_ui_host(token) -> None:
    _ui_host.reset(token)


def ui_host_enabled() -> bool:
    return _ui_host.get()


def header_enabled(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def ui_meta(tool_meta: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The ``ui`` metadata of a tool: ``{resourceUri?, visibility}``. Empty when absent.

    Reads ``_meta.ui`` and the deprecated flat ``_meta["ui/resourceUri"]`` key.
    """
    if not isinstance(tool_meta, Mapping):
        return {}
    ui = tool_meta.get("ui") if isinstance(tool_meta.get("ui"), Mapping) else {}
    resource_uri = ui.get("resourceUri") or tool_meta.get("ui/resourceUri")
    visibility = ui.get("visibility")
    out: dict[str, Any] = {}
    if isinstance(resource_uri, str) and resource_uri.startswith(UI_SCHEME):
        out["resourceUri"] = resource_uri
    if isinstance(visibility, list) and all(isinstance(v, str) for v in visibility):
        out["visibility"] = list(visibility)
    return out


def visibility_of(meta: Mapping[str, Any]) -> tuple[str, ...]:
    vis = meta.get("visibility")
    return tuple(vis) if vis else DEFAULT_VISIBILITY


def model_can_see(meta: Mapping[str, Any]) -> bool:
    """Hosts MUST NOT give the model tools whose visibility lacks ``"model"``."""
    return "model" in visibility_of(meta)


def app_can_call(meta: Mapping[str, Any]) -> bool:
    """Hosts MUST reject app ``tools/call`` for tools whose visibility lacks ``"app"``."""
    return "app" in visibility_of(meta)


def _dump(item: Any) -> Any:
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", by_alias=True, exclude_none=True)
    return item


def call_tool_result_dict(result: Any) -> dict[str, Any]:
    """A wire-format ``CallToolResult`` dict from an SDK result object or dict."""
    content = _get(result, "content") or []
    structured = _get(result, "structured_content")
    if structured is None:
        structured = _get(result, "structuredContent")
    meta = _get(result, "meta")
    if meta is None:
        meta = _get(result, "_meta")
    is_error = _get(result, "is_error")
    if is_error is None:
        is_error = _get(result, "isError")
    out: dict[str, Any] = {"content": [_dump(c) for c in content], "isError": bool(is_error)}
    if structured is not None:
        out["structuredContent"] = structured
    if meta:
        out["_meta"] = meta
    return out


def result_text(result: Mapping[str, Any]) -> str:
    """Concatenated text content of a wire-format ``CallToolResult``."""
    parts = [
        str(c.get("text", ""))
        for c in result.get("content") or []
        if isinstance(c, Mapping) and c.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)


def build_envelope(
    *,
    server: str,
    tool: str,
    tool_title: Optional[str],
    resource_uri: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """The JSON the registry returns for an MCP Apps tool call."""
    return {
        ENVELOPE_KEY: {
            "server": server,
            "tool": tool,
            "tool_title": tool_title,
            "resource_uri": resource_uri,
            "tool_input": dict(arguments or {}),
            "tool_result": dict(result),
        }
    }


def envelope_from(value: Any) -> Optional[dict[str, Any]]:
    """The envelope inside a registry response, or None for an ordinary result."""
    if isinstance(value, Mapping) and isinstance(value.get(ENVELOPE_KEY), Mapping):
        env = dict(value[ENVELOPE_KEY])
        if isinstance(env.get("resource_uri"), str) and env["resource_uri"].startswith(UI_SCHEME):
            return env
    return None


class McpAppInteractionPause(BaseException):
    """Raised by a tool wrapper when an MCP Apps tool needs the user.

    A ``BaseException`` on purpose: generated code's ``except Exception`` must
    not swallow it. It unwinds the code block to the CugaLite sandbox node.
    """

    def __init__(
        self, envelope: Mapping[str, Any], *, app_name: str, tool_name: str, agent_id: Optional[str]
    ):
        super().__init__(f"{tool_name} is waiting for the user in an interactive form")
        self.envelope = dict(envelope)
        self.app_name = app_name
        self.tool_name = tool_name
        self.agent_id = agent_id


def csp_header(csp: Optional[Mapping[str, Any]]) -> str:
    """Content-Security-Policy for the sandbox proxy, from the resource's ``_meta.ui.csp``.

    With no CSP declared, the app gets no network access at all (the spec's
    restrictive default). Inline script and style are allowed: an app is a
    single HTML document.
    """
    csp = csp or {}

    def domains(key: str) -> list[str]:
        values: Iterable[Any] = csp.get(key) or []
        return [v for v in values if isinstance(v, str) and _safe_source(v)]

    resource = domains("resourceDomains")
    connect = domains("connectDomains")
    frame = domains("frameDomains")
    base = domains("baseUriDomains")
    res = " ".join(resource)
    directives = [
        "default-src 'none'",
        f"script-src 'self' 'unsafe-inline' {res}".strip(),
        f"style-src 'self' 'unsafe-inline' {res}".strip(),
        f"img-src 'self' data: blob: {res}".strip(),
        f"font-src 'self' data: {res}".strip(),
        f"media-src 'self' data: blob: {res}".strip(),
        "connect-src " + (" ".join(connect) if connect else "'none'"),
        "frame-src " + (" ".join(frame) if frame else "'none'"),
        "base-uri " + (" ".join(base) if base else "'none'"),
        "form-action 'none'",
        "object-src 'none'",
    ]
    return "; ".join(directives)


def _safe_source(value: str) -> bool:
    """A CSP source expression that cannot break out of its directive."""
    return bool(value) and not any(ch in value for ch in ";,'\"\n\r\t ") and len(value) < 256
