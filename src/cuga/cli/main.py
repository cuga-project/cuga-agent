#!/usr/bin/env python3
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
import psutil
import typer
from loguru import logger
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cuga.config import PACKAGE_ROOT, TRAJECTORY_DATA_DIR, get_user_data_path, settings
from cuga.configurations.instructions_manager import InstructionsManager
from cuga.backend.cuga_graph.policy.cli import app as policy_app
from cuga.backend.server.demo_manage_setup import (
    build_tools_from_apps,
    get_default_apps_for_preset,
    setup_demo_manage_config,
)
from cuga.backend.server.managed_mcp import ensure_managed_mcp_file_exists, get_managed_mcp_path
from cuga.cli.app_manager import AppManager

instructions_manager = InstructionsManager()


def _build_workspace_policies(workspace_abs: str, include_email: bool = False) -> str:
    """Build full policy content for workspace: filesystem scope, cuga knowledge, email templates."""
    policy = f"""## Plan
For the filesystem application: write or read files only from `{workspace_abs}`
when user asks questions about cuga then answer the question by first reading the filesystem information inside the file `{workspace_abs}/cuga_knowledge.md` then answer the question
When user asks to use email templates assume it has <results> placeholder to replace with the results
The email of my assistant is jane@example.com"""
    if include_email:
        policy += "\nFor the email application: send emails only using the local SMTP sink"
    return policy


def _demo_uses_ssl() -> bool:
    return bool(os.environ.get("SSL_KEYFILE", "").strip() and os.environ.get("SSL_CERTFILE", "").strip())


def _demo_port() -> int:
    return int(os.environ.get("DYNACONF_SERVER_PORTS__DEMO", str(settings.server_ports.demo)))


def _make_app_manager() -> AppManager:
    sp = settings.server_ports
    return AppManager(
        process_registry=direct_processes,
        run_service=lambda n, c, e: run_direct_service(n, c, env_vars=e),
        kill_ports=kill_processes_by_port,
        kill_process=kill_process_tree,
        wait_tcp=lambda p, lbl, r, i: wait_for_tcp_port(p, lbl, max_retries=r, retry_interval=i),
        wait_http=lambda p, n: wait_for_server(
            p,
            n,
            max_retries=int(sp.demo_server_startup_max_retries) if p == _demo_port() else 240,
            https=_demo_uses_ssl() and p == _demo_port(),
        ),
    )


def _apply_demo_skills_env() -> None:
    """Turn on skills + shell tools for spawned demo/registry (Dynaconf-style env)."""
    os.environ["DYNACONF_SKILLS__ENABLED"] = "true"
    os.environ["DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL"] = "true"
    os.environ["DYNACONF_ADVANCED_FEATURES__REFLECTION_ENABLED"] = "true"

    sandbox_mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
    if sandbox_mode in ("native", "local"):
        os.environ["DYNACONF_ADVANCED_FEATURES__SANDBOX_MODE"] = sandbox_mode
        os.environ["DYNACONF_ADVANCED_FEATURES__OPENSANDBOX_SANDBOX"] = "false"
    else:
        os.environ["DYNACONF_ADVANCED_FEATURES__OPENSANDBOX_SANDBOX"] = "true"


def _apply_local_demo_workspace_env() -> None:
    """Demos that use ./cuga_workspace with runtime filesystem tools — not OpenSandbox /tmp paths from settings.toml."""
    os.environ["DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL"] = "false"
    os.environ["DYNACONF_ADVANCED_FEATURES__OPENSANDBOX_SANDBOX"] = "false"
    os.environ["DYNACONF_SKILLS__ENABLED"] = "false"


def _find_pyproject_root() -> Optional[Path]:
    """Walk upward from the package to find a directory containing pyproject.toml."""
    p = Path(PACKAGE_ROOT).resolve()
    for _ in range(10):
        if (p / "pyproject.toml").is_file():
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def _uv_sync_opensandbox_extra() -> None:
    """Install optional OpenSandbox client deps when running from a git checkout (uv sync --extra opensandbox)."""
    root = _find_pyproject_root()
    if root is None:
        logger.debug("No pyproject.toml found above package root; skip uv sync --extra opensandbox")
        return
    logger.info("Syncing optional OpenSandbox dependencies (uv sync --extra opensandbox)...")
    try:
        subprocess.run(
            ["uv", "sync", "--extra", "opensandbox"],
            cwd=str(root),
            check=True,
        )
    except FileNotFoundError:
        console.print(
            "[yellow]uv not found on PATH. Install OpenSandbox extras manually:[/yellow] "
            "[cyan]uv sync --extra opensandbox[/cyan]"
        )
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        logger.error("uv sync --extra opensandbox failed (exit %s)", e.returncode)
        raise typer.Exit(1) from e


def _opensandbox_host_port() -> Tuple[str, int]:
    # Align with OpenSandboxExecutor._get_connection_config: domain from settings.skills.opensandbox_domain
    # (see opensandbox_executor.py), then env overrides used by Dynaconf/CLI.
    raw = (
        (getattr(settings.skills, "opensandbox_domain", None) or "").strip()
        or (os.environ.get("OPEN_SANDBOX_DOMAIN") or "").strip()
        or (os.environ.get("DYNACONF_SKILLS__OPENSANDBOX_DOMAIN") or "").strip()
        or "localhost:8080"
    )
    if ":" in raw:
        host, port_s = raw.rsplit(":", 1)
        try:
            return host, int(port_s)
        except ValueError:
            return raw, 8080
    return raw, 8080


def _check_opensandbox_reachable() -> bool:
    """TCP check to OpenSandbox (settings.skills.opensandbox_domain, then env, same host:port as the executor)."""
    host, port = _opensandbox_host_port()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            pass
        logger.info(f"OpenSandbox reachable at {host}:{port}")
        return True
    except OSError as exc:
        logger.warning(f"OpenSandbox not reachable at {host}:{port}: {exc}")
        console.print(
            Panel(
                f"Could not open a TCP connection to OpenSandbox at [cyan]{host}:{port}[/cyan]. "
                "Shell tools (run_command, write_file, …) need a running OpenSandbox server.\n\n"
                "Open a [bold]new terminal[/bold], [cyan]cd[/cyan] into your [cyan]cuga-agent[/cyan] clone, "
                "then copy and run each line (README: [link=https://github.com/alibaba/OpenSandbox]https://github.com/alibaba/OpenSandbox[/link]):\n\n"
                "[cyan]uv venv[/cyan]\n"
                "[cyan]source .venv/bin/activate[/cyan]  [dim]# Windows: .venv\\Scripts\\activate[/dim]\n"
                "[cyan]uv pip install opensandbox-server[/cyan]\n"
                "[cyan]opensandbox-server init-config ~/.sandbox.toml --example docker[/cyan]\n"
                "[cyan]opensandbox-server[/cyan]\n\n"
                "Then retry this command. Override the server address with [cyan]OPEN_SANDBOX_DOMAIN[/cyan] or "
                "[cyan]DYNACONF_SKILLS__OPENSANDBOX_DOMAIN[/cyan] if needed.",
                title="[yellow]OpenSandbox not reachable[/yellow]",
                border_style="yellow",
            )
        )
        return False


console = Console()

os.environ["DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"] = "true"

app = typer.Typer(
    help="Cuga CLI for managing services with direct execution",
    short_help="Service management tool for Cuga components",
)

app.add_typer(policy_app, name="policy")

# ``cuga knowledge`` subcommand group — read/write the running engine's
# knowledge config from the CLI without having to curl the API. Combines:
#   - perf: config get/set + snapshot export/import (no-API JSON ops)
#   - client-adaptation: adaptation get/set/clear + glossary get/set + doctor
knowledge_app = typer.Typer(
    help="Manage the running knowledge engine config "
    "(settings, client adaptation, snapshots — reads/writes "
    "/api/knowledge/settings on the cuga server).",
    short_help="Knowledge engine config",
)


def _knowledge_api_base() -> str:
    base = os.environ.get("CUGA_API_BASE", "http://127.0.0.1:8005")
    return base.rstrip("/")


@knowledge_app.command("config-get", help="Print the running knowledge config as JSON.")
def knowledge_config_get(
    field: str | None = typer.Argument(
        None,
        help="Optional single field name (e.g. 'search_junk_filter'). Omit to print the full config.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit raw JSON (machine-readable).",
    ),
):
    """Read the live engine settings."""
    import json as _json

    import urllib.request as _ur

    url = f"{_knowledge_api_base()}/api/knowledge/settings"
    try:
        with _ur.urlopen(url, timeout=10) as resp:  # noqa: S310 — operator tool
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to fetch knowledge settings from %s: %s", url, exc)
        raise typer.Exit(code=1)

    knowledge_cfg = payload.get("knowledge", payload)
    if field:
        if field not in knowledge_cfg:
            logger.error("Unknown field %r. Available: %s", field, sorted(knowledge_cfg.keys()))
            raise typer.Exit(code=1)
        value = knowledge_cfg[field]
        typer.echo(_json.dumps(value) if json_output else str(value))
        return

    typer.echo(_json.dumps(knowledge_cfg, indent=None if json_output else 2))


@knowledge_app.command(
    "config-set",
    help="Update a single knowledge config field on the running engine.",
)
def knowledge_config_set(
    field: str = typer.Argument(
        ...,
        help="Field name (e.g. 'search_junk_filter', 'docling_drop_page_chrome').",
    ),
    value: str = typer.Argument(..., help="New value as a string. Booleans accept true/false."),
):
    """PATCH the running engine settings. Validation errors come back as
    HTTP 400 from the server (clearer than a TOML edit + restart loop)."""
    import json as _json
    import urllib.request as _ur

    # Coerce common scalar shapes so the user doesn't have to JSON-quote.
    coerced: object = value
    if value.lower() in ("true", "false"):
        coerced = value.lower() == "true"
    else:
        try:
            coerced = int(value)
        except ValueError:
            try:
                coerced = float(value)
            except ValueError:
                pass  # keep as string

    body = _json.dumps({"knowledge": {field: coerced}}).encode("utf-8")
    url = f"{_knowledge_api_base()}/api/knowledge/settings"
    req = _ur.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with _ur.urlopen(req, timeout=10) as resp:  # noqa: S310 — operator tool
            typer.echo(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to update knowledge settings: %s", exc)
        raise typer.Exit(code=1)


def _manage_api_base() -> str:
    """The manage-routes endpoint hangs off the main backend port —
    same host as ``/api/knowledge/*`` but a different prefix. Default
    matches the dev-server port; override via env for production."""
    base = os.environ.get("CUGA_API_BASE", "http://127.0.0.1:8005")
    return base.rstrip("/")


@knowledge_app.command(
    "snapshot-export",
    help="Export the published knowledge config snapshot to a file.",
)
def knowledge_snapshot_export(
    output: str = typer.Argument(
        ...,
        help="Path to write the JSON snapshot to (e.g. './kb-snapshot.json').",
    ),
    agent_id: str = typer.Option(
        "cuga-default",
        "--agent-id",
        help="Agent ID whose config to export (default 'cuga-default').",
    ),
    include_secrets: bool = typer.Option(
        False,
        "--include-secrets",
        help="Include API keys etc. Default OFF — match the publish contract "
        "so the file is safe to share across machines.",
    ),
):
    """Save the live published knowledge config (modes, providers,
    chunking, etc.) to a JSON file. The file is round-trippable via
    ``cuga knowledge snapshot-import`` on another machine — the same
    contract as the UI's publish/import flow."""
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur

    qs = _up.urlencode({"agent_id": agent_id})
    url = f"{_manage_api_base()}/api/manage/config?{qs}"
    try:
        with _ur.urlopen(url, timeout=15) as resp:  # noqa: S310 — operator tool
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to fetch manage config from %s: %s", url, exc)
        raise typer.Exit(code=1)

    config = payload.get("config", {})
    knowledge = config.get("knowledge", {})
    if not knowledge:
        logger.warning(
            "No 'knowledge' section in published config for agent_id=%s — "
            "exporting an empty snapshot. This usually means knowledge has "
            "never been configured for this agent.",
            agent_id,
        )

    snapshot = {
        "schema": "cuga.knowledge.snapshot.v1",
        "agent_id": agent_id,
        "knowledge": knowledge,
    }
    # Strip API key on export by default — matches the in-engine
    # ``to_dict(include_secrets=False)`` contract so an operator who
    # ``snapshot-export``s + emails the file can't accidentally leak keys.
    if not include_secrets and isinstance(snapshot["knowledge"].get("embedding_api_key"), str):
        snapshot["knowledge"]["embedding_api_key"] = ""

    try:
        with open(output, "w", encoding="utf-8") as f:
            _json.dump(snapshot, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as exc:
        logger.error("Failed to write snapshot to %s: %s", output, exc)
        raise typer.Exit(code=1)

    typer.echo(f"Knowledge snapshot exported to {output} (agent_id={agent_id})")


@knowledge_app.command(
    "snapshot-import",
    help="Publish a knowledge config snapshot from a file.",
)
def knowledge_snapshot_import(
    input: str = typer.Argument(
        ...,
        help="Path to a JSON snapshot file (from `snapshot-export`).",
    ),
    agent_id: Optional[str] = typer.Option(
        None,
        "--agent-id",
        help="Override the agent_id in the snapshot. Default: use whatever "
        "the snapshot recorded (usually 'cuga-default').",
    ),
):
    """Apply a previously-exported knowledge config to the running
    engine. Validation errors come back as HTTP 400 with the offending
    field name — same path the UI's publish form goes through."""
    import json as _json
    import urllib.parse as _up
    import urllib.request as _ur

    try:
        with open(input, encoding="utf-8") as f:
            snapshot = _json.load(f)
    except OSError as exc:
        logger.error("Failed to read snapshot file %s: %s", input, exc)
        raise typer.Exit(code=1)
    except _json.JSONDecodeError as exc:
        logger.error("Snapshot file %s is not valid JSON: %s", input, exc)
        raise typer.Exit(code=1)

    schema = snapshot.get("schema")
    if schema and schema != "cuga.knowledge.snapshot.v1":
        logger.error(
            "Unknown snapshot schema %r — expected 'cuga.knowledge.snapshot.v1'. "
            "Was this file produced by a different cuga version?",
            schema,
        )
        raise typer.Exit(code=1)

    knowledge = snapshot.get("knowledge")
    if not isinstance(knowledge, dict) or not knowledge:
        logger.error(
            "Snapshot at %s has no 'knowledge' section to import.",
            input,
        )
        raise typer.Exit(code=1)

    target_agent_id = agent_id or snapshot.get("agent_id") or "cuga-default"

    # Apply via the manage PATCH endpoint — same path the UI uses,
    # so we get the engine's full validation + the immediate take-effect
    # semantics (no restart needed).
    body = _json.dumps({"knowledge": knowledge}).encode("utf-8")
    qs = _up.urlencode({"agent_id": target_agent_id})
    url = f"{_manage_api_base()}/api/manage/config/draft/knowledge?{qs}"
    req = _ur.Request(
        url,
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _ur.urlopen(req, timeout=30) as resp:  # noqa: S310 — operator tool
            typer.echo(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to apply snapshot via PATCH %s: %s", url, exc)
        raise typer.Exit(code=1)

    typer.echo(
        f"Knowledge snapshot imported (agent_id={target_agent_id}). "
        f"Run `cuga knowledge config-get` to verify the new live values."
    )


app.add_typer(knowledge_app, name="knowledge")


def _cuga_server_base_url() -> str:
    """Resolve the cuga backend base URL for CLI HTTP calls.

    Distinct from ``_knowledge_api_base`` above: that one accepts a
    single ``CUGA_API_BASE`` env override (perf-branch convention used
    by ``config-get``/``config-set``/``snapshot-*``); this one composes
    host+port from ``CUGA_HOST``/``CUGA_PORT`` (client-adaptation
    convention used by ``adaptation-*``/``glossary-*``/``doctor``).
    Both are retained for back-compat with whichever env vars
    operators have already wired into their deployment scripts.
    """
    port = os.environ.get("CUGA_PORT") or "8000"
    host = os.environ.get("CUGA_HOST") or "127.0.0.1"
    return f"http://{host}:{port}"


@knowledge_app.command(
    "adaptation-get",
    help="Print the active client-adaptation text (markdown).",
)
def knowledge_adaptation_get():
    """Fetch and print the current client_adaptation_text from the running cuga server."""
    url = f"{_cuga_server_base_url()}/api/knowledge/settings"
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
    except Exception as e:
        typer.secho(f"Failed to reach cuga server at {url}: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    data = r.json().get("knowledge", {})
    text = data.get("client_adaptation_text", "")
    if not text:
        typer.secho("(no client adaptation set)", fg=typer.colors.YELLOW)
        return
    typer.echo(text)


@knowledge_app.command(
    "adaptation-set",
    help=(
        "Upload a markdown file as the client-adaptation text. "
        "The file's content is appended to the knowledge-agent system prompt "
        "for every request. Limit: 1500 chars."
    ),
)
def knowledge_adaptation_set(
    file: Path = typer.Argument(
        ...,
        help="Path to the markdown file containing the adaptation text.",
        exists=True,
        readable=True,
        dir_okay=False,
    ),
    agent_id: Optional[str] = typer.Option(
        None, "--agent-id", help="Target agent id (default: cuga-default)."
    ),
    publish: bool = typer.Option(
        False,
        "--publish/--draft-only",
        help="After patching draft, also publish a new version snapshot.",
    ),
):
    """Read FILE, validate length, PATCH the draft knowledge config, optionally publish."""
    from cuga.backend.knowledge.config import CLIENT_ADAPTATION_MAX_CHARS

    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        typer.secho(
            f"File is not valid UTF-8: {e}. Re-save as UTF-8 and retry.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if len(text) > CLIENT_ADAPTATION_MAX_CHARS:
        typer.secho(
            f"File too large: {len(text)} chars > {CLIENT_ADAPTATION_MAX_CHARS}. "
            "Trim to fit before retrying.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    aid = agent_id or "cuga-default"
    base = _cuga_server_base_url()

    patch_url = f"{base}/api/manage/config/draft/knowledge?agent_id={aid}"
    try:
        r = httpx.patch(patch_url, json={"client_adaptation_text": text}, timeout=15.0)
    except Exception as e:
        typer.secho(f"Failed to reach {patch_url}: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if r.status_code >= 400:
        typer.secho(f"PATCH rejected ({r.status_code}): {r.text}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(f"Client adaptation saved to draft ({len(text)} chars).", fg=typer.colors.GREEN)

    if publish:
        # Publish flow: server endpoint is POST /api/manage/config (not
        # ``/config/publish``). The body must be the full config we want
        # to publish — fetch the current draft first, then POST it.
        draft_url = f"{base}/api/manage/config?draft=1&agent_id={aid}"
        publish_url = f"{base}/api/manage/config?agent_id={aid}"
        try:
            r_draft = httpx.get(draft_url, timeout=15.0)
            r_draft.raise_for_status()
            draft_cfg = r_draft.json() or {}
        except Exception as e:
            typer.secho(f"Failed to read draft: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        try:
            r = httpx.post(publish_url, json={"config": draft_cfg}, timeout=60.0)
        except Exception as e:
            typer.secho(f"Failed to publish: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        if r.status_code >= 400:
            typer.secho(f"Publish rejected ({r.status_code}): {r.text}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        typer.secho("Published to new version snapshot.", fg=typer.colors.GREEN)


@knowledge_app.command(
    "adaptation-clear",
    help="Clear the client-adaptation text (sets it to empty string).",
)
def knowledge_adaptation_clear(
    agent_id: Optional[str] = typer.Option(None, "--agent-id"),
):
    aid = agent_id or "cuga-default"
    patch_url = f"{_cuga_server_base_url()}/api/manage/config/draft/knowledge?agent_id={aid}"
    try:
        r = httpx.patch(patch_url, json={"client_adaptation_text": ""}, timeout=15.0)
        r.raise_for_status()
    except Exception as e:
        typer.secho(f"Failed: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.secho("Client adaptation cleared.", fg=typer.colors.GREEN)


@knowledge_app.command(
    "glossary-get",
    help="Print the active client-adaptation glossary (JSON).",
)
def knowledge_glossary_get():
    """Fetch and print the current glossary entries from the running cuga server."""
    import json

    url = f"{_cuga_server_base_url()}/api/knowledge/settings"
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
    except Exception as e:
        typer.secho(f"Failed to reach cuga server at {url}: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    data = r.json().get("knowledge", {})
    glossary = data.get("client_adaptation_glossary", [])
    if not glossary:
        typer.secho("(no glossary entries set)", fg=typer.colors.YELLOW)
        return
    typer.echo(json.dumps(glossary, indent=2, ensure_ascii=False))


@knowledge_app.command(
    "glossary-set",
    help=(
        "Upload a JSON file containing the client-adaptation glossary. "
        "The file must be a JSON array of {term, aliases[], definition?} "
        "objects. Max 50 entries, 10 aliases each."
    ),
)
def knowledge_glossary_set(
    file: Path = typer.Argument(
        ...,
        help="Path to the JSON file containing the glossary entries.",
        exists=True,
        readable=True,
        dir_okay=False,
    ),
    agent_id: Optional[str] = typer.Option(None, "--agent-id"),
    publish: bool = typer.Option(
        False,
        "--publish/--draft-only",
        help="After patching draft, also publish a new version snapshot.",
    ),
):
    """Read FILE, validate structure, PATCH the draft knowledge config."""
    import json

    from cuga.backend.knowledge.config import CLIENT_GLOSSARY_MAX_ENTRIES

    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        typer.secho(f"File is not valid UTF-8: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        glossary = json.loads(text)
    except json.JSONDecodeError as e:
        typer.secho(f"File is not valid JSON: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if not isinstance(glossary, list):
        typer.secho(
            f"Glossary must be a JSON array, got {type(glossary).__name__}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if len(glossary) > CLIENT_GLOSSARY_MAX_ENTRIES:
        typer.secho(
            f"Glossary has {len(glossary)} entries — max is {CLIENT_GLOSSARY_MAX_ENTRIES}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    aid = agent_id or "cuga-default"
    base = _cuga_server_base_url()

    patch_url = f"{base}/api/manage/config/draft/knowledge?agent_id={aid}"
    try:
        r = httpx.patch(patch_url, json={"client_adaptation_glossary": glossary}, timeout=15.0)
    except Exception as e:
        typer.secho(f"Failed to reach {patch_url}: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if r.status_code >= 400:
        typer.secho(f"PATCH rejected ({r.status_code}): {r.text}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(f"Glossary saved to draft ({len(glossary)} entries).", fg=typer.colors.GREEN)

    if publish:
        # See knowledge_adaptation_set: publish endpoint is POST
        # ``/api/manage/config`` with the draft config as body. Split the
        # GET-draft and POST-publish into two try blocks so the operator
        # sees which step failed (mirrors adaptation_set's diagnostics).
        draft_url = f"{base}/api/manage/config?draft=1&agent_id={aid}"
        publish_url = f"{base}/api/manage/config?agent_id={aid}"
        try:
            r_draft = httpx.get(draft_url, timeout=15.0)
            r_draft.raise_for_status()
            draft_cfg = r_draft.json() or {}
        except Exception as e:
            typer.secho(f"Failed to read draft: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        try:
            r = httpx.post(publish_url, json={"config": draft_cfg}, timeout=60.0)
        except Exception as e:
            typer.secho(f"Failed to publish: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        if r.status_code >= 400:
            typer.secho(f"Publish rejected ({r.status_code}): {r.text}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        typer.secho("Published.", fg=typer.colors.GREEN)


@knowledge_app.command(
    "doctor",
    help=(
        "Print a quick diagnostic of the active client-adaptation config — "
        "hashes, lengths, glossary entry count. Use this when answering "
        "'is my adaptation actually applied?' or correlating support tickets "
        "to config versions."
    ),
)
def knowledge_doctor():
    """Run the on-call diagnostic for client-adaptation."""
    url = f"{_cuga_server_base_url()}/api/knowledge/settings"
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
    except Exception as e:
        typer.secho(f"Failed to reach cuga server: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    k = r.json().get("knowledge", {})
    adapt_hash = k.get("client_adaptation_hash", "")
    adapt_len = k.get("client_adaptation_len", 0)
    gloss_hash = k.get("client_adaptation_glossary_hash", "")
    gloss_count = k.get("client_adaptation_glossary_count", 0)

    active = bool(adapt_len) or bool(gloss_count)
    status_color = typer.colors.GREEN if active else typer.colors.YELLOW
    status_text = "ACTIVE" if active else "OFF"

    typer.secho(f"Client adaptation status: {status_text}", fg=status_color, bold=True)
    typer.echo("")
    typer.echo("  Adaptation text:")
    typer.echo(f"    length:   {adapt_len} chars")
    typer.echo(f"    hash:     {adapt_hash}")
    typer.echo("  Glossary:")
    typer.echo(f"    entries:  {gloss_count}")
    typer.echo(f"    hash:     {gloss_hash}")
    typer.echo("")
    if active:
        typer.echo(
            f"Grep logs for `cuga.knowledge.adaptation_applied` matching hash "
            f"{adapt_hash} to correlate prompt-assembly events to this config."
        )


# Global variables to track running direct processes (registry/demo)
direct_processes = {}
shutdown_event = threading.Event()

# OS detection
IS_WINDOWS = platform.system().lower().startswith("win")

# Playwright launcher state (for extension mode)
_playwright_thread: Optional[threading.Thread] = None
_playwright_started: bool = False


def kill_processes_by_port(ports: List[int], silent: bool = False):
    """Kill processes listening on specified ports.

    Args:
        ports: List of port numbers to check
        silent: If True, don't log (useful when called from signal handlers)
    """
    killed_any = False
    for port in ports:
        try:
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    # Get connections separately to handle cases where it's not available
                    try:
                        connections = proc.net_connections()
                    except (psutil.AccessDenied, AttributeError):
                        connections = []

                    for conn in connections:
                        if hasattr(conn, 'laddr') and conn.laddr and conn.laddr.port == port:
                            if not silent:
                                logger.info(
                                    f"🔄 Killing existing process {proc.info['name']} (PID: {proc.info['pid']}) on port {port}"
                                )
                            psutil.Process(proc.info['pid']).terminate()
                            killed_any = True
                            time.sleep(0.5)
                            try:
                                psutil.Process(proc.info['pid']).kill()
                            except psutil.NoSuchProcess:
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as e:
            if not silent:
                logger.debug(f"Error killing processes on port {port}: {e}")

    if killed_any:
        if not silent:
            logger.info("✨ Cleaned up existing processes")
        time.sleep(1)


def wait_for_tcp_port(
    port: int, server_name: str = "Server", max_retries: int = 20, retry_interval: float = 0.5
):
    """
    Wait for a TCP port to be listening (useful for non-HTTP servers like SMTP).

    Args:
        port: The port number to check
        server_name: Name of the server for logging (default: "Server")
        max_retries: Maximum number of retry attempts (default: 20)
        retry_interval: Time in seconds between retries (default: 0.5)

    Raises:
        TimeoutError: If the port doesn't become ready within max_retries attempts
    """
    import socket

    for attempt in range(max_retries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', port))
                if result == 0:
                    logger.info(f"{server_name} is ready on port {port}!")
                    return
        except Exception:
            pass

        if attempt < max_retries - 1:
            time.sleep(retry_interval)
        else:
            raise TimeoutError(
                f"{server_name} did not become ready after {max_retries * retry_interval:.1f} seconds. "
                f"Please check if the server started correctly on port {port}."
            )


def wait_for_server(
    port: int,
    server_name: str = "Server",
    max_retries: int = None,
    retry_interval: float = 0.5,
    https: bool = False,
):
    """
    Wait for a server to be ready by pinging its health endpoint.

    Args:
        port: The port number the server is running on
        server_name: Name of the server for logging (default: "Server")
        max_retries: Maximum number of retry attempts (default: 120 on Unix, 300 on Windows)
        retry_interval: Time in seconds between retries (default: 0.5)
        https: Whether to use HTTPS (default: False)

    Raises:
        TimeoutError: If the server doesn't become ready within max_retries attempts
    """
    # Use longer timeout on Windows due to slower package installation and process startup
    if max_retries is None:
        max_retries = 300 if platform.system() == "Windows" else 120

    scheme = "https" if https else "http"
    url = f"{scheme}://127.0.0.1:{port}/"

    for attempt in range(max_retries):
        if attempt > 0 and attempt % 20 == 0:
            logger.info(
                f"Still waiting for {server_name} on port {port}… "
                f"({attempt}/{max_retries} checks, ~{attempt * retry_interval:.0f}s elapsed)"
            )
        try:
            with httpx.Client(timeout=1.0, verify=False) as client:
                response = client.get(url)
                # Any non-5xx response means something is listening; many apps have no GET / route (404).
                if response.status_code < 500:
                    logger.info(f"{server_name} is ready!")
                    return
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            if attempt >= max_retries - 1:
                raise TimeoutError(
                    f"{server_name} did not become ready after {max_retries * retry_interval:.1f} seconds. "
                    f"Please check if the server started correctly on port {port}."
                )
        if attempt < max_retries - 1:
            time.sleep(retry_interval)

    raise TimeoutError(
        f"{server_name} did not become ready after {max_retries * retry_interval:.1f} seconds. "
        f"Please check if the server started correctly on port {port}."
    )


def wait_for_registry_server(port: int, max_retries: int = None, retry_interval: float = 0.5):
    """
    Wait for the registry server to be ready by pinging its health endpoint.

    Args:
        port: The port number the registry server is running on
        max_retries: Maximum number of retry attempts (default: 120)
        retry_interval: Time in seconds between retries (default: 0.5)

    Raises:
        TimeoutError: If the server doesn't become ready within max_retries attempts
    """
    wait_for_server(port, "Registry server", max_retries, retry_interval)


def kill_process_tree(pid):
    """Kill a process and all its children.

    Note: No logging in this function to avoid deadlock when called from signal handler.
    """
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)

        # Terminate children first
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass

        # Wait a bit for graceful termination
        psutil.wait_procs(children, timeout=3)

        # Kill any remaining children
        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except psutil.NoSuchProcess:
                pass

        # Now terminate the parent
        try:
            parent.terminate()
            parent.wait(timeout=3)
        except psutil.TimeoutExpired:
            parent.kill()
    except psutil.NoSuchProcess:
        pass
    except Exception:
        # Silently ignore errors to avoid deadlock in signal handler
        pass


def start_extension_browser_if_configured():
    """Start a Chromium instance with the MV3 extension if config enables it.

    Uses Playwright persistent context to load the extension from
    `frontend_workspaces/extension/releases/chrome-mv3`.
    Runs in a daemon thread and stops when the CLI receives a shutdown signal.
    """
    global _playwright_thread, _playwright_started

    use_extension = getattr(getattr(settings, "advanced_features", {}), "use_extension", False)
    if not use_extension:
        return

    if _playwright_started and _playwright_thread and _playwright_thread.is_alive():
        logger.info("Extension browser already running.")
        return

    extension_dir = os.path.join(
        PACKAGE_ROOT, "..", "frontend_workspaces", "extension", "releases", "chrome-mv3"
    )
    if not os.path.isdir(extension_dir):
        logger.error(
            f"Chrome MV3 extension directory not found: {extension_dir}. "
            "Build the extension or adjust your installation."
        )
        return

    def _runner():
        try:
            # Import here to avoid hard dependency if feature is off
            from playwright.sync_api import sync_playwright

            user_data_dir = get_user_data_path() or os.path.join(os.getcwd(), "logging", "pw_user_data")
            os.makedirs(user_data_dir, exist_ok=True)

            logger.info("Launching Chromium with extension (Playwright persistent context)...")
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=False,
                    args=[
                        f"--disable-extensions-except={extension_dir}",
                        f"--load-extension={extension_dir}",
                    ],
                    no_viewport=True,
                )
                # Open a page to the demo start URL (if available), otherwise about:blank
                try:
                    start_url = getattr(getattr(settings, "demo_mode", {}), "start_url", None)
                except Exception:
                    start_url = None
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if start_url:
                    page.goto(start_url, timeout=20000)
                else:
                    page.goto("about:blank", timeout=20000)

                # Keep context alive until shutdown
                while not shutdown_event.is_set():
                    time.sleep(0.2)

                try:
                    ctx.close()
                except Exception:
                    pass
        except ImportError:
            logger.error(
                "Playwright is not installed. Install with 'pip install playwright' "
                "and run 'playwright install chromium'."
            )
        except Exception as e:
            logger.error(f"Failed to launch Playwright with extension: {e}")

    _playwright_thread = threading.Thread(target=_runner, name="playwright-extension", daemon=True)
    _playwright_thread.start()
    _playwright_started = True


def signal_handler(signum, frame):
    """Handle SIGINT (Ctrl+C) to gracefully shutdown direct processes."""
    shutdown_event.set()

    # Force stop direct processes
    stop_direct_processes()

    # Only kill processes on ports that are actually being used by running services
    ports_to_kill = []
    if "registry" in direct_processes:
        ports_to_kill.append(settings.server_ports.registry)
    if "demo" in direct_processes:
        ports_to_kill.append(settings.server_ports.demo)
    if "appworld-environment" in direct_processes:
        ports_to_kill.append(settings.server_ports.environment_url)
    if "appworld-api" in direct_processes:
        ports_to_kill.append(settings.server_ports.apis_url)

    if ports_to_kill:
        kill_processes_by_port(ports_to_kill, silent=True)

    # Don't use logger here - signal handlers can't safely use loguru
    # Use print to stderr instead to avoid deadlock
    print("All processes stopped.", file=sys.stderr)
    sys.exit(0)


def stop_direct_processes():
    """Stop all direct processes gracefully, then forcefully.

    Note: No logging in this function to avoid deadlock when called from signal handler.
    """
    for service_name, process in direct_processes.items():
        if process and process.poll() is None:
            try:
                # First try to kill the entire process tree
                kill_process_tree(process.pid)
            except Exception:
                # Fallback to original method
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                except Exception:
                    # Silently ignore to avoid deadlock
                    pass

    direct_processes.clear()


def run_direct_service(
    service_name: str,
    command: List[str],
    cwd: Optional[str] = None,
    log_file: Optional[str] = None,
    env_vars: Optional[dict] = None,
):
    """Run a service command directly and return the process."""
    try:
        logger.info(f"Starting {service_name} directly with command: {' '.join(command)}")

        # Force colored output and ensure proper environment variables
        env = os.environ.copy()
        env['FORCE_COLOR'] = '1'

        # Ensure airgapped/container mode is fast by skipping syncs and setting paths
        env['UV_OFFLINE'] = '1'
        # Use PACKAGE_ROOT to find the src directory consistently across installations
        src_root = os.path.abspath(os.path.join(PACKAGE_ROOT, ".."))
        env['PYTHONPATH'] = os.path.pathsep.join([src_root, env.get('PYTHONPATH', '')]).strip(os.path.pathsep)
        # On Windows, set UTF-8 encoding to handle Unicode characters in subprocess output
        if IS_WINDOWS:
            env['PYTHONIOENCODING'] = 'utf-8'

        # Add any additional environment variables
        if env_vars:
            env.update(env_vars)

        # Ensure APPWORLD_ROOT is used only for appworld commands
        joined = ' '.join(command).lower()
        if 'appworld' in joined:
            cwd = env.get('APPWORLD_ROOT')
        else:
            # Keep current working dir for non-appworld services (e.g., memory)
            cwd = None
        # Log environment variables for debugging
        logger.debug(f"APPWORLD_ROOT: {env.get('APPWORLD_ROOT')}")
        logger.debug(f"Working directory: {cwd or os.getcwd()}")

        # Start the process with a new process group to make it easier to kill
        kwargs = {'cwd': cwd, 'env': env, 'preexec_fn': os.setsid if not IS_WINDOWS else None}

        # Redirect output to log file if provided
        if log_file:
            log_path = os.path.abspath(log_file)
            log_dir = os.path.dirname(log_path)
            os.makedirs(log_dir, exist_ok=True)
            log_handle = open(log_path, 'a', encoding='utf-8')
            kwargs['stdout'] = log_handle
            kwargs['stderr'] = subprocess.STDOUT
            logger.info(f"Redirecting {service_name} output to {log_path}")

        process = subprocess.Popen(command, **kwargs)

        direct_processes[service_name] = process
        return process

    except Exception as e:
        logger.error(f"Error starting {service_name}: {e}")
        return None


def wait_for_direct_processes():
    """Wait for all direct processes to complete or be interrupted."""
    try:
        while direct_processes and not shutdown_event.is_set():
            # Check if any process has terminated
            terminated = []
            for service_name, process in direct_processes.items():
                if process.poll() is not None:
                    terminated.append(service_name)
                    logger.info(f"{service_name} has terminated")

            # Remove terminated processes
            for service_name in terminated:
                del direct_processes[service_name]

            if not direct_processes:
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        stop_direct_processes()


@app.callback()
def callback(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose output with detailed logging information"
    ),
):
    """
    Cuga CLI: A management tool for Cuga services with direct execution.

    This tool helps you control various components of the Cuga ecosystem:

    - demo: Both registry and demo agent (runs directly)
    - demo_skills: Like demo; enables skills + shell tools; exits if OpenSandbox is unreachable
    - demo_crm: CRM demo with email MCP, mail sink, and CRM API (runs directly)
    - demo_supervisor: Same as demo_crm but with CugaSupervisor multi-agent coordination
    - travel_agent: Corporate travel planning demo with multi-agent supervisor
    - demo_health: Healthcare insurance demo (cuga-oak-health OpenAPI + manage UI)
    - registry: The MCP registry service only (runs directly)
    - appworld: AppWorld environment and API servers (runs directly)
    Examples:
      cuga start demo           # Start both registry and demo agent directly
      cuga start demo_skills    # Skills + OpenSandbox shell tools; stops if sandbox server is unreachable
      cuga start demo_crm       # Start CRM demo with all required services
      cuga start demo_supervisor # Start CRM demo with supervisor multi-agent mode
      cuga start travel_agent   # Start Travel Agent demo (flights, hotels, compliance, approval)
      cuga start registry       # Start registry only
      cuga start appworld       # Start AppWorld servers
    """
    if verbose:
        logger.level("DEBUG")

    # Set up signal handler for graceful shutdown of direct processes
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _start_demo_crm_services(
    host: str,
    sandbox: bool,
    read_only: bool,
    sample_memory_data: bool,
    no_email: bool,
    enable_supervisor: bool = False,
    tools: list | None = None,
    cuga_workspace: str | None = None,
    filesystem: bool = True,
):
    """Shared startup logic for demo_crm and demo_supervisor services.

    Args:
        enable_supervisor: If True, enables CugaSupervisor multi-agent coordination.
    """
    service_label = "Supervisor Demo" if enable_supervisor else "CRM Demo"

    try:
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        _apply_local_demo_workspace_env()
        ensure_managed_mcp_file_exists(get_managed_mcp_path())
        logger.info("🧹 Resetting config db and setting up manage demo_crm...")
        setup_demo_manage_config("demo_crm", no_email=no_email, tools=tools, filesystem=filesystem)

        # Configure supervisor mode
        if enable_supervisor:
            os.environ["DYNACONF_SUPERVISOR__ENABLED"] = "true"
            supervisor_config_path = os.path.join(
                PACKAGE_ROOT, "backend", "tools_env", "registry", "config", "supervisor_demo_crm.yaml"
            )
            os.environ["DYNACONF_SUPERVISOR__CONFIG_PATH"] = supervisor_config_path
            logger.info(f"Supervisor enabled with config: {supervisor_config_path}")
        else:
            os.environ["DYNACONF_SUPERVISOR__ENABLED"] = "false"

        workspace_path = cuga_workspace or os.path.join(os.getcwd(), "cuga_workspace")
        workspace_abs = os.path.abspath(workspace_path)
        app_mgr = _make_app_manager()
        app_mgr.prepare_workspace(workspace_path)
        if sample_memory_data:
            logger.info("📝 Generating sample CRM workspace files...")
            for p in app_mgr.create_demo_crm_samples(workspace_path):
                logger.info(f"   • {p}")

        tool_names = {t.get("name") for t in (tools or [])}
        start_email = (not no_email) and ("email" in tool_names if tools else True)
        policies_content = _build_workspace_policies(workspace_abs, include_email=start_email)
        os.environ["CUGA_POLICIES_CONTENT"] = policies_content
        os.environ["CUGA_LOAD_POLICIES"] = "true"
        logger.info(f"📋 Policies configured for {service_label}")

        start_crm = "crm" in tool_names if tools else True
        start_docs = "docs" in tool_names if tools else False
        start_oak_health = "oak_health" in tool_names if tools else False

        ports_to_clean = app_mgr.ports_for_apps(start_email, False, start_crm, start_docs, start_oak_health)
        ports_to_clean.extend([settings.server_ports.registry, settings.server_ports.demo])
        logger.info("🧹 Checking for existing processes on required ports...")
        kill_processes_by_port(ports_to_clean)

        os.environ["CUGA_HOST"] = host
        if sandbox:
            logger.info(
                f"Starting {service_label} with remote sandbox mode enabled (features.local_sandbox=false)"
            )
            os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

        if start_email:
            app_mgr.start_email()
        else:
            logger.info("Email services disabled (--no-email flag or not in tools)")

        if start_crm:
            crm_db_path = app_mgr.prepare_crm_db(workspace_path)
            app_mgr.start_crm(crm_db_path)

        if start_docs:
            app_mgr.start_docs()

        if start_oak_health:
            app_mgr.start_oak_health()

        registry_process = app_mgr.start_registry(host)
        if registry_process is None or registry_process.poll() is not None:
            logger.error("Registry service failed to start. Exiting.")
            stop_direct_processes()
            raise typer.Exit(1)

        demo_process = app_mgr.start_demo(host, sandbox=sandbox)
        if demo_process is None or demo_process.poll() is not None:
            logger.error("Demo service failed to start. Exiting.")
            stop_direct_processes()
            raise typer.Exit(1)

        if direct_processes:
            workspace_abs_path = os.path.abspath(workspace_path)

            services_table = Table(show_header=False, box=None, padding=(0, 1))
            services_table.add_column("Service", style="bold white", no_wrap=True)
            services_table.add_column("URL", style="cyan")
            if start_email:
                services_table.add_row("• Email Sink", f"smtp://localhost:{app_mgr.email_sink_port}")
                services_table.add_row("• Email MCP Server", f"http://localhost:{app_mgr.email_mcp_port}/sse")
            if start_crm:
                services_table.add_row("• CRM API Server", f"http://localhost:{app_mgr.crm_port}")
            if start_docs:
                services_table.add_row("• Docs MCP Server", f"http://localhost:{app_mgr.docs_port}/sse")
            if start_oak_health:
                services_table.add_row(
                    "• Oak Health API",
                    f"http://localhost:{app_mgr.oak_health_port}/openapi.json",
                )
            services_table.add_row("• Registry Server", f"http://localhost:{settings.server_ports.registry}")
            services_table.add_row("• Demo Server", f"http://localhost:{settings.server_ports.demo}")

            filesystem_text = Text()
            filesystem_text.append("  Read/Write allowed in:\n", style="bold white")
            filesystem_text.append(f"  {workspace_abs_path}", style="yellow")

            groups = [
                Text("📦 Started Services:", style="bold green"),
                services_table,
                Text(),
                Text("📁 Filesystem Access:", style="bold green"),
                filesystem_text,
            ]

            if enable_supervisor:
                groups.append(Text())
                groups.append(Text("🤖 Supervisor: enabled (multi-agent coordination)", style="bold magenta"))

            panel_content = Group(*groups)

            console.print()
            console.print(
                Panel(
                    panel_content,
                    title=f"[bold yellow]✅ {service_label} services are running. Press Ctrl+C to stop[/bold yellow]",
                    border_style="cyan",
                    padding=(1, 2),
                    expand=False,
                )
            )
            wait_for_direct_processes()

    except Exception as e:
        logger.error(f"Error starting {service_label} services: {e}")
        stop_direct_processes()
        raise typer.Exit(1)


# Helper function to validate service
def validate_service(service: str):
    """Validate service name."""
    valid_services = [
        "demo",
        "demo_skills",
        "demo_crm",
        "demo_docs",
        "demo_health",
        "demo_knowledge",
        "demo_supervisor",
        "travel_agent",
        "manager",
        "registry",
        "appworld",
    ]

    if service not in valid_services:
        logger.error(f"Unknown service: {service}. Valid options are: {', '.join(valid_services)}")
        raise typer.Exit(1)


def _resolve_apps(
    service: str,
    crm: bool,
    email: bool,
    digital_sales: bool,
    docs: bool,
    filesystem: bool,
    no_email: bool,
    oak_health: bool,
) -> tuple[bool, bool, bool, bool, bool, bool]:
    """Resolve app flags from preset + overrides. Returns (crm, email, digital_sales, docs, filesystem, oak_health)."""
    defaults = get_default_apps_for_preset(service)
    email_default = defaults["email"] and not no_email
    return (
        defaults["crm"] or crm,
        email_default or email,
        defaults["digital_sales"] or digital_sales,
        defaults["docs"] or docs,
        defaults["filesystem"] or filesystem,
        defaults.get("oak_health", False) or oak_health,
    )


@app.command(help="Start a specified service", short_help="Start service(s)")
def start(
    service: str = typer.Argument(
        ...,
        help="Service to start: demo, demo_skills, demo_knowledge, demo_crm, demo_docs, demo_health, demo_supervisor, manager, registry, appworld, or memory",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Host to bind to (default: 127.0.0.1). Use 0.0.0.0 to allow external connections.",
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Enable remote sandbox mode with llm-sandbox (requires --group sandbox to be installed)",
    ),
    read_only: bool = typer.Option(
        False,
        "--read-only",
        help="For demo_crm: prepare workspace in read-only context",
    ),
    sample_memory_data: bool = typer.Option(
        False,
        "--sample-memory-data/--no-sample-memory-data",
        help="For demo_crm: Generate sample workspace files (cities.txt, company.txt) in cuga_workspace",
    ),
    no_email: bool = typer.Option(
        False,
        "--no-email",
        help="For demo_crm: Disable email services (email sink and email MCP server)",
    ),
    crm: bool = typer.Option(
        False,
        "--crm",
        help="Enable CRM app (demo_crm preset includes it by default)",
    ),
    email: bool = typer.Option(
        False,
        "--email",
        help="Enable email app (demo_crm preset includes it by default)",
    ),
    digital_sales: bool = typer.Option(
        False,
        "--digital-sales",
        help="Enable Digital Sales OpenAPI tool (opt-in; off by default for demo / demo_knowledge)",
    ),
    filesystem: bool = typer.Option(
        False,
        "--filesystem",
        help="Enable workspace filesystem tools (enabled by default for demo/demo_crm/manager; use with demo_health/demo_docs to add filesystem access)",
    ),
    docs: bool = typer.Option(
        False,
        "--docs",
        help="Enable IBM Docs MCP server (search, summarize, ask questions on pages)",
    ),
    oak_health: bool = typer.Option(
        False,
        "--oak-health",
        help="Enable healthcare insurance OpenAPI (cuga-oak-health; port from settings server_ports.oak_health_api)",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="For demo_knowledge: Wipe all knowledge data (vector DB, metadata, files, sessions) before starting fresh",
    ),
    cuga_workspace: str | None = typer.Option(
        None,
        "--cuga-workspace",
        help="Path to cuga workspace; when set, configures policy env so all file operations use this dir (manager/demo_crm)",
    ),
    embeddings_provider: str | None = typer.Option(
        None,
        "--embeddings-provider",
        help="Override knowledge embeddings provider "
        "(fastembed | huggingface | openai | ollama | openrouter | litellm). "
        "Use 'litellm' for a unified interface across providers — pass any model with a provider prefix "
        "(e.g. 'openai/text-embedding-3-small', 'cohere/embed-english-v3.0', 'azure/<deployment>'). "
        "Use 'openrouter' to pick from openrouter.ai/models?output_modalities=embeddings with a single key — "
        "just set --embeddings-api-key + --embeddings-model. "
        "For other OpenAI-compatible endpoints (Together, Fireworks) use 'openai' + --embeddings-base-url. "
        "Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER.",
    ),
    embeddings_model: str | None = typer.Option(
        None,
        "--embeddings-model",
        help="Override knowledge embeddings model (provider-specific). Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL.",
    ),
    embeddings_base_url: str | None = typer.Option(
        None,
        "--embeddings-base-url",
        help="Override knowledge embeddings endpoint URL (use for OpenAI-compatible providers). "
        "Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__BASE_URL.",
    ),
    embeddings_api_key: str | None = typer.Option(
        None,
        "--embeddings-api-key",
        help="Override knowledge embeddings API key (use for openai / OpenAI-compatible providers). "
        "Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__API_KEY.",
    ),
    embeddings_batch_size: int | None = typer.Option(
        None,
        "--embeddings-batch-size",
        help="Override knowledge embeddings sub-batch size (default 64). Smaller = finer ingest progress; "
        "larger = lower per-call overhead. Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE.",
    ),
    embeddings_concurrency: int | None = typer.Option(
        None,
        "--embeddings-concurrency",
        help="Override knowledge embeddings concurrency for network providers (default 4, no-op on local providers). "
        "Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__CONCURRENCY.",
    ),
    docling_pdf_mode: str | None = typer.Option(
        None,
        "--docling-pdf-mode",
        help="Override knowledge Docling PDF parsing level: 'fast' (OCR + tables off; digital PDFs only, "
        "~3-10x faster), 'balanced' (OCR off, tables on; most digital PDFs), or 'accurate' (default; "
        "OCR + tables on; scanned PDFs supported). Sets DYNACONF_KNOWLEDGE__DOCLING__PDF_MODE.",
    ),
    use_gpu: bool | None = typer.Option(
        None,
        "--use-gpu/--no-use-gpu",
        help="Override knowledge embeddings GPU autodetect. Default: autodetect (CUDA / Apple CoreML); "
        "pass --no-use-gpu to force CPU. No effect on cloud providers. "
        "Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__USE_GPU.",
    ),
    embeddings_extra_params: str | None = typer.Option(
        None,
        "--embeddings-extra-params",
        help="Provider-specific embedding kwargs as JSON (Azure: api_version + azure_deployment; "
        "Bedrock: aws_region_name). Example: "
        "'{\"api_version\":\"2024-02-15\",\"azure_deployment\":\"my-dep\"}'. "
        "Sets DYNACONF_KNOWLEDGE__EMBEDDINGS__EXTRA_PARAMS.",
    ),
    docling_layout_engine: str | None = typer.Option(
        None,
        "--docling-layout-engine",
        help="Override Docling layout backend: 'auto' (default — ONNX on Mac, ONNX+CUDA on NVIDIA), "
        "'onnx' (explicit), or 'transformers' (PyTorch — engages MPS/CUDA via device_map; "
        "only path to Apple GPU for layout). Sets DYNACONF_KNOWLEDGE__DOCLING__LAYOUT_ENGINE.",
    ),
    # === Knowledge tuning knobs (parity with UI Settings tab) ===
    knowledge_enabled: bool | None = typer.Option(
        None,
        "--knowledge-enabled/--no-knowledge-enabled",
        help="Toggle the knowledge subsystem globally. Sets DYNACONF_KNOWLEDGE__ENABLED.",
    ),
    agent_level_enabled: bool | None = typer.Option(
        None,
        "--agent-level-knowledge/--no-agent-level-knowledge",
        help="Toggle permanent (agent-level) knowledge documents. Sets DYNACONF_KNOWLEDGE__AGENT_LEVEL_ENABLED.",
    ),
    session_level_enabled: bool | None = typer.Option(
        None,
        "--session-level-knowledge/--no-session-level-knowledge",
        help="Toggle ephemeral (session-level) knowledge documents. Sets DYNACONF_KNOWLEDGE__SESSION_LEVEL_ENABLED.",
    ),
    chunk_size: int | None = typer.Option(
        None,
        "--chunk-size",
        help="Override knowledge chunk size in tokens (default 1000). Sets DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_SIZE.",
    ),
    chunk_overlap: int | None = typer.Option(
        None,
        "--chunk-overlap",
        help="Override knowledge chunk overlap in tokens (default 200; must be < chunk_size). Sets DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_OVERLAP.",
    ),
    vector_insert_batch_size: int | None = typer.Option(
        None,
        "--vector-insert-batch-size",
        help="Override vector-store insert batch size (default 200). Caps each add_many transaction. "
        "Sets DYNACONF_KNOWLEDGE__ENGINE__VECTOR_INSERT_BATCH_SIZE.",
    ),
    rag_profile: str | None = typer.Option(
        None,
        "--rag-profile",
        help="RAG profile preset: 'speed' | 'standard' | 'balanced' | 'max_quality' | 'custom'. "
        "Sets DYNACONF_KNOWLEDGE__SEARCH__RAG_PROFILE.",
    ),
    metric_type: str | None = typer.Option(
        None,
        "--knowledge-metric-type",
        help="Vector-distance metric: 'COSINE' | 'IP' | 'L2'. Sets DYNACONF_KNOWLEDGE__SEARCH__METRIC_TYPE.",
    ),
    max_upload_size_mb: int | None = typer.Option(
        None,
        "--knowledge-max-upload-mb",
        help="Max upload size per file in MB (default 100). Sets DYNACONF_KNOWLEDGE__LIMITS__MAX_UPLOAD_SIZE_MB.",
    ),
    max_files_per_request: int | None = typer.Option(
        None,
        "--knowledge-max-files-per-request",
        help="Max files per upload request (default 10). Sets DYNACONF_KNOWLEDGE__LIMITS__MAX_FILES_PER_REQUEST.",
    ),
    max_url_download_size_mb: int | None = typer.Option(
        None,
        "--knowledge-max-url-download-mb",
        help="Max size for URL-fetched documents in MB (default 50). Sets DYNACONF_KNOWLEDGE__LIMITS__MAX_URL_DOWNLOAD_SIZE_MB.",
    ),
    max_chunks_per_document: int | None = typer.Option(
        None,
        "--knowledge-max-chunks-per-doc",
        help="Cap chunks per document (default 10000). Sets DYNACONF_KNOWLEDGE__LIMITS__MAX_CHUNKS_PER_DOCUMENT.",
    ),
    max_pending_tasks: int | None = typer.Option(
        None,
        "--knowledge-max-pending-tasks",
        help="Max queued ingestion tasks per collection (default 10). "
        "Sets DYNACONF_KNOWLEDGE__ENGINE__MAX_PENDING_TASKS.",
    ),
    knowledge_search_junk_filter: str | None = typer.Option(
        None,
        "--knowledge-search-junk-filter",
        help="Retrieval-time noise filter: 'off' (never filter), 'dry_run' (default; "
        "count + log what would be filtered, return everything), 'enforce' (drop). "
        "Sets DYNACONF_KNOWLEDGE__SEARCH__JUNK_FILTER.",
    ),
    knowledge_docling_drop_page_chrome: str | None = typer.Option(
        None,
        "--knowledge-docling-drop-page-chrome",
        help="Ingest-time drop of pure page_footer/page_header chunks (Docling labels): "
        "'off', 'dry_run', or 'enforce' (default). "
        "Sets DYNACONF_KNOWLEDGE__DOCLING__DROP_PAGE_CHROME.",
    ),
    knowledge_search_hybrid_mode: str | None = typer.Option(
        None,
        "--knowledge-search-hybrid-mode",
        help="Hybrid retrieval (BM25 + dense, RRF-fused). "
        "'auto' (default) runs both legs in parallel; 'off' uses dense only. "
        "Sets DYNACONF_KNOWLEDGE__SEARCH__HYBRID_MODE.",
    ),
):
    """
    Start the specified service.

    Demo MCP subprocesses and default workspace sample files are loaded from ``cuga.demo_tools``
    (on disk under ``site-packages/cuga/demo_tools`` when installed).

    Available services:
      - demo: Starts both registry and demo agent directly (registry on port 8001, demo on port 7860)
      - demo_skills: Like demo but sets skills + OpenSandbox shell tools via env; requires OpenSandbox TCP
      - demo_crm: Starts CRM demo with email MCP, mail sink, and CRM API servers
      - demo_knowledge: Starts registry + demo with knowledge engine enabled (upload docs, RAG search). Use --reset to wipe knowledge data.
      - demo_supervisor: Same as demo_crm but with CugaSupervisor multi-agent coordination enabled
      - demo_docs: Starts registry + demo with only IBM Docs MCP (search, summarize, ask questions on pages)
      - demo_health: Starts cuga-oak-health OpenAPI, registry, and demo (insurance member APIs + OAK playbooks; add --filesystem for workspace tools)
      - manager: Manage-config mode: registry uses managed MCP YAML, policy filesync off, demo on 7860
      - registry: Starts only the registry service directly (uvicorn on port 8001)
      - appworld: Starts AppWorld environment and API servers (environment on port 8000, api on port 9000)
    App flags (--crm, --email, --digital-sales, --docs, --filesystem) add apps to the preset:
      - demo: default = digital_sales + filesystem tools
      - demo_skills: default = digital_sales + skills/OpenSandbox shell tools
      - demo_crm: default = crm + filesystem tools + email
      - manager: default = filesystem tools
      - demo_health: default = oak_health only

    Examples:
      cuga start demo                     # registry + demo; digital_sales + filesystem tools
      cuga start demo_skills              # skills + OpenSandbox shell tools; aborts if unreachable
      cuga start demo --crm               # add CRM to demo
      cuga start demo_crm                 # crm + filesystem tools + email
      cuga start demo_crm --no-email      # crm + filesystem tools only
      cuga start manager --crm --email    # filesystem tools + crm + email
      cuga start manager --digital-sales  # filesystem tools + digital_sales
      cuga start manager --docs  # add IBM Docs MCP server
      cuga start demo_knowledge             # demo + knowledge engine
      cuga start demo_knowledge --reset     # wipe knowledge data + fresh start
      cuga start demo_docs  # registry + demo + IBM Docs MCP only
      cuga start demo_health  # oak health OpenAPI + registry + demo
      cuga start demo_health --filesystem  # also enable workspace filesystem tools
      cuga start manager --oak-health  # add insurance APIs to manager preset
      cuga start manager --cuga-workspace /path/to/workspace  # custom workspace + policy
      cuga start demo --sandbox           # with remote sandbox
      cuga start registry                 # registry only
      cuga start appworld                 # AppWorld servers
    """
    validate_service(service)

    if reset and service != "demo_knowledge":
        logger.warning("--reset is only supported for demo_knowledge and will be ignored for '%s'", service)

    # Embedding overrides — set as DYNACONF env vars BEFORE the service blocks
    # below so the engine picks them up when settings is first loaded. These
    # flags work for any service that touches the knowledge engine; the
    # validation of the provider value happens later inside KnowledgeConfig.
    _embedding_flag_env = [
        ("DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER", embeddings_provider),
        ("DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL", embeddings_model),
        ("DYNACONF_KNOWLEDGE__EMBEDDINGS__BASE_URL", embeddings_base_url),
        ("DYNACONF_KNOWLEDGE__EMBEDDINGS__API_KEY", embeddings_api_key),
        (
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__BATCH_SIZE",
            str(embeddings_batch_size) if embeddings_batch_size is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__CONCURRENCY",
            str(embeddings_concurrency) if embeddings_concurrency is not None else None,
        ),
        ("DYNACONF_KNOWLEDGE__DOCLING__PDF_MODE", docling_pdf_mode),
        (
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__USE_GPU",
            ("true" if use_gpu else "false") if use_gpu is not None else None,
        ),
        # extra_params is a JSON dict — DYNACONF supports the @json marker prefix
        # to coerce env strings to JSON values; if user typed bad JSON, dynaconf
        # surfaces the parse error at settings-load time (acceptable failure mode).
        (
            "DYNACONF_KNOWLEDGE__EMBEDDINGS__EXTRA_PARAMS",
            f"@json {embeddings_extra_params}" if embeddings_extra_params is not None else None,
        ),
        ("DYNACONF_KNOWLEDGE__DOCLING__LAYOUT_ENGINE", docling_layout_engine),
        # Bool toggles — coerce to "true"/"false" only when the flag was set.
        (
            "DYNACONF_KNOWLEDGE__ENABLED",
            ("true" if knowledge_enabled else "false") if knowledge_enabled is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__AGENT_LEVEL_ENABLED",
            ("true" if agent_level_enabled else "false") if agent_level_enabled is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__SESSION_LEVEL_ENABLED",
            ("true" if session_level_enabled else "false") if session_level_enabled is not None else None,
        ),
        # Numeric tuning knobs — DYNACONF reads strings; settings.toml typing
        # coerces back to int on load.
        ("DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_SIZE", str(chunk_size) if chunk_size is not None else None),
        (
            "DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_OVERLAP",
            str(chunk_overlap) if chunk_overlap is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__ENGINE__VECTOR_INSERT_BATCH_SIZE",
            str(vector_insert_batch_size) if vector_insert_batch_size is not None else None,
        ),
        ("DYNACONF_KNOWLEDGE__SEARCH__RAG_PROFILE", rag_profile),
        ("DYNACONF_KNOWLEDGE__SEARCH__METRIC_TYPE", metric_type),
        (
            "DYNACONF_KNOWLEDGE__LIMITS__MAX_UPLOAD_SIZE_MB",
            str(max_upload_size_mb) if max_upload_size_mb is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__LIMITS__MAX_FILES_PER_REQUEST",
            str(max_files_per_request) if max_files_per_request is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__LIMITS__MAX_URL_DOWNLOAD_SIZE_MB",
            str(max_url_download_size_mb) if max_url_download_size_mb is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__LIMITS__MAX_CHUNKS_PER_DOCUMENT",
            str(max_chunks_per_document) if max_chunks_per_document is not None else None,
        ),
        (
            "DYNACONF_KNOWLEDGE__ENGINE__MAX_PENDING_TASKS",
            str(max_pending_tasks) if max_pending_tasks is not None else None,
        ),
        ("DYNACONF_KNOWLEDGE__SEARCH__JUNK_FILTER", knowledge_search_junk_filter),
        ("DYNACONF_KNOWLEDGE__DOCLING__DROP_PAGE_CHROME", knowledge_docling_drop_page_chrome),
        ("DYNACONF_KNOWLEDGE__SEARCH__HYBRID_MODE", knowledge_search_hybrid_mode),
    ]
    # Names that hold a credential must NEVER appear in logs even at INFO.
    # Redaction list is matched against the SUFFIX after the last ``__`` so
    # any new ``DYNACONF_..._API_KEY``-style env var is covered automatically.
    _SECRET_SUFFIXES = {"API_KEY", "TOKEN", "SECRET", "PASSWORD"}
    for _env_name, _value in _embedding_flag_env:
        if _value is not None:
            os.environ[_env_name] = _value
            _suffix = _env_name.split("__")[-1]
            _shown = "<redacted>" if _suffix in _SECRET_SUFFIXES else _value
            logger.info("Embedding override: %s=%s", _suffix, _shown)

    app_crm, app_email, app_digital_sales, app_docs, app_filesystem, app_oak_health = _resolve_apps(
        service, crm, email, digital_sales, docs, filesystem, no_email, oak_health
    )
    resolved_tools = build_tools_from_apps(
        crm=app_crm,
        email=app_email,
        digital_sales=app_digital_sales,
        docs=app_docs,
        filesystem=app_filesystem,
        oak_health=app_oak_health,
    )

    if service == "manager":
        try:
            os.environ["CUGA_MANAGER_MODE"] = "true"
            os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
            managed_path = ensure_managed_mcp_file_exists(get_managed_mcp_path())
            os.environ["MCP_SERVERS_FILE"] = "none"
            _apply_local_demo_workspace_env()
            logger.info("Manager mode: policy filesystem sync disabled, MCP_SERVERS_FILE=%s", managed_path)
            setup_demo_manage_config("manager", tools=resolved_tools, filesystem=app_filesystem)

            app_mgr = _make_app_manager()
            workspace_path = cuga_workspace or os.path.join(os.getcwd(), "cuga_workspace")
            workspace_abs = os.path.abspath(workspace_path)
            os.environ["CUGA_POLICIES_CONTENT"] = _build_workspace_policies(
                workspace_abs, include_email=app_email
            )
            os.environ["CUGA_LOAD_POLICIES"] = "true"
            ports_to_kill = app_mgr.ports_for_apps(app_email, False, app_crm, app_docs, app_oak_health)
            ports_to_kill.extend([settings.server_ports.registry, settings.server_ports.demo])
            kill_processes_by_port(ports_to_kill)
            os.environ["CUGA_HOST"] = host

            if app_filesystem or app_crm:
                app_mgr.prepare_workspace(workspace_path)
            if app_email:
                app_mgr.start_email()
            if app_crm:
                crm_db_path = app_mgr.prepare_crm_db(workspace_path)
                app_mgr.start_crm(crm_db_path)
            if app_docs:
                app_mgr.start_docs()
            if app_oak_health:
                app_mgr.start_oak_health()

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)
            demo_process = app_mgr.start_demo(host)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)
            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                if app_email:
                    table.add_row("Email Sink:", f"smtp://localhost:{app_mgr.email_sink_port}")
                    table.add_row("Email MCP:", f"http://localhost:{app_mgr.email_mcp_port}/sse")
                if app_filesystem:
                    table.add_row("Filesystem tools:", os.path.abspath(workspace_path))
                if app_crm:
                    table.add_row("CRM API:", f"http://localhost:{app_mgr.crm_port}")
                if app_docs:
                    table.add_row("Docs MCP:", f"http://localhost:{app_mgr.docs_port}/sse")
                if app_oak_health:
                    table.add_row(
                        "Oak Health API:", f"http://localhost:{app_mgr.oak_health_port}/openapi.json"
                    )
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")
                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Manager mode. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()
        except Exception as e:
            logger.error(f"Error starting manager services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    # Handle direct execution services (demo and registry)
    if service in ("demo", "demo_skills"):
        if service == "demo_skills":
            _apply_demo_skills_env()
            if getattr(settings.advanced_features, "sandbox_mode", "opensandbox") == "opensandbox":
                _uv_sync_opensandbox_extra()
                if not _check_opensandbox_reachable():
                    raise typer.Exit(1)
        else:
            _apply_local_demo_workspace_env()
        demo_preset = "demo_skills" if service == "demo_skills" else "demo"
        os.environ["CUGA_DEMO_ADVANCED"] = "true"
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        ensure_managed_mcp_file_exists(get_managed_mcp_path())

        try:
            fs_for_demo = app_filesystem
            logger.info("🧹 Resetting config db and setting up manage %s...", demo_preset)
            setup_demo_manage_config(demo_preset, tools=resolved_tools, filesystem=fs_for_demo)
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            workspace_path = os.path.join(os.getcwd(), "cuga_workspace")
            ports_to_clean = [settings.server_ports.registry, settings.server_ports.demo]
            if service == "demo_skills":
                logger.info(
                    "demo_skills: filesystem tools %s for this agent",
                    "enabled" if fs_for_demo else "disabled",
                )
            ports_to_clean.extend(app_mgr.ports_for_apps(False, False, False, app_docs, app_oak_health))
            kill_processes_by_port(ports_to_clean)

            os.environ["CUGA_HOST"] = host
            if sandbox:
                logger.info("Starting demo with remote sandbox mode enabled (features.local_sandbox=false)")
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

            app_mgr.prepare_workspace(workspace_path)
            if app_docs:
                app_mgr.start_docs()
            if app_oak_health:
                app_mgr.start_oak_health()

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            demo_process = app_mgr.start_demo(host, sandbox=sandbox)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)
            # Optionally start Chromium with MV3 extension if configured

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                if fs_for_demo:
                    table.add_row("Filesystem tools:", os.path.abspath(workspace_path))
                if app_docs:
                    table.add_row("Docs MCP:", f"http://localhost:{app_mgr.docs_port}/sse")
                if app_oak_health:
                    table.add_row(
                        "Oak Health API:", f"http://localhost:{app_mgr.oak_health_port}/openapi.json"
                    )
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                demo_panel_title = (
                    "[bold yellow]Demo (skills + OpenSandbox env) running. Press Ctrl+C to stop[/bold yellow]"
                    if service == "demo_skills"
                    else "[bold yellow]Demo (manage mode) services are running. Press Ctrl+C to stop[/bold yellow]"
                )
                console.print(
                    Panel(
                        table,
                        title=demo_panel_title,
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting demo services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    if service == "demo_knowledge":
        os.environ["CUGA_DEMO_MODE"] = "knowledge"
        os.environ["CUGA_DEMO_ADVANCED"] = "true"
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        os.environ["DYNACONF_KNOWLEDGE__ENABLED"] = "true"
        os.environ["DYNACONF_KNOWLEDGE__AGENT_LEVEL_ENABLED"] = "true"
        os.environ["DYNACONF_KNOWLEDGE__SESSION_LEVEL_ENABLED"] = "true"
        ensure_managed_mcp_file_exists(get_managed_mcp_path())

        try:
            if reset:
                logger.info("🧹 Resetting knowledge data...")
            logger.info("🧹 Setting up demo_knowledge config...")
            setup_demo_manage_config(
                "demo_knowledge", tools=resolved_tools, reset_knowledge=reset, filesystem=app_filesystem
            )
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            workspace_path = os.path.join(os.getcwd(), "cuga_workspace")
            ports_to_clean = [settings.server_ports.registry, settings.server_ports.demo]
            ports_to_clean.extend(app_mgr.ports_for_apps(False, False, False, app_docs, app_oak_health))
            kill_processes_by_port(ports_to_clean)

            os.environ["CUGA_HOST"] = host
            if sandbox:
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

            app_mgr.prepare_workspace(workspace_path)

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            demo_process = app_mgr.start_demo(host, sandbox=sandbox)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                if app_filesystem:
                    table.add_row("Filesystem tools:", os.path.abspath(workspace_path))
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Knowledge demo (manage mode). Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting demo_knowledge services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    if service == "demo_docs":
        os.environ["CUGA_DEMO_ADVANCED"] = "true"
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        _apply_local_demo_workspace_env()
        ensure_managed_mcp_file_exists(get_managed_mcp_path())

        try:
            logger.info("🧹 Resetting config db and setting up manage demo_docs (docs only)...")
            setup_demo_manage_config("demo_docs", tools=resolved_tools)
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            ports_to_clean = [settings.server_ports.registry, settings.server_ports.demo]
            ports_to_clean.extend(app_mgr.ports_for_apps(False, False, False, True))
            kill_processes_by_port(ports_to_clean)

            os.environ["CUGA_HOST"] = host
            app_mgr.start_docs()

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            demo_process = app_mgr.start_demo(host, sandbox=sandbox)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                table.add_row("Docs MCP:", f"http://localhost:{app_mgr.docs_port}/sse")
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Demo Docs (docs-only mode). Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting demo_docs services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    if service == "demo_health":
        os.environ["CUGA_DEMO_ADVANCED"] = "true"
        os.environ["CUGA_MANAGER_MODE"] = "true"
        os.environ["CUGA_DEMO_MODE"] = "health"
        os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
        os.environ["MCP_SERVERS_FILE"] = "none"
        _apply_local_demo_workspace_env()
        ensure_managed_mcp_file_exists(get_managed_mcp_path())

        try:
            logger.info("🧹 Resetting config db and setting up manage demo_health (oak_health)...")
            setup_demo_manage_config("demo_health", tools=resolved_tools, filesystem=app_filesystem)
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            ports_to_clean = [settings.server_ports.registry, settings.server_ports.demo]
            ports_to_clean.extend(app_mgr.ports_for_apps(False, False, False, False, True))
            kill_processes_by_port(ports_to_clean)

            os.environ["CUGA_HOST"] = host
            if sandbox:
                logger.info(
                    "Starting demo_health with remote sandbox mode enabled (features.local_sandbox=false)"
                )
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

            if app_filesystem:
                workspace_path = os.path.join(os.getcwd(), "cuga_workspace")
                app_mgr.prepare_workspace(workspace_path)
            app_mgr.start_oak_health()

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            demo_process = app_mgr.start_demo(host, sandbox=sandbox)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                if app_filesystem:
                    table.add_row(
                        "Filesystem tools:", os.path.abspath(os.path.join(os.getcwd(), "cuga_workspace"))
                    )
                table.add_row("Oak Health API:", f"http://localhost:{app_mgr.oak_health_port}/openapi.json")
                table.add_row("Registry:", f"http://localhost:{settings.server_ports.registry}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]Demo Health (insurance APIs). Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting demo_health services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service in ("demo_crm", "demo_supervisor"):
        _start_demo_crm_services(
            host=host,
            sandbox=sandbox,
            read_only=read_only,
            sample_memory_data=sample_memory_data,
            no_email=no_email,
            enable_supervisor=(service == "demo_supervisor"),
            tools=resolved_tools,
            cuga_workspace=cuga_workspace,
            filesystem=app_filesystem,
        )
        return

    elif service == "travel_agent":
        try:
            # Enable supervisor mode with travel agent configuration
            os.environ["DYNACONF_SUPERVISOR__ENABLED"] = "true"
            _cli_dir = Path(__file__).resolve().parent
            supervisor_config_path = str(
                _cli_dir.joinpath(
                    "..",
                    "..",
                    "..",
                    "docs",
                    "examples",
                    "travel_agent",
                    "config",
                    "supervisor_travel_agent.yaml",
                ).resolve()
            )

            if not os.path.exists(supervisor_config_path):
                logger.error(f"Travel Agent config not found: {supervisor_config_path}")
                logger.error(
                    "Please ensure docs/examples/travel_agent/config/supervisor_travel_agent.yaml exists"
                )
                raise typer.Exit(1)

            os.environ["DYNACONF_SUPERVISOR__CONFIG_PATH"] = supervisor_config_path

            # Load the travel agent's own .env file (SERPAPI_API_KEY, SLACK_BOT_TOKEN, etc.)
            # Use dotenv_values to read without affecting the current process, then set
            # each value explicitly in os.environ so the subprocess inherits them.
            from dotenv import dotenv_values

            travel_agent_env_path = str(
                _cli_dir.joinpath("..", "..", "..", "docs", "examples", "travel_agent", ".env").resolve()
            )
            if os.path.exists(travel_agent_env_path):
                travel_agent_env = dotenv_values(travel_agent_env_path)
                for key, value in travel_agent_env.items():
                    if value is not None:
                        os.environ[key] = value
                logger.info(f"✅ Loaded travel agent env from {travel_agent_env_path}")
            else:
                logger.warning(
                    f"Travel agent .env not found at {travel_agent_env_path}. "
                    "Copy .env.example to .env and fill in SERPAPI_API_KEY etc."
                )

            # CRITICAL: Reload settings after setting supervisor environment variables
            # so the backend server picks up the new DYNACONF_SUPERVISOR__* values.
            settings.reload()
            logger.info(f"✈️  Travel Agent supervisor enabled with config: {supervisor_config_path}")
            logger.info(f"   Supervisor enabled: {settings.supervisor.enabled}")
            logger.info(f"   Supervisor config path: {settings.supervisor.config_path}")

            # Reset config database and set Travel Agent configuration
            os.environ["CUGA_MANAGER_MODE"] = "true"
            os.environ["DYNACONF_POLICY__FILESYSTEM_SYNC"] = "false"
            os.environ["MCP_SERVERS_FILE"] = "none"

            # Set agent name BEFORE setup so it gets saved to database
            os.environ["CUGA_AGENT_NAME"] = "Travel Agent"
            os.environ["CUGA_AGENT_DESCRIPTION"] = "AI-powered corporate travel planning system"

            from cuga.backend.server.config_store import reset_config_db, save_draft
            import asyncio

            ensure_managed_mcp_file_exists(get_managed_mcp_path())
            logger.info("🧹 Resetting config db for Travel Agent...")

            reset_config_db()

            # Build LLM config from environment (same as setup_demo_manage_config does)
            llm_api_key_ref = ""
            try:
                from cuga.backend.secrets.seed import resolve_llm_api_key_ref

                llm_api_key_ref = resolve_llm_api_key_ref()
            except Exception:
                pass

            llm_cfg = {"model": os.environ.get("MODEL_NAME", "")}
            if llm_api_key_ref:
                llm_cfg["api_key"] = llm_api_key_ref

            travel_agent_config = {
                "agent": {
                    "name": "Travel Agent",
                    "description": "AI-powered corporate travel planning system",
                },
                "tools": [],
                "llm": llm_cfg,
            }
            asyncio.run(save_draft(travel_agent_config, "cuga-default"))
            logger.info(
                "✅ Travel Agent configuration saved (model: %s)", llm_cfg.get("model") or "(default)"
            )

            app_mgr = _make_app_manager()
            logger.info("🧹 Checking for existing processes on required ports...")
            kill_processes_by_port([app_mgr.registry_port, settings.server_ports.demo])

            os.environ["CUGA_HOST"] = host
            if sandbox:
                logger.info("Starting Travel Agent with remote sandbox mode enabled")
                os.environ["DYNACONF_FEATURES__LOCAL_SANDBOX"] = "false"

            registry_process = app_mgr.start_registry(host)
            if registry_process is None or registry_process.poll() is not None:
                logger.error("Registry service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            demo_process = app_mgr.start_demo(host, sandbox=sandbox)
            if demo_process is None or demo_process.poll() is not None:
                logger.error("Demo service failed to start. Exiting.")
                stop_direct_processes()
                raise typer.Exit(1)

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                table.add_row("Registry:", f"http://localhost:{app_mgr.registry_port}")
                table.add_row("Demo:", f"http://localhost:{settings.server_ports.demo}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]✅ Travel Agent is running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                        expand=False,
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting Travel Agent: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service == "registry":
        try:
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            kill_processes_by_port([app_mgr.registry_port])
            app_mgr.start_registry(host)

            if direct_processes:
                console.print()
                console.print(
                    Panel(
                        f"[bold white]Registry:[/bold white] [cyan]http://localhost:{app_mgr.registry_port}[/cyan]",
                        title="[bold yellow]Registry service is running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()
        except Exception as e:
            logger.error(f"Error starting registry service: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return

    elif service == "appworld":
        try:
            logger.info("🧹 Checking for existing processes on required ports...")
            app_mgr = _make_app_manager()
            kill_processes_by_port([settings.server_ports.environment_url, settings.server_ports.apis_url])
            app_mgr.start_appworld()

            if direct_processes:
                table = Table(show_header=False, box=None, padding=(0, 1))
                table.add_column("Service", style="bold white")
                table.add_column("URL", style="cyan")
                table.add_row("Environment:", f"http://localhost:{settings.server_ports.environment_url}")
                table.add_row("API:", f"http://localhost:{settings.server_ports.apis_url}")

                console.print()
                console.print(
                    Panel(
                        table,
                        title="[bold yellow]AppWorld services are running. Press Ctrl+C to stop[/bold yellow]",
                        border_style="cyan",
                        padding=(1, 2),
                    )
                )
                wait_for_direct_processes()

        except Exception as e:
            logger.error(f"Error starting AppWorld services: {e}")
            stop_direct_processes()
            raise typer.Exit(1)
        return


def manage_service(action: str, service: str):
    """Common function for stopping or restarting services."""
    validate_service(service)

    if action == "stop":
        if service in ("demo", "demo_skills", "manager", "travel_agent"):
            stopped_any = False
            for service_name in ["oak-health", "docs-mcp", "registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                service_label = "Travel Agent" if service == "travel_agent" else "Demo/manager"
                logger.info(f"{service_label} services are not running")
        elif service in ("demo_crm", "demo_supervisor"):
            # Stop all CRM/supervisor demo services
            stopped_any = False
            for service_name in [
                "email-sink",
                "email-mcp",
                "crm-server",
                "oak-health",
                "registry",
                "demo",
            ]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info(f"{service} services are not running")
        elif service == "demo_docs":
            stopped_any = False
            for service_name in ["docs-mcp", "registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("demo_docs services are not running")
        elif service == "demo_health":
            stopped_any = False
            for service_name in ["oak-health", "registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("demo_health services are not running")
        elif service == "demo_knowledge":
            stopped_any = False
            for service_name in ["registry", "demo"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("demo_knowledge services are not running")
        elif service == "registry":
            # Stop only registry for registry service
            if "registry" in direct_processes:
                process = direct_processes["registry"]
                if process and process.poll() is None:
                    logger.info("Stopping registry...")
                    kill_process_tree(process.pid)
                del direct_processes["registry"]
            else:
                logger.info("Registry service is not running")
        elif service == "appworld":
            # Stop both appworld services
            stopped_any = False
            for service_name in ["appworld-environment", "appworld-api"]:
                if service_name in direct_processes:
                    process = direct_processes[service_name]
                    if process and process.poll() is None:
                        logger.info(f"Stopping {service_name}...")
                        kill_process_tree(process.pid)
                        stopped_any = True
                    del direct_processes[service_name]
            if not stopped_any:
                logger.info("AppWorld services are not running")
    elif action == "restart":
        # Stop if running, then start
        manage_service("stop", service)
        time.sleep(1)
        # Call start command
        start(service)


@app.command(help="Stop a specified service", short_help="Stop service(s)")
def stop(
    service: str = typer.Argument(
        ...,
        help="Service to stop: demo, demo_crm, demo_docs, demo_health, demo_knowledge, demo_supervisor, travel_agent, registry, or appworld",
    ),
):
    """
    Stop the specified service.

    Available services:
      - demo: Stops both registry and demo agent (direct processes)
      - demo_skills: Same processes as demo
      - demo_crm: Stops all CRM demo services (email sink, email MCP, CRM API, registry, demo)
      - demo_docs: Stops docs MCP, registry, and demo
      - demo_health: Stops oak-health API, registry, and demo
      - demo_knowledge: Stops registry and demo
      - demo_supervisor: Same as demo_crm
      - travel_agent: Stops Travel Agent demo services (registry, demo)
      - registry: Stops only the registry service (direct process)
      - appworld: Stops both AppWorld environment and API servers (direct processes)
    Examples:
      cuga stop demo             # Stop both registry and demo services
      cuga stop demo_crm         # Stop all CRM demo services
      cuga stop demo_knowledge   # Stop knowledge demo services
      cuga stop demo_supervisor  # Stop all supervisor demo services
      cuga stop travel_agent     # Stop Travel Agent demo services
      cuga stop registry         # Stop only the registry service
      cuga stop appworld         # Stop AppWorld servers
    """
    manage_service("stop", service)


@app.command(help="Start trajectory viewer", short_help="Start trajectory viewer")
def viz():
    """
    Start the trajectory viewer.

    This command launches a web-based dashboard for viewing and analyzing trajectory data from agent executions.

    Example:
      cuga viz         # Start the trajectory viewer
    """
    try:
        trajectory_data_path = TRAJECTORY_DATA_DIR
        subprocess.run(
            ["uv", "run", "--no-sync", "--group", "dev", "cuga-viz", "run", trajectory_data_path],
            capture_output=False,
            text=False,
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Error starting dashboard: {e}")
        raise typer.Exit(1)
    except Exception as e:
        logger.error(f"Error starting dashboard: {e}")
        return False


@app.command(help="Show status of services", short_help="Display service status")
def status(
    service: str = typer.Argument(
        "all",
        help="Service to check status: demo, demo_crm, demo_docs, demo_health, demo_supervisor, travel_agent, registry, appworld, or all",
    ),
):
    """
    Display the current status of services.

    Available services:
      - demo: Shows status of both registry and demo agent (direct processes)
      - demo_skills: Same as demo
      - demo_crm: Shows status of all CRM demo services (email sink, email MCP, CRM API, registry, demo)
      - demo_docs: Shows docs MCP, registry, and demo
      - demo_health: Shows oak-health API, registry, and demo
      - demo_supervisor: Same as demo_crm
      - travel_agent: Shows status of Travel Agent demo services (registry, demo)
      - registry: Shows status of registry service only (direct process)
      - appworld: Shows status of both AppWorld environment and API servers (direct processes)
      - all: Shows status of all services (default)

    Examples:
      cuga status              # Show status of all services
      cuga status demo         # Show status of demo services (registry + demo)
      cuga status demo_crm     # Show status of CRM demo services
      cuga status travel_agent # Show status of Travel Agent demo services
      cuga status registry     # Show status of registry only
      cuga status appworld     # Show status of AppWorld servers
    """
    if service in ("demo", "demo_skills", "manager", "travel_agent"):
        for service_name in ["registry", "demo"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(f"{service_name.capitalize()} service: Running (PID: {process.pid})")
                else:
                    logger.info(f"{service_name.capitalize()} service: Terminated")
            else:
                logger.info(f"{service_name.capitalize()} service: Not running")
        if service == "travel_agent":
            logger.info("Travel Agent uses registry + demo services")
        return

    elif service == "demo_docs":
        for service_name in ["docs-mcp", "registry", "demo"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(f"{service_name} service: Running (PID: {process.pid})")
                else:
                    logger.info(f"{service_name} service: Terminated")
            else:
                logger.info(f"{service_name} service: Not running")
        return

    elif service == "demo_health":
        for service_name in ["oak-health", "registry", "demo"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(f"{service_name} service: Running (PID: {process.pid})")
                else:
                    logger.info(f"{service_name} service: Terminated")
            else:
                logger.info(f"{service_name} service: Not running")
        return

    elif service in ("demo_crm", "demo_supervisor"):
        # Show status of all CRM/supervisor demo services
        for service_name in ["email-sink", "email-mcp", "crm-server", "registry", "demo"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(f"{service_name} service: Running (PID: {process.pid})")
                else:
                    logger.info(f"{service_name} service: Terminated")
            else:
                logger.info(f"{service_name} service: Not running")
        return

    elif service == "registry":
        if "registry" in direct_processes:
            process = direct_processes["registry"]
            if process.poll() is None:
                logger.info(f"Registry service: Running (PID: {process.pid})")
            else:
                logger.info("Registry service: Terminated")
        else:
            logger.info("Registry service: Not running")
        return

    elif service == "appworld":
        # Show status of both appworld services
        for service_name in ["appworld-environment", "appworld-api"]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    logger.info(
                        f"{service_name.replace('appworld-', '').capitalize()} service: Running (PID: {process.pid})"
                    )
                else:
                    logger.info(f"{service_name.replace('appworld-', '').capitalize()} service: Terminated")
            else:
                logger.info(f"{service_name.replace('appworld-', '').capitalize()} service: Not running")
        return

    elif service == "all":
        # Show direct processes status
        logger.info("Services:")
        for service_name in [
            "demo",
            "registry",
            "email-sink",
            "email-mcp",
            "crm-server",
            "oak-health",
            "docs-mcp",
            "appworld-environment",
            "appworld-api",
        ]:
            if service_name in direct_processes:
                process = direct_processes[service_name]
                if process.poll() is None:
                    display_name = (
                        service_name.replace('appworld-', 'appworld-')
                        if 'appworld-' in service_name
                        else service_name
                    )
                    logger.info(f"  {display_name}: Running (PID: {process.pid})")
                else:
                    display_name = (
                        service_name.replace('appworld-', 'appworld-')
                        if 'appworld-' in service_name
                        else service_name
                    )
                    logger.info(f"  {display_name}: Terminated")
            else:
                display_name = (
                    service_name.replace('appworld-', 'appworld-')
                    if 'appworld-' in service_name
                    else service_name
                )
                logger.info(f"  {display_name}: Not running")
        return

    # Validate service for any other service
    validate_service(service)


@app.command(
    help="Diagnose cuga's GPU stack — print device visibility, ONNX providers, "
    "torch CUDA, fastembed session, shm, image build flag. Copy-pasteable for "
    "support tickets.",
    short_help="GPU health check",
)
def doctor() -> None:
    """Print a structured GPU diagnosis.

    Reads the actual loaded runtime — not just config — so a misconfigured
    image (CPU build, missing libcudnn, --gpus all forgotten, ...) surfaces
    as a single readable report instead of multiple buried log lines. Designed
    to be run inside the container/pod with ``docker exec`` / ``kubectl exec``
    immediately after deploy.
    """
    import os as _os
    import platform as _platform
    import shutil as _shutil
    import subprocess as _subprocess

    def _row(label: str, value: str) -> None:
        typer.echo(f"  {label:<28} {value}")

    typer.echo("\n[cuga doctor] GPU stack diagnosis")
    typer.echo("=" * 60)

    # 1. Image / env signals
    typer.echo("\n[1] Image / environment")
    _row("python", _platform.python_version())
    _row("platform", f"{_platform.system()} {_platform.machine()}")
    _row("CUGA_GPU_BUILD", _os.environ.get("CUGA_GPU_BUILD", "(unset → CPU image)"))
    _row("CUGA_GPU_REQUIRED", _os.environ.get("CUGA_GPU_REQUIRED", "(unset → warn-only)"))

    # 2. NVIDIA driver visibility
    typer.echo("\n[2] NVIDIA driver visibility")
    nvsmi = _shutil.which("nvidia-smi")
    if not nvsmi:
        _row("nvidia-smi", "NOT FOUND on PATH (container can't see the GPU)")
    else:
        try:
            out = _subprocess.run(
                [nvsmi, "--query-gpu=name,driver_version,memory.total,memory.free", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                for line in out.stdout.strip().splitlines():
                    _row("gpu", line.strip())
            else:
                _row("nvidia-smi", f"exit {out.returncode}: {out.stderr.strip()[:120]}")
        except Exception as e:
            _row("nvidia-smi", f"ERROR: {e!r}")

    # 3. onnxruntime providers
    typer.echo("\n[3] onnxruntime")
    try:
        import onnxruntime as _ort

        providers = _ort.get_available_providers()
        _row("ort version", _ort.__version__)
        _row("available providers", str(providers))
        if "CUDAExecutionProvider" in providers:
            _row("status", "GPU-capable (CUDA)")
        elif "CoreMLExecutionProvider" in providers:
            _row("status", "GPU-capable (CoreML / Apple Silicon)")
        else:
            _row(
                "status",
                "CPU-only (GPU support is deferred to a follow-up release)",
            )
    except Exception as e:
        _row("onnxruntime", f"IMPORT FAILED: {e!r}")

    # 4. torch CUDA
    typer.echo("\n[4] torch / CUDA")
    try:
        import torch

        _row("torch version", torch.__version__)
        _row("cuda.is_available", str(torch.cuda.is_available()))
        if torch.cuda.is_available():
            _row("device count", str(torch.cuda.device_count()))
            _row("device 0 name", torch.cuda.get_device_name(0))
            _row("cudnn version", str(torch.backends.cudnn.version()))
            try:
                free, total = torch.cuda.mem_get_info(0)
                _row("device 0 mem", f"{free / (1024**3):.1f} GB free / {total / (1024**3):.1f} GB total")
            except Exception:
                pass
    except Exception as e:
        _row("torch", f"IMPORT FAILED: {e!r}")

    # 5. fastembed loaded providers (the actual runtime, not the list)
    typer.echo("\n[5] fastembed live session")
    try:
        from fastembed import TextEmbedding

        # Use the model the engine's auto-default uses so we test what users actually hit.
        m = TextEmbedding("BAAI/bge-small-en-v1.5")
        sess_providers: list[str] = []
        cur = m
        for _ in range(4):
            for a in ("model", "_model", "session", "_session", "ort_session"):
                obj = getattr(cur, a, None)
                if obj is None:
                    continue
                if hasattr(obj, "get_providers"):
                    sess_providers = list(obj.get_providers())
                    break
                cur = obj
                break
            if sess_providers:
                break
        _row("active providers", str(sess_providers) if sess_providers else "(could not introspect)")
        if sess_providers and "CUDAExecutionProvider" in sess_providers:
            _row("status", "embed will use GPU")
        elif sess_providers:
            _row("status", "embed will run on CPU")
    except Exception as e:
        _row("fastembed", f"FAILED: {e!r}")

    # 6. /dev/shm size (Docling DataLoader workers OOM at the 64 MB default)
    typer.echo("\n[6] /dev/shm")
    try:
        out = _subprocess.run(["df", "-h", "/dev/shm"], capture_output=True, text=True, timeout=3)
        for line in out.stdout.strip().splitlines()[-1:]:
            _row("size", line.strip())
        _row("recommended", ">= 2G for Docling transformers layout DataLoader")
    except Exception as e:
        _row("/dev/shm", f"NOT CHECKABLE: {e!r}")

    typer.echo("\n" + "=" * 60)
    typer.echo("Tip: paste this entire output into a support ticket or PR comment.\n")


@app.command(help="Test sandbox execution", short_help="Test sandbox")
def test_sandbox(
    remote: bool = typer.Option(
        False,
        "--remote",
        help="Test with remote sandbox (Docker/Podman) instead of local execution",
    ),
):
    """
    Test sandbox execution to verify code execution works correctly.

    Examples:
      cuga test-sandbox           # Test local sandbox (default)
      cuga test-sandbox --remote  # Test remote sandbox with Docker/Podman
    """
    try:
        from scripts.commands import test_sandbox as run_test

        if remote:
            # Ensure sandbox dependencies are available
            logger.info("Testing remote sandbox mode (requires --group sandbox)")
            run_test(remote=True)
        else:
            logger.info("Testing local sandbox mode")
            run_test(remote=False)

        logger.info("✅ Sandbox test completed successfully")
    except Exception as e:
        logger.error(f"❌ Sandbox test failed: {e}")
        raise typer.Exit(1)


@app.command(help="Evaluate Cuga on your test cases", short_help="Run Cuga Evaluation")
def evaluate(
    test_cases_file_path: str = typer.Argument(
        "",
        help="Path to your test cases file",
    ),
    output_file_path: str = typer.Argument(
        default="results.json",
        help="Path to your output file, it defaults to 'results.json'",
    ),
):
    """
    Run Cuga on your test cases.
    """
    # start the registry
    try:
        run_direct_service(
            "registry",
            [
                "uv",
                "run",
                "--no-sync",
                "uvicorn",
                "cuga.backend.tools_env.registry.registry.api_registry_server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(settings.server_ports.registry),
            ],
        )

        if direct_processes:
            console.print()
            console.print(
                Panel(
                    f"[bold white]Registry:[/bold white] [cyan]http://localhost:{settings.server_ports.registry}[/cyan]",
                    title="[bold yellow]Registry service is running. Press Ctrl+C to stop[/bold yellow]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )
            # Wait for registry to start
            logger.info("Waiting for registry to start...")
            wait_for_registry_server(settings.server_ports.registry)

            # Then start demo - using explicit fastapi command
            run_direct_service(
                "evaluation",
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "--group",
                    "dev",
                    os.path.join(PACKAGE_ROOT, "evaluation/evaluate_cuga.py"),
                    "-t",
                    test_cases_file_path,
                    "-r",
                    output_file_path,
                ],
            )
        wait_for_direct_processes()

    except Exception as e:
        logger.error(f"Error starting registry service: {e}")
        stop_direct_processes()
        raise typer.Exit(1)
    return


if __name__ == "__main__":
    app()
