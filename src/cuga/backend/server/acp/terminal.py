"""Interactive text console for DAM's terminal attachment."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stderr, redirect_stdout
import sys
from typing import Any, TextIO


class _DiscardTextStream:
    """Keep library prints out of the terminal, including prompt data in logs."""

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        pass


def _terminal_text(value: str) -> str:
    """Remove control characters that could alter the attached terminal."""
    return "".join(char for char in value if char in "\n\t" or char.isprintable())


class _TerminalClient:
    def __init__(self, input_stream: TextIO, output_stream: TextIO) -> None:
        self.input = input_stream
        self.output = output_stream
        self.has_text = False
        self.last_character = ""

    def write(self, value: str) -> None:
        self.output.write(value)
        self.output.flush()

    def read(self, prompt: str) -> str | None:
        self.write(prompt)
        line = self.input.readline()
        return None if line == "" else line.rstrip("\r\n")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        del session_id, kwargs
        content = getattr(update, "content", None)
        if getattr(content, "type", None) != "text":
            return
        value = _terminal_text(content.text)
        if not value:
            return
        if not self.has_text:
            self.write("CUGA> ")
        self.write(value)
        self.has_text = True
        self.last_character = value[-1]

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any
    ) -> Any:
        del session_id, options, kwargs
        from acp.schema import AllowedOutcome, RequestPermissionResponse

        if self.has_text and self.last_character != "\n":
            self.write("\n")
        title = _terminal_text(str(tool_call.title))
        answer = self.read(f"CUGA requests permission: {title}\nAllow once? [y/N] ")
        option_id = "allow-once" if answer and answer.strip().lower() in {"y", "yes"} else "reject-once"
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId=option_id))

    def finish_turn(self) -> None:
        if self.has_text and self.last_character != "\n":
            self.write("\n")
        self.has_text = False
        self.last_character = ""


async def run_console(
    runner: Any | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    """Run one terminal attachment using the same CUGA agent as ACP stdio."""
    from acp.schema import TextContentBlock

    from cuga.backend.server.acp import create_agent

    client = _TerminalClient(input_stream or sys.stdin, output_stream or sys.stdout)
    discard = _DiscardTextStream()
    with redirect_stdout(discard), redirect_stderr(discard):
        agent = create_agent(runner)
        agent.on_connect(client)
        try:
            session = await agent.new_session(cwd="/home/agent/work")
            client.write("CUGA chat. Type :help for commands.\n")
            while True:
                message = client.read("You> ")
                if message is None or message.strip() in {":quit", ":exit"}:
                    client.write("\n")
                    return 0
                message = message.strip()
                if not message:
                    continue
                if message == ":help":
                    client.write(":new  Start a fresh conversation\n:quit Exit CUGA chat\n")
                    continue
                if message == ":new":
                    session = await agent.new_session(cwd="/home/agent/work")
                    client.write("Started a fresh conversation.\n")
                    continue
                try:
                    await agent.prompt(
                        session_id=session.session_id,
                        prompt=[TextContentBlock(type="text", text=message)],
                    )
                except Exception as exc:
                    client.finish_turn()
                    client.write(f"CUGA> Request failed ({type(exc).__name__}).\n")
                else:
                    client.finish_turn()
        finally:
            await agent.shutdown()


def main() -> int:
    from cuga.backend.server.acp.stdio import configure_logging

    configure_logging()
    try:
        return asyncio.run(run_console())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
