"""Does the agent actually *invoke* the palette skill?

Discovery, prompt injection, and a working CLI are each necessary and none of
them is the thing that matters. What matters is the loop: the agent sees the
skill, calls `load_skill`, gets real instructions back, and acts on them.

These tests run the **real** `CugaLiteGraph` against the **real** installed
`palette` skill — no stub SKILL.md. A scripted model stands in for the LLM,
which means one link is deliberately not covered here: whether a real model
*chooses* to call the skill. That needs credentials and lives in the Tier 3
suite. Everything downstream of the decision is covered.

    Tier 2  (no LLM, no server)  — prompt -> load_skill -> instructions returned
    Tier 2+ (no LLM, live server) — ...and the instructions actually reach Palette

The second class skips unless a Palette server is up; start one with
`palette-skill serve ensure`.
"""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tests.e2e.skills.conftest import (
    CaptureChatModel,
    MinimalToolProvider,
    extract_system_content,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALLED_SKILL = REPO_ROOT / ".cuga" / "skills" / "palette"
PALETTE_URL = os.environ.get("PALETTE_URL", "http://127.0.0.1:18814")

pytestmark = pytest.mark.skipif(
    not (INSTALLED_SKILL / "SKILL.md").is_file(),
    reason=(
        "palette skill is not installed — run `make skill-install CUGA=<this repo>` "
        "from the project-palette checkout"
    ),
)


def palette_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{PALETTE_URL}/health", timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def install_real_skill(root: Path) -> Path:
    """Copy the installed palette skill into a throwaway skills root."""
    target = root / ".cuga" / "skills" / "palette"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(INSTALLED_SKILL, target)
    return target


def configure_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The settings a skills-enabled agent runs under (mirrors demo_palette)."""
    from cuga.config import settings

    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    monkeypatch.setattr(settings.policy, "enabled", False)


def build_graph(model: CaptureChatModel):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import create_cuga_lite_graph

    return create_cuga_lite_graph(
        model=model,
        tool_provider=MinimalToolProvider(),
        apps_list=[],
    ).compile()


async def run_graph(model: CaptureChatModel, prompt: str) -> str:
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState

    thread_id = f"palette_{uuid.uuid4().hex[:8]}"
    state = CugaLiteState(chat_messages=[HumanMessage(content=prompt)], thread_id=thread_id)
    await build_graph(model).ainvoke(
        state, config={"configurable": {"thread_id": thread_id, "apps_list": []}}
    )
    return thread_id


def conversation_text(model: CaptureChatModel) -> str:
    """Everything the model was shown, across every turn."""
    parts: list[str] = []
    for turn in model.captured_inputs:
        for message in turn:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


def code_block(body: str) -> AIMessage:
    return AIMessage(content=f"```python\n{body}\n```")


class TestAgentSeesTheSkill:
    """Before it can invoke anything, the skill has to be offered."""

    @pytest.mark.asyncio
    async def test_palette_is_advertised_in_the_system_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(responses=[AIMessage(content="Noted.")])
        await run_graph(model, "Build me a deck about vector databases")

        system = extract_system_content(model.captured_inputs[0])
        assert "palette" in system, "the skill is installed but never reaches the prompt"
        # The description is what a model routes on — assert the triggers survived.
        assert "deck" in system.lower() and "presentation" in system.lower()

    @pytest.mark.asyncio
    async def test_load_skill_is_offered_as_a_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(responses=[AIMessage(content="Noted.")])
        await run_graph(model, "Build me a deck")

        system = extract_system_content(model.captured_inputs[0])
        assert "load_skill" in system, "the agent is never told how to open the skill"


class TestAgentInvokesTheSkill:
    """The loop that matters: call load_skill, get real instructions back."""

    @pytest.mark.asyncio
    async def test_load_skill_returns_the_real_palette_playbook(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(
            responses=[
                code_block('result = await load_skill("palette")\nprint(result)'),
                AIMessage(content="I have the palette instructions."),
            ]
        )
        await run_graph(model, "Build me a deck about vector databases")

        assert len(model.captured_inputs) >= 2, (
            "the graph never came back to the model after load_skill — "
            "the skill output was not fed into the loop"
        )
        transcript = conversation_text(model)

        # The instructions the agent now has must be Palette's, not a stub's.
        for expected in (
            "palette-skill",  # the CLI it must drive
            "palette-skill deck",  # the one command that makes a deck
            "pptxgenjs",  # the thing it is forbidden to hand-write
        ):
            assert expected in transcript, (
                f"{expected!r} missing from what the agent received after load_skill"
            )

    @pytest.mark.asyncio
    async def test_agent_is_told_not_to_block_on_a_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The per-step limit is the whole reason the skill exists in this shape.

        A deck is three to ten minutes and no step is, so the instructions must
        describe a resumable loop rather than one blocking call. They must also
        say what ends it: every observed failure ended a turn mid-build, either
        by asking whether to keep going or by narrating progress with no command
        attached, and on this host a turn of plain prose reads as a final answer.
        """
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(
            responses=[
                code_block('print(await load_skill("palette"))'),
                AIMessage(content="Understood."),
            ]
        )
        await run_graph(model, "Build a deck")

        transcript = conversation_text(model)
        for expected, why in (
            ("--max-seconds", "no bounded poll — a blocking call would be killed by the step limit"),
            ('"done": true', "nothing tells the agent what ends the loop"),
            ("every turn you take must contain a `deck` call",
             "nothing stops the agent narrating progress instead of polling for it"),
            ("verified", "completion is left to the agent's belief rather than the filesystem"),
        ):
            assert expected in transcript, f"{expected!r} missing: {why}"

    @pytest.mark.asyncio
    async def test_unknown_skill_does_not_masquerade_as_palette(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo must fail loudly, not silently return nothing useful."""
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(
            responses=[
                code_block('print(await load_skill("palete"))'),
                AIMessage(content="That skill does not exist."),
            ]
        )
        await run_graph(model, "Build a deck")

        transcript = conversation_text(model)
        assert "Unknown skill" in transcript
        assert "palette" in transcript, "the error should name the skills that do exist"


@pytest.mark.manual
@pytest.mark.skipif(not palette_is_up(), reason=f"no Palette server at {PALETTE_URL}")
class TestAgentReachesPalette:
    """The full round trip, with a real Palette server on the other end.

    Still no LLM — the model is scripted — but every other component is real:
    the graph, the skill, the sandbox, the CLI, and the server.
    """

    @pytest.mark.asyncio
    async def test_agent_follows_the_skill_all_the_way_to_the_server(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(
            responses=[
                # 1. Open the skill, exactly as the prompt instructs.
                code_block('print(await load_skill("palette"))'),
                # 2. Do what it says: install the client, then ask the server
                #    whether it is alive and configured.
                code_block(
                    "out = await run_command("
                    '"uv pip install --quiet ./skills/palette/vendor/palette_skill-*.whl '
                    f'&& palette-skill --base-url {PALETTE_URL} health")\n'
                    "print(out)"
                ),
                AIMessage(content="Palette is reachable."),
            ]
        )
        await run_graph(model, "Build me a deck about vector databases")

        transcript = conversation_text(model)
        assert '"status": "ok"' in transcript or '"status":"ok"' in transcript, (
            "the agent followed the skill but never got a live answer from Palette.\n"
            f"Transcript tail:\n{transcript[-1500:]}"
        )
        assert "roster" in transcript, "the health payload did not come back intact"


@pytest.mark.e2e
@pytest.mark.skipif(not palette_is_up(), reason=f"no Palette server at {PALETTE_URL}")
class TestRealModelChoosesPalette:
    """Tier 3: does a real model *decide* to use the skill?

    Everything else in this file substitutes the model. This is the one test
    that does not — it uses the project's configured LLM and asserts on what
    the model chose, unprompted, from a plain deck request. Needs credentials
    (see conftest's `real_llm`) and a live Palette server.

    Step budget is deliberately small: the point is the routing decision and
    the first few instructed actions, not a full two-to-four minute build.
    """

    @pytest.mark.asyncio
    async def test_deck_request_routes_to_the_palette_skill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_llm
    ) -> None:
        from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
            CugaLiteState,
            create_cuga_lite_graph,
        )

        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)
        monkeypatch.setenv("PALETTE_URL", PALETTE_URL)

        graph = create_cuga_lite_graph(
            model=real_llm, tool_provider=MinimalToolProvider(), apps_list=[]
        ).compile()

        thread_id = f"palette_real_{uuid.uuid4().hex[:8]}"
        state = CugaLiteState(
            chat_messages=[
                HumanMessage(content="Build me a deck about vector databases for backend engineers.")
            ],
            thread_id=thread_id,
        )
        result = await graph.ainvoke(
            state,
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "apps_list": [],
                    "cuga_lite_max_steps": 4,
                }
            },
        )

        messages = result.get("chat_messages", []) if isinstance(result, dict) else []
        transcript = "\n".join(str(getattr(m, "content", "")) for m in messages)
        # Only what the agent itself wrote. The full transcript also contains
        # the skill body (load_skill output is fed back into the loop), which
        # legitimately mentions pptxgenjs while forbidding its use.
        authored = "\n".join(
            str(getattr(m, "content", "")) for m in messages if type(m).__name__ == "AIMessage"
        )

        assert "load_skill" in transcript, (
            "a plain deck request did not route to any skill. If this fails, the "
            "SKILL.md `description` is the thing to fix — regenerate and reinstall.\n"
            f"Transcript:\n{transcript[:2000]}"
        )
        assert 'load_skill("palette")' in transcript or "load_skill('palette')" in transcript, (
            f"a skill was loaded, but not palette.\nTranscript:\n{transcript[:2000]}"
        )
        # Having read the instructions, it should reach for the CLI they describe
        # rather than start hand-writing slide code.
        assert "palette-skill" in transcript, (
            "the skill was loaded but its instructions were not acted on"
        )
        hand_authoring = ("require('pptxgenjs')", 'require("pptxgenjs")', "new pptxgen",
                          "from pptx import", "import pptxgenjs")
        offenders = [marker for marker in hand_authoring if marker in authored]
        assert not offenders, (
            f"the agent started hand-writing deck code ({offenders}) instead of driving Palette"
        )
