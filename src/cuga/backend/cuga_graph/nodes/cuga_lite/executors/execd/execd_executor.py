"""Remote code execution against OpenSandbox's execd over HTTP.

execd is the in-sandbox execution daemon from OpenSandbox. Unlike the
OpenSandbox *server*, it runs standalone and needs no container runtime, so it
can live inside an OpenShell sandbox. Each thread gets its own execd code
context — a separate Jupyter kernel, i.e. a separate OS process with its own
Python state that persists across calls.

Tools are not serialised into the kernel: everything except the filesystem
tools and run_command is a stub that calls back into CUGA (``tool_callback``),
and each block reports its outcome through ``block_runner``.

Logging — what you see in ``docker logs`` / ``kubectl logs``
------------------------------------------------------------
Every significant event is logged at a consistent level with a fixed prefix so
you can grep the container output without regex gymnastics:

  grep "execd"                 — all execd events
  grep "execd:code"            — code execution events only
  grep "execd:shell"           — shell / run_command events only
  grep "execd:fs"              — filesystem tool events
  grep "execd:ctx"             — kernel lifecycle (create, evict, bootstrap)
  grep "execd:route"           — routing decision (which backend was chosen)

Log levels:
  INFO  — per-request events with timing (start, result, duration_ms)
  DEBUG — full payloads (code sent, raw output) — verbose, off by default
  WARNING — degraded paths (no key, bootstrap failed, throwaway context)
"""

import asyncio
import inspect
import json
import re
import shlex
import time
import uuid
from typing import Any, Callable, List, Optional

import httpx
from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.config import get_tenant_id, settings
from ..common.run_output import format_run_command_output
from ..filesystem.paths import normalize_shell_command_paths
from ..base_executor import RemoteExecutor
from ...tracking.tracker import counted_tool_call
from . import block_runner, connection, tool_callback


def _block_timeout() -> int:
    """Seconds a block may run in the sandbox, tool round trips included."""
    adv = settings.advanced_features
    return int(getattr(adv, "execd_execution_timeout", 0) or adv.sandbox_execution_timeout)


def _is_callable_table(value: Any) -> bool:
    """A dict whose values are all callables, like the function-calling ``_fc_tools`` lookup."""
    return isinstance(value, dict) and bool(value) and all(callable(v) for v in value.values())


# Name of the callback the in-kernel tools charge the call budget through.
_CHARGE_NAME = "_cuga_charge_tool_call"


def _budget_charge() -> Callable[..., Any]:
    """A counted no-op: calling it charges one unit against the run, block and thread budgets.

    Filesystem tools and ``run_command`` run in the kernel, so the counted
    wrappers ``CodeExecutor`` put around them never run. Each in-kernel tool
    calls this back in CUGA before doing its work, so it is charged — and
    refused — exactly as a tool answered by CUGA would be.
    """

    async def _charge(tool_name: str = "") -> None:
        return None

    return counted_tool_call(_charge)


def _charged_tools_code(names: list[str]) -> str:
    """Kernel Python wrapping each in-kernel tool so it charges the budget before it runs."""
    lines = [
        "def _cuga_charged(_fn):",
        "    async def _cuga_charged_call(*args, **kwargs):",
        f"        await {_CHARGE_NAME}(_fn.__name__)",
        "        return await _fn(*args, **kwargs)",
        "    _cuga_charged_call.__name__ = _fn.__name__",
        "    _cuga_charged_call.__doc__ = _fn.__doc__",
        "    return _cuga_charged_call",
        "",
    ]
    for name in names:
        lines.append(f"if {name!r} in globals(): {name} = _cuga_charged({name})")
    return "\n".join(lines) + "\n"


class _KernelGone(Exception):
    """The sandbox no longer has the thread's kernel (evicted, released or reassigned)."""


class ExecdExecutor(RemoteExecutor):
    """Executes agent code in a remote execd sandbox."""

    # thread_id -> execd context id. A context is a live Jupyter kernel, so
    # reusing it across turns is what keeps variables alive between steps.
    _contexts: dict[str, str] = {}
    _locks: dict[str, asyncio.Lock] = {}
    # context id -> thread id sent with every request; sandbox-api routes on it
    # so a thread always reaches the execd pod that holds its kernel.
    _thread_of_context: dict[str, str] = {}
    # thread id -> {"tenant_id", "agent_id"} of the conversation, from AgentState.service_scope.
    _scope_of_thread: dict[str, dict[str, str]] = {}

    # Endpoint the cached contexts belong to; they are meaningless on any other.
    _contexts_url: Optional[str] = None

    @staticmethod
    def _connection() -> connection.SandboxConnection:
        # Called even when the sandbox is off if a caller forces mode="execd";
        # fall back to the configured URL rather than failing.
        conn = connection.resolve()
        if conn is None:
            return connection.SandboxConnection(url=connection.manual_config()["url"], api_key="")
        return conn

    @classmethod
    def _base_url(cls) -> str:
        url = cls._connection().url.rstrip("/")
        if not url:
            raise RuntimeError(
                "Sandbox URL is not configured: mount the sandbox secret (sandbox_secret_dir), "
                "set DYNACONF_ADVANCED_FEATURES__SANDBOX_URL / EXECD_URL, or enter it in Manage → Code sandbox"
            )
        return url

    @classmethod
    def _auth_headers(cls, thread: Optional[str] = None) -> dict[str, str]:
        """Return the ``X-API-Key`` header for every execd request.

        With no key configured no header is sent: execd is then expected to be
        reachable only through ``openshell forward`` or a cluster-internal
        Service, so network isolation replaces application auth. In a
        multi-tenant deployment the key is the ``SANDBOX_API_KEY`` issued at
        provision time; see :mod:`.connection` for where it comes from. The
        same key protects the control plane (``sandbox-api``).
        """
        conn = cls._connection()
        if not conn.api_key and not conn.url.startswith(
            ("http://localhost", "http://127.", "http://host.openshell", "http://host.docker")
        ):
            # A loopback or .internal address is expected to be network-isolated;
            # anything else is likely an endpoint that should be protected.
            logger.warning(
                "[ExecdExecutor] execd URL looks like a remote endpoint ({}) but no API key is configured. "
                "Set one in Manage → Code sandbox, or use the cluster secret.",
                conn.url,
            )
        headers = {"X-API-Key": conn.api_key} if conn.api_key else {}
        headers.update(cls._identity_headers(thread))
        return headers

    @classmethod
    def _identity_headers(cls, thread: Optional[str] = None) -> dict[str, str]:
        """``X-Tenant-Id`` / ``X-Agent-Id`` that sandbox-api scopes threads by.

        The conversation's own tenant and agent, from ``AgentState.service_scope``.
        """
        scope = cls._scope_of_thread.get(thread or "", {})
        tenant = scope.get("tenant_id") or get_tenant_id() or "default"
        # Fallback is CUGA's default agent, so a thread seen before any code ran in
        # this process (e.g. the workspace panel after a restart) keeps its scope.
        agent = scope.get("agent_id") or "cuga-default"
        return {"X-Tenant-Id": str(tenant), "X-Agent-Id": str(agent)}

    @classmethod
    def _remember_scope(cls, thread_id: Optional[str], state: Optional[AgentState]) -> None:
        scope = getattr(state, "service_scope", None) if state is not None else None
        if not thread_id or not isinstance(scope, dict):
            return
        known = {k: str(scope[k]) for k in ("tenant_id", "agent_id") if scope.get(k)}
        if known:
            cls._scope_of_thread[thread_id] = known

    @classmethod
    def _client(cls, timeout: float, thread: Optional[str] = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, headers=cls._auth_headers(thread), verify=cls._verify_ssl())

    @staticmethod
    def _verify_ssl() -> bool | str:
        """``advanced_features.execd_verify_ssl``: true (default), false, or a CA bundle path.

        On by default: every request carries the sandbox API key, so the server
        must be authenticated. Self-signed routes should name their CA bundle.
        """
        value = getattr(settings.advanced_features, "execd_verify_ssl", True)
        if isinstance(value, bool):
            return value
        text = str(value or "").strip()
        if text.lower() in ("false", "0", "no", "off"):
            return False
        if text.lower() in ("", "true", "1", "yes", "on"):
            return True
        return text

    @classmethod
    def _sync_endpoint(cls) -> None:
        """Forget cached contexts when the sandbox endpoint changed at runtime."""
        url = cls._base_url()
        if cls._contexts_url is not None and cls._contexts_url != url and cls._contexts:
            logger.info(
                f"[execd:ctx] Sandbox endpoint changed {cls._contexts_url} -> {url}; "
                f"dropping {len(cls._contexts)} cached contexts"
            )
            cls._contexts.clear()
            cls._thread_of_context.clear()
        cls._contexts_url = url

    @classmethod
    def _get_key_lock(cls, key: str) -> asyncio.Lock:
        # Safe without an outer lock: asyncio does not preempt between the
        # membership test and the assignment.
        if key not in cls._locks:
            cls._locks[key] = asyncio.Lock()
        return cls._locks[key]

    @classmethod
    async def _context_for_thread(cls, thread_id: Optional[str]) -> str:
        if not thread_id:
            # Without an identity there is nothing to isolate by, and a shared
            # fallback context would put unrelated sessions — potentially
            # different users — in one kernel with one another's variables and
            # files. A throwaway context loses state, which is the unavoidable
            # cost of having no identity, but nothing leaks sideways.
            logger.warning(
                "[execd:ctx] No thread_id supplied; using a throwaway context. "
                "State will not persist across calls."
            )
            return await cls._create_context(bootstrap_key=None)

        key = thread_id
        cls._sync_endpoint()
        async with cls._get_key_lock(key):
            existing = cls._contexts.get(key)
            if existing is not None:
                logger.debug(f"[execd:ctx] Reusing existing kernel context_id={existing} thread={key}")
                return existing

            context_id = await cls._create_context(bootstrap_key=key)
            cls._contexts[key] = context_id
            logger.info(f"[execd:ctx] Created kernel context_id={context_id} thread={key}")
        # Outside the lock: the copy is a /command, which resolves this context again.
        await cls._copy_skills_to_workspace(key)
        return context_id

    @classmethod
    def _skill_files(cls) -> dict[str, bytes]:
        """Skill folders as ``skills/<name>/<path>`` → content, like the other sandbox modes copy them.

        ``load_skill`` runs in CUGA and returns the instructions, then tells the
        agent the skill's companion files (scripts, templates) are at
        ``/workspace/skills/<name>/``. Those files are read with ``read_file``
        and run with ``run_command``, both of which happen in the sandbox — so
        the files have to be there. ``.pyc`` and ``.xsd`` are skipped, as the
        other modes skip them.
        """
        import os
        from pathlib import Path

        if not getattr(getattr(settings, "skills", None), "enabled", False):
            return {}
        from cuga.backend.skills.loader import discover_skills

        cuga_folder = (os.getenv("CUGA_FOLDER") or "").strip() or (
            getattr(settings.policy, "cuga_folder", None) or ""
        ).strip()
        files: dict[str, bytes] = {}
        for entry in discover_skills(cuga_folder or None):
            root = Path(entry.source).parent
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.suffix.lower() not in (".pyc", ".xsd"):
                    # entry.name, not the folder name: it is the path load_skill tells the agent.
                    files[f"skills/{entry.name}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
        return files

    # sandbox-api takes the command as one string, so the archive rides inside it.
    _SKILLS_MAX_BYTES = 4 * 1024 * 1024

    @classmethod
    async def _copy_skills_to_workspace(cls, thread_id: str) -> None:
        """Copy the skill folders into a new kernel's workspace with one ``/command``.

        Files already there are kept (``--skip-old-files``): a kernel recreated
        for a live thread must not overwrite what the agent has since changed.
        Best effort — without the copy ``load_skill`` still works, only the
        companion files it names are missing.
        """
        import base64
        import io
        import tarfile

        try:
            files = cls._skill_files()
        except Exception as exc:
            logger.warning(f"[execd:ctx] Could not collect skill files for thread={thread_id}: {exc}")
            return
        if not files:
            return
        total = sum(len(data) for data in files.values())
        if total > cls._SKILLS_MAX_BYTES:
            logger.warning(
                f"[execd:ctx] Skills are {total} bytes, over {cls._SKILLS_MAX_BYTES}; "
                f"not copied to thread={thread_id}"
            )
            return

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel, data in files.items():
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        archive = base64.b64encode(buf.getvalue()).decode("ascii")
        command = 'printf %s "$CUGA_SKILLS_TGZ" | base64 -d | tar -xz --skip-old-files -f -'
        try:
            _out, err, failed = await cls._run_shell_command(
                command, thread_id, extra_env={"CUGA_SKILLS_TGZ": archive}
            )
        except Exception as exc:
            logger.warning(f"[execd:ctx] Copying skills to thread={thread_id} failed: {exc}")
            return
        if failed:
            logger.warning(f"[execd:ctx] Copying skills to thread={thread_id} failed: {err[:300]}")
        else:
            logger.info(f"[execd:ctx] Copied {len(files)} skill files ({total} bytes) to thread={thread_id}")

    @classmethod
    async def _create_context(cls, bootstrap_key: Optional[str]) -> str:
        t0 = time.monotonic()
        timeout = float(_block_timeout())
        # A throwaway context still needs a routing id, or its /code calls could
        # land on a pod that does not hold the kernel.
        routing_id = bootstrap_key or f"throwaway-{uuid.uuid4().hex}"
        async with cls._client(timeout, routing_id) as client:
            response = await client.post(
                f"{cls._base_url()}/code/context", json={"thread_id": routing_id, "language": "python"}
            )
            response.raise_for_status()
            context_id = response.json()["id"]
        cls._thread_of_context[context_id] = routing_id

        elapsed = int((time.monotonic() - t0) * 1000)
        logger.info(
            f"[execd:ctx] POST /code/context → context_id={context_id} "
            f"key={bootstrap_key or '(throwaway)'} duration_ms={elapsed}"
        )
        await cls._bootstrap_context(context_id, routing_id)
        return context_id

    @staticmethod
    def _workspace_name(key: str) -> str:
        """Filesystem-safe directory name for a thread id.

        Thread ids reach us from callers, so anything outside this set — a
        slash or a `..` in particular — must not survive into a path.
        """
        safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in key)
        return safe[:64] or "default"

    @classmethod
    async def _bootstrap_context(cls, context_id: str, key: str) -> None:
        """Give the context its own workspace directory and virtualenv.

        All contexts share one interpreter, so isolation here is by `sys.path`
        rather than by a separate Python: the per-thread venv is prepended, so
        anything installed into it shadows the image-wide site-packages while
        the shared packages stay importable.

        Best-effort by design. A missing `uv`, a read-only mount or a failed
        venv creation degrades to the shared environment rather than failing
        the turn — the agent can still run code, just without private packages.
        """
        name = cls._workspace_name(key)
        workspace = f"/workspace/{name}"
        index_url = getattr(settings.advanced_features, "execd_package_index", "") or ""
        callback = tool_callback.callback_url()
        logger.info(
            f"[execd:ctx] Bootstrap start context_id={context_id} "
            f"workspace={workspace} "
            f"package_index={index_url or '(none — installs will fail closed)'}"
        )
        bootstrap = f"""
import os, site, subprocess, sys

_ws = os.path.join("/workspace", {name!r})
os.makedirs(_ws, exist_ok=True)
os.chdir(_ws)

_venv = os.path.join(_ws, ".venv")
if not os.path.isdir(_venv):
    try:
        subprocess.run(["uv", "venv", "--seed", _venv], capture_output=True, timeout=120)
    except Exception as _exc:
        print("execd bootstrap: uv venv failed:", _exc)

# Fallback in case seed is missing pip or pip executable
_pip_bin = os.path.join(_venv, "bin", "pip")
if not os.path.exists(_pip_bin):
    try:
        subprocess.run(["uv", "pip", "install", "pip", "--python", os.path.join(_venv, "bin", "python")], capture_output=True, timeout=120)
    except Exception:
        pass

_sp = os.path.join(
    _venv, "lib", "python{{}}.{{}}".format(*sys.version_info[:2]), "site-packages"
)
if os.path.isdir(_sp):
    site.addsitedir(_sp)
    # addsitedir appends; put the private packages ahead of the shared ones.
    if _sp in sys.path:
        sys.path.remove(_sp)
    sys.path.insert(0, _sp)
import importlib as _importlib
_importlib.invalidate_caches()

# Package installs land in this thread's venv, never in the image-wide
# site-packages (which is read-only under the sandbox policy anyway).
# UV_PYTHON pins the interpreter for uv so `uv pip install` targets the venv
# even when uv's discovery heuristics run before VIRTUAL_ENV is read.
os.environ["VIRTUAL_ENV"] = _venv
os.environ["UV_PYTHON"] = os.path.join(_venv, "bin", "python")
os.environ["PATH"] = os.path.join(_venv, "bin") + os.pathsep + os.environ.get("PATH", "")

_index = {index_url!r}
if _index:
    os.environ["UV_INDEX_URL"] = _index
    os.environ["PIP_INDEX_URL"] = _index
    # uv resolves against this index only; nothing reaches the default PyPI
    # unless the operator configured it explicitly.
    os.environ["UV_INDEX_STRATEGY"] = "first-index"

# Every tool call goes back to CUGA; say now, not at the first tool call, if it cannot.
_cb = {callback!r}
if _cb:
    import urllib.request as _ur, urllib.error as _ue
    try:
        _ur.urlopen(_ur.Request(_cb + "/sandbox/tools/call", data=b"{{}}", method="POST",
                                headers={{"Content-Type": "application/json"}}), timeout=5)
    except _ue.HTTPError:
        pass  # any HTTP answer means CUGA is reachable
    except Exception as _exc:
        print("CUGA_CALLBACK_UNREACHABLE", _cb, _exc)
"""
        t0 = time.monotonic()
        try:
            output = await cls._execute(context_id, bootstrap)
            if "CUGA_CALLBACK_UNREACHABLE" in output:
                logger.warning(
                    f"[execd:callback] The sandbox cannot reach CUGA at {callback}: tool calls from "
                    f"code will fail. Check sandbox_callback_url and the sandbox egress policy. "
                    f"({output.strip()[-300:]})"
                )
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.info(
                f"[execd:ctx] Bootstrap complete context_id={context_id} "
                f"workspace={workspace} duration_ms={elapsed}"
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning(
                f"[execd:ctx] Bootstrap FAILED context_id={context_id} "
                f"workspace={workspace} duration_ms={elapsed} error={exc}"
            )

    @classmethod
    async def _run_remote(cls, code: str, thread_id: Optional[str]) -> str:
        """Resolve the thread's context and execute code in it."""
        context_id = await cls._context_for_thread(thread_id)
        try:
            return await cls._execute(context_id, code)
        except _KernelGone as exc:
            logger.warning(f"[execd:ctx] Kernel gone for thread={thread_id} ({exc}); creating a new context")
            cls._forget_thread(thread_id)
            return await cls._execute(await cls._context_for_thread(thread_id), code)

    @classmethod
    def has_context(cls, thread_id: Optional[str]) -> bool:
        """Whether this process holds a kernel for the thread, without creating one."""
        cls._sync_endpoint()
        return (thread_id or "_default") in cls._contexts

    @classmethod
    def _forget_thread(cls, thread_id: Optional[str]) -> None:
        """Drop the cached context so the next call creates a fresh one."""
        key = thread_id or "_default"
        context_id = cls._contexts.pop(key, None)
        if context_id:
            cls._thread_of_context.pop(context_id, None)

    @staticmethod
    async def _raise_for_status(response: Any) -> None:
        """``raise_for_status``, but report a lost kernel as :class:`_KernelGone`.

        sandbox-api answers ``kernel_not_found`` (marked recoverable) once it has
        dropped a thread, while this process still holds the old context id.
        """
        if not response.is_error:
            return
        body = (await response.aread()).decode("utf-8", "replace")
        if "kernel_not_found" in body:
            raise _KernelGone(body[:300])
        response.raise_for_status()

    @classmethod
    async def _execute(cls, context_id: str, code: str) -> str:
        """Send code to one execd context and return the combined output.

        The response is a newline-delimited JSON stream of typed events:
        ``init`` and ``ping`` are handshake noise, ``stdout``/``stderr`` carry
        output, ``execution_complete`` ends the run, and ``error`` carries a
        Jupyter traceback. Only the output events contribute to the result.
        """
        # The in-sandbox asyncio.wait_for already bounds the agent's own code;
        # allow headroom on the wire so a clean timeout beats a torn stream.
        timeout = float(_block_timeout()) + 30.0
        # Truncate for readability in logs — full code at DEBUG level only
        code_preview = code[:120].replace("\n", "↵") + ("…" if len(code) > 120 else "")
        logger.info(
            f"[execd:code] → POST /code context_id={context_id} code_len={len(code)} preview={code_preview!r}"
        )
        t0 = time.monotonic()

        payload: dict[str, Any] = {"context": {"id": context_id, "language": "python"}, "code": code}
        thread = cls._thread_of_context.get(context_id)
        if thread:
            payload["thread_id"] = thread
        async with cls._client(timeout, thread) as client:
            async with client.stream("POST", f"{cls._base_url()}/code", json=payload) as response:
                await cls._raise_for_status(response)
                chunks, _out, _err, failure = await cls._consume_stream(response)

        elapsed = int((time.monotonic() - t0) * 1000)

        if failure:
            name = failure.get("ename", "Error")
            value = failure.get("evalue", "")
            traceback = failure.get("traceback") or []
            tail = "\n".join(traceback[-3:]) if traceback else ""
            logger.warning(
                f"[execd:code] ✗ context_id={context_id} error={name}: {value!r} duration_ms={elapsed}"
            )
            raise RuntimeError(f"{name}: {value}\n{tail}".strip())

        output_len = sum(len(c) for c in chunks)
        logger.info(f"[execd:code] ✓ context_id={context_id} output_len={output_len} duration_ms={elapsed}")
        logger.debug(f"[execd:code] full output context_id={context_id}: {''.join(chunks)[:500]}")
        return "".join(chunks)

    @classmethod
    async def _consume_stream(cls, response: Any) -> tuple[list[str], list[str], list[str], Optional[dict]]:
        """Read one execd event stream into (combined, stdout, stderr, failure).

        ``/code`` and ``/command`` speak the same newline-delimited JSON: ``init``
        and ``ping`` are handshake noise, ``stdout``/``stderr`` carry output, and
        ``error`` ends the run. The two callers differ only in what they do with
        an ``error`` — a kernel traceback is a failure to raise, a non-zero exit
        status is output to report — so the split streams are returned alongside
        the interleaved one rather than merged here.

        Reading stops at ``execution_complete`` or ``error`` instead of waiting
        for the server to close the stream: sandbox-api holds ``/command`` open
        for about a second after the command has finished, which every shell
        and filesystem call would otherwise pay.
        """
        combined: list[str] = []
        out: list[str] = []
        err: list[str] = []
        failure: Optional[dict[str, Any]] = None

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"[ExecdExecutor] Ignoring non-JSON stream line: {line[:200]}")
                continue

            event_type = event.get("type")
            if event_type in ("stdout", "stderr"):
                text = event.get("text", "")
                combined.append(text)
                (out if event_type == "stdout" else err).append(text)
            elif event_type == "error":
                failure = event.get("error") or {}
                break
            elif event_type == "execution_complete":
                break
            elif event_type is None and "code" in event and "message" in event:
                # execd rejects a request mid-stream with a bare {"code", "message"}
                # (e.g. "session is busy"); without this it read as empty output.
                failure = {"ename": str(event["code"]), "evalue": str(event["message"])}
                break

        return combined, out, err, failure

    @classmethod
    def _workspace_path(cls, thread_id: Optional[str]) -> str:
        """The thread's workspace directory, as seen inside the sandbox."""
        return f"/workspace/{cls._workspace_name(thread_id or '_default')}"

    @classmethod
    async def _run_shell_command(
        cls,
        command: str,
        thread_id: Optional[str],
        extra_env: Optional[dict[str, str]] = None,
    ) -> tuple[str, str, bool]:
        try:
            return await cls._run_shell_command_once(command, thread_id, extra_env)
        except _KernelGone as exc:
            logger.warning(f"[execd:ctx] Kernel gone for thread={thread_id} ({exc}); creating a new context")
            cls._forget_thread(thread_id)
            return await cls._run_shell_command_once(command, thread_id, extra_env)

    @classmethod
    async def _run_shell_command_once(
        cls,
        command: str,
        thread_id: Optional[str],
        extra_env: Optional[dict[str, str]] = None,
    ) -> tuple[str, str, bool]:
        """Run one shell command in the thread's workspace via ``POST /command``.

        Resolving the code context first is what makes the shell and the agent's
        Python share a workspace: the context bootstrap is what creates
        ``/workspace/<thread>`` and its virtualenv. Without it a command could
        land in a directory that does not exist yet, and installs would miss the
        venv that the generated code imports from.

        A non-zero exit arrives as an ``error`` event whose ``evalue`` is the
        status. That is a result to hand back to the agent, not a transport
        failure, so it is reported rather than raised.
        """
        context_id = await cls._context_for_thread(thread_id)
        workspace = cls._workspace_path(thread_id)
        venv = f"{workspace}/.venv"
        timeout_s = float(_block_timeout())

        # Log the command but not the env (may contain CUGA_FS_SRC — a full
        # Python script — which would drown the log line).
        is_fs_op = extra_env and "CUGA_FS_SRC" in extra_env
        cmd_preview = command[:120].replace("\n", "↵") + ("…" if len(command) > 120 else "")
        if is_fs_op:
            op_hint = ""
            try:
                spec = json.loads(
                    __import__("base64").b64decode(extra_env.get("CUGA_FS_OP", "e30=")).decode()
                )
                op_hint = f" fs_op={spec.get('op', '?')}"
            except Exception:
                pass
            logger.info(
                f"[execd:fs] → POST /command (filesystem){op_hint} workspace={workspace} thread={thread_id}"
            )
        else:
            logger.info(
                f"[execd:shell] → POST /command cmd={cmd_preview!r} cwd={workspace} thread={thread_id}"
            )

        t0 = time.monotonic()
        payload: dict[str, Any] = {
            "thread_id": cls._thread_of_context.get(context_id) or thread_id,
            "command": command,
            "cwd": workspace,
            "timeout": int(timeout_s * 1000),
            # A command is a fresh process, so nothing the context bootstrap set
            # in the kernel carries over — the venv has to be named again here
            # or `uv pip install` would write to the image-wide site-packages.
            # UV_PYTHON pins the interpreter so `uv pip install` targets the
            # thread venv even when uv's discovery runs before VIRTUAL_ENV is
            # read (observed on the OCP/UBI9 image).
            "envs": {
                "VIRTUAL_ENV": venv,
                "PATH": f"{venv}/bin:/usr/local/bin:/usr/bin:/bin",
                "UV_PYTHON": f"{venv}/bin/python",
            },
        }
        index_url = getattr(settings.advanced_features, "execd_package_index", "") or ""
        if index_url:
            payload["envs"]["UV_INDEX_URL"] = index_url
            payload["envs"]["PIP_INDEX_URL"] = index_url
            payload["envs"]["UV_INDEX_STRATEGY"] = "first-index"
        if extra_env:
            # Callers pass operands here rather than in the command string —
            # see ExecdFilesystemBackend, which keeps file contents and paths
            # out of shell quoting entirely.
            payload["envs"].update(extra_env)

        # sandbox-api's /command accepts only thread_id and command and drops cwd and
        # envs, so they are also folded into the command itself. Plain execd honours
        # both fields; for it the prefix is redundant but harmless.
        exports = " ".join(f"export {k}={shlex.quote(v)};" for k, v in payload["envs"].items())
        payload["command"] = f"cd {shlex.quote(workspace)} || exit 1; {exports} {command}"

        async with cls._client(timeout_s + 30.0, payload["thread_id"]) as client:
            async with client.stream("POST", f"{cls._base_url()}/command", json=payload) as response:
                await cls._raise_for_status(response)
                _combined, out, err, failure = await cls._consume_stream(response)

        elapsed = int((time.monotonic() - t0) * 1000)
        # Joined with newlines, unlike /code: a command emits one event per
        # output line with the terminator stripped, while a kernel sends its
        # text with the newlines already in it. Concatenating command events
        # would run every line of output together.
        stdout = "\n".join(out)
        stderr = "\n".join(err)
        if failure:
            detail = failure.get("evalue") or failure.get("ename") or "command failed"
            stderr = f"{stderr}\n[exit] {detail}".strip()

        status = "✗" if failure else "✓"
        if is_fs_op:
            logger.info(
                f"[execd:fs] {status}{op_hint} "
                f"stdout_len={len(stdout)} stderr_len={len(stderr)} duration_ms={elapsed}"
            )
        else:
            logger.info(
                f"[execd:shell] {status} cmd={cmd_preview!r} "
                f"stdout_len={len(stdout)} stderr_len={len(stderr)} duration_ms={elapsed}"
            )
            if stdout:
                logger.debug(f"[execd:shell] stdout thread={thread_id}: {stdout[:300]}")
            if stderr:
                logger.debug(f"[execd:shell] stderr thread={thread_id}: {stderr[:300]}")

        return stdout, stderr, bool(failure)

    # A bare ``/workspace`` — the shared helper only rewrites ``/workspace/...``,
    # leaving the root itself alone because under opensandbox the sandbox is
    # per thread and that root is already the right one.
    _BARE_WORKSPACE = re.compile(r"(?<![\w./])/workspace(?![\w/])")

    @classmethod
    def _confine_to_thread_workspace(cls, command: str, thread_id: Optional[str]) -> str:
        """Point a bare ``/workspace`` at this thread's directory.

        One execd container holds every thread, so ``ls /workspace`` would
        otherwise list every other thread's workspace.

        Best-effort, like the shared path normalization it follows: a command
        that builds a path at runtime can still name the shared root. It is not
        the isolation boundary — per-thread separation here is by directory, and
        anything needing a real boundary needs its own sandbox (see
        `sandbox/sandbox/README.md`). It removes the obvious accident.
        """
        return cls._BARE_WORKSPACE.sub(cls._workspace_path(thread_id), command)

    def create_run_command_tool(self, thread_id: Optional[str] = None) -> Callable:
        """Build the ``run_command`` coroutine bound to one thread."""

        async def run_command(cmd: str) -> str:
            try:
                normalized = self._confine_to_thread_workspace(normalize_shell_command_paths(cmd), thread_id)
                stdout, stderr, failed = await self._run_shell_command(normalized, thread_id)
                return format_run_command_output(stdout, stderr, failed=failed)
            except Exception as exc:
                return f"[run_command error] {exc}"

        return run_command

    def create_sandbox_tools(
        self,
        thread_id: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        skills_enabled: Optional[bool] = None,
    ) -> list[StructuredTool]:
        """Return the ``run_command`` tool, matching OpenSandboxExecutor's contract.

        ``cuga_folder`` / ``skills_enabled`` are accepted so the two executors are
        interchangeable at the call site in ``build_runtime_tools``. execd copies
        skills when a thread's kernel is created (``_copy_skills_to_workspace``),
        not here, so they reach the sandbox with or without the shell tool.
        """
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command inside the sandbox and return its output. "
                    "Commands run from the thread workspace with its virtual environment "
                    "activated, alongside the Python you execute. "
                    "Install with `uv pip install <pkg>` (no --target flag — the venv is already active). "
                    "Verify with `python -c \"import pkg; print('ok')\"` "
                    "or `uv pip show pkg` — not `python -m pip` or `pip show`. "
                    "Run with `python ./script.py` first; retry with `uv run --no-project ...` if that fails. "
                    "Node commands must start with plain `node ...`; npm commands must start with plain `npm ...`. "
                    "Never use `uv npm`, `uv run node`, or `uv run npm`."
                ),
            ),
        ]

    @classmethod
    async def release_thread(cls, thread_id: Optional[str], agent_id: Optional[str] = None) -> None:
        """Tell sandbox-api to free a thread's kernel and routing entry.

        Sent whether or not this process cached a context for the thread: after
        a restart, or on another replica, the kernel still lives in the sandbox.
        ``agent_id`` fills the scope header when nothing is cached for the thread.
        """
        if not thread_id:
            return
        key = thread_id
        context_id = cls._contexts.pop(key, None)
        cls._locks.pop(key, None)
        routing_id = (cls._thread_of_context.pop(context_id, None) if context_id else None) or key
        if agent_id and key not in cls._scope_of_thread:
            cls._scope_of_thread[key] = {"agent_id": str(agent_id)}
        logger.info(f"[execd:ctx] Releasing thread={key} context_id={context_id}")
        try:
            timeout = float(_block_timeout())
            async with cls._client(timeout, routing_id) as client:
                # sandbox-api frees a thread's kernel and routing entry by thread id;
                # it has no per-context delete.
                response = await client.delete(f"{cls._base_url()}/threads/{routing_id}")
                if response.status_code != 404:
                    response.raise_for_status()
            logger.info(f"[execd:ctx] Thread released thread={key} status={response.status_code}")
        except Exception as exc:
            # The local mapping is already gone, so the next turn starts a fresh
            # kernel either way; a stale one is a leak, not a correctness bug.
            logger.warning(f"[execd:ctx] Failed to release thread={key} context_id={context_id} error={exc}")
        finally:
            cls._scope_of_thread.pop(key, None)

    @staticmethod
    def _shell_tool_code(workspace_root: str, index_url: str = "") -> str:
        """Inline ``run_command`` that runs directly in the sandbox.

        Same reasoning as ``_filesystem_tools_code``: the agent's kernel already
        runs inside execd at ``workspace_root``, so a shell command belongs right
        here — no HTTP callback to CUGA and back.

        Without this the tool fell through to the generic registry stub, which is
        wrong twice over: ``run_command`` is not a registry API, and the stub is
        ``(**kwargs)`` while the prompt documents ``await run_command("cmd")``,
        so every call died with "takes 0 positional arguments but 1 was given".

        Output contract matches ``format_run_command_output``: stdout on success,
        stdout + "\n[stderr]\n" + stderr on failure.
        """
        return f'''
import asyncio as _cuga_sh_aio
import importlib as _cuga_sh_importlib
import os as _cuga_sh_os
import re as _cuga_sh_re

_CUGA_SH_ROOT = {workspace_root!r}
_CUGA_SH_VENV = _cuga_sh_os.path.join(_CUGA_SH_ROOT, ".venv")
_CUGA_SH_INDEX = {index_url!r}

# Matches `uv pip install` or `pip install` so we can rewrite them to target
# the thread venv explicitly and refresh the kernel's import cache afterwards.
_CUGA_SH_INSTALL_RE = _cuga_sh_re.compile(r"\\buv\\s+pip\\s+install\\b|\\bpip\\s+install\\b")
# Matches an existing `--python ...` argument so we never double-inject it.
_CUGA_SH_PYTHON_ARG_RE = _cuga_sh_re.compile(r"--python\\s+\\S+|--python=\\S+")


def _cuga_sh_rewrite_install(cmd: str) -> str:
    """Inject `--python <venv>/bin/python` into every uv/pip install command.

    `uv pip install` respects `VIRTUAL_ENV` in theory, but under the OCP image
    `uv` can resolve against the system Python when a project file is present in
    the cwd or when its interpreter discovery runs before the env var is read.
    Passing `--python` makes the target venv unambiguous regardless of any
    discovery heuristics.  Idempotent: skipped when `--python` is already there.
    """
    if not _CUGA_SH_INSTALL_RE.search(cmd) or _CUGA_SH_PYTHON_ARG_RE.search(cmd):
        return cmd
    _python = _cuga_sh_os.path.join(_CUGA_SH_VENV, "bin", "python")
    # Insert --python right after the `uv pip install` / `pip install` token.
    return _CUGA_SH_INSTALL_RE.sub(
        lambda m: m.group(0) + " --python " + _python,
        cmd,
        count=1,
    )


async def run_command(cmd: str) -> str:
    """Run a shell command in the workspace and return its output."""
    _env = dict(_cuga_sh_os.environ)
    _env["VIRTUAL_ENV"] = _CUGA_SH_VENV
    _env["UV_PYTHON"] = _cuga_sh_os.path.join(_CUGA_SH_VENV, "bin", "python")
    _env["PATH"] = _CUGA_SH_VENV + "/bin:" + _env.get("PATH", "")
    if _CUGA_SH_INDEX:
        _env["UV_INDEX_URL"] = _CUGA_SH_INDEX
        _env["PIP_INDEX_URL"] = _CUGA_SH_INDEX
        _env["UV_INDEX_STRATEGY"] = "first-index"
    _cmd = _cuga_sh_rewrite_install(cmd)
    _proc = await _cuga_sh_aio.create_subprocess_shell(
        _cmd,
        cwd=_CUGA_SH_ROOT,
        env=_env,
        stdout=_cuga_sh_aio.subprocess.PIPE,
        stderr=_cuga_sh_aio.subprocess.PIPE,
    )
    _out, _err = await _proc.communicate()
    _out_s = _out.decode("utf-8", "replace")
    _err_s = _err.decode("utf-8", "replace")
    if _proc.returncode != 0 and _err_s.strip():
        return _out_s + "\\n[stderr]\\n" + _err_s
    # After a successful install refresh the kernel's import finder cache so
    # newly installed packages are importable immediately without a restart.
    if _proc.returncode == 0 and _CUGA_SH_INSTALL_RE.search(cmd):
        _cuga_sh_importlib.invalidate_caches()
    return _out_s or "(command completed with no output)"
'''

    @staticmethod
    def _filesystem_tools_code(workspace_root: str) -> str:
        """Inline Python definitions for filesystem tools that run directly in the sandbox.

        No HTTP callback to CUGA needed — the agent's kernel already runs inside
        the execd sandbox at ``workspace_root``, so Path operations work directly.
        The signatures match WorkspaceFilesystem so the agent's generated code is identical
        regardless of which backend is active.
        """
        # workspace_root is e.g. /workspace/d57c950e-...
        return f'''
import base64 as _cuga_b64
import fnmatch as _cuga_fnm
import glob as _cuga_glob
import json as _cuga_json
import os as _cuga_os
import ast as _cuga_ast
import textwrap as _cuga_tw
from pathlib import Path as _CugaPath

_CUGA_WS_ROOT = _CugaPath({workspace_root!r})

def _cuga_resolve(path):
    raw = str(path or "").strip().replace("\\\\", "/")
    if not raw or raw in ("/workspace", "."):
        return _CUGA_WS_ROOT
    for pfx in ("/workspace/", "/workspace", "/tmp/"):
        if raw.startswith(pfx):
            raw = raw[len(pfx):]
            break
    import posixpath as _pp
    rel = _pp.normpath(raw.lstrip("/"))
    if rel == ".":
        return _CUGA_WS_ROOT
    if rel == ".." or rel.startswith("../"):
        raise ValueError("path escapes workspace: " + path)
    target = (_CUGA_WS_ROOT / rel).resolve()
    base   = _CUGA_WS_ROOT.resolve()
    if target != base and base not in target.parents:
        raise ValueError("path escapes workspace: " + path)
    return target

def _cuga_public(p):
    rel = _cuga_os.path.relpath(str(p), str(_CUGA_WS_ROOT.resolve()))
    return "/workspace" if rel == "." else "/workspace/" + rel.replace(_cuga_os.sep, "/")

def _cuga_validate_py(content, path):
    try:
        _cuga_ast.parse(content)
        return None
    except SyntaxError as e:
        return str(e)

async def write_file(path, content):
    """Write text content into a file in the workspace."""
    import ast as _a, textwrap as _tw
    p = _cuga_resolve(path)
    if str(path).endswith(".py"):
        dedented = _tw.dedent(content)
        err = _cuga_validate_py(dedented, path)
        if err:
            return "[write_file error] " + err + ". Rewrite and retry."
        content = dedented
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return "File written: " + _cuga_public(p) + " (" + str(len(content)) + " chars)"

async def read_file(path, start_line=None, end_line=None, grep_pattern=None):
    """Read a text file from the workspace."""
    p = _cuga_resolve(path)
    if not p.is_file():
        return "[read_file error] File not found: " + str(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    if start_line is not None or end_line is not None:
        lines = text.splitlines(keepends=True)
        s = (start_line or 1) - 1
        e = end_line or len(lines)
        text = "".join(lines[s:e])
    if grep_pattern:
        import re as _re
        try:
            pat = _re.compile(grep_pattern)
            text = "\\n".join("LINE|" + ln for ln in text.splitlines() if pat.search(ln))
        except Exception as _ge:
            return "[read_file error] invalid grep_pattern: " + str(_ge)
    return text

async def list_files(path=".", pattern="*"):
    """List files and directories in the workspace as JSON."""
    import json as _j
    p = _cuga_resolve(path)
    if p == _CUGA_WS_ROOT:
        p.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        return "[list_files error] Path not found: " + str(path)
    entries = []
    for child in sorted(p.glob(pattern)):
        entries.append({{
            "name": child.name,
            "path": _cuga_public(child),
            "is_dir": child.is_dir(),
            "size_bytes": child.stat().st_size if child.is_file() else 0,
        }})
    return _j.dumps({{"sandbox_path": str(path), "entries": entries}})

async def make_directory(path):
    """Create a directory in the workspace."""
    p = _cuga_resolve(path)
    p.mkdir(parents=True, exist_ok=True)
    return "Directory created: " + _cuga_public(p)

async def move_file(source, destination):
    """Move a file within the workspace."""
    src = _cuga_resolve(source)
    dst = _cuga_resolve(destination)
    if dst.exists():
        return "[move_file error] Destination already exists: " + str(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _cuga_os.rename(str(src), str(dst))
    return "Moved " + _cuga_public(src) + " to " + _cuga_public(dst)

async def search_files(path, pattern, excludePatterns=None):
    """Search files in the workspace."""
    base = _cuga_resolve(path)
    exclude = excludePatterns or []
    results = []
    if "**" in pattern:
        for m in _cuga_glob.glob(str(base / pattern), recursive=True):
            rel = _cuga_os.path.relpath(m, str(base))
            if not any(_cuga_fnm.fnmatch(rel, ex) for ex in exclude):
                results.append(_cuga_public(_CugaPath(m)))
    else:
        for item in sorted(_cuga_os.listdir(str(base))):
            if _cuga_fnm.fnmatch(item, pattern) and not any(_cuga_fnm.fnmatch(item, ex) for ex in exclude):
                results.append(_cuga_public(base / item))
    return "\\n".join(results)

async def get_file_info(path):
    """Get metadata for a file in the workspace."""
    p = _cuga_resolve(path)
    st = p.stat()
    from datetime import datetime as _dt
    return "\\n".join([
        "path: " + _cuga_public(p),
        "size: " + str(st.st_size),
        "isDirectory: " + str(p.is_dir()),
        "isFile: " + str(p.is_file()),
        "modified: " + _dt.fromtimestamp(st.st_mtime).isoformat(),
    ])

async def edit_file(path, edits, dryRun=False):
    """Make exact-text edits to a file."""
    p = _cuga_resolve(path)
    if not p.is_file():
        return "[edit_file error] File not found: " + str(path)
    content = p.read_text(encoding="utf-8", errors="replace")
    new_content = content
    applied = []
    for edit in (edits or []):
        old, new = edit.get("oldText", ""), edit.get("newText", "")
        if content.count(old) != 1:
            return "[edit_file error] oldText not found exactly once: " + repr(old[:80])
        new_content = new_content.replace(old, new, 1)
        applied.append("- " + repr(old[:40]) + " → " + repr(new[:40]))
    if not dryRun and new_content != content:
        p.write_text(new_content, encoding="utf-8")
    return ("DRY RUN\\n" if dryRun else "") + "\\n".join(applied)
'''

    async def execute_for_cuga_lite(
        self,
        wrapped_code: str,
        context_locals: dict[str, Any],
        state: AgentState,
        thread_id: Optional[str] = None,
        apps_list: Optional[List[str]] = None,
    ) -> tuple[str, dict[str, Any]]:
        if context_locals is None:
            context_locals = {}

        self._remember_scope(thread_id, state)
        tool_names = [k for k, v in context_locals.items() if callable(v) and not k.startswith("_")]
        logger.info(
            f"[execd:route] execute_for_cuga_lite thread={thread_id} "
            f"tools={tool_names} variables={list(state.variables_manager.get_variable_names()) if state and state.variables_manager else []}"
        )

        try:
            var_manager = state.variables_manager if state else VariablesManager()
            variables_code = block_runner.variables_code(
                {name: var_manager.get_variable(name) for name in var_manager.get_variable_names()}
            )
            workspace = self._workspace_path(thread_id)

            # Filesystem tools and run_command run in the kernel itself, next to the
            # files and the venv; every other callable is answered by CUGA. The
            # in-kernel ones still charge the call budget through a callback.
            in_kernel: dict[str, Any] = {}
            remote: dict[str, Any] = {}
            for name, value in context_locals.items():
                if callable(value) and getattr(value, "_cuga_app_name", None) == "filesystem":
                    in_kernel[name] = value
                elif name == "run_command" and callable(value):
                    in_kernel[name] = value
                elif callable(value) or inspect.ismodule(value) or _is_callable_table(value):
                    remote[name] = value
            if in_kernel:
                # In-kernel tools bypass the counted wrappers; they charge through this callback.
                remote[_CHARGE_NAME] = _budget_charge()
            fs_code = (
                self._filesystem_tools_code(workspace) if any(n != "run_command" for n in in_kernel) else ""
            )
            shell_code = (
                self._shell_tool_code(
                    workspace, getattr(settings.advanced_features, "execd_package_index", "") or ""
                )
                if "run_command" in in_kernel
                else ""
            )

            # int() cast: the value is substituted into a code template. A
            # non-int would inject syntactically broken Python and surface as a
            # NameError inside the sandbox instead of failing here.
            sandbox_timeout = _block_timeout()
            tool_timeout = float(getattr(settings.advanced_features, "tool_call_timeout", 30) or 30)

            async with tool_callback.registered_block(remote, thread_id, sandbox_timeout) as block:
                url = tool_callback.callback_url()
                if remote and not url:
                    raise RuntimeError(
                        "Tools cannot be called from the execd sandbox: set "
                        "advanced_features.sandbox_callback_url (or server_ports.function_call_host) "
                        "to an address of this server the sandbox can reach"
                    )
                tools_code = (
                    tool_callback.stub_code(
                        remote,
                        url=url,
                        block_id=block.block_id,
                        token=block.token,
                        timeout=sandbox_timeout + tool_timeout,
                    )
                    if remote
                    else ""
                )

                charged_code = _charged_tools_code(sorted(in_kernel)) if in_kernel else ""

                complete_code = f"""
import asyncio
{fs_code}
{shell_code}
{tools_code}
{charged_code}
{variables_code}
{wrapped_code}
{block_runner.runner_code(sandbox_timeout)}"""

                logger.debug(
                    f"[execd:route] Assembled code for execd "
                    f"vars={var_manager.get_variable_count()} tools={len(tool_names)} "
                    f"remote_callables={len(remote)} code_len={len(complete_code)}"
                )

                t0 = time.monotonic()
                try:
                    raw = await self._run_remote(complete_code, thread_id)
                finally:
                    # A tool paused the turn (e.g. ACP permission): the block ended
                    # in the sandbox, and the pause continues to the graph from here,
                    # ahead of the kernel's own error for the interrupted block.
                    block.raise_pending_signal()
                elapsed = int((time.monotonic() - t0) * 1000)

            outcome = block_runner.parse_outcome(raw)
            if outcome is None:
                # The runner never reported: the kernel died or the block could not compile.
                logger.warning(f"[execd:route] No block outcome thread={thread_id} duration_ms={elapsed}")
                return block_runner.strip_marker(raw) or "<code ran, no output printed to stdout>", {}
            result_locals = outcome.get("locals") or {}
            logger.info(
                f"[execd:route] {outcome.get('status')} execute_for_cuga_lite thread={thread_id} "
                f"new_vars={list(result_locals.keys())} duration_ms={elapsed}"
            )
            return block_runner.render(outcome, timeout=sandbox_timeout), result_locals

        except Exception as e:
            raise RuntimeError(f"execd sandbox execution failed: {e}")

    async def execute_for_code_agent(self, wrapped_code, state, thread_id=None):
        # The CodeAgent graph that called this path was retired (#580); CugaLite and
        # the supervisor both execute through execute_for_cuga_lite.
        raise NotImplementedError("execd runs agent code through execute_for_cuga_lite only")
