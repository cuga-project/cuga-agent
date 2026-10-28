"""How execd runs code and commands: context reuse, the event stream, failures and recovery."""

import json
import re

import httpx
import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import ExecdExecutor, connection
from cuga.config import settings

pytestmark = pytest.mark.unit


# The context bootstrap is itself a POST /code; it is told apart from the agent's code by this line.
_BOOTSTRAP_MARK = '_ws = os.path.join("/workspace"'


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events)


class _Sandbox:
    """sandbox-api double: one kernel context per POST /code/context, scripted /code and /command replies."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.contexts = 0
        self.code_replies: list[httpx.Response] = []
        self.command_reply = httpx.Response(200, text=_stream({"type": "stdout", "text": "ok"}))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/code/context":
            self.contexts += 1
            return httpx.Response(200, json={"id": f"ctx-{self.contexts}"})
        if path == "/code":
            body = json.loads(request.content)
            if _BOOTSTRAP_MARK in body["code"]:
                return httpx.Response(200, text=_stream({"type": "execution_complete"}))
            if self.code_replies:
                return self.code_replies.pop(0)
            return httpx.Response(200, text=_stream({"type": "stdout", "text": "ok"}))
        if path == "/command":
            return self.command_reply
        return httpx.Response(204)

    def user_code_requests(self) -> list[dict]:
        bodies = [json.loads(r.content) for r in self.requests if r.url.path == "/code"]
        return [b for b in bodies if _BOOTSTRAP_MARK not in b["code"]]


@pytest.fixture
def sandbox(monkeypatch):
    fake = _Sandbox()

    def client(cls, timeout, thread=None):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handler), timeout=timeout, headers=cls._auth_headers(thread)
        )

    monkeypatch.setattr(ExecdExecutor, "_client", classmethod(client))
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", "", raising=False)
    monkeypatch.setattr(settings.advanced_features, "execd_url", "https://sandbox.example", raising=False)
    monkeypatch.setattr(settings.advanced_features, "execd_api_key", "K", raising=False)
    connection.set_runtime_config({"enabled": True})
    ExecdExecutor._contexts.clear()
    ExecdExecutor._thread_of_context.clear()
    ExecdExecutor._contexts_url = None
    yield fake
    connection.set_runtime_config(None)
    ExecdExecutor._contexts.clear()
    ExecdExecutor._thread_of_context.clear()
    ExecdExecutor._contexts_url = None


@pytest.mark.asyncio
async def test_a_thread_keeps_its_kernel_and_threads_do_not_share_one(sandbox):
    await ExecdExecutor._run_remote("a = 1", "thread-a")
    await ExecdExecutor._run_remote("print(a)", "thread-a")
    await ExecdExecutor._run_remote("print(1)", "thread-b")

    assert sandbox.contexts == 2
    ids = [b["context"]["id"] for b in sandbox.user_code_requests()]
    assert ids == ["ctx-1", "ctx-1", "ctx-2"]


@pytest.mark.asyncio
async def test_only_output_events_reach_the_result(sandbox):
    sandbox.code_replies.append(
        httpx.Response(
            200,
            text=_stream(
                {"type": "init"},
                {"type": "ping"},
                {"type": "stdout", "text": "hello "},
                {"type": "stderr", "text": "warn "},
                {"type": "stdout", "text": "world"},
                {"type": "execution_complete"},
            )
            + "\nnot json\n",
        )
    )

    assert await ExecdExecutor._run_remote("print('x')", "thread-a") == "hello warn world"


@pytest.mark.asyncio
async def test_a_kernel_traceback_is_raised_with_its_name_and_value(sandbox):
    sandbox.code_replies.append(
        httpx.Response(
            200,
            text=_stream(
                {
                    "type": "error",
                    "error": {
                        "ename": "ZeroDivisionError",
                        "evalue": "division by zero",
                        "traceback": ["t1"],
                    },
                }
            ),
        )
    )

    with pytest.raises(RuntimeError, match="ZeroDivisionError: division by zero"):
        await ExecdExecutor._run_remote("1/0", "thread-a")


@pytest.mark.asyncio
async def test_a_lost_kernel_is_recreated_and_the_code_retried_once(sandbox):
    await ExecdExecutor._run_remote("a = 1", "thread-a")
    sandbox.code_replies.append(httpx.Response(404, json={"code": "kernel_not_found", "recoverable": True}))

    assert await ExecdExecutor._run_remote("print(1)", "thread-a") == "ok"
    assert sandbox.contexts == 2
    assert ExecdExecutor._contexts["thread-a"] == "ctx-2"


@pytest.mark.asyncio
async def test_other_http_errors_are_not_retried(sandbox):
    sandbox.code_replies.append(httpx.Response(401, json={"detail": "bad key"}))

    with pytest.raises(httpx.HTTPStatusError):
        await ExecdExecutor._run_remote("print(1)", "thread-a")
    assert sandbox.contexts == 1


@pytest.mark.asyncio
async def test_changing_the_endpoint_drops_contexts_of_the_old_one(sandbox, monkeypatch):
    await ExecdExecutor._run_remote("a = 1", "thread-a")
    monkeypatch.setattr(settings.advanced_features, "execd_url", "https://other.example", raising=False)

    await ExecdExecutor._run_remote("print(1)", "thread-a")

    assert sandbox.contexts == 2
    assert sandbox.requests[-1].url.host == "other.example"


@pytest.mark.asyncio
async def test_command_runs_in_the_thread_venv_and_reports_exit_as_output(sandbox, monkeypatch):
    monkeypatch.setattr(
        settings.advanced_features, "execd_package_index", "https://pypi.example/simple", raising=False
    )
    sandbox.command_reply = httpx.Response(
        200,
        text=_stream(
            {"type": "stdout", "text": "line1"},
            {"type": "stdout", "text": "line2"},
            {"type": "stderr", "text": "boom"},
            {"type": "error", "error": {"ename": "CommandError", "evalue": "exit status 2"}},
        ),
    )

    stdout, stderr, failed = await ExecdExecutor._run_shell_command("make", "thread-a")

    assert failed
    assert stdout == "line1\nline2"
    assert stderr == "boom\n[exit] exit status 2"
    body = json.loads(next(r for r in sandbox.requests if r.url.path == "/command").content)
    venv = "/workspace/thread-a/.venv"
    assert body["cwd"] == "/workspace/thread-a"
    assert body["envs"]["VIRTUAL_ENV"] == venv and body["envs"]["UV_PYTHON"] == f"{venv}/bin/python"
    assert body["envs"]["UV_INDEX_URL"] == "https://pypi.example/simple"
    # sandbox-api exports envs inside the thread's confinement; values stay off the command line.
    assert body["command"] == "make"


@pytest.mark.parametrize(
    "thread_id",
    ["thread-a", "c36d2ccb-bffa-4589-987e-bdded531de11", "_default", "x" * 64],
)
def test_ids_sandbox_api_keeps_are_sent_unchanged(thread_id):
    # Existing conversations keep their workspace: UUIDs map to themselves.
    assert ExecdExecutor._workspace_name(thread_id) == thread_id


@pytest.mark.parametrize("thread_id", ["../../etc", "a/b c", "victim#x", "a.b", "zażółć", "", "x" * 80])
def test_other_ids_become_safe_hashed_names(thread_id):
    name = ExecdExecutor._workspace_name(thread_id)
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
    assert name != thread_id
    assert ExecdExecutor._workspace_name(thread_id) == name


@pytest.mark.parametrize(
    "a, b",
    [
        ("a.b", "a-b"),
        ("a.b", "a/b"),
        ("c36d2ccb.bffa-4589-987e-bdded531de11", "c36d2ccb-bffa-4589-987e-bdded531de11"),
        ("x" * 64 + "1", "x" * 64 + "2"),
        ("x" * 65, "x" * 64),
    ],
)
def test_ids_sandbox_api_would_merge_get_different_workspaces(a, b):
    # sandbox-api maps both of each pair to one directory; the sent ids must differ.
    assert ExecdExecutor._workspace_name(a) != ExecdExecutor._workspace_name(b)


def test_a_missing_url_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "sandbox_secret_dir", "", raising=False)
    monkeypatch.setattr(settings.advanced_features, "execd_url", "", raising=False)
    connection.set_runtime_config({"enabled": True})
    try:
        with pytest.raises(RuntimeError, match="Sandbox URL is not configured"):
            ExecdExecutor._base_url()
    finally:
        connection.set_runtime_config(None)


@pytest.mark.asyncio
async def test_stream_reading_stops_at_completion_without_waiting_for_close(sandbox):
    """sandbox-api keeps /command open ~1s after the command ends; nothing past execution_complete is read."""
    sandbox.command_reply = httpx.Response(
        200,
        text=_stream(
            {"type": "stdout", "text": "hi"},
            {"type": "execution_complete"},
            {"type": "stdout", "text": "after-complete"},
        ),
    )
    out, err, failed = await ExecdExecutor._run_shell_command("echo hi", "thread-a")
    assert out == "hi" and not failed


@pytest.mark.asyncio
async def test_a_bare_runtime_error_in_the_stream_is_a_failure_not_empty_output(sandbox):
    """execd answers a busy kernel with {"code", "message"}; that must surface, not read as no output."""
    sandbox.code_replies.append(
        httpx.Response(200, text=_stream({"code": "RUNTIME_ERROR", "message": "session is busy"}))
    )
    with pytest.raises(RuntimeError, match="session is busy"):
        await ExecdExecutor._run_remote("print(1)", "thread-a")


@pytest.mark.asyncio
async def test_a_new_kernel_gets_the_skill_folders_in_one_command(sandbox, monkeypatch, tmp_path):
    """load_skill names /workspace/skills/<name>/; the companion files must be there for read_file/run_command."""
    import base64
    import io
    import tarfile

    skill = tmp_path / "skills" / "pdf-tools"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: pdf\ndescription: d\n---\nRun scripts/convert.py")
    (skill / "scripts" / "convert.py").write_text("print('converted')")
    (skill / "scripts" / "cache.pyc").write_bytes(b"\x00")
    monkeypatch.setattr(settings.skills, "enabled", True, raising=False)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path))

    await ExecdExecutor._run_remote("print(1)", "thread-a")
    await ExecdExecutor._run_remote("print(2)", "thread-a")  # existing kernel: no second copy

    bodies = [json.loads(r.content) for r in sandbox.requests if r.url.path == "/command"]
    copies = [b for b in bodies if "CUGA_SKILLS_TGZ" in b["command"]]
    assert len(copies) == 1
    encoded = copies[0]["envs"]["CUGA_SKILLS_TGZ"]
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(encoded))) as tar:
        names = sorted(tar.getnames())
    # Named by the skill's name (what load_skill tells the agent), not the folder; .pyc skipped.
    assert names == ["skills/pdf/SKILL.md", "skills/pdf/scripts/convert.py"]


@pytest.mark.asyncio
async def test_no_skills_no_copy(sandbox, monkeypatch):
    monkeypatch.setattr(settings.skills, "enabled", False, raising=False)
    await ExecdExecutor._run_remote("print(1)", "thread-a")
    commands = [json.loads(r.content)["command"] for r in sandbox.requests if r.url.path == "/command"]
    assert not any("CUGA_SKILLS_TGZ" in c for c in commands)


@pytest.mark.asyncio
async def test_has_context_reports_a_kernel_without_creating_one(sandbox):
    assert ExecdExecutor.has_context("thread-a") is False
    assert sandbox.contexts == 0

    await ExecdExecutor._run_remote("print(1)", "thread-a")
    assert ExecdExecutor.has_context("thread-a") is True

    await ExecdExecutor.release_thread("thread-a")
    assert ExecdExecutor.has_context("thread-a") is False
