"""Manually exercise a DAM image through ACP over Docker stdio.

Run from this checkout after installing its ``acp`` extra. No DAM CLI or
platform service is needed; a prompt does need your own model endpoint/key.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    RequestPermissionResponse,
    TextContentBlock,
)


CONTAINER_CWD = "/home/agent/work"


class ManualClient:
    """Show ACP updates and ask the operator before granting permissions."""

    def __init__(self) -> None:
        self.agent_text_seen = False

    async def session_update(self, session_id: str, update: Any, **_kwargs: Any) -> None:
        kind = getattr(update, "session_update", "")
        if kind == "agent_message_chunk":
            content = getattr(update, "content", None)
            text = getattr(content, "text", None)
            if isinstance(text, str):
                print(text, end="", flush=True)
                self.agent_text_seen = True
        elif kind in {"tool_call", "tool_call_update"}:
            print(f"\n[ACP {kind}] {getattr(update, 'title', '')}", flush=True)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **_kwargs: Any
    ) -> RequestPermissionResponse:
        del session_id
        print(f"\n[ACP permission] {getattr(tool_call, 'title', 'CUGA action')}", flush=True)
        for option in options:
            print(f"  {option.kind}: {option.name}", flush=True)
        if not sys.stdin.isatty():
            print("No interactive terminal; permission cancelled.", flush=True)
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        try:
            choice = (await asyncio.to_thread(input, "Type allow-once to approve; Enter rejects: ")).strip()
        except EOFError:
            choice = ""
        wanted = "allow_once" if choice == "allow-once" else "reject_once"
        option = next((item for item in options if item.kind == wanted), None)
        if option is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", optionId=option.option_id)
        )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Image tag or digest to launch")
    parser.add_argument("--check", action="store_true", help="Handshake and session check only (default)")
    parser.add_argument("--prompt", help="Send a text prompt after the handshake")
    parser.add_argument("--volume", help="Docker named volume for persistent /home/agent state")
    parser.add_argument("--session", help="Load an existing session ID from --volume")
    args = parser.parse_args()
    if args.check and args.prompt:
        parser.error("--check and --prompt cannot be combined")
    if args.session and not args.volume:
        parser.error("--session requires --volume")
    if args.prompt:
        missing = [
            name for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "MODEL_NAME") if not os.getenv(name)
        ]
        if missing:
            parser.error("a prompt requires these host environment variables: " + ", ".join(missing))
        if os.environ["OPENAI_API_KEY"] == "dummy-placeholder":
            parser.error("replace the image's dummy-placeholder with your own model API key")
    if shutil.which("docker") is None:
        parser.error("docker is not on PATH")
    return args


def _docker_command(args: argparse.Namespace) -> list[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--platform",
        "linux/amd64",
        "--entrypoint",
        "/usr/local/bin/harness-chat",
    ]
    if args.volume:
        # The image owns /home/agent as UID 65532. Docker copies that ownership
        # into a new named volume, so the unprivileged harness can create work/.
        command.extend(["--mount", f"type=volume,source={args.volume},target=/home/agent"])
    else:
        command.extend(
            [
                "-e",
                "CUGA_FOLDER=/tmp/cuga",
                "-e",
                "DYNACONF_POLICY__CUGA_FOLDER=/tmp/cuga",
                "-e",
                "CUGA_DBS_DIR=/tmp/cuga/dbs",
            ]
        )
    if args.prompt:
        for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "MODEL_NAME"):
            command.extend(["-e", name])
    command.append(args.image)
    return command


async def _run(args: argparse.Namespace) -> None:
    client = ManualClient()
    print(f"Image: {args.image}")
    print("Launching /usr/local/bin/harness-chat over Docker stdin/stdout...")
    async with spawn_agent_process(
        client,
        *_docker_command(args),
        env=os.environ.copy(),
        receive_timeout=300,
        transport_kwargs={"shutdown_timeout": 5, "stderr": None},
    ) as (connection, _process):
        initialized = await asyncio.wait_for(
            connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="local-docker-acp-test", version="1"),
            ),
            timeout=120,
        )
        if initialized.agent_info.name != "cuga":
            raise RuntimeError(f"unexpected ACP agent: {initialized.agent_info.name}")
        print(f"ACP initialize: {initialized.agent_info.name} (protocol {initialized.protocol_version})")

        if args.session:
            print(f"ACP session/load: {args.session}")
            await asyncio.wait_for(
                connection.load_session(cwd=CONTAINER_CWD, session_id=args.session, mcp_servers=[]),
                timeout=120,
            )
            session_id = args.session
            print("\nLoaded transcript above, if the session had messages.")
        else:
            session = await asyncio.wait_for(
                connection.new_session(cwd=CONTAINER_CWD, mcp_servers=[]),
                timeout=120,
            )
            session_id = session.session_id
            print(f"ACP session/new: {session_id}")

        listed = await asyncio.wait_for(connection.list_sessions(cwd=CONTAINER_CWD), timeout=30)
        if session_id not in {item.session_id for item in listed.sessions}:
            raise RuntimeError("ACP session/list omitted the active session")
        print(f"ACP session/list: {len(listed.sessions)} session(s); active session found")

        if args.prompt:
            # A loaded transcript can contain earlier agent text. Count only
            # chunks emitted by this prompt when judging its result.
            client.agent_text_seen = False
            print("ACP session/prompt: waiting for a text response...")
            response = await asyncio.wait_for(
                connection.prompt(session_id, [TextContentBlock(type="text", text=args.prompt)]),
                timeout=300,
            )
            print(f"\nACP prompt stopReason: {response.stop_reason}")
            if response.stop_reason != "end_turn" or not client.agent_text_seen:
                raise RuntimeError("prompt did not finish with an agent text response")
        else:
            print("Protocol check passed. A model prompt was not sent.")

        print(f"Session ID: {session_id}")


def main() -> int:
    args = _arguments()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ACP test failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
