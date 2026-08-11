"""Does the agent actually *invoke* the palette skill?

Discovery, prompt injection, and a working CLI are each necessary and none of
them is the thing that matters. What matters is the loop: the agent sees the
skill, calls `load_skill`, gets real instructions back, and acts on them.

These tests run the **real** `CugaLiteGraph` against the **real** installed
`palette` skill — no stub SKILL.md. A scripted model stands in for the LLM,
which means one link is deliberately not covered here: whether a real model
*chooses* to call the skill. That needs credentials and lives in the Tier 3
suite. Everything downstream of the decision is covered.

    Tier 2 (no LLM, no models) — prompt -> load_skill -> instructions returned
    Tier 3 (real LLM, real RITS) — ...and a real .pptx comes out the far end

Nothing here reaches a model or a Palette checkout, so it runs anywhere and in
seconds. Tier 3 is `test_palette_deck_e2e.py`.
"""

from __future__ import annotations

import os
import shutil
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

pytestmark = pytest.mark.skipif(
    not (INSTALLED_SKILL / "SKILL.md").is_file(),
    reason=(
        "palette skill is not installed — run `make skill-install CUGA=<this repo>` "
        "from the project-palette checkout"
    ),
)


def install_real_skill(root: Path) -> Path:
    """Copy the installed palette skill into a throwaway skills root."""
    target = root / ".cuga" / "skills" / "palette"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(INSTALLED_SKILL, target)
    return target


def configure_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The settings a skills-enabled agent runs under (mirrors demo_palette)."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
    from cuga.config import settings

    # The Seatbelt policy bakes in <cwd>/cuga_workspace as the only writable
    # tree, and the executor caches that it has been written. Production has one
    # cwd per process so that is fine; here each test has its own tmp_path, and
    # a stale policy silently denies every write — the sandbox just exits 1.
    # Drop the cached executor so the next call rebuilds the policy for this cwd.
    CodeExecutor._native_executor = None

    monkeypatch.setattr(settings.skills, "enabled", True)
    monkeypatch.setenv("CUGA_FOLDER", str(tmp_path / ".cuga"))
    monkeypatch.setattr(settings.advanced_features, "enable_shell_tool", True)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", False)
    # Mirrors demo_palette. Drafting a plan is a blocking model call of about a
    # minute, so the 30s default is not enough even for the cheap step.
    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 120)
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
            "palette.py",            # the CLI it must drive
            "build-plan",            # step one of the two-step workflow
            "build-deck",            # step two
            "pptxgenjs",             # the thing it is forbidden to hand-write
        ):
            assert expected in transcript, (
                f"{expected!r} missing from what the agent received after load_skill"
            )

    @pytest.mark.asyncio
    async def test_agent_is_told_not_to_block_on_a_build(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The per-step limit is the whole reason the skill ships a helper.

        `palette.py build-deck` blocks for three to ten minutes. No CUGA step
        does, so a direct call is killed part-way while the render keeps going
        — the work completes and nobody collects it. The instructions must
        describe the detached start-and-poll path, and must say what ends the
        loop: `verified`, computed from the filesystem rather than an exit code.
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
            ("deck.py start", "no detached build — a direct call is killed by the step limit"),
            ("deck.py status", "nothing tells the agent how to find out when it finished"),
            ('"done": true', "nothing tells the agent what ends the loop"),
            ("verified", "completion is left to the agent's belief rather than the filesystem"),
            (
                "timed out tells you",
                "a killed step reads as failure, and the agent gives up on a live build",
            ),
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


PALETTE_HOME = Path(
    os.environ.get("PALETTE_HOME", REPO_ROOT.parent / "project-palette-july25")
)


@pytest.mark.skipif(
    not (PALETTE_HOME / "palette.py").is_file(),
    reason=f"no Palette checkout at {PALETTE_HOME}; set PALETTE_HOME",
)
class TestAgentReachesPalette:
    """The full round trip into a real Palette checkout.

    Still no LLM — the model is scripted — but every other component is real:
    the graph, the skill, the sandbox, and `palette.py` itself. Deliberately
    uses commands that make **no model call**, so this tier stays fast and
    keeps working when the inference endpoint is unreachable. Whether the
    models produce a good deck is Tier 3's job; whether the sandbox can reach
    Palette at all is this one's, and that is the link that actually breaks.
    """

    @pytest.mark.asyncio
    async def test_the_sandbox_can_run_palette(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sandbox confines writes, not reads or exec — so this must work."""
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)
        monkeypatch.setenv("PALETTE_HOME", str(PALETTE_HOME))

        model = CaptureChatModel(
            responses=[
                code_block('print(await load_skill("palette"))'),
                code_block(
                    "out = await run_command("
                    f'"cd {PALETTE_HOME} && python palette.py --help")\n'
                    "print(out)"
                ),
                AIMessage(content="Palette is reachable."),
            ]
        )
        await run_graph(model, "Build me a deck about vector databases")

        transcript = conversation_text(model)
        for expected in ("build-plan", "edit-plan", "build-deck"):
            assert expected in transcript, (
                f"the agent could not list palette.py's commands ({expected!r} missing).\n"
                f"Transcript tail:\n{transcript[-1200:]}"
            )

    @pytest.mark.asyncio
    async def test_the_helper_starts_a_build_without_blocking_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole reason deck.py exists: `start` must return at once.

        A direct `build-deck` runs for minutes and a CUGA step is capped at
        120s, so the agent would be killed mid-render with the work continuing
        unseen. `start` spawns and returns, and `status` is what reports the
        outcome — including, here, an honest failure when the models cannot be
        reached, rather than a deck that does not exist.
        """
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)
        monkeypatch.setenv("PALETTE_HOME", str(PALETTE_HOME))

        # The plan is a fixture, not the thing under test: a sandbox may read
        # anywhere, so an absolute path outside the workspace is fine and keeps
        # write_file's escaping out of a test about starting a build.
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan\n\n## Slide 1\n- hello\n")

        model = CaptureChatModel(
            responses=[
                code_block('print(await load_skill("palette"))'),
                code_block(
                    "out = await run_command("
                    f'"python ./skills/palette/scripts/deck.py start '
                    f'--plan {plan} --out-dir ./deck")\n'
                    "print(out)"
                ),
                AIMessage(content="Started."),
            ]
        )
        thread_id = await run_graph(model, "Build me a deck about vector databases")

        transcript = conversation_text(model)
        # Not '"state": "running"' — SKILL.md says that too, so it would pass
        # whether or not anything ran. "pid" only ever comes from deck.py.
        assert '"pid":' in transcript, (
            "deck.py start did not report a spawned build.\n"
            f"Transcript tail:\n{transcript[-1500:]}"
        )

        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
            thread_workspace_root,
        )

        state_file = thread_workspace_root(thread_id) / "deck" / ".palette-build.json"
        assert state_file.is_file(), "no build state was written into the workspace"


class TestTheSecondTurnIsWhereDecksAreWonOrLost:
    """A deck cannot happen in one turn, so the reply is half the interaction.

    The confirmation gate means the agent presents a plan and stops. Whatever
    the user types next decides everything, and only one of the four things
    they might say should reach `build-deck`. These run the real graph against
    the real installed skill, so they fail if the instructions the agent
    actually receives stop covering a case.
    """

    @staticmethod
    def _instructions(model: CaptureChatModel) -> str:
        """The skill text as the agent received it, whitespace collapsed."""
        return " ".join(conversation_text(model).split())

    @pytest.mark.asyncio
    async def test_the_agent_is_told_how_to_read_each_reply(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        install_real_skill(tmp_path)
        configure_skills(monkeypatch, tmp_path)

        model = CaptureChatModel(
            responses=[
                code_block('print(await load_skill("palette"))'),
                AIMessage(content="Understood."),
            ]
        )
        await run_graph(model, "Build a deck about RAG")
        text = self._instructions(model)

        for expected, why in (
            ("Approval", "nothing tells the agent which reply builds"),
            ("A change", "an edit instruction has no route to edit-plan"),
            ("A question", "a question about the plan could be read as approval"),
            ("A different deck", "a new topic would be edited into the old plan"),
            ("is a change, not an approval", '"yes, but shorter" would build the rejected deck'),
            ("verbatim", "the agent may paraphrase the user into edit-plan"),
            ("Never build straight after an edit", "a revised plan could be built unapproved"),
        ):
            assert expected in text, why

    @pytest.mark.asyncio
    async def test_the_edit_route_exists_and_is_reachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`edit` has to be named with its flag, or the agent re-plans instead.

        Re-planning silently discards the version the user just read and
        reviewed — the most expensive way to handle "make it 3 slides".
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
        await run_graph(model, "Build a deck about RAG")
        text = self._instructions(model)

        assert "deck.py edit" in text, "no documented route to edit-plan"
        assert "--instruction" in text, "the agent does not know how to pass the change"
        assert "--context" in text, "pasted material has no route"

    @pytest.mark.asyncio
    async def test_the_gate_survives_auto_continue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The preset turns on auto-continue; the gate must still yield.

        `demo_palette` sets cuga_lite_nl_auto_continue so a progress note
        mid-build does not end the run. The confirmation gate is the opposite
        case — prose that *must* hand back to the user. CUGA's deterministic
        planning-text detector bails on second-person text, on anything over
        400 characters, and on questions; the gate wording is all three, so it
        falls through to the classifier, which yields on "a choice the user
        must make".

        This asserts the wording keeps those properties. A gate rewritten in
        the first person and under 400 characters would be auto-continued, and
        the agent would answer its own question.
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
            looks_like_planning_text,
        )

        skill = (INSTALLED_SKILL / "SKILL.md").read_text(encoding="utf-8")
        gate = [
            line.strip("> ").strip()
            for line in skill.splitlines()
            if line.strip().startswith(">") and "?" in line
        ]
        assert gate, "the skill no longer quotes a confirmation prompt"

        prompt = " ".join(gate)
        assert not looks_like_planning_text(prompt), (
            "the confirmation prompt reads as planning text, so CUGA would "
            "auto-continue past it and the user would never be asked"
        )
