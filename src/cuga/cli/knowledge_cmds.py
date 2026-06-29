"""``cuga knowledge`` sub-command tree — settings, client adaptation, snapshots.

Extracted from ``cli/main.py`` per Sami's review ("Make knowledge as cli
module to avoid overloading of this file"). ``cli/main.py`` was approaching
3000 lines with this surface and the broader ``cuga start`` / ``cuga
stop`` machinery interleaved; the knowledge subcommands are independent
enough to live in their own module and be imported back via
``app.add_typer(knowledge_app, name="knowledge")`` in main.

Wire is unchanged: every command still talks to the running cuga server
over HTTP (``/api/knowledge/settings``, ``/api/manage/config``,
``/api/manage/config/draft/knowledge``). No behavior change from this
extraction — pure file move.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
import typer
from loguru import logger

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
        logger.error(f"Failed to fetch knowledge settings from {url}: {exc}")
        raise typer.Exit(code=1)

    knowledge_cfg = payload.get("knowledge", payload)
    if field:
        if field not in knowledge_cfg:
            logger.error(f"Unknown field {field!r}. Available: {sorted(knowledge_cfg.keys())}")
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
        logger.error(f"Failed to update knowledge settings: {exc}")
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
        logger.error(f"Failed to fetch manage config from {url}: {exc}")
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
        logger.error(f"Failed to write snapshot to {output}: {exc}")
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
        logger.error(f"Failed to read snapshot file {input}: {exc}")
        raise typer.Exit(code=1)
    except _json.JSONDecodeError as exc:
        logger.error(f"Snapshot file {input} is not valid JSON: {exc}")
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
        logger.error(f"Failed to apply snapshot via PATCH {url}: {exc}")
        raise typer.Exit(code=1)

    typer.echo(
        f"Knowledge snapshot imported (agent_id={target_agent_id}). "
        f"Run `cuga knowledge config-get` to verify the new live values."
    )


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
