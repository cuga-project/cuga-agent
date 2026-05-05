#!/usr/bin/env python3
"""Verify markitdown installation behavior inside OpenSandbox.

Run from the repository root:
    python verify.py

This creates/uses an OpenSandbox interpreter via OpenSandboxExecutor, runs shell
commands inside the sandbox, and prints the exact outputs around installing and
importing markitdown. It uses /tmp as the sandbox working directory, matching the
agent prompt and OpenSandbox skill upload paths.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (  # noqa: E402
    OpenSandboxExecutor,
)


async def run_step(run_command, label: str, command: str) -> None:
    print(f"\n===== {label} =====")
    print(f"$ {command}")
    output = await run_command(command)
    print(output)


async def main() -> None:
    executor = OpenSandboxExecutor()
    run_command = executor.create_run_command_tool(thread_id="verify-markitdown-install")

    await run_step(
        run_command,
        "Python and pip environment",
        "set -eux; pwd; which python || true; python --version; which pip || true; python -m pip --version",
    )

    # await run_step(
    #     run_command,
    #     "Install markitdown[pptx]",
    #     "uv pip install 'markitdown[pptx]'",
    # )

    await run_step(
        run_command,
        "Install markitdown[pptx]",
        "pnpm --version",
    )

    await asyncio.sleep(5)

    await run_step(
        run_command,
        "Immediately after install: import and CLI check",
        "python - <<'PY'\nimport importlib.metadata, importlib.util, shutil\nprint('markitdown spec:', importlib.util.find_spec('markitdown'))\nprint('markitdown distribution version:', importlib.metadata.version('markitdown'))\nprint('markitdown cli:', shutil.which('markitdown'))\nPY\npython -m markitdown --help | head -40",
    )

    await run_step(
        run_command,
        "Second shell after install: import and CLI check",
        "python - <<'PY'\nimport importlib.metadata, importlib.util, shutil\nprint('markitdown spec:', importlib.util.find_spec('markitdown'))\nprint('markitdown distribution version:', importlib.metadata.version('markitdown'))\nprint('markitdown cli:', shutil.which('markitdown'))\nPY\npython -m markitdown --help | head -40",
    )


if __name__ == "__main__":
    asyncio.run(main())
