"""Demo test: create a real .pptx file using the anthropics/skills pptx skill.

This is a visual-confirmation test, not a unit test.  Its job is to prove that
the full skill pipeline — discovery → load → companion guide → code execution →
file output — produces a file you can actually open in PowerPoint or LibreOffice.

What happens end-to-end
-----------------------
1. Downloads ``SKILL.md`` and ``pptxgenjs.md`` from github.com/anthropics/skills.
2. Pre-installs pptxgenjs (Node.js) locally in the temp working directory.
3. Provides a ``run_node_script`` function-calling tool the agent can use to
   actually execute a Node.js script (without needing a code-execution sandbox).
4. Creates a CugaAgent in tools mode with both ``load_skill`` and
   ``run_node_script`` bound.
5. The agent loads the pptx skill → reads the pptxgenjs companion guide →
   writes a pptxgenjs script → calls ``run_node_script`` to run it →
   a real .pptx file lands in the working directory.
6. Copies the result to ``tests/e2e/demo_outputs/`` and prints the path.

Run with
--------
    uv run pytest tests/e2e/skills/test_skills_presentation_e2e.py -m e2e -v -s

After the test open the file path printed to stdout.
"""

from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.tools import tool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface

if TYPE_CHECKING:
    from cuga.sdk import CugaAgent

_GITHUB_RAW = "https://raw.githubusercontent.com/anthropics/skills/main/skills/pptx"
_DEMO_OUTPUTS = Path(__file__).parent / "demo_outputs"

_TASK = """
Use the pptx skill to create a 5-slide presentation titled "Agent Skills in Practice".

Slide outline:
  1. Title slide — "Agent Skills in Practice", subtitle "From Discovery to Execution"
  2. What Are Skills? — reusable SKILL.md playbooks that give AI agents domain-specific
     procedural knowledge
  3. How It Works — skill discovery → load_skill → companion guides → execution
  4. Real-World Skill Examples — pptx, deploy-to-vercel, react-best-practices, docx
  5. Summary — "Skills turn general-purpose agents into domain experts"

Design: dark color scheme (navy or charcoal background, white text), colored accent
shapes, concise bullets (max 4 per slide).

When you have the pptxgenjs script ready, call run_node_script to execute it.
The script must end with pres.writeFile({ fileName: "presentation.pptx" }) so the
file lands in the current directory.
"""


def _fetch(path: str) -> str:
    """Download a file from the anthropics/skills pptx directory. Skip if unreachable."""
    url = f"{_GITHUB_RAW}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"GitHub unreachable ({exc}); skipping presentation demo test")


def _install_skill_files(tmp_path: Path) -> None:
    """Place the real pptx SKILL.md and pptxgenjs companion guide in the skills directory."""
    skill_dir = tmp_path / ".cuga" / "skills" / "pptx"
    skill_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / "SKILL.md").write_text(_fetch("SKILL.md"), encoding="utf-8")

    pptxgenjs_md = _fetch("pptxgenjs.md")
    (skill_dir / "pptxgenjs.md").write_text(pptxgenjs_md, encoding="utf-8")
    # Also at cwd root — agents sometimes look for companions via a bare filename.
    (tmp_path / "pptxgenjs.md").write_text(pptxgenjs_md, encoding="utf-8")


def _npm_install_pptxgenjs(tmp_path: Path) -> None:
    """Pre-install pptxgenjs so the agent can require() it immediately."""
    result = subprocess.run(
        ["npm", "install", "pptxgenjs", "--prefix", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.skip(f"npm install pptxgenjs failed: {result.stderr[:300]}")


class NodeExecutorToolProvider(ToolProviderInterface):
    """Exposes a single ``run_node_script`` tool that executes a pptxgenjs script.

    This gives the agent a real execution mechanism without needing the full
    code-execution sandbox.  The script runs in ``workdir`` so that
    ``require('pptxgenjs')`` resolves against ``workdir/node_modules/``.
    """

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir

    async def initialize(self) -> None:
        pass

    async def get_apps(self) -> list:
        return []

    async def get_all_tools(self) -> list:
        workdir = self._workdir

        @tool
        async def run_node_script(script: str) -> str:
            """Execute a Node.js pptxgenjs script to produce a .pptx file.

            Write a complete self-contained script that ends with
            pres.writeFile({ fileName: "presentation.pptx" }).
            Returns stdout / stderr so you can verify success.
            """
            script_path = workdir / "create_presentation.js"
            script_path.write_text(script, encoding="utf-8")

            env = {**os.environ, "NODE_PATH": str(workdir / "node_modules")}
            proc = subprocess.run(
                ["node", str(script_path)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(workdir),
                env=env,
            )
            if proc.returncode != 0:
                return f"Exit {proc.returncode}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
            return proc.stdout.strip() or "Script completed (no stdout)."

        return [run_node_script]

    async def get_tools(self, app_name: str) -> list[Any]:
        return []


def _make_sdk_agent(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> "CugaAgent":
    from cuga.config import settings
    from cuga.sdk import CugaAgent

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    # Tools mode: load_skill and run_node_script are bound as function-call tools.
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_bind_tools_mode", "tools")
    monkeypatch.setattr(
        settings.advanced_features,
        "cuga_lite_bind_tools_tool_names",
        ["load_skill", "run_node_script"],
    )
    # Allow multi-turn: the agent writes a planning step before calling load_skill.
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)
    monkeypatch.setattr(settings.policy, "enabled", False)

    return CugaAgent(
        model=real_llm,
        enable_skills=True,
        skills_folder=str(tmp_path),
        enable_knowledge=False,
        tool_provider=NodeExecutorToolProvider(tmp_path),
    )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_pptx_skill_creates_real_presentation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_llm,
) -> None:
    """Agent uses the real anthropics/skills pptx skill to produce a .pptx file.

    The assertion is minimal: the file must exist and be non-trivially sized.
    The primary value is visual — open the file and confirm a real presentation
    was generated.
    """
    _install_skill_files(tmp_path)
    _npm_install_pptxgenjs(tmp_path)

    agent = _make_sdk_agent(tmp_path=tmp_path, monkeypatch=monkeypatch, real_llm=real_llm)

    result = await agent.invoke(
        _TASK,
        thread_id=f"pptx_demo_{uuid.uuid4().hex[:8]}",
    )

    print(f"\n[pptx_demo] agent answer:\n{result.answer[:800]}")

    pptx_files = list(tmp_path.glob("**/*.pptx"))
    assert pptx_files, (
        f"No .pptx file found in {tmp_path}.\n"
        f"Agent answer: {result.answer[:500]!r}\n"
        f"Working dir: {sorted(str(p.name) for p in tmp_path.iterdir())}"
    )

    output_file = max(pptx_files, key=lambda p: p.stat().st_size)
    assert output_file.stat().st_size > 1_000, (
        f"Output file {output_file.name} is suspiciously small ({output_file.stat().st_size} bytes)."
    )

    _DEMO_OUTPUTS.mkdir(parents=True, exist_ok=True)
    dest = _DEMO_OUTPUTS / f"presentation_{uuid.uuid4().hex[:6]}.pptx"
    dest.write_bytes(output_file.read_bytes())

    print(f"\n{'=' * 60}")
    print("  Presentation saved — open to visually inspect:")
    print(f"  {dest}")
    print(f"  Size: {dest.stat().st_size:,} bytes")
    print(f"{'=' * 60}\n")
