"""events_up.sh must boot (exit 0) when optional .env keys / tunnel logs are absent (see #984).

WHY THIS EXISTS
---------------
The script runs under `set -euo pipefail`. `env_val` and `cuga_tunnel_url` end in a `grep` whose
exit status 1 ("no match") is a normal outcome, not an error. With pipefail that status escaped the
`$(...)` and aborted the script silently, e.g. on `make up-noap` with no `EVENTS_NGROK_DOMAIN`.

The test runs a COPY of the script in a throw-away repo layout. Every external command (uv, curl,
pkill, ...) is a stub on PATH, and RUN is redirected, so nothing real is started or killed.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "events" / "scripts" / "events_up.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required to execute events_up.sh")

# `$REAL_SLEEP` is exported by _run(); it is the genuine sleep binary, resolved before the stub dir
# is put on PATH. The `sleep` stub shortens the script's boot-wait loops to ~nothing.
STUBS = {
    "uv": "exit 0",
    "curl": "exit 0",  # every health probe succeeds immediately
    "pkill": "exit 0",  # never kill real processes
    "sleep": 'exec "$REAL_SLEEP" 0.01',
    "ngrok": "echo 'started tunnel'",
    "cloudflared": "echo 'https://stub-demo.trycloudflare.com'",
}
# Fake long-running services must stay alive through the script's `kill -0 <pid>` liveness checks.
VENV_STUBS = {"cuga": '"$REAL_SLEEP" 20', "python": '"$REAL_SLEEP" 20'}


def _write_exe(path: pathlib.Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _make_repo(root: pathlib.Path, dotenv: str | None) -> pathlib.Path:
    (root / "events" / "scripts").mkdir(parents=True)
    script = root / "events" / "scripts" / "events_up.sh"
    text = SCRIPT.read_text(encoding="utf-8")
    # Redirect the hard-coded run dir into the sandbox, and put the stub dir first on PATH from inside
    # the script (some shells, e.g. Git Bash, re-order PATH at startup and would shadow the stubs).
    run_dir = (root / "run").as_posix()
    text = text.replace("RUN=/tmp/events_up", f'RUN="{run_dir}"')
    text = text.replace('REPO="$(pwd)"', 'REPO="$(pwd)"\nexport PATH="$REPO/bin:$PATH"', 1)
    script.write_text(text, encoding="utf-8", newline="\n")
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    (root / ".venv" / "bin").mkdir(parents=True)
    for name, body in VENV_STUBS.items():
        _write_exe(root / ".venv" / "bin" / name, body)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        _write_exe(bin_dir / name, body)
    if dotenv is not None:
        (root / ".env").write_text(dotenv, encoding="utf-8", newline="\n")
    return script


def _real_sleep() -> str:
    out = subprocess.run([BASH, "-c", "command -v sleep"], capture_output=True, text=True, timeout=10)
    return out.stdout.strip() or "sleep"


def _run(root: pathlib.Path, script: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "REAL_SLEEP": _real_sleep(),
    }
    return subprocess.run(
        [BASH, str(script), *args],
        cwd=root,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


DOTENVS = {
    "no-dotenv-file": None,
    "empty-dotenv": "",
    "key-absent": "FOO=bar\nAP_BASE_URL=http://localhost:8081\n",
    "key-commented-out": "# EVENTS_NGROK_DOMAIN=demo.ngrok.app\n",
    "key-empty": "EVENTS_NGROK_DOMAIN=\n",
}


@pytest.mark.parametrize("dotenv", DOTENVS.values(), ids=DOTENVS.keys())
@pytest.mark.parametrize("flags", [["--no-tunnel"], []], ids=["no-tunnel", "cloudflared-quick-tunnel"])
def test_boot_succeeds_without_ngrok_domain(tmp_path, dotenv, flags):
    script = _make_repo(tmp_path, dotenv)

    proc = _run(tmp_path, script, *flags)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "next: 'make channels'" in proc.stdout
    assert "No such file or directory" not in proc.stderr + proc.stdout


@pytest.mark.parametrize("dotenv", DOTENVS.values(), ids=DOTENVS.keys())
def test_status_succeeds_without_tunnel_log(tmp_path, dotenv):
    script = _make_repo(tmp_path, dotenv)

    proc = _run(tmp_path, script, "--status")

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "CUGA tunnel: none" in proc.stdout


@pytest.mark.parametrize("dotenv", DOTENVS.values(), ids=DOTENVS.keys())
def test_public_url_succeeds_without_tunnel_log(tmp_path, dotenv):
    script = _make_repo(tmp_path, dotenv)

    proc = _run(tmp_path, script, "--public-url")

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "PUBLIC URL" in proc.stdout


@pytest.mark.parametrize("dotenv", DOTENVS.values(), ids=DOTENVS.keys())
def test_reload_succeeds_without_tunnel_log(tmp_path, dotenv):
    script = _make_repo(tmp_path, dotenv)
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "cuga.pid").write_text("999999", encoding="utf-8")  # a "prior run" marker

    proc = _run(tmp_path, script, "--reload")

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "reloaded." in proc.stdout


@pytest.mark.parametrize(
    "line",
    [
        "EVENTS_NGROK_DOMAIN=demo.ngrok.app",
        'EVENTS_NGROK_DOMAIN="demo.ngrok.app"',
        "EVENTS_NGROK_DOMAIN=demo.ngrok.app   # reserved domain",
        "EVENTS_NGROK_DOMAIN=old.ngrok.app\nEVENTS_NGROK_DOMAIN=demo.ngrok.app",
    ],
    ids=["plain", "quoted", "inline-comment", "duplicate-last-wins"],
)
def test_configured_ngrok_domain_is_still_honoured(tmp_path, line):
    script = _make_repo(tmp_path, line + "\n")

    proc = _run(tmp_path, script, "--status")

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "CUGA tunnel: https://demo.ngrok.app" in proc.stdout
    assert "(ngrok static)" in proc.stdout


def test_boot_with_ngrok_domain_uses_ngrok_branch(tmp_path):
    script = _make_repo(tmp_path, "EVENTS_NGROK_DOMAIN=demo.ngrok.app\n")

    proc = _run(tmp_path, script)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "ngrok STATIC: demo.ngrok.app" in proc.stdout
    assert "https://demo.ngrok.app" in proc.stdout
