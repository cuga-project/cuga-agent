"""Unit tests for AppWorld file_system path normalization (#730).

The normalizer must rewrite the malformed path family observed on the
AppWorld hard suite (cwd-relative ``./...``, bare relative, ``/./`` echoes,
double slashes) to canonical ``~/``/absolute form, leave already-canonical
paths untouched, stay inert outside the appworld benchmark and the
file_system app, and run before the rejected-call guard computes signatures.
"""

import json
from types import SimpleNamespace

import pytest

from cuga.backend.tools_env.registry.registry.appworld_path_normalizer import (
    normalize_appworld_path,
    normalize_file_system_path_args,
)


def _set_settings(monkeypatch, benchmark="appworld", **extra):
    """Pin cuga.config.settings (read lazily inside the normalizer)."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(advanced_features=SimpleNamespace(benchmark=benchmark, **extra)),
    )


# ── normalize_appworld_path ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Evidence family from #730: cwd-relative paths the model actually sent.
        ("./downloads/habit_tracker.csv", "~/downloads/habit_tracker.csv"),
        ("./owe_list.csv", "~/owe_list.csv"),
        (".", "~/"),
        ("./", "~/"),
        # Server-side echo of a relative path (what evaluation sees stored).
        ("/./documents/work/recruiting", "~/documents/work/recruiting"),
        ("/.", "~/"),
        # Double slashes — AppWorld rejects these outright with a 422 (#599 exemplar).
        ("~/documents//file.txt", "~/documents/file.txt"),
        ("~//downloads///x.csv", "~/downloads/x.csv"),
        # Bare relative: AppWorld has no cwd, so home is the only sane anchor.
        ("owe_list.csv", "~/owe_list.csv"),
        ("downloads/x.csv", "~/downloads/x.csv"),
        # Dot-segment resolution, never escaping the anchor.
        ("~/a/../b.csv", "~/b.csv"),
        ("~/a/./b.csv", "~/a/b.csv"),
        ("../x.csv", "~/x.csv"),
        ("/a/../../b.csv", "/b.csv"),
        # Bare tilde: AppWorld itself would mangle it to "/~".
        ("~", "~/"),
        # Whitespace is stripped (AppWorld strips too, but after the "//" check).
        ("  ./x.csv  ", "~/x.csv"),
        # Trailing slash on a directory path survives.
        ("./documents/", "~/documents/"),
    ],
)
def test_malformed_paths_are_canonicalized(raw, expected):
    assert normalize_appworld_path(raw) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "~/downloads/habit_tracker.csv",
        "~/documents/work/recruiting/resume.pdf",
        "~/",
        "/home/user/x.csv",
        "/",
    ],
)
def test_canonical_paths_pass_through_unchanged(value):
    assert normalize_appworld_path(value) == value


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        # Windows-style separators: rewriting them would be guesswork.
        ".\\downloads\\x.csv",
        "~\\x.csv",
        # Empty / non-string values are out of scope.
        "",
        "   ",
        None,
        42,
    ],
)
def test_out_of_scope_values_untouched(value):
    assert normalize_appworld_path(value) == value


@pytest.mark.unit
def test_pure_string_logic_is_host_os_independent():
    """The output must be POSIX regardless of host: no os.path/ntpath
    separator flips, no expanduser injection of the host home directory."""
    import os.path

    result = normalize_appworld_path("./downloads/x.csv")
    assert result == "~/downloads/x.csv"
    assert os.path.expanduser("~") not in result


# ── normalize_file_system_path_args (scoping) ──────────────────────────────

ARGS = {"file_path": "./downloads/x.csv", "content": "a,b\n1,2\n", "page_index": 0}


@pytest.mark.unit
def test_only_path_keys_rewritten(monkeypatch):
    _set_settings(monkeypatch)
    args, changes = normalize_file_system_path_args("file_system", ARGS)
    assert args["file_path"] == "~/downloads/x.csv"
    assert args["content"] == ARGS["content"]  # non-path key untouched
    assert changes == {"file_path": ("./downloads/x.csv", "~/downloads/x.csv")}
    # The input dict is not mutated; a new dict is returned.
    assert ARGS["file_path"] == "./downloads/x.csv"


@pytest.mark.unit
def test_all_path_suffix_keys_covered(monkeypatch):
    """file_system's API uses *_path names throughout (source_file_path,
    destination_directory_path, compressed_file_path, ...)."""
    _set_settings(monkeypatch)
    raw = {
        "source_file_path": "./a.csv",
        "destination_file_path": "docs/b.csv",
        "directory_path": ".",
    }
    args, changes = normalize_file_system_path_args("file_system", raw)
    assert args == {
        "source_file_path": "~/a.csv",
        "destination_file_path": "~/docs/b.csv",
        "directory_path": "~/",
    }
    assert set(changes) == set(raw)


@pytest.mark.unit
def test_other_apps_never_touched(monkeypatch):
    """Production MCP tools (e.g. the sandbox filesystem server) document
    relative paths as the correct convention — they must not be rewritten."""
    _set_settings(monkeypatch)
    args, changes = normalize_file_system_path_args("filesystem", dict(ARGS))
    assert args["file_path"] == "./downloads/x.csv"
    assert changes == {}


@pytest.mark.unit
def test_other_benchmarks_never_touched(monkeypatch):
    _set_settings(monkeypatch, benchmark="default")
    args, changes = normalize_file_system_path_args("file_system", dict(ARGS))
    assert args["file_path"] == "./downloads/x.csv"
    assert changes == {}


@pytest.mark.unit
def test_no_changes_returns_original_object(monkeypatch):
    _set_settings(monkeypatch)
    clean = {"file_path": "~/downloads/x.csv"}
    args, changes = normalize_file_system_path_args("file_system", clean)
    assert args is clean
    assert changes == {}


# ── Route integration: normalization runs before the #599 guard ────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_normalizes_before_guard_and_call(monkeypatch):
    """Two facts pinned here: (1) the registry receives the canonical path,
    not the model's malformed one; (2) guard signatures are computed on
    canonical args, so a malformed and an already-canonical spelling of the
    same call share one rejection counter."""
    from cuga.backend.tools_env.registry.registry import api_registry_server as srv
    from cuga.backend.tools_env.registry.registry.rejected_call_guard import RejectedCallGuard

    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(
                benchmark="appworld",
                rejected_call_escalate_after=1,
                rejected_call_block_after=2,
            )
        ),
    )
    monkeypatch.setattr(srv, "rejected_call_guard", RejectedCallGuard())
    monkeypatch.setattr(srv, "database_mode", False)

    rejection_text = json.dumps(
        {
            "status": "exception",
            "error_type": "HTTPError",
            "message": "File not found.",
            "status_code": 404,
            "method": "GET",
        }
    )

    class FakeText:
        def __init__(self, text):
            self.text = text

    seen_args = []

    class FakeReg:
        async def show_apis_for_app(self, app_name):
            return {"show_file": {"secure": False, "method": "GET", "path": "/file"}}

        async def call_function(self, **kwargs):
            seen_args.append(kwargs["arguments"])
            return [FakeText(rejection_text)]

    monkeypatch.setattr(srv, "registry", FakeReg(), raising=False)
    monkeypatch.setattr(srv, "mcp_manager", SimpleNamespace(auth_config={}), raising=False)

    malformed = srv.FunctionCallRequest(
        app_name="file_system", function_name="show_file", args={"file_path": "./owe_list.csv"}
    )
    first = await srv.call_mcp_function(malformed)
    assert first["status"] == "exception"
    assert seen_args[-1]["file_path"] == "~/owe_list.csv"  # canonical on the wire

    # Same logical call, already-canonical spelling: shares the signature,
    # so this second rejection escalates.
    canonical = srv.FunctionCallRequest(
        app_name="file_system", function_name="show_file", args={"file_path": "~/owe_list.csv"}
    )
    second = await srv.call_mcp_function(canonical)
    assert "[Repeated failure]" in second["message"]
